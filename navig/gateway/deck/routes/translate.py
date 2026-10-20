"""Deck routes for NAVIG Translate — select a passage, translate it, write it back.

    POST /api/deck/translate   {text, target, hint?} → {ok, data: {text, source, target, chars}}

The Dock's Translate feature (context menu **🌐 Translate with NAVIG**, or the bar's
translate button) replaces the selection in the page with its translation, so this route is
the whole engine behind it. Contract:

* ``target`` is REQUIRED and must be a bare language code — ``en``, ``fr``, ``ru``,
  ``pt-BR`` — matched against :data:`_LANG_CODE`. A code, never prose: it enters the prompt,
  so its shape is validated before it is used. Anything else is 400 ``bad_target``.
* ``text`` is REQUIRED, non-empty after strip, at most :data:`MAX_CHARS` characters. A
  selection can be a whole page; a longer one is refused with the limit stated in the
  message (400 ``too_long``) — never silently truncated, the same rule as voice's chunking.
* ``hint`` (alias ``source``) is the client's language hint — the page's ``lang`` near the
  selection. Only a clean code is used as a hint; anything else is dropped (hints are
  advisory, so a weird page language is not an error). With no usable hint the route asks
  ``navig_audio`` to guess the source language — best effort inside ``try/except``: the
  plugin may not be installed, and detection failure means ``source: ""``, the honest
  "unknown", never a hard dependency on a plugin.
* The engine is NAVIG's one LLM seam (:func:`navig.llm.generate.run_llm`) with the builtin
  ``translate/selection`` prompt (:func:`navig.prompts.loader.load_prompt`), temperature
  capped low. The user message carries ONLY the target, the source hint and the passage —
  delimited inside ``<document>`` tags and declared by the prompt to be data, so nothing in
  a page selection can act as an instruction.
* Responses: ``{ok, data: {text, source, target, chars}}``, where ``source`` is the hint or
  detection actually used ("" when unknown — honest), or ``{ok: false, error, code}`` with
  400 (validation) · 502 ``translate_failed`` (the engine said why not) · 503
  ``llm_required`` (no AI connection answers — the same honesty as ``/api/deck/ask``).

Auth is the deck's own: this module registers its route like every other ``/api/deck/*``
route, so ``deck_auth_middleware`` already answers for it (loopback + trusted Origin +
brain-link). Nothing here re-implements it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)

#: Largest selection one request may carry. Beyond this the client is told the limit
#: instead of receiving a silently shortened translation.
MAX_CHARS = 5000
#: A bare language code: ``en`` · ``fr`` · ``pt-BR`` · ``zh-Hans``. Prompt-safe by shape —
#: letters only, so a target can never smuggle instructions into the prompt.
_LANG_CODE = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")
#: The system prompt this route runs (builtin store, no prompt text in source).
_PROMPT_SLUG = "translate/selection"
#: How the engine failed when NO provider could answer (ask.py's own markers).
_NO_PROVIDER_MARKERS = ("error", "all_fallbacks_failed")


class _NoProvider(RuntimeError):
    """No AI connection answered at all — the caller maps this to 503 ``llm_required``."""


def _ok(data: Any) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": True, "data": data})


def _err(msg: str, status: int, code: str) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": False, "error": msg, "code": code}, status=status)


def _valid_code(value: Any) -> str:
    """``value`` as a language code, or "" when it is not one (never an error: hints are
    advisory — a page's ``lang`` like ``en-US-x-internal`` must not break a translation)."""
    if not isinstance(value, str):
        return ""
    code = value.strip()
    return code if _LANG_CODE.match(code) else ""


async def _detect_source(text: str) -> str:
    """Best-effort source language from navig_audio, when that plugin happens to exist.

    Never a dependency: an absent or broken plugin, or a text it cannot judge, all mean
    "" (unknown) — the response says what was actually used.
    """
    try:
        from navig_audio.reader import detect_language

        return (await asyncio.to_thread(detect_language, text)) or ""
    except Exception:  # noqa: BLE001 — plugin absent or unsure: unknown is the honest answer
        return ""


def _user_message(text: str, target: str, source: str) -> str:
    """The passage as DATA: target + hint around a delimited document, no instructions."""
    return (
        f"Target language: {target}\n"
        f"Source language: {source or 'unknown'} (a hint — verify it, do not trust it blindly)\n"
        "\n"
        "<document>\n"
        f"{text}\n"
        "</document>"
    )


def _run_translation(text: str, target: str, source: str) -> str:
    """Run one translation through NAVIG's LLM seam. Raises on every failure, saying why."""
    from navig.llm.generate import run_llm
    from navig.prompts.loader import load_prompt

    system = load_prompt(_PROMPT_SLUG)
    if system.startswith("Warning: Prompt"):  # the loader's miss sentinel — never send it
        raise RuntimeError(f"prompt {_PROMPT_SLUG} is missing")
    result = run_llm(
        [{"role": "system", "content": system}, {"role": "user", "content": _user_message(text, target, source)}],
        user_input=text,
        temperature=0.2,  # a faithful translation, not a creative one
        max_tokens=6000,  # a 5,000-char selection must not be cut off mid-sentence
    )
    out = (getattr(result, "content", "") or "").strip()
    if not out:
        finish = str(getattr(result, "finish_reason", "") or "")
        if finish.startswith(_NO_PROVIDER_MARKERS):
            raise _NoProvider(finish)
        raise RuntimeError(finish or "no AI provider answered")
    return out


async def handle_translate(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON is a 400, not a 500
        body = None
    if not isinstance(body, dict):
        return _err("Body must be a JSON object", 400, "bad_request")

    target = body.get("target")
    target = target.strip() if isinstance(target, str) else ""
    if not _LANG_CODE.match(target):
        return _err(
            "target must be a language code such as en, fr, ru or pt-BR",
            400,
            "bad_target",
        )

    raw = body.get("text")
    if not isinstance(raw, str) or not raw.strip():
        return _err("Missing 'text'", 400, "bad_request")
    text = raw.strip()
    if len(text) > MAX_CHARS:
        return _err(
            f"Text is {len(text)} characters; translate at most {MAX_CHARS} at a time "
            "(select a shorter passage)",
            400,
            "too_long",
        )

    # The client's hint (``hint``; ``source`` accepted too), else best-effort detection.
    source = _valid_code(body.get("hint")) or _valid_code(body.get("source"))
    if not source:
        source = await _detect_source(text)

    try:
        translated = await asyncio.to_thread(_run_translation, text, target, source)
    except _NoProvider as exc:
        logger.info("deck translate: no AI provider answered (%s)", str(exc)[:200])
        return _err(
            "no AI provider available — add a provider in Settings → Vault or run `navig connect`",
            503,
            "llm_required",
        )
    except Exception as exc:  # noqa: BLE001 — the engine's own reason, as a 502
        logger.warning("deck translate failed: %s", exc)
        return _err(str(exc), 502, "translate_failed")

    return _ok({"text": translated, "source": source, "target": target, "chars": len(text)})


def register(app: "web.Application") -> None:
    app.router.add_post("/api/deck/translate", handle_translate)
