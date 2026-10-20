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

from datetime import UTC, datetime, timedelta, tzinfo

__all__ = ["LOCAL", "from_utc_iso", "local_now", "to_local", "to_utc_iso"]


class _SystemZone(tzinfo):
    """The system zone, WITH its daylight-saving rules.

    `datetime.now().astimezone()` returns a FIXED offset — today's. Every date the
    PIM builds is `now.replace(day=…, hour=…)` or `now + timedelta(…)`, so a fixed
    offset carries today's offset onto every future date. In October that is the
    summer offset, and an appointment "lundi 26 octobre à 10h30" — the day after
    the clocks go back — was stored as 09:30 local and pinged an hour early.

    This zone asks the operating system for the offset of EACH wall-clock moment,
    so 10:30 on the 26th is 10:30 on the 26th whatever today's offset is.
    """

    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return _local_view(dt).utcoffset()

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None  # unknown — nothing here derives a meaning from it

    def tzname(self, dt: datetime | None) -> str | None:
        return _local_view(dt).tzname()

    def fromutc(self, dt: datetime) -> datetime:
        utc = dt.replace(tzinfo=UTC)
        try:
            local = utc.astimezone()
        except (OverflowError, OSError, ValueError):  # outside the platform's range
            local = utc.astimezone(datetime.now().astimezone().tzinfo)
        return local.replace(tzinfo=self)

    def __repr__(self) -> str:
        return "LOCAL"


def _local_view(dt: datetime | None) -> datetime:
    """``dt``'s wall-clock time as a fixed-offset aware datetime, per the system rules."""
    if dt is None:
        return datetime.now().astimezone()
    try:
        return dt.replace(tzinfo=None).astimezone()
    except (OverflowError, OSError, ValueError):
        # Windows' mktime refuses years before 1970; the current offset is the
        # honest fallback for a date that far out of reach.
        return datetime.now().astimezone()


#: The operator's zone. Use THIS, never a bare `.astimezone()`, for anything that
#: builds or shows a wall-clock time.
LOCAL = _SystemZone()


def local_now() -> datetime:
    """An AWARE datetime in the operator's zone.

    Aware, never naive: a naive value silently loses the offset and every conversion
    after it is wrong by that offset with nothing to signal it.
    """
    return datetime.now(LOCAL)


def to_utc_iso(when: datetime) -> str:
    """Store a datetime the way the reminder table expects to read it.

    The shape (`…THH:MM:SS.mmmZ`) matches `store.base._utcnow()` exactly, because the
    poller compares these as strings. A different shape sorts wrong at the character
    where they diverge — `increment_reminder_retry` shipped that bug with a space
    instead of a `T`, and the backoff was simply ignored.

    A naive input is read as LOCAL, matching `create_reminder`.
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=LOCAL)
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
    return when.astimezone(LOCAL) if when else None
