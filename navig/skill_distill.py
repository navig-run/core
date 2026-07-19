"""
Skill distillation (T-069, plan-evidence-ledger.md) — turn a slice of the
operations ledger into a draft, reusable ``SKILL.md``.

You do a real task through NAVIG — twenty commands, two failed attempts before
it worked. ``navig skill distill --last 2h`` reads that slice of
``operations.jsonl`` and writes the recipe: the SUCCESSFUL path as ordered
steps, the failed attempts as "don't do this" pitfalls, reversibility labels
as danger annotations, and conservative placeholders where values look
instance-specific.

Pipeline (each stage is a pure function, testable in isolation):

    slice   — ``slice_ledger()``: a time window (``--last 2h``) or explicit
              operation ids, chronological order.
    filter  — ``distill()``: SUCCESS-only steps; failed operations become
              pitfalls; undone operations (T-068 ``collect_undone``) and
              undo entries are excluded; consecutive duplicates collapse;
              distill's own meta-invocations are dropped.
    sanitize— ``sanitize_command()``: secret sweep FIRST (redact_sensitive_text
              + sensitive key=value + secret-bearing flags + opaque tokens),
              then conservative instance-placeholders (``<host>``, ``<user>``,
              ``<email>``, ``<ip>``). When unsure the literal stays — a wrong
              placeholder is worse than a reviewable literal.
    emit    — ``render_skill_md()``: a Claude-Code-compatible SKILL.md per
              docs/authoring-guide.md §1–§2 (real YAML frontmatter, a
              description rich enough to route, ``safety`` derived from the
              worst reversibility label in the recipe).

Secrets contract (non-negotiable, plan §3): sanitization runs BEFORE anything
leaves this module — the deterministic renderer and the optional AI drafter
(``navig/agent/skill_distiller.py``) both receive already-sanitized text only.
Secret placeholders never carry an example value.

Nothing here prints; the CLI surface lives in ``navig/commands/skills.py``
(``navig skill distill``). Refusals raise :class:`DistillError` with a
user-facing message.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.operation_recorder import OperationRecord, OperationRecorder

__all__ = [
    "DistillError",
    "DistillResult",
    "DistilledStep",
    "Pitfall",
    "distill",
    "parse_duration",
    "render_skill_md",
    "sanitize_command",
    "slice_ledger",
]

#: How far back the slicer looks. The ledger rotates at 10k entries, so this
#: covers everything retained (same bound as navig.undo).
_SCAN_LIMIT = 20_000

#: Cap on error text carried into a pitfall note (redacted first).
_ERROR_SNIPPET_CHARS = 200

#: Commands that ARE the distillation machinery — a previous `navig skill
#: distill` run must never become a step of the skill it produced.
_META_MARKERS: tuple[str, ...] = ("skill distill", "skills distill")


class DistillError(Exception):
    """Nothing distillable; ``str(exc)`` is the honest user-facing why."""


# ---------------------------------------------------------------------------
# Duration parsing — the --last window
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smhdw])\s*$", re.IGNORECASE)

_DURATION_UNITS: dict[str, float] = {
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}


def parse_duration(text: str) -> timedelta:
    """``"2h"`` → 2 hours. Accepts s/m/h/d/w; the unit is required.

    A bare number is rejected on purpose — "``--last 30``" is ambiguous, and a
    silently-guessed unit slices the wrong window.
    """
    match = _DURATION_RE.match(text or "")
    if not match:
        raise ValueError(
            f"invalid duration {text!r} — use <number><unit> with unit s/m/h/d/w, e.g. 2h, 30m, 1d"
        )
    value, unit = match.groups()
    seconds = float(value) * _DURATION_UNITS[unit.lower()]
    if seconds <= 0:
        raise ValueError(f"duration must be positive: {text!r}")
    return timedelta(seconds=seconds)


# ---------------------------------------------------------------------------
# Slice — pick the ledger records to distill from
# ---------------------------------------------------------------------------


def slice_ledger(
    recorder: OperationRecorder,
    *,
    last: timedelta | None = None,
    op_ids: list[str] | None = None,
    now: datetime | None = None,
) -> list[OperationRecord]:
    """The records to distill, oldest first.

    ``op_ids`` wins over ``last`` (explicit beats implicit); ids that do not
    exist raise :class:`DistillError` — silently dropping a requested id would
    distill a different recipe than the one asked for.
    """
    if op_ids:
        records = []
        for op_id in op_ids:
            rec = recorder.get_operation(op_id)
            if rec is None:
                raise DistillError(f"operation not found in the ledger: {op_id}")
            records.append(rec)
        records.sort(key=lambda r: r.timestamp)
        return records

    window = last or timedelta(hours=2)
    moment = now or datetime.now(timezone.utc)
    since = (moment - window).isoformat()
    return list(recorder.iter_operations(limit=_SCAN_LIMIT, since=since, reverse=False))


# ---------------------------------------------------------------------------
# Sanitize — secret sweep first, then conservative placeholders
# ---------------------------------------------------------------------------

#: Flags whose FOLLOWING token is secret material, whatever it looks like.
_SECRET_FLAGS = frozenset(
    {
        "--password",
        "--passwd",
        "--token",
        "--secret",
        "--api-key",
        "--apikey",
        "--auth",
        "--credential",
    }
)

#: ``***REDACTED***`` markers left by record-time redaction (T-068) — swap the
#: whole token for the placeholder so drafts read cleanly.
_REDACTED_MARKER = "***REDACTED***"

#: Home-directory user segments (Windows + POSIX) — the "paths with usernames"
#: rule from the plan. Only the user segment is replaced; the rest of the path
#: stays literal and reviewable.
_HOME_USER_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)((?:[A-Za-z]:)?[\\/](?:Users|home)[\\/])([^\\/\s\"']+)"),
)

_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

#: Opaque-token heuristic: ≥32 chars of token alphabet, mixing letters AND
#: digits, with no path separators or dots — the shape of an API key we have
#: no prefix pattern for. Deliberately strict (32, not 20): operation ids,
#: package names, and slugs must stay literal.
_OPAQUE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{32,}$")


def _looks_opaque_secret(token: str) -> bool:
    if not _OPAQUE_TOKEN_RE.match(token):
        return False
    has_alpha = any(c.isalpha() for c in token)
    has_digit = any(c.isdigit() for c in token)
    return has_alpha and has_digit


def sanitize_command(
    command: str,
    *,
    host: str | None = None,
) -> tuple[str, dict[str, str]]:
    """(sanitized command, placeholder → example map).

    Secret material is swept FIRST and its placeholder never carries an
    example. Instance placeholders (host, user, email, ip) keep one example
    value so the reader knows what to substitute. Anything not confidently
    matched stays literal — reviewable beats guessed.
    """
    placeholders: dict[str, str] = {}

    # 1. Secret sweep — pattern redaction over the whole line first.
    try:
        from navig.core.security import redact_sensitive_text

        text = redact_sensitive_text(command)
    except Exception:  # noqa: BLE001 — sweep must never crash distillation
        text = command

    from navig.reversibility import is_sensitive_config_key

    tokens = text.split()
    out: list[str] = []
    secret_next = False
    for token in tokens:
        if secret_next:
            out.append("<secret>")
            placeholders.setdefault("<secret>", "")
            secret_next = False
            continue
        if token.lower() in _SECRET_FLAGS:
            out.append(token)
            secret_next = True
            continue
        if "=" in token:
            key, _, value = token.partition("=")
            if key and (
                is_sensitive_config_key(key.lstrip("-"))
                or _REDACTED_MARKER in value
                or _looks_opaque_secret(value)
            ):
                out.append(f"{key}=<secret>")
                placeholders.setdefault("<secret>", "")
                continue
        if _REDACTED_MARKER in token:
            out.append("<secret>")
            placeholders.setdefault("<secret>", "")
            continue
        if _looks_opaque_secret(token):
            out.append("<secret>")
            placeholders.setdefault("<secret>", "")
            continue

        # 2. Instance placeholders — conservative, example kept.
        if host and token == host:
            out.append("<host>")
            placeholders.setdefault("<host>", host)
            continue

        new_token = token
        for home_re in _HOME_USER_RES:
            replaced = home_re.sub(lambda m: m.group(1) + "<user>", new_token)
            if replaced != new_token:
                user = home_re.search(token)
                placeholders.setdefault("<user>", user.group(2) if user else "")
                new_token = replaced
        if _EMAIL_RE.search(new_token):
            placeholders.setdefault("<email>", _EMAIL_RE.search(new_token).group(0))
            new_token = _EMAIL_RE.sub("<email>", new_token)
        if _IPV4_RE.search(new_token):
            placeholders.setdefault("<ip>", _IPV4_RE.search(new_token).group(0))
            new_token = _IPV4_RE.sub("<ip>", new_token)
        out.append(new_token)

    return " ".join(out), placeholders


def _sanitize_text(text: str) -> str:
    """Redact free text (error messages) — patterns only, no tokenization."""
    try:
        from navig.core.security import redact_sensitive_text

        return redact_sensitive_text(text)
    except Exception:  # noqa: BLE001 — sweep must never crash distillation
        return text


# ---------------------------------------------------------------------------
# Distill — filter to the successful path, collapse noise
# ---------------------------------------------------------------------------


@dataclass
class DistilledStep:
    """One step of the working recipe."""

    command: str  # sanitized
    operation_type: str
    reversibility: str
    count: int = 1  # consecutive identical invocations collapsed
    op_ids: list[str] = field(default_factory=list)


@dataclass
class Pitfall:
    """One failed attempt worth warning about."""

    command: str  # sanitized
    error: str  # redacted + truncated
    exit_code: int
    op_id: str = ""


@dataclass
class DistillResult:
    """Everything the renderer (and the --json summary) needs."""

    name: str
    slug: str
    description: str
    safety: str  # safe | elevated | destructive (worst step label)
    steps: list[DistilledStep]
    pitfalls: list[Pitfall]
    placeholders: dict[str, str]
    window_from: str
    window_to: str
    window_label: str
    total_records: int
    undone_excluded: int
    meta_excluded: int
    collapsed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "slug": self.slug,
            "description": self.description,
            "safety": self.safety,
            "steps": [
                {
                    "command": s.command,
                    "operation_type": s.operation_type,
                    "reversibility": s.reversibility,
                    "count": s.count,
                    "op_ids": s.op_ids,
                }
                for s in self.steps
            ],
            "pitfalls": [
                {
                    "command": p.command,
                    "error": p.error,
                    "exit_code": p.exit_code,
                    "op_id": p.op_id,
                }
                for p in self.pitfalls
            ],
            "placeholders": {
                k: (v if v else None) for k, v in sorted(self.placeholders.items())
            },
            "window": {
                "from": self.window_from,
                "to": self.window_to,
                "label": self.window_label,
            },
            "counts": {
                "total_records": self.total_records,
                "steps": len(self.steps),
                "pitfalls": len(self.pitfalls),
                "undone_excluded": self.undone_excluded,
                "meta_excluded": self.meta_excluded,
                "collapsed": self.collapsed,
            },
        }


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    return _SLUG_RE.sub("-", text.strip().lower()).strip("-")[:64]


def _derive_name(steps: list[DistilledStep]) -> str:
    """A default skill name from the resources touched, in first-seen order."""
    resources: list[str] = []
    for step in steps:
        tokens = step.command.split()
        # commands are recorded as "navig <resource> <action> ..."
        resource = tokens[1] if len(tokens) > 1 and tokens[0] == "navig" else tokens[0] if tokens else ""
        resource = _slugify(resource)
        if resource and resource not in resources:
            resources.append(resource)
        if len(resources) >= 3:
            break
    base = "-".join(resources) if resources else "distilled"
    return f"{base}-workflow"


def _effective_label(record: OperationRecord) -> str:
    """The stored label, or (for pre-T-068 entries) the computed one."""
    from navig.reversibility import classify

    return record.reversibility or classify(
        record.operation_type.value, record.undo_data or None, record.tags
    ).value


#: Worst-label → SKILL.md ``safety`` enum (authoring-guide §1). A recipe with
#: an irreversible step IS destructive — the label must not undersell it.
_SAFETY_BY_LABEL = {
    "red": "destructive",
    "yellow": "elevated",
    "green": "safe",
    "none": "safe",
    "": "safe",
}

_LABEL_SEVERITY = {"red": 3, "yellow": 2, "green": 1, "none": 0, "": 0}


def distill(
    records: list[OperationRecord],
    *,
    name: str | None = None,
    window_label: str = "",
) -> DistillResult:
    """Filter *records* to the successful path and build the recipe.

    Raises :class:`DistillError` when there is nothing distillable — an empty
    slice, or a slice where nothing succeeded.
    """
    from navig.operation_recorder import OperationStatus
    from navig.undo import collect_undone

    if not records:
        raise DistillError("no operations in the selected slice — nothing to distill")

    undone = collect_undone(list(reversed(records)))  # collect_undone expects newest-first

    steps: list[DistilledStep] = []
    pitfalls: list[Pitfall] = []
    placeholders: dict[str, str] = {}
    undone_excluded = 0
    meta_excluded = 0
    collapsed = 0

    for rec in records:
        command = rec.command or ""
        if any(marker in command for marker in _META_MARKERS):
            meta_excluded += 1
            continue
        if rec.tags and "undo" in rec.tags:
            undone_excluded += 1
            continue
        if rec.id in undone:
            undone_excluded += 1
            continue

        sanitized, found = sanitize_command(command, host=rec.host)
        for key, example in found.items():
            placeholders.setdefault(key, example)

        if rec.status == OperationStatus.SUCCESS:
            if steps and steps[-1].command == sanitized:
                steps[-1].count += 1
                steps[-1].op_ids.append(rec.id)
                collapsed += 1
                continue
            steps.append(
                DistilledStep(
                    command=sanitized,
                    operation_type=rec.operation_type.value,
                    reversibility=_effective_label(rec),
                    op_ids=[rec.id],
                )
            )
        elif rec.status == OperationStatus.FAILED:
            error = _sanitize_text(rec.error or "").strip()
            if len(error) > _ERROR_SNIPPET_CHARS:
                error = error[:_ERROR_SNIPPET_CHARS] + "…"
            pitfalls.append(
                Pitfall(
                    command=sanitized,
                    error=error,
                    exit_code=rec.exit_code,
                    op_id=rec.id,
                )
            )
        # PARTIAL / CANCELLED / PENDING / INTERRUPTED: neither a working step
        # nor a warnable failure (an interrupted op's real outcome is unknown).

    if not steps:
        raise DistillError(
            f"{len(records)} operation(s) in the slice but none form a successful path "
            "— nothing distillable (failed, undone, and meta operations are excluded)"
        )

    slug = _slugify(name) if name else _slugify(_derive_name(steps))
    if not slug:
        raise DistillError(f"invalid skill name: {name!r}")

    worst = max((s.reversibility for s in steps), key=lambda v: _LABEL_SEVERITY.get(v, 3))
    safety = _SAFETY_BY_LABEL.get(worst, "destructive")

    used = [r for r in records if r.status in (OperationStatus.SUCCESS, OperationStatus.FAILED)]
    window_from = min((r.timestamp for r in used), default="")
    window_to = max((r.timestamp for r in used), default="")

    return DistillResult(
        name=slug,
        slug=slug,
        description=_build_description(slug, steps, window_to),
        safety=safety,
        steps=steps,
        pitfalls=pitfalls,
        placeholders=placeholders,
        window_from=window_from,
        window_to=window_to,
        window_label=window_label,
        total_records=len(records),
        undone_excluded=undone_excluded,
        meta_excluded=meta_excluded,
        collapsed=collapsed,
    )


def _yaml_safe(text: str) -> str:
    """Text safe inside the frontmatter block scalar: a literal ``---`` would
    terminate the frontmatter early and void every field (lint's §1 trap)."""
    return text.replace("---", "–").replace("\n", " ")


def _build_description(slug: str, steps: list[DistilledStep], when: str) -> str:
    """A description rich enough to route (authoring-guide §2): what it does,
    verbatim trigger phrases, and read-only-vs-mutating in one clause."""
    preview = " → ".join(s.command for s in steps[:3])
    if len(steps) > 3:
        preview += " → …"
    day = when[:10] if when else "an earlier session"
    mutating = any(s.reversibility in ("yellow", "red") for s in steps)
    risk = "Contains mutating steps — review before running." if mutating else "Read-only steps."
    text = (
        f"Replays a verified {len(steps)}-step sequence distilled from the NAVIG "
        f'operations ledger on {day}. Use when the user says "{slug.replace("-", " ")}", '
        f'"repeat that workflow", or wants to re-run: {preview}. {risk}'
    )
    return _yaml_safe(text)


# ---------------------------------------------------------------------------
# Emit — the SKILL.md (deterministic; works offline)
# ---------------------------------------------------------------------------

_LABEL_ANNOTATIONS = {
    "red": " — ⚠ irreversible (red): confirm before running",
    "yellow": " — compensable (yellow): has a manual counter-action",
    "none": " — read-only",
}


def render_skill_md(result: DistillResult) -> str:
    """The draft SKILL.md — Claude-Code-compatible frontmatter + recipe body.

    Structured to pass ``navig skill lint`` clean: real YAML frontmatter,
    ``name`` + a routing-rich ``description``, a valid ``safety`` enum.
    """
    lines: list[str] = [
        "---",
        f"name: {result.slug}",
        "description: >-",
        f"  {result.description}",
        f"safety: {result.safety}",
        "category: distilled",
        "tags: [distilled, ledger]",
        "---",
        "",
        f"# {result.slug.replace('-', ' ').title()}",
        "",
        "> Draft skill distilled from the operations ledger — a recipe written",
        "> from what actually worked. Review every step before trusting it;",
        "> replace placeholders with your real values.",
        "",
        "## Steps",
        "",
    ]
    for i, step in enumerate(result.steps, start=1):
        note = _LABEL_ANNOTATIONS.get(step.reversibility, "")
        times = f" (ran {step.count}×)" if step.count > 1 else ""
        lines.append(f"{i}. `{step.command}`{times}{note}")
    lines.append("")

    if result.pitfalls:
        lines += [
            "## Pitfalls observed",
            "",
            "These attempts FAILED during the recorded session — avoid repeating them:",
            "",
        ]
        for pit in result.pitfalls:
            detail = f" — {pit.error}" if pit.error else ""
            lines.append(f"- `{pit.command}` failed (exit {pit.exit_code}){detail}")
        lines.append("")

    if result.placeholders:
        lines += [
            "## Placeholders",
            "",
            "Replace before running:",
            "",
            "| Placeholder | Example from the session |",
            "|-------------|--------------------------|",
        ]
        for key in sorted(result.placeholders):
            example = result.placeholders[key]
            shown = f"`{example}`" if example else "_(secret — value never stored)_"
            lines.append(f"| `{key}` | {shown} |")
        lines.append("")

    first_id = result.steps[0].op_ids[0] if result.steps and result.steps[0].op_ids else ""
    last_ids = result.steps[-1].op_ids if result.steps else []
    last_id = last_ids[-1] if last_ids else ""
    window = result.window_label or f"{result.window_from} → {result.window_to}"
    lines += [
        "## Provenance",
        "",
        f"- Distilled from the NAVIG operations ledger · window: {window}",
        (
            f"- {result.total_records} operation(s) in slice → {len(result.steps)} step(s), "
            f"{len(result.pitfalls)} pitfall(s); excluded: {result.undone_excluded} undone/undo, "
            f"{result.meta_excluded} meta; {result.collapsed} duplicate(s) collapsed"
        ),
        f"- Operation ids: {first_id} … {last_id}",
        "- Secret material was redacted at the source; placeholders replaced instance-specific values.",
        "",
    ]
    return "\n".join(lines)
