"""The one place the PIM reads the clock, and the one place it converts zones.

`dates.py` is deliberately pure — every function takes `now` — which makes the date
grammar deterministic but leaves somebody having to actually ask what time it is. That
somebody is this module, and it is one module so that:

* a test can redirect the whole PIM's sense of "now" through a single seam;
* the local↔UTC conversion is written once. Stored timestamps are UTC ``…Z`` strings
  compared as PLAIN STRINGS by the reminder poller, so a local wall-clock time written
  as-if-UTC fires off by the server's offset — the trap `RuntimeStore.create_reminder`
  documents and this module exists to keep out of every caller.

The operator's zone is the SYSTEM zone. That is a decision, not an omission: NAVIG runs
on the operator's own machine, and a configurable PIM timezone that disagrees with the
desktop clock is a source of confusion with no upside until they are somewhere else.
`local_now()` is the seam to widen when that changes.
"""

from __future__ import annotations

from datetime import UTC, datetime

__all__ = ["from_utc_iso", "local_now", "to_local", "to_utc_iso"]


def local_now() -> datetime:
    """An AWARE datetime in the operator's zone.

    Aware, never naive: a naive value silently loses the offset and every conversion
    after it is wrong by that offset with nothing to signal it.
    """
    return datetime.now().astimezone()


def to_utc_iso(when: datetime) -> str:
    """Store a datetime the way the reminder table expects to read it.

    The shape (`…THH:MM:SS.mmmZ`) matches `store.base._utcnow()` exactly, because the
    poller compares these as strings. A different shape sorts wrong at the character
    where they diverge — `increment_reminder_retry` shipped that bug with a space
    instead of a `T`, and the backoff was simply ignored.

    A naive input is read as LOCAL, matching `create_reminder`.
    """
    if when.tzinfo is None:
        when = when.astimezone()
    return when.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def from_utc_iso(raw: str | None) -> datetime | None:
    """Read a stored timestamp back as an AWARE UTC datetime.

    Returns None for anything unparseable rather than raising: a corrupt stored value
    must degrade one row to "no date", not take down the whole card render.
    """
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def to_local(when: datetime | None) -> datetime | None:
    """UTC in, operator's zone out. Everything a person READS goes through here."""
    return when.astimezone() if when else None
