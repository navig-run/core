"""Scheduled AI briefings — compose from existing sources, optionally polish
with the LLM, then fan out through the notification router per the user's
briefing-channel preference."""

from __future__ import annotations

import logging
from datetime import datetime

from navig.notify import prefs
from navig.notify.router import get_notification_router

logger = logging.getLogger("navig.notify")


def _compose_raw() -> str:
    """Best-effort plain-text briefing from whatever data sources are available."""
    parts: list[str] = []
    # Life dashboard (habits / plans / calendar)
    try:
        from navig.commands.life_dashboard import build_dashboard  # type: ignore

        d = build_dashboard()
        txt = d if isinstance(d, str) else (d.get("text") if isinstance(d, dict) else "")
        if txt:
            parts.append(str(txt))
    except Exception:  # noqa: BLE001
        pass
    # Spaces progress
    try:
        from navig.spaces.briefing import build_spaces_briefing_lines  # type: ignore

        lines = build_spaces_briefing_lines()
        if lines:
            parts.append("\n".join(lines) if isinstance(lines, (list, tuple)) else str(lines))
    except Exception:  # noqa: BLE001
        pass
    # Finance one-liner
    try:
        from navig_harbor import bizops

        snap = bizops.get_overview()
        if snap.get("briefing"):
            parts.append(str(snap["briefing"]))
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(p for p in parts if p).strip()


def _polish(raw: str) -> str:
    """Run the raw briefing through the LLM for a crisp summary. Falls back to
    raw text if no model is configured."""
    if not raw:
        return ""
    try:
        from navig.llm_generate import llm_generate

        out = llm_generate(
            messages=[
                {"role": "system", "content": "You are a concise daily briefer. Summarize the "
                 "operator's status into 3-5 short bullet lines. No preamble."},
                {"role": "user", "content": raw[:4000]},
            ],
            mode="summarize",
            temperature=0.4,
            max_tokens=400,
        )
        return (out or "").strip() or raw
    except Exception:  # noqa: BLE001
        return raw


async def build_and_dispatch_briefing(*, force: bool = False) -> dict:
    """Compose + dispatch the briefing. No-op when briefings are disabled
    (unless ``force`` is set, e.g. a manual 'send now' / test)."""
    settings = prefs.get_settings()
    if not settings["briefing_enabled"] and not force:
        return {"skipped": "disabled"}
    raw = _compose_raw()
    text = _polish(raw) if raw else "No new activity to brief on yet — add data in Apps to start tracking."
    title = f"Briefing · {datetime.now().strftime('%a %d %b')}"
    channels = settings["briefing_channels"] or None
    return await get_notification_router().dispatch(
        "briefing", title, text, priority="normal", only_channels=channels
    )
