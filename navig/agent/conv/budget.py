"""Context budget with truncation cause attribution.

The problem this replaces
-------------------------
Identity content used to be cut silently in three places — ``_condense_soul``'s
``raw[:_MAX_SOUL_CHARS]`` and ``raw[:2000]``, and ``_load_user_profile``'s
1,500-char cap. A 9k-char ``SOUL.md`` reached the model as 4k with a bare ``…``,
and nothing — not the operator, not the model — knew which half was missing. The
model then answered confidently from a fragment of its own instructions.

So a truncation now carries **why** (``per-file-limit`` vs ``total-limit``) and
gets one line in the prompt telling the model its own identity was shortened, so
it can say so instead of guessing.

This is also the answer to "should we conditionally load identity by domain?" —
a budget fails *safe* (you always consider every source, you just shorten it),
whereas a keyword classifier fails *open* (a mis-fire silently drops a file
entirely). Truncating a guardrail is visible; never loading it is not.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal, Sequence

#: ``per-file-limit`` — the item alone exceeded its own cap.
#: ``total-limit`` — the item was shortened to fit the combined budget.
TruncationCause = Literal["per-file-limit", "total-limit"]

#: Marker appended to a truncated chunk. Matches the pre-existing
#: ``_condense_soul`` output contract exactly, so nothing downstream shifts.
TRUNCATION_MARKER = "\n…"

#: Below this many surviving chars a budgeted item is dropped rather than
#: emitted as a meaningless stub.
_MIN_USEFUL_CHARS = 80


@dataclass(frozen=True, slots=True)
class FileBudget:
    """What one source cost and what happened to it."""

    name: str
    path: str
    raw_chars: int
    injected_chars: int
    truncated: bool
    causes: tuple[TruncationCause, ...] = ()

    @property
    def removed_pct(self) -> int:
        if not self.raw_chars or not self.truncated:
            return 0
        return max(0, round((1 - self.injected_chars / self.raw_chars) * 100))


@dataclass(frozen=True, slots=True)
class BudgetReport:
    """Aggregate outcome of one :func:`apply_budget` pass."""

    files: tuple[FileBudget, ...] = ()
    raw_chars: int = 0
    injected_chars: int = 0
    per_file_max: int = 0
    total_max: int = 0

    @property
    def truncated_files(self) -> tuple[FileBudget, ...]:
        return tuple(f for f in self.files if f.truncated)

    @property
    def has_truncation(self) -> bool:
        return bool(self.truncated_files)

    def signature(self) -> str:
        """Stable, order-independent key for deduping repeat warnings.

        Two turns with the same truncation state produce the same signature, so a
        warning can be emitted once per session instead of once per turn — which
        also keeps it out of the volatile part of the prompt.
        """
        if not self.has_truncation:
            return ""
        payload = sorted(
            (f.name, f.raw_chars, f.injected_chars, tuple(sorted(f.causes)))
            for f in self.truncated_files
        )
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]

    def warning_lines(self, max_files: int = 3) -> list[str]:
        """Operator-facing detail (CLI report), most-truncated first."""
        truncated = sorted(self.truncated_files, key=lambda f: f.raw_chars - f.injected_chars,
                           reverse=True)
        lines = [
            f"{f.name}: {f.raw_chars:,} → {f.injected_chars:,} chars "
            f"(~{f.removed_pct}% removed; {', '.join(f.causes)})"
            for f in truncated[:max_files]
        ]
        if len(truncated) > max_files:
            lines.append(f"+{len(truncated) - max_files} more truncated source(s)")
        return lines

    def prompt_note(self) -> str:
        """One line for the model, or ``""`` when nothing was truncated.

        Empty on a healthy install, so this costs zero tokens for the common case
        and never destabilises the cached prefix.
        """
        if not self.has_truncation:
            return ""
        names = ", ".join(f"{f.name} (~{f.removed_pct}% removed)" for f in self.truncated_files[:3])
        return (
            f"⚠ Part of your own instructions was too long to include and was shortened: {names}. "
            "If the operator's request seems to depend on guidance you cannot see, say so rather "
            "than guessing."
        )


def _cut(text: str, limit: int) -> str:
    """Trim *text* to *limit* chars, preserving the historical ``…`` marker."""
    if len(text) <= limit:
        return text
    keep = max(0, limit - len(TRUNCATION_MARKER))
    return text[:keep].rstrip() + TRUNCATION_MARKER


def apply_budget(
    items: Sequence[tuple[str, str, str]],
    *,
    per_file_max: int,
    total_max: int,
) -> tuple[list[tuple[str, str]], BudgetReport]:
    """Fit ``(name, path, raw)`` items into a char budget, attributing every cut.

    Items are budgeted in the order given, so callers must pass them
    highest-priority first: an earlier item keeps its full allowance and later
    ones absorb the ``total-limit`` pressure. Returns ``([(name, injected)], report)``.
    """
    injected: list[tuple[str, str]] = []
    stats: list[FileBudget] = []
    raw_total = 0
    used = 0

    for name, path, raw in items:
        raw = raw or ""
        raw_total += len(raw)
        if not raw:
            stats.append(FileBudget(name, path, 0, 0, False))
            continue

        causes: list[TruncationCause] = []
        text = raw
        if len(text) > per_file_max:
            text = _cut(text, per_file_max)
            causes.append("per-file-limit")

        remaining = max(0, total_max - used)
        if len(text) > remaining:
            text = _cut(text, remaining) if remaining >= _MIN_USEFUL_CHARS else ""
            causes.append("total-limit")

        used += len(text)
        stats.append(
            FileBudget(
                name=name,
                path=path,
                raw_chars=len(raw),
                injected_chars=len(text),
                truncated=bool(causes),
                causes=tuple(causes),
            )
        )
        if text:
            injected.append((name, text))

    report = BudgetReport(
        files=tuple(stats),
        raw_chars=raw_total,
        injected_chars=used,
        per_file_max=per_file_max,
        total_max=total_max,
    )
    return injected, report
