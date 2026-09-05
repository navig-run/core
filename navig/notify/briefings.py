"""Scheduled AI briefings — compose from existing sources, optionally polish
with the LLM, then fan out through the notification router per the user's
briefing-channel preference."""

from __future__ import annotations

import logging
from datetime import datetime

from navig.notify import prefs
from navig.notify.router import get_notification_router

logger = logging.getLogger("navig.notify")


def _compose_raw() -> tuple[str, list[str]]:
    """Plain-text briefing, plus the names of any sources that FAILED.

    A source that raises is not the same as a source with nothing to say. Every
    failure used to be swallowed by a bare ``pass`` with no log at all, so a crashed
    life dashboard produced "No new activity to brief on yet — add data in Apps to
    start tracking" — a message that sends the operator off to add data they already
    have. The caller distinguishes the three states: content, total failure, and a
    genuinely empty install.

    A missing optional source is NOT a failure: ``navig_harbor`` is a closed plugin
    that most installs do not have, so an ImportError there is expected and silent.
    """
    parts: list[str] = []
    failed: list[str] = []

    # Life dashboard (habits / plans / calendar)
    try:
        from navig.commands.life_dashboard import build_dashboard  # type: ignore

        d = build_dashboard()
        txt = d if isinstance(d, str) else (d.get("text") if isinstance(d, dict) else "")
        if txt:
            parts.append(str(txt))
    except Exception as exc:  # noqa: BLE001 — one bad source must not lose the briefing
        failed.append("life dashboard")
        logger.warning("briefing: life dashboard source failed: %s", exc, exc_info=True)

    # Spaces progress
    try:
        from navig.spaces.briefing import build_spaces_briefing_lines  # type: ignore
        from navig.spaces.progress import collect_spaces_progress

        # Gate on the DATA api. build_spaces_briefing_lines is a *display* helper: with
        # no spaces it returns a human-facing ["_No spaces available for briefing._"]
        # placeholder rather than an empty list. Appending that made `raw` non-empty on
        # a fresh install, which suppressed the real empty state below and handed the
        # LLM a markdown placeholder as its only input. Its contract is shared with
        # four other surfaces that render it directly, so it is right to leave alone.
        if collect_spaces_progress():
            lines = build_spaces_briefing_lines()
            if lines:
                parts.append("\n".join(lines) if isinstance(lines, (list, tuple)) else str(lines))
    except Exception as exc:  # noqa: BLE001
        failed.append("spaces progress")
        logger.warning("briefing: spaces progress source failed: %s", exc, exc_info=True)

    # Finance one-liner — optional closed plugin; absence is normal, not a failure.
    try:
        from navig_harbor import bizops
    except ImportError:
        bizops = None  # type: ignore[assignment]
    if bizops is not None:
        try:
            snap = bizops.get_overview()
            if snap.get("briefing"):
                parts.append(str(snap["briefing"]))
        except Exception as exc:  # noqa: BLE001
            failed.append("finance overview")
            logger.warning("briefing: finance source failed: %s", exc, exc_info=True)

    return "\n\n".join(p for p in parts if p).strip(), failed


def _polish(raw: str) -> str:
    """Run the raw briefing through the LLM for a crisp summary. Falls back to
    raw text if no model is configured."""
    if not raw:
        return ""
    try:
        from navig.llm.generate import llm_generate

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
    import asyncio

    raw, failed = _compose_raw()
    if raw:
        # _polish is SYNC (calls llm_generate) — never run it on the gateway event
        # loop; offload so the daemon stays responsive during the summarize call.
        text = await asyncio.to_thread(_polish, raw)
        if failed:
            # Appended AFTER polishing so the model can't summarise the caveat away:
            # a briefing silently missing a section is the same misleading shape as
            # telling the operator there is no data at all.
            text = f"{text}\n\n_(unavailable: {', '.join(failed)})_"
    elif failed:
        # Don't send them off to add data — nothing could be read.
        text = (
            "Couldn't build today's briefing — "
            f"{', '.join(failed)} unavailable. Run `navig doctor`; no data was lost."
        )
    else:
        text = "No new activity to brief on yet — add data in Apps to start tracking."
    title = f"Briefing · {datetime.now().strftime('%a %d %b')}"
    channels = settings["briefing_channels"] or None
    return await get_notification_router().dispatch(
        "briefing", title, text, priority="normal", only_channels=channels
    )
