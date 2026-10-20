"""When the scheduled weigh-in may ask — the cadence rules for ``body checkin --send``.

The morning weigh-in is a cron job (``navig body checkin --send``). The scheduler
fires any job whose saved ``next_run`` has passed, so a daemon that was down at
08:10 catches up the moment it comes back — at 13:35, or at dinner — and a run
that overran its timeout after the message went out is retried five minutes
later. Both asked the operator twice. Neither is wrong for the scheduler in
general (a missed backup SHOULD catch up), so the rules live here, scoped to the
one command they are about, not in ``cron_service``.

Rules, first match wins — each returns a reason, ``None`` means "ask":

1. ``health.weigh_in.enabled`` is false            → off
2. a scheduled prompt already went out for the day → never twice a day
3. a weight is recorded within ``every_days``      → already weighed
4. ``weekdays`` is set and the day is not in it    → not a weigh-in day
5. the local hour is ``>= latest_hour``            → too late; next run tomorrow

Defaults (enabled, every 1 day, any weekday, no cutoff) keep today's
behaviour apart from the dedup. The cutoff is opt-in: a default of noon would
silently stop every weigh-in scheduled in the afternoon. An interactive ``/weigh`` is never subject to
any of this — those are the operator asking, not the bot.

Pure functions over explicit inputs, so tests pass dates instead of freezing
clocks; :func:`load_settings` is the only reader of config.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

#: Config subtree. Keys: enabled · every_days · weekdays · latest_hour.
CONFIG_KEY = "health.weigh_in"

WEEKDAY_NAMES: tuple[str, ...] = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

MIN_EVERY_DAYS = 1
MAX_EVERY_DAYS = 30
#: ``latest_hour`` is "ask only while the local hour is below this". 24 means
#: "never too late"; 0 would mean "never", which ``enabled=false`` already says.
MIN_LATEST_HOUR = 1
MAX_LATEST_HOUR = 24

DEFAULT_EVERY_DAYS = 1
DEFAULT_LATEST_HOUR = 24


@dataclass(frozen=True)
class WeighInSettings:
    enabled: bool = True
    every_days: int = DEFAULT_EVERY_DAYS
    #: ``date.weekday()`` numbers (Mon=0). Empty = any day.
    weekdays: tuple[int, ...] = field(default_factory=tuple)
    latest_hour: int = DEFAULT_LATEST_HOUR

    def weekday_names(self) -> list[str]:
        return [WEEKDAY_NAMES[i] for i in self.weekdays]

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "every_days": self.every_days,
            "weekdays": self.weekday_names(),
            "latest_hour": self.latest_hour,
        }


def parse_weekdays(value: Any) -> tuple[int, ...]:
    """``"mon,thu"`` / ``["mon", "thu"]`` → ``(0, 3)``. Raises ``ValueError``.

    ``""``, ``"any"``, ``"all"``, ``"-"`` and an empty list mean "any day".
    Accepts full names (``monday``) and any case; rejects anything else rather
    than guessing which day was meant.
    """
    if value is None:
        return ()
    if isinstance(value, str):
        items = [p for p in value.replace(" ", ",").split(",") if p.strip()]
    elif isinstance(value, (list, tuple, set)):
        items = [str(p) for p in value]
    else:
        raise ValueError(f"weekdays must be a list or a comma-separated string, got {value!r}")
    if len(items) == 1 and items[0].strip().lower() in {"any", "all", "-", "none"}:
        return ()
    out: set[int] = set()
    for raw in items:
        token = raw.strip().lower()[:3]
        if token not in WEEKDAY_NAMES:
            raise ValueError(f"unknown weekday {raw.strip()!r} — use {', '.join(WEEKDAY_NAMES)}")
        out.add(WEEKDAY_NAMES.index(token))
    return tuple(sorted(out))


def settings_from(raw: Any) -> WeighInSettings:
    """Build settings from a config subtree, never raising.

    A malformed value falls back to its default rather than wedging the
    weigh-in: ``navig config set`` stores strings, and a typo must not turn the
    morning question off (or on) by accident.
    """
    from navig.core.coerce import coerce_bool, coerce_int  # noqa: PLC0415

    data = raw if isinstance(raw, dict) else {}
    every = coerce_int(data.get("every_days"), DEFAULT_EVERY_DAYS, minimum=MIN_EVERY_DAYS)
    every = min(every, MAX_EVERY_DAYS)
    hour = coerce_int(data.get("latest_hour"), DEFAULT_LATEST_HOUR, minimum=MIN_LATEST_HOUR)
    if hour > MAX_LATEST_HOUR:
        hour = DEFAULT_LATEST_HOUR
    try:
        weekdays = parse_weekdays(data.get("weekdays"))
    except ValueError:
        weekdays = ()
    return WeighInSettings(
        enabled=coerce_bool(data.get("enabled"), default=True),
        every_days=every,
        weekdays=weekdays,
        latest_hour=hour,
    )


#: The Check-ins engine's own subtree for the weigh-in (``navig checkin``). The
#: cadence keys may be set there too; ``health.weigh_in`` stays the canonical home
#: because every existing install already has its settings under it.
CHECKINS_KEY = "checkins.weigh"

_CADENCE_KEYS = ("enabled", "every_days", "weekdays", "latest_hour")


def _merged(cm: Any) -> dict[str, Any]:
    """``health.weigh_in`` with any cadence key also set under ``checkins.weigh`` on top.

    Two homes for one setting is only safe when exactly one rule decides. The rule:
    a key present under ``checkins.weigh`` wins. :func:`save_settings` keeps the two
    in step, so the only way they disagree is a hand edit — and then the newer,
    Check-ins-specific key is the one the operator just typed.
    """
    base = cm.get(CONFIG_KEY, default={}) or {}
    data = dict(base) if isinstance(base, dict) else {}
    alias = cm.get(CHECKINS_KEY, default={}) or {}
    if isinstance(alias, dict):
        for key in _CADENCE_KEYS:
            if key in alias:
                data[key] = alias[key]
    return data


def load_settings() -> WeighInSettings:
    """Read the weigh-in cadence from the global config. Defaults on any failure."""
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        return settings_from(_merged(get_config_manager()))
    except Exception:  # noqa: BLE001 — an unreadable config must not stop the weigh-in
        return WeighInSettings()


def save_settings(updates: dict[str, Any]) -> WeighInSettings:
    """Write cadence *updates* and return what now reads back.

    Writes ``health.weigh_in.<key>`` — and ``checkins.weigh.<key>`` too when that
    alias already holds the key, because otherwise the alias would silently shadow
    the write and the caller's read-back would report a value that did not stick.
    """
    from navig.config import get_config_manager  # noqa: PLC0415

    cm = get_config_manager()
    for key, value in updates.items():
        cm.set_global(f"{CONFIG_KEY}.{key}", value)
        alias = cm.get(CHECKINS_KEY, default={}) or {}
        if isinstance(alias, dict) and key in alias:
            cm.set_global(f"{CHECKINS_KEY}.{key}", value)
    return load_settings()


def skip_reason(
    settings: WeighInSettings,
    *,
    day: date,
    now: datetime,
    last_weighed: date | None,
    asked_on: str | None,
) -> tuple[str, str] | None:
    """``(code, human reason)`` when the scheduled weigh-in must NOT ask, else ``None``."""
    if not settings.enabled:
        return "off", "weigh-in reminders are off (navig body cadence --on)"
    if asked_on == day.isoformat():
        return "asked", f"already asked for {day.isoformat()}"
    if last_weighed is not None and (day - last_weighed).days < settings.every_days:
        if last_weighed == day:
            return "weighed", f"already weighed today ({day.isoformat()})"
        return (
            "weighed",
            f"weighed {last_weighed.isoformat()} — every {settings.every_days} days, "
            f"next on {(last_weighed + timedelta(days=settings.every_days)).isoformat()}",
        )
    if settings.weekdays and day.weekday() not in settings.weekdays:
        return (
            "weekday",
            f"{WEEKDAY_NAMES[day.weekday()]} is not a weigh-in day "
            f"({', '.join(settings.weekday_names())})",
        )
    if day == now.date() and now.hour >= settings.latest_hour:
        return (
            "late",
            f"too late today (after {settings.latest_hour:02d}:00) — next run tomorrow",
        )
    return None


def next_eligible(
    settings: WeighInSettings,
    *,
    now: datetime,
    last_weighed: date | None,
    asked_on: str | None,
    horizon_days: int = 60,
) -> date | None:
    """The first day on or after today the scheduled weigh-in would ask, or ``None``."""
    if not settings.enabled:
        return None
    today = now.date()
    for offset in range(horizon_days + 1):
        day = today + timedelta(days=offset)
        if (
            skip_reason(settings, day=day, now=now, last_weighed=last_weighed, asked_on=asked_on)
            is None
        ):
            return day
    return None
