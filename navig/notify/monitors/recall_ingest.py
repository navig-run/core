"""Recall ingest — keep the Recall bank current without anyone running ``navig recall ingest``.

Every :data:`POLL_S` the daemon runs one incremental ingest: transcripts whose size and
mtime did not change are skipped by a stat, a growing live session is read only from its
cursor, and a half-written last line is left for the next pass. So a sweep on a quiet
machine costs a directory walk, and a new Claude Code session is searchable within one
poll of being written (the S4 target is "within 7 minutes").

Two switches, both required:

* ``monitors.recall_ingest.enabled`` — the monitor itself (default OFF, like every monitor
  that reads something; turn it on with ``navig recall enable --auto``);
* ``recall.read_local`` — the owner's consent to read transcripts at all. It is re-read on
  every pass, so ``navig recall disable`` stops the reading without a daemon restart.

Rules it keeps:

* **Off the loop.** An ingest parses JSONL and writes SQLite, so it runs in a worker thread
  (``asyncio.to_thread``); inline, a first-time ingest would freeze the daemon for minutes.
* **One at a time.** A pass that is still running when the next one is due is skipped, not
  queued (a cancelled task cannot stop its thread, so a toggle off→on could otherwise
  start a second ingest beside the first).
* **Never raises.** A bad pass is logged and counted; after :data:`FAIL_STREAK` failed
  passes in a row it records ONE incident (``navig doctor`` → Config Health, and the
  config-incident push) — self-healing that tells nobody is how a stale bank would hide.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger("navig.notify")

#: How often to look for new or grown transcripts.
POLL_S = 300.0

#: Consecutive failed passes before an incident is recorded (once per streak).
FAIL_STREAK = 3

_busy = threading.Lock()
_state: dict[str, Any] = {"failures": 0, "reported": False}


def _reading_allowed() -> bool:
    """``recall.read_local``, read fresh each pass. A config hiccup means "don't read"."""
    try:
        from navig.recall.query import is_enabled

        return is_enabled()
    except Exception:  # noqa: BLE001 — consent we cannot read is consent we do not have
        return False


def _ingest() -> dict[str, Any]:
    from navig.recall.ingest import Ingestor
    from navig.recall.sources import ADAPTERS

    return Ingestor().run(list(ADAPTERS)).to_dict()


def _failed(reason: str) -> None:
    _state["failures"] += 1
    if _state["failures"] >= FAIL_STREAK and not _state["reported"]:
        _state["reported"] = True
        try:
            from navig.core import incidents

            incidents.record(incidents.RECALL_INGEST_FAILING, failures=_state["failures"], reason=reason[:200])
        except Exception as exc:  # noqa: BLE001 — an observation must never break the observed
            logger.debug("[recall_ingest] could not record the incident: %s", exc)


def _recovered() -> None:
    _state["failures"] = 0
    _state["reported"] = False


def sweep_once() -> dict[str, Any]:
    """One pass. Returns ``{"skipped": why}`` or the ingest report; never raises."""
    if not _reading_allowed():
        return {"skipped": "disabled"}
    if not _busy.acquire(blocking=False):
        return {"skipped": "busy"}
    try:
        report = _ingest()
    except Exception as exc:  # noqa: BLE001 — a monitor must survive a bad pass
        logger.warning("[recall_ingest] pass failed: %s", type(exc).__name__)
        _failed(f"{type(exc).__name__}: {exc}")
        return {"skipped": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
    finally:
        _busy.release()
    errors = report.get("errors") or []
    if errors and not report.get("sources_read") and not report.get("unchanged"):
        # Every source failed: the bank is not being kept current.
        _failed(f"{len(errors)} source(s) failed, none read")
    else:
        _recovered()
    return report


async def run_recall_ingest(poll_s: float = POLL_S) -> None:
    """Ingest new conversations forever. Silent unless something new arrived or it keeps failing."""
    logger.info("[recall_ingest] started (poll=%ss)", poll_s)
    while True:
        result = await asyncio.to_thread(sweep_once)
        if result.get("turns_added") or result.get("records_created"):
            logger.info("[recall_ingest] +%s turn(s), +%s record(s) from %s source(s)",
                        result.get("turns_added", 0), result.get("records_created", 0),
                        result.get("sources_read", 0))
        if result.get("errors"):
            # Counts only: an error string can quote a transcript, and transcript text never goes to logs.
            logger.info("[recall_ingest] %s source(s) could not be read — navig recall sources --errors",
                        len(result["errors"]))
        await asyncio.sleep(poll_s)
