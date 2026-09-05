"""Durable record of the self-healing events that precede a silent outage.

The config/identity layer now recovers from things that used to be catastrophic: a
refused config wipe, a load that fell back to the last known-good cache, a deck.api_key
restored from the vault mirror. Each of those is *survivable* — and each is also a
symptom of something that went wrong and will likely go wrong again.

Today they exist only as log lines in a file nobody reads. A daemon that quietly heals
itself at 3am and never tells anyone is how a bot ends up 100% deaf with every light
green: the whole point of the fixes above is defeated if the operator cannot SEE that
they fired.

So each event is appended to ``<config_dir>/perf/config_incidents.jsonl`` through the
existing :func:`navig.core.yaml_io.log_shadow_anomaly` seam (append-only JSONL, never
raises), and ``navig doctor`` reads them back under "Config Health".

Recording must never break the code path it observes — a health note is not worth an
outage — so every function here swallows its own failures.

**This file has writers outside Python.** The Rust desktop apps (Anchor and Echo) append
to the same JSONL through ``packages/shared-desktop/src/jsonstore.rs``, reusing the
``store_read_failed`` / ``store_write_refused`` ids below — a rescue in a desktop store
has to reach ``navig doctor`` too, not just a toast the operator dismissed. So the record
shape (``{"ts", "event", "data"}``), the log filename, and those two ids are a
**cross-language contract**: changing any of them here silently orphans the desktop
writers, and nothing errors — the reader is per-line tolerant, so mismatched entries just
stop appearing. Two consequences worth knowing: entries arrive from three processes
concurrently (so each writer must emit a line in ONE write), and a new id added here is
rendered for desktop events only once it exists in :data:`DESCRIPTIONS`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

LOG_NAME = "config_incidents"

# Event identifiers. Keep these stable — `navig doctor` renders them by name.
WIPE_REFUSED = "config_wipe_refused"
LOAD_FAILED = "config_load_failed"
RECOVERED_FROM_CACHE = "config_recovered_from_cache"
DECK_KEY_RESTORED = "deck_key_restored"
DECK_KEY_REIDENTIFIED = "deck_key_reidentified"
SPACE_MANIFEST_UNREADABLE = "space_manifest_unreadable"
ACTIVE_SPACE_UNREADABLE = "active_space_unreadable"
FTS_INDEX_REPAIRED = "fts_index_repaired"
FTS_INDEX_UNREPAIRABLE = "fts_index_unrepairable"
WEBHOOK_TENANT_HEALED = "webhook_tenant_healed"
STORE_WRITE_REFUSED = "store_write_refused"
STORE_READ_FAILED = "store_read_failed"
STORE_WRITE_FAILED = "store_write_failed"
APPROVAL_EXPIRED = "approval_expired"
APPROVAL_ANSWERED_TOO_LATE = "approval_answered_too_late"
BROKER_REGISTER_FAILED = "broker_register_failed"

# Human-readable, operator-facing summaries (doctor prints these, not the raw ids).
DESCRIPTIONS: dict[str, str] = {
    WIPE_REFUSED: "a save tried to write an EMPTY config over your settings — refused",
    APPROVAL_EXPIRED: (
        "an approval request timed out with no answer — the action was auto-decided "
        "and, before this, nobody outside the terminal was told"
    ),
    APPROVAL_ANSWERED_TOO_LATE: (
        "you answered an approval AFTER it had already expired — your decision was "
        "not applied. Re-run the action, or raise approval.timeout_seconds"
    ),
    BROKER_REGISTER_FAILED: (
        "the broker never learned this daemon's address, so the Mini App resolves to "
        "whatever was registered LAST — typically a dead tunnel from a previous session"
    ),
    LOAD_FAILED: "config.yaml could not be read",
    RECOVERED_FROM_CACHE: "config was recovered from the last known-good cache",
    DECK_KEY_RESTORED: "deck.api_key was restored from the vault (config had lost it)",
    DECK_KEY_REIDENTIFIED: (
        "a NEW deck.api_key was minted over a used config (no vault mirror to restore) — "
        "the install was RE-IDENTIFIED; check for a config wipe"
    ),
    SPACE_MANIFEST_UNREADABLE: (
        "a space manifest could not be parsed — that space is running with EMPTY "
        "settings (no name, no pinned apps, and its finance BOOK falls back to personal)"
    ),
    ACTIVE_SPACE_UNREADABLE: (
        "the file recording which space is active could not be read — the agent fell "
        "back to another directory, so it may be reading and writing in the WRONG space"
    ),
    FTS_INDEX_REPAIRED: (
        "a search index was rebuilt after its unsafe sync triggers were corrected — "
        "results before this may have been stale, and editing the same record twice "
        "could have failed"
    ),
    FTS_INDEX_UNREPAIRABLE: (
        "a search index could NOT be rebuilt — full-text search for that store falls "
        "back to a plain LIKE match until it is repaired"
    ),
    WEBHOOK_TENANT_HEALED: (
        "the Telegram webhook was pointing at a DEAD lighthouse tenant (deck.api_key "
        "had been rotated) — every inbound message was being queued at the edge and "
        "never delivered, with every light green. Re-pointed at the live tenant"
    ),
    STORE_WRITE_REFUSED: (
        "a store could not be READ, so a save that would have replaced it with an "
        "incomplete set was refused — your data is intact, but that store is not "
        "accepting changes until the file is fixed or moved"
    ),
    STORE_WRITE_FAILED: (
        "an append to an audit/ledger store FAILED — the operation itself happened, "
        "but there is no record of it, so `navig undo` and `navig insights` cannot "
        "see it and the history is missing a line rather than showing a gap"
    ),
    STORE_READ_FAILED: (
        "a store could not be READ, so whatever runs on it is running on an EMPTY "
        "set — the daemon does not error, it simply matches nothing, which looks "
        "exactly like having nothing configured"
    ),
}


# Optional push sink. The gateway's config-incident reporter registers here so a recorded
# incident can ALSO fan out as a proactive notification — turning Config Health from a
# doctor PULL into a PUSH. Unset (None) on the CLI and in tests, where record() just writes
# the log. See navig/notify/producers/config_incidents.py.
_notify_hook: "Callable[[str, dict[str, Any]], None] | None" = None


def set_notify_hook(hook: "Callable[[str, dict[str, Any]], None] | None") -> None:
    """Register (or clear with ``None``) the push sink for recorded incidents."""
    global _notify_hook
    _notify_hook = hook


def record(event: str, **data: Any) -> None:
    """Append *event* to the incident log, and push it if a sink is registered.

    Never raises — an observation must never break the thing it observes, so BOTH the log
    write and the optional push are independently best-effort.
    """
    try:
        from navig.core.yaml_io import log_shadow_anomaly

        log_shadow_anomaly(LOG_NAME, event, data)
    except Exception:  # noqa: BLE001 — an observation must never break the observed
        pass

    hook = _notify_hook
    if hook is not None:
        try:
            hook(event, dict(data))
        except Exception:  # noqa: BLE001 — a push must never break record()
            pass


def _log_path():
    from navig.platform.paths import config_dir

    return config_dir() / "perf" / f"{LOG_NAME}.jsonl"


def recent(limit: int = 5, *, max_age_days: float | None = 30.0) -> list[dict[str, Any]]:
    """The most recent incidents, newest first. Empty on any failure (never raises).

    Older entries are ignored by default: a wipe that was refused two months ago is
    history, not an open problem, and a doctor row that never goes green again is a row
    people learn to skip past.
    """
    try:
        path = _log_path()
        if not path.exists():
            return []
        cutoff = 0.0 if max_age_days is None else time.time() - max_age_days * 86400
        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a torn last line — skip it, never fail the health check
            if isinstance(entry, dict) and float(entry.get("ts") or 0) >= cutoff:
                out.append(entry)
        out.sort(key=lambda e: float(e.get("ts") or 0), reverse=True)
        return out[:limit]
    except Exception:  # noqa: BLE001
        return []


def describe(entry: dict[str, Any]) -> str:
    """A one-line, operator-facing rendering of one incident."""
    event = str(entry.get("event") or "?")
    text = DESCRIPTIONS.get(event, event)
    # Several stores share STORE_WRITE_REFUSED, so the description alone leaves the
    # operator with a problem and no address. Name the file they have to fix.
    data = entry.get("data")
    if isinstance(data, dict):
        target = data.get("path") or data.get("store")
        if target:
            text = f"{text} [{target}]"
        # …and when the unreadable bytes were quarantined, name THAT too. Five
        # different producers copy a damaged file to ``<name>.corrupt`` before
        # falling back to defaults, and that copy is the entire recovery path — the
        # operator cannot restore what they do not know exists. `doctor` used to
        # surface it for exactly one hardcoded filename (config.yaml.corrupt); any
        # producer that passes ``backup`` now gets it for free.
        backup = data.get("backup")
        if backup:
            text = f"{text} — original preserved at {backup}"
    try:
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(float(entry.get("ts") or 0)))
    except Exception:  # noqa: BLE001
        stamp = "?"
    return f"{stamp} — {text}"
