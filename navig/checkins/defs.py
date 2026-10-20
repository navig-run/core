"""What a check-in IS — the templates, and the items an operator configured.

A :class:`CheckinDef` is a template (weigh · pills · water): its kind, its unit,
how a typed answer is parsed, where the answer is stored, which Telegram
extension gates it and how chat tidy treats its prompts. A :class:`CheckinItem`
is one configured instance ("Metformin 500 mg at 08:00 and 20:00").

Where items live: ``checkins.<id>`` in the global config. The weigh-in is the
exception — it is built in, always listed, and its cadence stays in
``health.weigh_in.*`` (``checkins.weigh.*`` is honoured as an alias, see
:mod:`navig.spaces.weigh_cadence`).

The dose text of a medication is stored EXACTLY as typed and is never parsed into
an amount: nothing in this package does arithmetic on a dose, and nothing in it
ever suggests one.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

#: Config subtree holding every configured check-in.
CONFIG_KEY = "checkins"

KIND_NUMBER = "number"
KIND_DOSE = "dose"
KIND_COUNT = "count"
KINDS = (KIND_NUMBER, KIND_DOSE, KIND_COUNT)

STORAGE_METRICS = "metrics.csv"
STORAGE_JSONL = "checkins.jsonl"

#: Chat-tidy policies. ``routine``: swept the day after like any routine card,
#: but never while the prompt is still open. ``keep_pending``: the same, stated
#: for the kinds where losing an open prompt is a safety problem (a dose).
TIDY_ROUTINE = "routine"
TIDY_KEEP_PENDING = "keep_pending"

#: Ids live inside callback data (Telegram caps it at 64 bytes) and job names.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")
#: Reserved: the built-in weigh-in.
WEIGH_ID = "weigh"
WATER_ID = "water"

MAX_TIMES = 8
MAX_NAME = 60
MAX_DOSE = 60
DEFAULT_MISSED_AFTER = 120  # minutes
MIN_MISSED_AFTER = 15
MAX_MISSED_AFTER = 720
MIN_TARGET, MAX_TARGET = 1, 30
MAX_EVERY_HOURS = 12

_TIME_RE = re.compile(r"^\s*([01]?\d|2[0-3])\s*[:h.]\s*([0-5]\d)\s*$", re.IGNORECASE)
_HOUR_ONLY_RE = re.compile(r"^\s*([01]?\d|2[0-3])\s*h?\s*$", re.IGNORECASE)


@dataclass(frozen=True)
class CheckinDef:
    """A check-in template. Pure data — behaviour lives in the engine."""

    id: str
    kind: str
    emoji: str
    title_key: str
    prompt_key: str
    unit: str = ""
    bounds: tuple[float, float] | None = None
    parser: Callable[[str], float] | None = None
    quick_answers: tuple[str, ...] = ()
    storage: str = STORAGE_JSONL
    actions: tuple[str, ...] = ()
    #: The Telegram extension that gates this check-in's prompts, taps and cron.
    extension: str = "checkins"
    tidy: str = TIDY_ROUTINE
    opt_in: bool = True


def _parse_weight(text: str) -> float:
    from navig.spaces import body_metrics as bm  # noqa: PLC0415

    return bm.parse_weight(text)


TEMPLATES: dict[str, CheckinDef] = {
    "weigh": CheckinDef(
        id="weigh",
        kind=KIND_NUMBER,
        emoji="⚖️",
        title_key="checkins.weigh.title",
        prompt_key="checkins.weigh.prompt",
        unit="kg",
        bounds=(20.0, 400.0),
        parser=_parse_weight,
        quick_answers=("same", "skip"),
        storage=STORAGE_METRICS,
        actions=("reply",),
        # The weigh-in predates the Check-ins extension and has always been gated
        # by Health — switching Check-ins off must not silence it, and switching
        # Health off must keep silencing it.
        extension="health",
        tidy=TIDY_ROUTINE,
        opt_in=False,
    ),
    "pills": CheckinDef(
        id="pills",
        kind=KIND_DOSE,
        emoji="💊",
        title_key="checkins.pills.title",
        prompt_key="checkins.pills.prompt",
        actions=("taken", "skip", "snooze", "earlier"),
        tidy=TIDY_KEEP_PENDING,
    ),
    "water": CheckinDef(
        id="water",
        kind=KIND_COUNT,
        emoji="💧",
        title_key="checkins.water.title",
        prompt_key="checkins.water.prompt",
        unit="glass",
        bounds=(0, 99),
        actions=("add1", "add2", "undo"),
    ),
}


@dataclass(frozen=True)
class CheckinItem:
    """One configured check-in."""

    id: str
    template: str
    enabled: bool = True
    # pills
    name: str = ""
    dose: str = ""
    times: tuple[str, ...] = ()
    missed_after: int = DEFAULT_MISSED_AFTER
    # water
    target: int = 8
    every_hours: int = 0
    from_hour: int = 9
    to_hour: int = 21
    created: str = ""
    extra: dict[str, Any] = field(default_factory=dict, compare=False)

    @property
    def definition(self) -> CheckinDef:
        return TEMPLATES[self.template]

    @property
    def kind(self) -> str:
        return self.definition.kind

    def label(self) -> str:
        """What the operator calls it — the medication name exactly as typed."""
        if self.template == "pills":
            return f"{self.name} {self.dose}".strip()
        return {"weigh": "Weigh-in", "water": "Water"}.get(self.template, self.template)

    def slots(self) -> list[str]:
        """``HHMM`` slots the scheduler fires at (sorted, unique)."""
        if self.template == "pills":
            return sorted({t.replace(":", "") for t in self.times})
        if self.template == "water" and self.every_hours:
            from navig.checkins.cadence import water_slots  # noqa: PLC0415

            return water_slots(self.every_hours, self.from_hour, self.to_hour)
        return []

    def created_date(self) -> date | None:
        try:
            return date.fromisoformat(self.created[:10]) if self.created else None
        except ValueError:
            return None

    def to_config(self) -> dict[str, Any]:
        out: dict[str, Any] = {"template": self.template, "enabled": self.enabled}
        if self.template == "pills":
            out.update(
                name=self.name,
                dose=self.dose,
                times=[_fmt_time(t) for t in self.times],
                missed_after=self.missed_after,
            )
        elif self.template == "water":
            out.update(
                target=self.target,
                every_hours=self.every_hours,
                from_hour=self.from_hour,
                to_hour=self.to_hour,
            )
        if self.created:
            out["created"] = self.created
        return out

    def as_dict(self) -> dict[str, Any]:
        """The public shape (CLI ``--json``, deck route)."""
        d = self.to_config()
        d.update(
            id=self.id,
            kind=self.kind,
            emoji=self.definition.emoji,
            label=self.label(),
            slots=[_fmt_time(s) for s in self.slots()],
        )
        return d


# ── parsing and validation ────────────────────────────────────────────────────


def _fmt_time(value: str) -> str:
    v = value.replace(":", "")
    return f"{v[:2]}:{v[2:]}" if len(v) == 4 else value


def parse_time(text: str) -> str:
    """``8:00`` / ``08h30`` / ``20.15`` / ``8`` → ``"08:00"``. Raises ``ValueError``."""
    raw = (text or "").strip()
    m = _TIME_RE.match(raw)
    if m:
        return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"
    m = _HOUR_ONLY_RE.match(raw)
    if m:
        return f"{int(m.group(1)):02d}:00"
    raise ValueError(f"not a time of day: {raw!r} — use HH:MM, e.g. 08:00")


def parse_times(value: Any) -> tuple[str, ...]:
    """``"08:00,20:00"`` or a list → sorted unique ``("08:00", "20:00")``."""
    if isinstance(value, str):
        items = [p for p in re.split(r"[,\s;]+", value) if p.strip()]
    elif isinstance(value, (list, tuple)):
        items = [str(p) for p in value]
    else:
        raise ValueError("times must be a list or a comma-separated string")
    times = sorted({parse_time(p) for p in items})
    if not times:
        raise ValueError("at least one time of day is required")
    if len(times) > MAX_TIMES:
        raise ValueError(f"at most {MAX_TIMES} times a day")
    return tuple(times)


def parse_every(value: Any) -> int:
    """``"2h"`` / ``"2"`` / ``2`` / ``"off"`` → hours (0 = no reminders)."""
    if value is None or value == "" or str(value).strip().lower() in {"off", "0", "none", "no"}:
        return 0
    if isinstance(value, bool):
        raise ValueError("every must be a number of hours")
    text = str(value).strip().lower().removesuffix("h").removesuffix("hours").strip()
    try:
        hours = int(text)
    except ValueError as exc:
        raise ValueError(f"every must be a number of hours, got {value!r}") from exc
    if not 1 <= hours <= MAX_EVERY_HOURS:
        raise ValueError(f"every must be 1–{MAX_EVERY_HOURS} hours")
    return hours


def _int_field(data: dict, key: str, default: int, lo: int, hi: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be a whole number")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a whole number") from exc
    if not lo <= number <= hi:
        raise ValueError(f"{key} must be {lo}–{hi}")
    return number


def slugify(name: str) -> str:
    """A callback-safe id from a medication name: ``"Vitamin D3"`` → ``"vitamin-d3"``."""
    ascii_ = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_.lower()).strip("-")[:20].strip("-")
    return slug


def validate(template: str, data: dict[str, Any], *, item_id: str | None = None) -> CheckinItem:
    """Build a :class:`CheckinItem` from untrusted input. Raises ``ValueError``.

    Used by the CLI and the deck route alike, so the two cannot accept different
    things. The dose text is length-checked and stored verbatim — never parsed.
    """
    if template not in ("pills", "water"):
        raise ValueError("template must be 'pills' or 'water'")
    from navig.core.coerce import coerce_bool  # noqa: PLC0415

    enabled = coerce_bool(data.get("enabled"), default=True)
    created = str(data.get("created") or "")
    if template == "pills":
        name = " ".join(str(data.get("name") or "").split())
        if not name:
            raise ValueError("name is required (the medication, as you call it)")
        if len(name) > MAX_NAME:
            raise ValueError(f"name is at most {MAX_NAME} characters")
        dose = " ".join(str(data.get("dose") or "").split())
        if len(dose) > MAX_DOSE:
            raise ValueError(f"dose is at most {MAX_DOSE} characters")
        times = parse_times(
            data.get("times") if data.get("times") is not None else data.get("at", "")
        )
        missed = _int_field(
            data, "missed_after", DEFAULT_MISSED_AFTER, MIN_MISSED_AFTER, MAX_MISSED_AFTER
        )
        iid = item_id or slugify(name) or "pill"
        if not ID_RE.match(iid) or iid in (WEIGH_ID, WATER_ID):
            raise ValueError(f"invalid id {iid!r}")
        return CheckinItem(
            id=iid,
            template="pills",
            enabled=enabled,
            name=name,
            dose=dose,
            times=times,
            missed_after=missed,
            created=created,
        )
    target = _int_field(data, "target", 8, MIN_TARGET, MAX_TARGET)
    every = parse_every(data.get("every_hours", data.get("every")))
    from_hour = _int_field(data, "from_hour", 9, 0, 23)
    to_hour = _int_field(data, "to_hour", 21, 1, 24)
    if every and from_hour >= to_hour:
        raise ValueError("from_hour must be before to_hour")
    return CheckinItem(
        id=item_id or WATER_ID,
        template="water",
        enabled=enabled,
        target=target,
        every_hours=every,
        from_hour=from_hour,
        to_hour=to_hour,
        created=created,
    )


# ── reading and writing the configured items ─────────────────────────────────


def _config_tree() -> dict[str, Any]:
    try:
        from navig.config import get_config_manager  # noqa: PLC0415

        tree = get_config_manager().get(CONFIG_KEY, default={}) or {}
    except Exception:  # noqa: BLE001 — unreadable config: the weigh-in still lists
        return {}
    return tree if isinstance(tree, dict) else {}


def weigh_item() -> CheckinItem:
    """The built-in weigh-in, its on/off read from the weigh-in cadence."""
    from navig.spaces import weigh_cadence as wc  # noqa: PLC0415

    return CheckinItem(id=WEIGH_ID, template="weigh", enabled=wc.load_settings().enabled)


def load_items(*, include_weigh: bool = True) -> list[CheckinItem]:
    """Every configured check-in, weigh-in first. A malformed entry is skipped, not fatal."""
    items: list[CheckinItem] = [weigh_item()] if include_weigh else []
    for key, raw in sorted(_config_tree().items()):
        if key == WEIGH_ID or not isinstance(raw, dict):
            continue
        template = str(raw.get("template") or "")
        if template not in ("pills", "water"):
            continue
        try:
            items.append(validate(template, raw, item_id=str(key)))
        except ValueError:
            continue
    return items


def get_item(item_id: str) -> CheckinItem | None:
    for item in load_items():
        if item.id == item_id:
            return item
    return None


def unique_id(base: str, taken: set[str]) -> str:
    """*base*, or *base*-2, -3… — never one of *taken*."""
    if base not in taken:
        return base
    stem = base[:17].rstrip("-")
    for n in range(2, 100):
        candidate = f"{stem}-{n}"
        if candidate not in taken:
            return candidate
    raise ValueError("too many check-ins with that name")


def save_item(item: CheckinItem) -> CheckinItem:
    """Write *item* to ``checkins.<id>`` and return what reads back."""
    from navig.config import get_config_manager  # noqa: PLC0415

    if item.template == "weigh":
        raise ValueError("the weigh-in is configured with `navig body cadence`")
    if not item.created:
        from datetime import datetime  # noqa: PLC0415

        item = replace(item, created=datetime.now().date().isoformat())
    get_config_manager().set_global(f"{CONFIG_KEY}.{item.id}", item.to_config())
    saved = get_item(item.id)
    if saved is None or saved.to_config() != item.to_config():
        raise ValueError(f"check-in {item.id!r} did not persist")
    return saved


def remove_item(item_id: str) -> bool:
    """Delete ``checkins.<id>``. The records already written are kept."""
    from navig.config import get_config_manager  # noqa: PLC0415

    cm = get_config_manager()
    cm.refresh_global_config()
    tree = dict(cm.get(CONFIG_KEY, default={}) or {})
    if item_id not in tree or item_id == WEIGH_ID:
        return False
    tree.pop(item_id)
    cm.set_global(CONFIG_KEY, tree)
    return True


def set_enabled(item_id: str, enabled: bool) -> CheckinItem:
    """Pause or resume. The weigh-in maps onto its cadence's on/off."""
    if item_id == WEIGH_ID:
        from navig.spaces import weigh_cadence as wc  # noqa: PLC0415

        wc.save_settings({"enabled": enabled})
        return weigh_item()
    item = get_item(item_id)
    if item is None:
        raise KeyError(item_id)
    return save_item(replace(item, enabled=enabled))
