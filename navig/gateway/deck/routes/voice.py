"""Deck routes for NAVIG Voice — the reader surface every client shares (Dock, OS, remote).

    GET  /api/deck/voice/catalog   engines usable here (cost, price, fr/ru/en voices, real
                                   controls), the voice profiles, the policy and budget
    POST /api/deck/voice/speak     {text, lang?, provider?, voice?, profile?} → audio/mpeg
    GET  /api/deck/voice/usage     paid characters today / last 7 days, against the budget
    POST /api/deck/voice/settings  {policy?, daily_char_budget?, paid_fallback?, stt_model?}
    POST /api/deck/voice/transcribe  multipart {audio, lang?, mode?, provider?} → {text, raw, …}
    POST /api/deck/voice/warm      load the on-device speech model now (202; the Dock calls it
                                   when recording starts, so the words come back fast)

Transcribe contract (dictation): the recording goes to the engine the voice policy allows —
on-device Whisper for auto / free / offline, a keyed cloud engine only when named (or under
premium). Nothing but the audio is sent: no page text, no URL, no clipboard. ``mode`` shapes
the words afterwards (``verbatim`` never rewrites; ``notes`` / ``prompt`` use NAVIG's LLM
seam and fall back to ``clean`` when no model answers, saying so in ``transform_error``).

Why these live under ``/api/deck`` and not next to ``/api/voice/*``: deck auth already
answers every caller NAVIG has — the local desktop and the Dock extension over loopback
(a website's Origin is refused), and a remote client through Lighthouse with the deck key
or a brain-link device token. One route serves all of them; nothing new to pair.

Speak contract:

* **One chunk per request, at most** :data:`MAX_CHARS`. The client splits long text on
  sentence boundaries. A bounded chunk keeps the answer well under Lighthouse's 1 MB frame
  and means text is never silently truncated (the engine cuts at 4,096).
* **The policy decides, server-side** (``voice.policy``: auto · free · premium · offline —
  :func:`navig_audio.voice.catalog.route`). Auto never pays unless a provider is named (in
  the request or the chosen voice profile — naming it is the opt-in). Free never pays.
  Offline never sends text out.
* **Paid speech is budgeted.** Before a paid request, today's paid characters plus this
  chunk must fit ``voice.daily_char_budget`` (default 20,000; 0 = no paid speech), else 429
  ``budget_exceeded``. A cache hit costs nothing and is served regardless. Every billed
  request is recorded in the usage ledger (navig_audio.voice.usage).
* **Paid fallback is opt-in** (``voice.paid_fallback``, default off): only then may Auto
  retry a failed free engine on a keyed paid one — still within the budget, and said in
  ``X-Navig-Voice-Fallback``.
* **Speed is the client's job** (``playbackRate``): audio is synthesized at 1.0, so one
  cached clip serves every speed. A profile's speed comes back as ``X-Navig-Voice-Rate``.

Responses: audio bytes with ``X-Navig-Voice-Provider`` / ``-Id`` / ``-Rate`` / ``-Ignored`` /
``-Note``, or ``{"ok": false, "error": …, "code": …}`` with 400/404/413/422/429/502/503.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiohttp import web

logger = logging.getLogger(__name__)

#: Largest chunk one request may carry. ~1,200 chars ≈ 75 s of speech ≈ 450 KB of Edge mp3.
MAX_CHARS = 1200
_LANG_OK = {"fr", "ru", "en"}
#: Concurrent syntheses across all clients — a runaway client cannot fan out unbounded work.
_SPEAK_SLOTS = asyncio.Semaphore(4)
DEFAULT_DAILY_BUDGET = 20000
#: Largest recording one dictation may send (≈ 25 min of Opus; the client stops at 5 min).
MAX_AUDIO_BYTES = 25 * 1024 * 1024
_STT_MODELS = ("tiny", "base", "small", "medium")
_AUDIO_SUFFIX = {"audio/webm": ".webm", "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav",
                 "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}


def _ok(data: Any) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": True, "data": data})


def _err(msg: str, status: int, code: str) -> "web.Response":
    from aiohttp import web

    return web.json_response({"ok": False, "error": msg, "code": code}, status=status)


def _plugin_required() -> "web.Response":
    return _err(
        "Reading aloud needs the navig-audio plugin (navig store install pip:navig-audio)",
        503,
        "plugin_required",
    )


def voice_settings() -> dict[str, Any]:
    """The operator's voice policy, read fresh from config (raw strings tolerated).

    Fresh means ``get_live``: the daemon caches config for its whole life, so a
    ``navig voice policy offline`` from a terminal would otherwise stay invisible
    until a restart — and the daemon would keep sending text to a cloud engine after
    the operator turned that off. One ``stat`` when nothing changed.
    """
    from navig.config import get_config_manager
    from navig.core.coerce import coerce_bool, coerce_int

    cm = get_config_manager()
    policy = str(cm.get_live("voice.policy", "auto") or "auto").strip().lower()
    if policy not in ("auto", "free", "premium", "offline"):
        policy = "auto"
    stt_model = str(cm.get_live("voice.stt_model", "base") or "base").strip().lower()
    return {
        "policy": policy,
        "daily_char_budget": coerce_int(cm.get_live("voice.daily_char_budget", DEFAULT_DAILY_BUDGET), DEFAULT_DAILY_BUDGET, minimum=0),
        "paid_fallback": coerce_bool(cm.get_live("voice.paid_fallback", False), default=False),
        "stt_model": stt_model if stt_model in _STT_MODELS else "base",
    }


def _profiles() -> list[dict[str, Any]]:
    try:
        from navig.presets import catalog

        default = catalog.get_default("speech")
        return [
            {
                "id": p.id, "name": p.name, "description": p.description, "params": dict(p.params),
                "source": p.source, "default": p.id == default,
            }
            for p in catalog.list_presets(tool="speech")
        ]
    except Exception as exc:  # noqa: BLE001 — a broken preset store must not hide the engines
        logger.warning("voice profiles unavailable: %s", exc)
        return []


async def handle_catalog(request: "web.Request") -> "web.Response":
    try:
        from navig_audio.voice.catalog import voice_catalog
        from navig_audio.voice.usage import spent_today
    except ImportError:
        return _plugin_required()

    def _build() -> dict[str, Any]:
        data = voice_catalog()
        data["profiles"] = _profiles()
        data["settings"] = voice_settings()
        data["usage"] = {"paid_chars_today": spent_today()}
        return data

    return _ok(await asyncio.to_thread(_build))


async def handle_usage(request: "web.Request") -> "web.Response":
    try:
        from navig_audio.voice.usage import day_rows, recent, spent_today
    except ImportError:
        return _plugin_required()

    def _build() -> dict[str, Any]:
        s = voice_settings()
        spent = spent_today()
        return {
            **s,
            "paid_chars_today": spent,
            "remaining_today": max(0, s["daily_char_budget"] - spent),
            "today": day_rows(),
            "recent": recent(7),
        }

    return _ok(await asyncio.to_thread(_build))


async def handle_settings(request: "web.Request") -> "web.Response":
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = None
    if not isinstance(body, dict):
        return _err("Body must be a JSON object", 400, "bad_request")
    updates: dict[str, Any] = {}
    if "policy" in body:
        pol = str(body["policy"]).strip().lower()
        if pol not in ("auto", "free", "premium", "offline"):
            return _err("policy must be one of auto, free, premium, offline", 400, "bad_request")
        updates["voice.policy"] = pol
    if "daily_char_budget" in body:
        try:
            n = int(body["daily_char_budget"])
        except (TypeError, ValueError):
            return _err("daily_char_budget must be a whole number of characters", 400, "bad_request")
        if not 0 <= n <= 10_000_000:
            return _err("daily_char_budget must be between 0 and 10,000,000", 400, "bad_request")
        updates["voice.daily_char_budget"] = n
    if "paid_fallback" in body:
        if not isinstance(body["paid_fallback"], bool):
            return _err("paid_fallback must be true or false", 400, "bad_request")
        updates["voice.paid_fallback"] = body["paid_fallback"]
    if "stt_model" in body:
        model = str(body["stt_model"]).strip().lower()
        if model not in _STT_MODELS:
            return _err(f"stt_model must be one of {', '.join(_STT_MODELS)}", 400, "bad_request")
        updates["voice.stt_model"] = model
    if not updates:
        return _err("Nothing to change (policy, daily_char_budget, paid_fallback, stt_model)", 400, "bad_request")

    def _write() -> dict[str, Any]:
        from navig.config import get_config_manager

        cm = get_config_manager()
        for key, value in updates.items():
            cm.set_global(key, value)
        return voice_settings()  # read back: the answer is what is now stored

    return _ok(await asyncio.to_thread(_write))


async def handle_speak(request: "web.Request") -> "web.Response":
    from aiohttp import web

    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 — malformed JSON is a 400, not a 500
        body = None
    if not isinstance(body, dict):
        return _err("Body must be a JSON object", 400, "bad_request")

    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return _err("Missing 'text'", 400, "bad_request")
    text = text.strip()
    if len(text) > MAX_CHARS:
        return _err(
            f"Text is {len(text)} characters; send at most {MAX_CHARS} per request "
            "(split on sentences)",
            413,
            "too_long",
        )

    try:
        from navig_audio.voice import catalog as vc
        from navig_audio.voice import usage
        from navig_audio.voice.profiles import apply_pronounce, load_profile, plan_from_params
        from navig_audio.voice.tts import TTS, TTSConfig
    except ImportError:
        return _plugin_required()

    # ── the voice profile (explicit, else the operator's default speech preset) ──
    profile_id = body.get("profile") if isinstance(body.get("profile"), str) else ""
    explicit = bool(body.get("provider") or body.get("voice"))
    if not profile_id and not explicit:
        try:
            from navig.presets import catalog as pcat

            profile_id = await asyncio.to_thread(pcat.get_default, "speech") or ""
        except Exception:  # noqa: BLE001 — no preset store: no default profile
            profile_id = ""
    profile_name = ""
    if profile_id:
        try:
            profile_name, plan = await asyncio.to_thread(load_profile, profile_id.strip())
        except LookupError as exc:
            return _err(str(exc), 404, "unknown_profile")
        except ImportError:
            return _err("Voice profiles need NAVIG's preset catalog", 503, "plugin_required")
    else:
        plan = plan_from_params({})

    lang = body.get("lang")
    lang = lang.strip().lower()[:2] if isinstance(lang, str) and lang.strip() else plan.lang
    if lang is not None and lang not in _LANG_OK:
        lang = None  # an unknown language is not an error — the voice decides
    if lang is None:
        # Neither the client nor a profile named one: take the language the TEXT is written
        # in, so the default voice, the lexicon and the on-device pack all follow the words
        # on the page. The Dock and the CLI already detect; a raw API caller gets the same
        # answer instead of whichever engine default happens to come first.
        try:
            from navig_audio.reader import (
                detect_language,  # noqa: PLC0415 — lazy: the same detector the plugin ships
            )

            lang = detect_language(text)
        except Exception:  # noqa: BLE001 — detection is a courtesy; the engine default still speaks
            lang = None

    settings = await asyncio.to_thread(voice_settings)
    try:
        requested = vc.parse_provider(body.get("provider")) or plan.provider
        chosen = await asyncio.to_thread(vc.route, requested, settings["policy"])
    except vc.RoutingError as exc:
        return _err(str(exc), 422, "provider_unavailable")
    provider = chosen.provider

    voice = body.get("voice")
    voice = voice.strip() if isinstance(voice, str) and voice.strip() else None
    if voice is None and plan.voice and (plan.provider in (None, provider)):
        voice = plan.voice
    if voice is not None and not vc.known_voice(provider, voice):
        return _err(f"{voice!r} is not a {provider.value} voice", 400, "bad_voice")
    voice = voice or vc.default_voice(provider, lang)

    spoken = apply_pronounce(text, plan.pronounce) if plan.pronounce else text
    style = plan.kwargs.get("style")
    # Speed stays 1.0 here: the client applies it (playbackRate), one clip serves every speed.
    overrides = {k: v for k, v in plan.config.items() if k not in ("speed", "google_cloud_speaking_rate")}

    def _tts_for(prov) -> TTS:  # noqa: ANN001
        # An explicit config per call: `get_tts()` would hand back a process-wide default
        # that ignores the operator's config and carries a fallback chain we must not use.
        return TTS(TTSConfig(provider=prov, fallback_providers=[], speed=1.0, **(overrides if prov == provider else {})))

    def _budget_refusal(prov, chars: int) -> "web.Response | None":  # noqa: ANN001
        budget = settings["daily_char_budget"]
        spent = usage.spent_today()
        if spent + chars <= budget:
            return None
        label = vc.provider_status(prov)["label"]
        if budget == 0:
            msg = f"Paid voices are off (daily budget 0) — {label} was not used. Set a budget: navig voice budget 20000"
        else:
            msg = (
                f"Today's paid voice budget is used up ({spent:,} of {budget:,} characters) — "
                f"{label} was not used. Raise it (navig voice budget N) or pick a free voice."
            )
        return _err(msg, 429, "budget_exceeded")

    async def _speak_with(prov, v):  # noqa: ANN001, ANN202
        tts = _tts_for(prov)
        paid = vc.is_paid(prov)
        hit = await asyncio.to_thread(tts.cached_clip, spoken, prov, v, lang=lang, style=style) if paid else None
        if paid and hit is None:
            refusal = await asyncio.to_thread(_budget_refusal, prov, len(spoken))
            if refusal is not None:
                return None, refusal
        kwargs: dict[str, Any] = {"lang": lang}
        if style:
            kwargs["style"] = style
        async with _SPEAK_SLOTS:
            try:
                res = await tts.synthesize(spoken, provider=prov, voice=v, **kwargs)
            except Exception as exc:  # noqa: BLE001 — a provider crash is a 502, not a 500
                logger.warning("deck voice speak failed: %s", exc)
                return None, _err(f"{prov.value} failed: {exc}", 502, "tts_error")
        if not res.success or not res.audio_path:
            return None, _err(f"{prov.value} returned no audio: {res.error or 'unknown error'}", 502, "tts_error")
        if paid and hit is None:
            await asyncio.to_thread(usage.record, prov.value, len(spoken), usd=vc.estimate_usd(prov, len(spoken)))
        return res, None

    result, failure = await _speak_with(provider, voice)
    fallback_note = ""
    if (
        failure is not None
        and failure.status == 502
        and settings["policy"] == "auto"
        and requested is None
        and settings["paid_fallback"]
    ):
        alt = await asyncio.to_thread(vc.paid_fallback)
        if alt is not None:
            fallback_note = f"{provider.value} failed; read with {alt.value} (paid fallback)"
            provider, voice = alt, vc.default_voice(alt, lang)
            result, failure = await _speak_with(provider, voice)
    if failure is not None:
        return failure

    try:
        audio = await asyncio.to_thread(Path(result.audio_path).read_bytes)
    except OSError as exc:
        return _err(f"Could not read the synthesized audio: {exc}", 502, "tts_error")
    if not audio:
        return _err(f"{provider.value} returned empty audio", 502, "tts_error")

    headers = {
        "X-Navig-Voice-Provider": provider.value,
        "X-Navig-Voice-Id": result.voice or voice or "",
        "X-Navig-Voice-Rate": f"{plan.speed:g}",
        "Cache-Control": "no-store",
        "Access-Control-Expose-Headers": (
            "X-Navig-Voice-Provider, X-Navig-Voice-Id, X-Navig-Voice-Rate, X-Navig-Voice-Ignored, "
            "X-Navig-Voice-Note, X-Navig-Voice-Fallback, X-Navig-Voice-Profile"
        ),
    }
    if profile_id:
        headers["X-Navig-Voice-Profile"] = profile_id
    if plan.ignored:
        headers["X-Navig-Voice-Ignored"] = ",".join(plan.ignored)
    if chosen.note:
        headers["X-Navig-Voice-Note"] = chosen.note
    if fallback_note:
        headers["X-Navig-Voice-Fallback"] = fallback_note
    # The header must name the bytes: the on-device engine returns WAV, every cloud
    # engine mp3 — a hardcoded audio/mpeg over WAV would make every client guess.
    content_type = "audio/wav" if result.format == "wav" else "audio/mpeg"
    return web.Response(body=audio, content_type=content_type, headers=headers)


def _llm_shape(text: str, mode: str) -> str:
    """Run an LLM dictation mode through NAVIG's one LLM seam (the operator's connections)."""
    from navig.llm.generate import run_llm
    from navig.prompts.loader import load_prompt

    slug = f"voice/dictation_{mode}"
    system = load_prompt(slug)
    if system.startswith("Warning: Prompt"):  # the loader's miss sentinel — never send it
        raise RuntimeError(f"prompt {slug} is missing")
    result = run_llm(
        [{"role": "system", "content": system}, {"role": "user", "content": text}],
        user_input=text, temperature=0.2, max_tokens=1200,
    )
    out = (getattr(result, "content", "") or "").strip()
    if not out:
        raise RuntimeError(str(getattr(result, "finish_reason", "") or "no AI provider answered"))
    return out


async def handle_transcribe(request: "web.Request") -> "web.Response":
    import tempfile

    try:
        from navig_audio.voice import dictation as dc
        from navig_audio.voice.stt import STTProvider
    except ImportError:
        return _plugin_required()

    try:
        reader = await request.multipart()
    except Exception:  # noqa: BLE001 — not multipart
        return _err("Send multipart/form-data with an 'audio' part", 400, "bad_request")
    audio = b""
    content_type = ""
    fields: dict[str, str] = {}
    while True:
        part = await reader.next()
        if part is None:
            break
        if part.name == "audio":
            content_type = (part.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            chunks = []
            size = 0
            while True:
                chunk = await part.read_chunk(256 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    return _err("Recording too large (25 MB max)", 413, "too_large")
                chunks.append(chunk)
            audio = b"".join(chunks)
        elif part.name in ("lang", "mode", "provider"):
            fields[part.name] = (await part.text()).strip()[:40]
    if not audio:
        return _err("Missing 'audio'", 400, "bad_request")

    mode = fields.get("mode") or "verbatim"
    if mode not in dc.MODES:
        return _err(f"mode must be one of {', '.join(dc.MODES)}", 400, "bad_request")
    lang = (fields.get("lang") or "").lower()[:2]
    lang = lang if lang in _LANG_OK else None

    settings = await asyncio.to_thread(voice_settings)
    requested = None
    if fields.get("provider"):
        try:
            requested = STTProvider(fields["provider"].lower())
        except ValueError:
            return _err(f"unknown speech-to-text engine {fields['provider']!r}", 422, "provider_unavailable")
    try:
        route = await asyncio.to_thread(dc.route_stt, settings["policy"], requested)
    except dc.DictationRoutingError as exc:
        return _err(str(exc), 422, "provider_unavailable")

    stt = dc.make_stt(route, local_model=settings["stt_model"])
    suffix = _AUDIO_SUFFIX.get(content_type, ".webm")
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
            f.write(audio)
            tmp = Path(f.name)
        result = await stt.transcribe(tmp, provider=route.provider, language=lang)
    except Exception as exc:  # noqa: BLE001 — an engine crash is a 502, not a 500
        logger.warning("deck voice transcribe failed: %s", exc)
        return _err(f"{route.provider.value} failed: {exc}", 502, "stt_error")
    finally:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
    if not result.success:
        # Silence is not an error the user can fix by retrying the engine — say what it is.
        msg = result.error or "no speech recognised"
        return _err(f"Nothing recognised — {msg}", 422 if not result.error else 502, "no_speech" if not result.error else "stt_error")

    raw = (result.text or "").strip()
    detected = (result.language or lang or "") or None
    text = dc.shape(raw, mode, detected)
    transform_error = ""
    if mode in dc.LLM_MODES and raw:
        try:
            text = await asyncio.to_thread(_llm_shape, raw, mode)
        except Exception as exc:  # noqa: BLE001 — no model: the clean text, and why
            transform_error = f"{mode} needs an AI connection ({exc}) — inserted the cleaned text"
            text = dc.shape(raw, "clean", detected)
    return _ok({
        "text": text,
        "raw": raw,
        "mode": mode,
        "language": detected,
        "provider": route.provider.value,
        "on_device": route.on_device,
        "note": route.note,
        "transform_error": transform_error,
    })


_WARMING: set[str] = set()


async def handle_warm(request: "web.Request") -> "web.Response":
    """Load the dictation model in the background; answer at once (202)."""
    from aiohttp import web

    try:
        from navig_audio.voice import dictation as dc
    except ImportError:
        return _plugin_required()
    settings = await asyncio.to_thread(voice_settings)
    try:
        route = await asyncio.to_thread(dc.route_stt, settings["policy"], None)
    except dc.DictationRoutingError as exc:
        return _err(str(exc), 422, "provider_unavailable")
    if not route.on_device:
        return web.json_response({"ok": True, "data": {"warming": False, "provider": route.provider.value}}, status=202)
    model = settings["stt_model"]
    if model not in _WARMING:
        _WARMING.add(model)

        async def _load() -> None:
            try:
                await asyncio.to_thread(dc.warm, model)
            finally:
                _WARMING.discard(model)

        from navig.core.background import spawn

        spawn(_load(), name=f"voice-warm-{model}")
    return web.json_response({"ok": True, "data": {"warming": True, "model": model}}, status=202)


def register(app: "web.Application") -> None:
    app.router.add_get("/api/deck/voice/catalog", handle_catalog)
    app.router.add_post("/api/deck/voice/speak", handle_speak)
    app.router.add_get("/api/deck/voice/usage", handle_usage)
    app.router.add_post("/api/deck/voice/settings", handle_settings)
    app.router.add_post("/api/deck/voice/transcribe", handle_transcribe)
    app.router.add_post("/api/deck/voice/warm", handle_warm)
