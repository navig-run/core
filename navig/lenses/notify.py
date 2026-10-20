"""Tell the person when a Lens waits on them — so a proposal or an update never sits unseen.

Two producers: the deck save route (an agent proposed a Lens, or staged an edit of one) and
the ``lens_updates`` monitor (an update was fetched from an install link). Both go out the
ordinary notify fan-out as ``lens_review`` — the deck feed and Telegram by default, routable
in Settings like every other type. Best-effort: a failed push never fails the save.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("navig.notify")

NOTIFY_TYPE = "lens_review"
_WHERE = "Review it in NAVIG OS → Settings → Lenses, or `navig lens review`."


def proposal_message(lens: dict[str, Any]) -> tuple[str, str]:
    name = str(lens.get("name") or lens.get("id") or "a Lens")
    pending = lens.get("pending") or {}
    if pending.get("kind") == "agent":
        return (
            "Lens change to review",
            f"The agent changed “{name}”. Your current version keeps running until you approve.\n{_WHERE}",
        )
    return (
        "Lens proposed",
        f"The agent proposed “{name}”. It stays off until you turn it on.\n{_WHERE}",
    )


def updates_message(rows: list[dict[str, Any]]) -> tuple[str, str] | None:
    staged = [r for r in rows if r.get("status") == "staged"]
    if not staged:
        return None
    names = ", ".join(f"{r.get('name')} {r.get('version') or ''}".strip() for r in staged)
    noun = "update" if len(staged) == 1 else "updates"
    return (
        f"{len(staged)} Lens {noun} to review",
        f"{names}. Nothing changes until you approve.\n{_WHERE}",
    )


async def notify_review(title: str, body: str, *, lens_ids: list[str]) -> Any:
    try:
        from navig.notify import dispatch

        return await dispatch(
            NOTIFY_TYPE, title, body, priority="normal", data={"lenses": lens_ids}
        )
    except Exception:  # noqa: BLE001 — a push must never fail the save or the check
        logger.debug("lens review notify failed", exc_info=True)
        return None
