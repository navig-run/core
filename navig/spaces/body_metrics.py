"""Body-metrics store — the metrics.csv a space owns.

The sibling of :mod:`navig.spaces.habit_tracker`, and deliberately shaped like it
so the CLI and the Telegram check-in card read and write through one
implementation. The gateway must not import a CLI command module to record a
weight a button press produced.

**The file shape is the one the space already had** —
``date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes``
— so rows written by hand stay valid and nothing needs migrating. A column a
human added that this module has never heard of is preserved verbatim.

Two structural differences from the habit tracker, both deliberate:

* **Wide, not long.** ``habits.csv`` is one row per ``(date, habit)``;
  ``metrics.csv`` is one row per ``date`` with a column per measurement. That is
  the shape the space's own ``weekly-health-review.md`` prompt and the registry's
  ``body-metrics-report.yaml`` skill already read, so it is not ours to change.

* **The write is atomic and refuses to shrink.** ``habits.csv`` is a habit ledger
  that is rebuilt daily; ``metrics.csv`` is a body record spanning years that
  cannot be reconstructed from anything. See :func:`write_metrics`.
"""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

METRICS_FILE = "metrics.csv"

#: The columns the space's file already defines. A file on disk wins over this
#: list (see :func:`read_metrics`) — it is the header used only when creating a
#: file from nothing.
CANONICAL_FIELDS: list[str] = [
    "date",
    "weight_kg",
    "body_fat_pct",
    "resting_hr",
    "sleep_hours",
    "steps",
    "mood_1_10",
    "hrv",
    "notes",
]

#: Bounds outside which a "weight" is a typo, not a measurement. Wide on purpose:
#: the job is to catch a mis-keyed ``874`` or a stray gram value, not to have an
#: opinion about anybody's body.
MIN_WEIGHT_KG = 20.0
MAX_WEIGHT_KG = 400.0

#: A day-over-day change larger than this is almost certainly a typo (or a
#: different unit). The caller confirms rather than silently recording it.
IMPLAUSIBLE_JUMP_KG = 3.0

#: A weekly LOSS beyond this earns one neutral "mention it at your next
#: appointment" line. It lives here, not in either surface, because the CLI and
#: the Telegram card must not disagree about when that line appears — and
#: because the gateway must never import a CLI command module to find out.
FAST_LOSS_KG_PER_WEEK = 1.5

_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


class MetricsReadError(Exception):
    """The file exists and has content, but could not be read or parsed.

    Raised rather than returning ``[]`` so a caller doing read-modify-write
    aborts instead of writing emptiness over a real record. This is the
    "a failed read became a destructive write" class, and it is the reason this
    exception exists at all.
    """


class WeightParseError(ValueError):
    """A weight the user typed that we will not guess at."""


# ── locating the file ─────────────────────────────────────────────────────────


def metrics_path(space: str | None = None) -> Path:
    """Locate the metrics.csv this invocation means.

    Resolution order is copied from :func:`navig.spaces.habit_tracker.tracker_path`
    on purpose — two trackers in the same space that disagreed about which space
    they were in would be worse than either rule alone.

    ``space`` wins, then the directory the command was invoked from **if it
    already holds a metrics.csv**, then the active space.

    ``NAVIG_INVOCATION_CWD`` rather than ``Path.cwd()``: main.py chdir's to the
    active space before any command runs, so by now cwd is the pin, not the
    shell's directory.
    """
    if space:
        p = Path(space).expanduser()
        return p if p.suffix.lower() == ".csv" else p / METRICS_FILE

    launched_from = os.environ.get("NAVIG_INVOCATION_CWD") or ""
    here = (Path(launched_from) if launched_from else Path.cwd()) / METRICS_FILE
    if here.exists():
        return here

    from navig.spaces.active import get_active_working_dir  # noqa: PLC0415

    return get_active_working_dir() / METRICS_FILE


# ── reading and writing ───────────────────────────────────────────────────────


def read_metrics(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    """Return ``(fieldnames, rows)`` for *path*.

    The header on disk wins over :data:`CANONICAL_FIELDS`, so a column somebody
    added by hand survives a round-trip through this module instead of being
    silently dropped on the next write.

    A missing file is ``(CANONICAL_FIELDS, [])`` — nothing to lose. A file that
    exists with content but cannot be read or parsed raises
    :class:`MetricsReadError`; it must never look the same as "empty".
    """
    if not path.exists():
        return list(CANONICAL_FIELDS), []

    from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

    try:
        # Rides out a transient Windows lock (antivirus, backup agent, a read
        # landing mid-os.replace) and only raises when it SURVIVES the retries.
        text = read_text_retrying(path)
    except OSError as exc:
        raise MetricsReadError(f"{path} exists but could not be read: {exc}") from exc

    if not text.strip():
        return list(CANONICAL_FIELDS), []

    try:
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = list(reader.fieldnames or [])
        rows = [
            {k: (row.get(k) or "") for k in fieldnames}
            for row in reader
            if (row.get("date") or "").strip()
        ]
    except csv.Error as exc:
        raise MetricsReadError(f"{path} exists but is not valid CSV: {exc}") from exc

    if "date" not in fieldnames:
        raise MetricsReadError(
            f"{path} has no 'date' column (header: {fieldnames or 'none'}) — "
            "refusing to treat it as a metrics file."
        )

    return fieldnames, rows


def write_metrics(
    path: Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    *,
    allow_shrink: bool = False,
) -> None:
    """Write the whole file atomically, refusing to lose rows.

    Two guards, both there because this file is irreplaceable:

    * **Atomic.** Written to a temp file in the same directory and renamed over
      the target, so a crash or a concurrent reader can never see — or be left
      with — a half-written body record.

    * **Refuses to shrink.** If the file on disk currently holds MORE rows than
      we are about to write, the write is refused unless *allow_shrink* is set.
      Every normal operation here is an upsert, which can only keep the row count
      the same or add one; a shrink means a read returned less than the truth and
      we are one ``os.replace`` away from destroying the record. ``navig body
      log --undo`` is the one legitimate shrink and passes the flag.
    """
    if not allow_shrink and path.exists():
        try:
            _, existing = read_metrics(path)
        except MetricsReadError:
            # Unreadable on the way out too. We cannot prove this write is safe,
            # and the whole point of the guard is to not find out afterwards.
            raise
        if len(rows) < len(existing):
            raise MetricsReadError(
                f"refusing to write {len(rows)} rows over {len(existing)} already in "
                f"{path} — this would lose {len(existing) - len(rows)} day(s). "
                "Pass allow_shrink=True if the removal is intended."
            )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in fieldnames})

    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    # NOTE the argument order: atomic_write_text(path, content) — the reverse of
    # atomic_write_json(data, path). Getting it backwards writes the path INTO
    # the file.
    atomic_write_text(path, buf.getvalue(), encoding="utf-8")


def upsert(path: Path, day: str, values: dict[str, str]) -> dict[str, str]:
    """Merge *values* into the row for *day*, returning the previous values.

    Idempotent by ``date``: recording a weight twice on one day overwrites rather
    than appending, so a day can never be counted twice. Only the keys present in
    *values* are touched — writing a mood does not blank the morning's weight.

    A key that is not yet a column is APPENDED to the header rather than dropped,
    so the file grows to fit rather than silently discarding a measurement.
    """
    fieldnames, rows = read_metrics(path)

    for key in values:
        if key not in fieldnames:
            fieldnames.append(key)

    previous: dict[str, str] = {}
    for row in rows:
        if row.get("date") == day:
            for key, value in values.items():
                previous[key] = row.get(key, "")
                row[key] = value
            rows.sort(key=lambda r: r.get("date", ""))
            write_metrics(path, fieldnames, rows)
            return previous

    new_row = dict.fromkeys(fieldnames, "")
    new_row["date"] = day
    new_row.update(values)
    rows.append(new_row)
    rows.sort(key=lambda r: r.get("date", ""))
    write_metrics(path, fieldnames, rows)
    return dict.fromkeys(values, "")


# ── parsing what a human typed ────────────────────────────────────────────────

#: ``87,4 кг`` / ``87.4 kg`` / ``87.4`` — a decimal COMMA is not a typo here. The
#: operator is in France and reads a Russian interface; both write 87,4.
_WEIGHT_RE = re.compile(
    r"^\s*(\d{1,3}(?:[.,]\d{1,2})?)\s*(?:kg|kgs|kilo|kilos|кг|kilogrammes?)?\s*$",
    re.IGNORECASE,
)


def parse_weight(text: str) -> float:
    """Parse a typed weight in kilograms, or raise :class:`WeightParseError`.

    Accepts ``87.4``, ``87,4``, ``87.4 kg``, ``87,4 кг``. Rejects anything else
    rather than guessing — a body record with one fabricated number in it is
    worse than a body record with one gap.
    """
    match = _WEIGHT_RE.match(text or "")
    if not match:
        raise WeightParseError(
            f"could not read {text.strip()!r} as a weight — send just the number, "
            "e.g. 87.4"
        )
    value = float(match.group(1).replace(",", "."))
    if not (MIN_WEIGHT_KG <= value <= MAX_WEIGHT_KG):
        raise WeightParseError(
            f"{value:g} kg is outside {MIN_WEIGHT_KG:g}–{MAX_WEIGHT_KG:g} kg — "
            "probably a typo, so nothing was recorded."
        )
    return round(value, 2)


def is_implausible_jump(new: float, previous: float | None) -> bool:
    """True when *new* is far enough from *previous* to be worth confirming."""
    if previous is None:
        return False
    return abs(new - previous) > IMPLAUSIBLE_JUMP_KG


# ── reading the series back ───────────────────────────────────────────────────


def _as_float(value: str) -> float | None:
    text = (value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _as_date(value: str) -> date | None:
    try:
        return datetime.strptime((value or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def series(
    path: Path, field: str = "weight_kg", *, since: date | None = None
) -> list[tuple[date, float]]:
    """``[(day, value)]`` for *field*, oldest first, skipping blanks and junk."""
    _, rows = read_metrics(path)
    out: list[tuple[date, float]] = []
    for row in rows:
        day = _as_date(row.get("date", ""))
        value = _as_float(row.get(field, ""))
        if day is None or value is None:
            continue
        if since is not None and day < since:
            continue
        out.append((day, value))
    out.sort(key=lambda pair: pair[0])
    return out


def latest(path: Path, field: str = "weight_kg") -> tuple[date, float] | None:
    """The most recent recorded value for *field*, or ``None``."""
    points = series(path, field)
    return points[-1] if points else None


def moving_average(
    path: Path, field: str = "weight_kg", *, days: int = 7, ending: date | None = None
) -> float | None:
    """Mean of *field* over the *days* ending at *ending*, or ``None`` if no data.

    **This is the headline number, not the latest reading.** Day-to-day weight is
    dominated by water, food in transit and time of day; on a GLP-1 a single pair
    of readings a week apart cannot distinguish a real change from noise. The
    average over a window can.
    """
    end = ending or date.today()
    start = end - timedelta(days=days - 1)
    values = [v for d, v in series(path, field) if start <= d <= end]
    return round(sum(values) / len(values), 2) if values else None


def trend(
    path: Path, field: str = "weight_kg", *, days: int = 7, ending: date | None = None
) -> float | None:
    """Change in the *days*-average versus the preceding window of equal length.

    ``None`` when either window has no data — an unknown trend must not render
    as ``0.0``, which reads as "no change" rather than "not enough recorded".
    """
    end = ending or date.today()
    current = moving_average(path, field, days=days, ending=end)
    previous = moving_average(
        path, field, days=days, ending=end - timedelta(days=days)
    )
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


def logged_ratio(
    path: Path, field: str = "weight_kg", *, days: int = 7, ending: date | None = None
) -> tuple[int, int]:
    """``(recorded, days)`` for *field* over the window ending at *ending*."""
    end = ending or date.today()
    start = end - timedelta(days=days - 1)
    recorded = len({d for d, _ in series(path, field) if start <= d <= end})
    return recorded, days


def sparkline(values: list[float]) -> str:
    """A block sparkline. Flat input renders flat rather than dividing by zero."""
    if not values:
        return ""
    low, high = min(values), max(values)
    if high - low < 1e-9:
        return _SPARK_BLOCKS[len(_SPARK_BLOCKS) // 2] * len(values)
    span = high - low
    return "".join(
        _SPARK_BLOCKS[
            min(
                len(_SPARK_BLOCKS) - 1,
                int((v - low) / span * (len(_SPARK_BLOCKS) - 1) + 0.5),
            )
        ]
        for v in values
    )


def summarize(
    path: Path, *, field: str = "weight_kg", days: int = 7, ending: date | None = None
) -> dict[str, object]:
    """Everything a card or a ``--json`` payload needs, in one read-shaped call."""
    end = ending or date.today()
    start = end - timedelta(days=days - 1)
    window = [(d, v) for d, v in series(path, field) if start <= d <= end]
    recorded, total = logged_ratio(path, field, days=days, ending=end)
    last = latest(path, field)
    return {
        "field": field,
        "days": total,
        "latest": last[1] if last else None,
        "latest_date": last[0].isoformat() if last else None,
        "average": moving_average(path, field, days=days, ending=end),
        "trend": trend(path, field, days=days, ending=end),
        "recorded": recorded,
        "sparkline": sparkline([v for _, v in window]),
        "points": [{"date": d.isoformat(), "value": v} for d, v in window],
    }


# ── Which metrics.csv a Telegram card belongs to ──────────────────────────────
#
# The card is SENT by the CLI (`navig body checkin --send`), which knows the space
# because it was told one. Its buttons and replies are handled by the GATEWAY, a
# different process whose active space is very likely something else. Without this
# pin the answers would land in the wrong metrics.csv — the same class of silent
# mis-write `metrics_path()` exists to prevent. The chat the card went to
# identifies the file it belongs to.
#
# It also carries the PENDING PROMPT: which question is outstanding, for which
# day, and the message id the answer must be a reply to. On disk rather than in
# channel memory because a gateway restart between the question and the answer
# would otherwise drop a measurement silently.


def _targets_file() -> Path:
    from navig.platform import paths  # noqa: PLC0415

    return paths.config_dir() / "cache" / "body_checkin_targets.json"


def _read_state() -> dict:
    import json  # noqa: PLC0415

    try:
        data = json.loads(_targets_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(data: dict) -> None:
    import json  # noqa: PLC0415

    from navig.core.yaml_io import atomic_write_text  # noqa: PLC0415

    atomic_write_text(_targets_file(), json.dumps(data, indent=2), encoding="utf-8")


def _chat_state(data: dict, chat_id: int) -> dict:
    entry = data.get(str(chat_id))
    return entry if isinstance(entry, dict) else {}


def remember_target(
    chat_id: int,
    path: Path,
    message_id: int | None = None,
    day: str | None = None,
) -> None:
    """Record which metrics.csv the card sent to *chat_id* writes into."""
    data = _read_state()
    entry = _chat_state(data, chat_id)
    entry["path"] = str(path)
    if message_id is not None:
        entry["message_id"] = message_id
    if day is not None:
        entry["day"] = day
    data[str(chat_id)] = entry
    _write_state(data)


def resolve_target(chat_id: int) -> Path:
    """The metrics.csv a tap from *chat_id* belongs to; falls back to normal resolution."""
    recorded = _chat_state(_read_state(), chat_id).get("path")
    return Path(recorded) if recorded else metrics_path()


def default_path() -> Path:
    """The metrics.csv to READ when no chat identifies one — the deck, a dashboard.

    An HTTP request carries no chat id, and ``metrics_path()`` alone would resolve
    to the ACTIVE space, which is very often not the health one: this operator's
    check-ins are pinned to ``human-health-space`` while the active space is
    usually ``human-growth-space``. A dashboard that silently read the wrong
    (empty) file would report "nothing recorded" over a full record — the same
    class of confident-wrong answer the rest of this module exists to avoid.

    So: if every chat that has been sent a card agrees on one path, that is the
    file. If they disagree, this cannot be resolved without guessing, and
    guessing is what we are avoiding — fall back to normal resolution.
    """
    paths = {
        entry.get("path")
        for entry in _read_state().values()
        if isinstance(entry, dict) and entry.get("path")
    }
    if len(paths) == 1:
        return Path(next(iter(paths)))
    return metrics_path()


def last_card(chat_id: int) -> tuple[int | None, str | None]:
    """``(message_id, day)`` of the last card sent to *chat_id*."""
    entry = _chat_state(_read_state(), chat_id)
    mid = entry.get("message_id")
    return (int(mid) if mid is not None else None), entry.get("day")


def set_prompt(chat_id: int, kind: str, day: str, message_id: int | None) -> None:
    """Record that *kind* ("weigh" | "note") is awaiting a reply to *message_id*."""
    data = _read_state()
    entry = _chat_state(data, chat_id)
    entry["prompt"] = {"kind": kind, "day": day, "message_id": message_id}
    data[str(chat_id)] = entry
    _write_state(data)


def pending_prompt(chat_id: int) -> tuple[str, str, int] | None:
    """``(kind, day, message_id)`` of the outstanding question, or ``None``."""
    prompt = _chat_state(_read_state(), chat_id).get("prompt")
    if not isinstance(prompt, dict):
        return None
    kind, day, mid = prompt.get("kind"), prompt.get("day"), prompt.get("message_id")
    if not kind or not day or mid is None:
        return None
    return str(kind), str(day), int(mid)


def clear_prompt(chat_id: int) -> None:
    data = _read_state()
    entry = _chat_state(data, chat_id)
    entry.pop("prompt", None)
    data[str(chat_id)] = entry
    _write_state(data)


def confirm_value(chat_id: int, value: float) -> bool:
    """True when *value* repeats the one already pending confirmation.

    Sending the same surprising number twice IS the confirmation, so this both
    reads the pending value and records a new one in a single pass — the caller
    should not have to know that "ask again" and "remember what was asked" are
    the same write.

    Anything different replaces the pending value and asks again. A typo is
    rarely made twice identically; a genuine six-kilo change is two sends.
    """
    data = _read_state()
    entry = _chat_state(data, chat_id)
    prompt = entry.get("prompt")
    if not isinstance(prompt, dict):
        return False
    if prompt.get("awaiting_confirm") == value:
        return True
    prompt["awaiting_confirm"] = value
    entry["prompt"] = prompt
    data[str(chat_id)] = entry
    _write_state(data)
    return False
