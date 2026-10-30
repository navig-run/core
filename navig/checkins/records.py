"""``checkins.jsonl`` — the pills and water record, append-only, in the health space.

One JSON object per line::

    {"ts": "2026-10-08T06:05:12Z", "date": "2026-10-08", "item": "metformin",
     "kind": "dose", "slot": "0800", "status": "taken", "at": "2026-10-08T06:05:12Z",
     "source": "tap", "name": "Metformin", "dose": "500 mg"}
    {"ts": "...", "date": "2026-10-08", "item": "water", "kind": "count",
     "status": "add", "value": 1, "source": "tap"}

* ``ts`` is UTC; ``date`` is the operator's LOCAL calendar day the answer belongs
  to (for a dose: the day of the slot, not of the tap — a 23:00 dose taken at
  00:20 is still the 23:00 dose). Totals and adherence read ``date``, never
  re-derive it from ``ts``, so a DST change or a midnight tap cannot move a glass
  to another day.
* Append-only: a correction is a new line, never an edit. Nothing here rewrites
  the file, so a failed read can never become a destructive write.
* Lives next to ``metrics.csv`` (the weigh-in's record) — the same space, the same
  owner. It is ``.jsonl``, which the project indexer does not ingest, and
  :data:`navig.memory.project_indexer.DEFAULT_EXCLUDES` names it explicitly so a
  future ``.jsonl`` indexer cannot pull a medication record into AI context.

Dose statuses: ``taken`` · ``skipped`` · ``missed``. ``missed`` is only ever
written by expiry — and nothing anywhere writes ``taken`` without a tap or a
typed answer from the operator.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

RECORDS_FILE = "checkins.jsonl"

TAKEN = "taken"
SKIPPED = "skipped"
MISSED = "missed"
ADD = "add"


class RecordsReadError(Exception):
    """The file exists but could not be read. Never confused with "no records"."""


def records_path(chat: int | None = None) -> Path:
    """``checkins.jsonl`` beside the weigh-in's ``metrics.csv`` (the health space)."""
    from navig.spaces import body_metrics as bm  # noqa: PLC0415

    metrics = bm.resolve_target(chat) if chat else bm.default_path()
    return metrics.parent / RECORDS_FILE


def utc_iso(instant: datetime | None = None) -> str:
    instant = instant or datetime.now(UTC)
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=UTC)
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(raw: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def append(path: Path, record: dict[str, Any]) -> dict[str, Any]:
    """Append one record durably and return it (with ``ts`` filled in)."""
    rec = {"ts": utc_iso(), **record}
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:
            pass
    return rec


def read(path: Path) -> list[dict[str, Any]]:
    """Every well-formed record. A malformed line is skipped (a torn last write)."""
    if not path.exists():
        return []
    try:
        from navig.core.yaml_io import read_text_retrying  # noqa: PLC0415

        text = read_text_retrying(path)
    except OSError as exc:
        raise RecordsReadError(f"{path} exists but could not be read: {exc}") from exc
    out: list[dict[str, Any]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict) and rec.get("item") and rec.get("date"):
            out.append(rec)
    return out


# ── doses ─────────────────────────────────────────────────────────────────────


def dose_records(rows: list[dict], item: str, day: str, slot: str | None) -> list[dict]:
    return [
        r
        for r in rows
        if r.get("item") == item
        and r.get("date") == day
        and r.get("slot") == slot
        and r.get("kind") == "dose"
    ]


def taken(rows: list[dict], item: str, day: str, slot: str | None) -> list[dict]:
    """Every ``taken`` record for one dose — more than one is a recorded double dose."""
    return [r for r in dose_records(rows, item, day, slot) if r.get("status") == TAKEN]


def dose_status(rows: list[dict], item: str, day: str, slot: str | None) -> str | None:
    """``taken`` if ANY taken record exists, else the latest status, else ``None``.

    ``taken`` wins over a later ``missed`` on purpose: expiry must never be able to
    erase that a dose was taken, and a "missed" written after a real answer is a
    bookkeeping race, not a fact about the operator.
    """
    recs = dose_records(rows, item, day, slot)
    if any(r.get("status") == TAKEN for r in recs):
        return TAKEN
    return str(recs[-1].get("status")) if recs else None


def adherence(
    rows: list[dict],
    item: str,
    slots: list[str],
    *,
    today: date,
    days: int = 7,
    since: date | None = None,
    now_local: datetime | None = None,
) -> dict[str, Any]:
    """Taken doses over scheduled doses in the window ending *today*.

    A scheduled dose with no answer counts as not taken — that is what adherence
    means — but a slot still in the future today is not counted at all.
    """
    start = today - timedelta(days=days - 1)
    if since and since > start:
        start = since
    scheduled = taken_n = skipped_n = missed_n = 0
    day = start
    while day <= today:
        for slot in slots:
            if day == today and now_local is not None:
                hhmm = now_local.strftime("%H%M")
                if slot > hhmm:
                    continue
            scheduled += 1
            status = dose_status(rows, item, day.isoformat(), slot)
            if status == TAKEN:
                taken_n += 1
            elif status == SKIPPED:
                skipped_n += 1
            else:
                missed_n += 1
        day += timedelta(days=1)
    pct = round(100 * taken_n / scheduled) if scheduled else None
    return {
        "scheduled": scheduled,
        "taken": taken_n,
        "skipped": skipped_n,
        "missed": missed_n,
        "percent": pct,
        "days": days,
    }


# ── counts ────────────────────────────────────────────────────────────────────


def count_total(rows: list[dict], item: str, day: str) -> int:
    """Today's glasses: the day's ``add`` values applied in record order, floored at
    zero after EACH one — an undo larger than the running total must not leave a hidden
    negative balance that swallows the next glass logged."""
    total = 0
    for r in rows:
        if r.get("item") == item and r.get("date") == day and r.get("status") == ADD:
            try:
                total = max(0, total + int(r.get("value") or 0))
            except (TypeError, ValueError):
                continue
    return total


def done_today(rows: list[dict], item: Any, day: str, *, now_hhmm: str | None = None) -> bool:
    """Has *item* been answered today? (deck "done" signal)

    Pills: every slot already due today has an answer (taken or skipped).
    Water: the day's target is reached.
    """
    if item.template == "water":
        return count_total(rows, item.id, day) >= item.target
    due = [s for s in item.slots() if now_hhmm is None or s <= now_hhmm]
    if not due:
        return False
    return all(dose_status(rows, item.id, day, s) in (TAKEN, SKIPPED) for s in due)
