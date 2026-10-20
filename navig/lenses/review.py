"""What a staged Lens change would do — the diff a person reviews, and the power it adds.

A Lens change reaches the store two ways without a person writing it: an agent's edit of
an approved Lens, and an update fetched from the Lens's install link. Neither is applied
unseen. :meth:`navig.lenses.store.LensStore.stage` parks it beside the running copy, and
the review shows two things:

* the **diff** of the source, and
* the **changes in power** — sites it now runs on, grants, ``@require`` code, the Harbor
  capability, timing. A one-line code change that adds ``@match <all_urls>`` is the line
  that matters; this list puts it first instead of leaving it in the diff.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any

from navig.lenses.manifest import LensManifest

MAX_DIFF_LINES = 2000


@dataclass(frozen=True)
class Change:
    field: str
    label: str
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    broadens: bool = False  # the Lens can do or reach MORE than before

    def to_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "label": self.label,
            "added": list(self.added),
            "removed": list(self.removed),
            "broadens": self.broadens,
        }


# (field, label, adding widens?) — for excludes, REMOVING one widens where the Lens runs.
_LIST_FIELDS = (
    ("matches", "Runs on (@match)", True),
    ("includes", "Runs on (@include)", True),
    ("exclude_matches", "Skips (@exclude-match)", False),
    ("excludes", "Skips (@exclude)", False),
    ("grants", "Permissions (@grant)", True),
    ("requires", "Extra code (@require)", True),
)


def changes(old: LensManifest | None, new: LensManifest) -> list[Change]:
    """The power ``new`` has that ``old`` did not (or everything, for a new Lens)."""
    out: list[Change] = []
    for field, label, widens_on_add in _LIST_FIELDS:
        before = list(getattr(old, field)) if old else []
        after = list(getattr(new, field))
        added = tuple(v for v in after if v not in before)
        removed = tuple(v for v in before if v not in after)
        if added or removed:
            broadens = bool(added) if widens_on_add else bool(removed)
            out.append(Change(field, label, added, removed, broadens))
    before_cap = old.capability if old else None
    if new.capability != before_cap:
        out.append(
            Change(
                "capability",
                "Harbor capability",
                (new.capability,) if new.capability else (),
                (before_cap,) if before_cap else (),
            )
        )
    if old is None or new.run_at != old.run_at:
        out.append(Change("run_at", "Runs at", (new.run_at,), (old.run_at,) if old else (), False))
    if old is not None and old.noframes and not new.noframes:
        out.append(Change("noframes", "Frames", ("runs inside frames too",), (), True))
    out.sort(key=lambda c: not c.broadens)  # what widens the Lens first
    return out


def unified_diff(current: str, proposed: str, *, name: str = "lens.user.js") -> str:
    lines = list(
        difflib.unified_diff(
            current.splitlines(),
            proposed.splitlines(),
            fromfile=f"{name} (running)",
            tofile=f"{name} (proposed)",
            lineterm="",
        )
    )
    if len(lines) > MAX_DIFF_LINES:
        hidden = len(lines) - MAX_DIFF_LINES
        lines = [*lines[:MAX_DIFF_LINES], f"… {hidden} more diff line(s) not shown"]
    return "\n".join(lines)


def _version_key(version: str) -> tuple[tuple[int, str], ...]:
    parts = re.split(r"[.\-+]", version.strip()) if version.strip() else ["0"]
    return tuple((int(p), "") if p.isdigit() else (-1, p) for p in parts)


def is_older(candidate: str, current: str) -> bool:
    """``candidate`` is an older ``@version`` than ``current`` (a downgrade is never staged)."""
    a, b = _version_key(candidate), _version_key(current)
    width = max(len(a), len(b))
    pad = ((0, ""),)
    return a + pad * (width - len(a)) < b + pad * (width - len(b))
