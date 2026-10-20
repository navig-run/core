"""navig.agent.memory_summary — "What NAVIG knows about you", written from approved facts.

The memory manager shows a short profile so the user can see, at a glance, what the
agent will bring into a conversation. It is generated from APPROVED facts only (pending
proposals and rejected facts never appear), cached in the fact store's ``kf_meta`` side
table, and regenerated only when the approved set changes or the user asks.

Lives under ``navig/agent/`` because it calls an LLM (the agent-boundary law).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_CACHE_KEY = "profile_summary"
_MAX_FACTS = 200

SUMMARY_PROMPT = """\
Below are facts a personal assistant has saved about its user, each approved by the user.
Write a short profile of the user for the user to read, in the second person ("You …").

Rules:
- 3 to 6 bullet points, each one line, grouped by topic (work, preferences, tools, life).
- Use ONLY the facts below. Never guess, infer personality, or add anything new.
- Leave out anything marked ***REDACTED***.
- No heading, no preamble, no closing line — just the bullets.

Facts:
{facts}
"""

LLMCall = Callable[[str], Awaitable[str]]


def _fingerprint(facts: list[Any]) -> str:
    h = hashlib.sha256()
    for f in sorted(facts, key=lambda x: x.id):
        h.update(f"{f.id}|{f.updated_at}\n".encode())
    return h.hexdigest()[:16]


async def _default_llm(prompt: str) -> str:
    from navig.llm.generate import llm_generate

    return await asyncio.to_thread(
        llm_generate, [{"role": "user", "content": prompt}], mode="summarize"
    )


def _cached(store: Any) -> dict[str, Any] | None:
    raw = store.get_meta(_CACHE_KEY)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def read_summary(store: Any) -> dict[str, Any]:
    """The cached summary without generating: ``{summary, generated_at, fact_count, stale}``.

    ``stale`` is True when the approved facts changed since it was written (or when
    there is none yet) — the UI offers a refresh instead of a silent LLM call.
    """
    facts = store.get_active(limit=_MAX_FACTS, approved=1)
    cached = _cached(store)
    fp = _fingerprint(facts)
    if not cached:
        return {"summary": "", "generated_at": None, "fact_count": len(facts), "stale": bool(facts)}
    return {
        "summary": cached.get("summary", ""),
        "generated_at": cached.get("generated_at"),
        "fact_count": len(facts),
        "stale": cached.get("fingerprint") != fp,
    }


async def generate_summary(
    store: Any, *, force: bool = False, llm_call: LLMCall | None = None
) -> dict[str, Any]:
    """Write (or reuse) the profile summary. Returns the same shape as :func:`read_summary`.

    No approved facts → an empty summary without an LLM call. A failed LLM call
    raises — the caller reports it; a stale cache is never presented as fresh.
    """
    facts = await asyncio.to_thread(lambda: store.get_active(limit=_MAX_FACTS, approved=1))
    fp = _fingerprint(facts)
    cached = await asyncio.to_thread(_cached, store)
    if cached and not force and cached.get("fingerprint") == fp:
        return {
            "summary": cached.get("summary", ""),
            "generated_at": cached.get("generated_at"),
            "fact_count": len(facts),
            "stale": False,
        }
    if not facts:
        result = {"summary": "", "generated_at": None, "fact_count": 0, "stale": False}
        await asyncio.to_thread(store.set_meta, _CACHE_KEY, json.dumps({"fingerprint": fp}))
        return result

    lines = "\n".join(f"- [{f.category}] {f.content}" for f in facts)
    text = (await (llm_call or _default_llm)(SUMMARY_PROMPT.format(facts=lines))).strip()
    if not text:
        raise RuntimeError("the model returned an empty summary")
    now = datetime.now(timezone.utc).isoformat()
    await asyncio.to_thread(
        store.set_meta,
        _CACHE_KEY,
        json.dumps({"summary": text, "generated_at": now, "fingerprint": fp}),
    )
    return {"summary": text, "generated_at": now, "fact_count": len(facts), "stale": False}
