"""The reuse-first decision ladder — deterministic, evidence-driven, LLM-free.

Given a goal, normalized evidence, and the capability index, answer the owner's
ordered question (mission §4):

1. **reuse**    an existing capability directly
2. **configure** an existing capability (unwired / missing config)
3. **compose**  connect existing capabilities (glue only)
4. **extend**   repair a broken capability (scoped to that id)
5. **create**   new capability — only when nothing matches AND evidence clears thresholds
6. **do nothing** if the benefit does not justify the complexity

Because reuse is checked FIRST, "never generate a wrapper for an existing tool"
is structural, not a lint. Every ``Decision`` carries the fields the proposal
card shows: reason, reused components, genuine gap, benefit, complexity,
verification — and ``assumptions`` records capability names the AI used that the
index could NOT confirm (the mission's third provenance class, on proposals only).

No LLM imports — guard-tested. The chat agent calls this through the
``creative`` tool and may only enrich wording, never verdicts.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

__all__ = [
    "Evidence",
    "Match",
    "ArtifactSpec",
    "Decision",
    "IndexView",
    "decide",
    "REPEAT_CMD_MIN",
    "FAIL_CMD_MIN",
    "SEQUENCE_MIN",
    "MATCH_SCORE_MIN",
]

# Thresholds — mirrored from what `navig insights` already treats as worth a
# recommendation (recurring ≥3, frequent use), made explicit and testable.
REPEAT_CMD_MIN = 5  # same normalized command within the evidence window
FAIL_CMD_MIN = 3  # same command failing within the window
SEQUENCE_MIN = 3  # same 2–3 command sequence repeated
MATCH_SCORE_MIN = 3  # index.find score that counts as "this IS the capability" (name hit)

Verdict = Literal["reuse", "configure", "compose", "extend", "create", "noop"]


@dataclass
class Evidence:
    """Normalized, permission-safe friction signals (windows enforced upstream).

    Everything here is derived from the operator's own operation history and
    goal state — never from chat content (slice scope, privacy).
    """

    #: [(normalized command, count)] within the window
    repeated_commands: list[tuple[str, int]] = field(default_factory=list)
    #: [(command, failure count)] within the window
    failing_commands: list[tuple[str, int]] = field(default_factory=list)
    #: [(command sequence, occurrence count)]
    sequences: list[tuple[tuple[str, ...], int]] = field(default_factory=list)
    #: Human-readable blocked/stopped goal notes
    blocked_goals: list[str] = field(default_factory=list)
    #: Capability names the operator asked about in this turn (agent-provided)
    requested: list[str] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not (
            self.repeated_commands
            or self.failing_commands
            or self.sequences
            or self.blocked_goals
            or self.requested
        )


@dataclass
class Match:
    """One index hit the decision relied on."""

    id: str
    name: str
    kind: str
    score: int
    state: str


@dataclass
class ArtifactSpec:
    """The artifact NAVIG would build itself — the user never picks the type."""

    kind: Literal["skill"]
    slug: str
    title: str
    description: str
    #: Command literals the artifact wires together (all index-confirmed or
    #: drawn from the operator's own history — never free-form prose).
    commands: list[str] = field(default_factory=list)


@dataclass
class Decision:
    verdict: Verdict
    reason: str
    matched: list[Match] = field(default_factory=list)
    reused_components: list[str] = field(default_factory=list)
    genuine_gap: str = ""
    plan: list[str] = field(default_factory=list)
    artifact: ArtifactSpec | None = None
    benefit: str = ""
    complexity: Literal["none", "low", "medium"] = "none"
    verification: str = ""
    assumptions: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return self.verdict == "noop"


class IndexView:
    """Read-only window over a capability-index snapshot."""

    def __init__(self, snapshot: Mapping[str, Any] | None):
        self._snap: dict[str, Any] = dict(snapshot or {})
        self._by_id: dict[str, dict[str, Any]] = {
            e["id"]: e for e in self._snap.get("entries", []) if isinstance(e, dict) and "id" in e
        }

    @classmethod
    def live(cls) -> "IndexView":
        from navig.creative.index import ensure_index

        return cls(ensure_index())

    def find(self, query: str, *, kinds: list[str] | None = None, limit: int = 10) -> list[dict]:
        from navig.creative.index import find as _find

        return _find(query, kinds=kinds, limit=limit, index=self._snap)

    def get(self, entry_id: str) -> dict | None:
        return self._by_id.get(entry_id)

    def __len__(self) -> int:
        return len(self._by_id)


def _match_from(entry: dict, score: int) -> Match:
    observed = entry.get("observed") or {}
    return Match(
        id=entry["id"],
        name=str(entry.get("name") or entry["id"]),
        kind=str(entry.get("kind") or "?"),
        score=score,
        state=str(observed.get("state") or "unknown"),
    )


def _state(entry: dict) -> str:
    return str(((entry.get("observed") or {}).get("state")) or "unknown")


def _is_wired(entry: dict) -> bool:
    return _state(entry) in ("available", "wired")


def _is_broken(entry: dict) -> bool:
    return _state(entry) in ("broken", "error")


def _missing_config(entry: dict, config: Mapping[str, Any]) -> list[str]:
    keys = ((entry.get("requires") or {}).get("config")) or []
    return [k for k in keys if k not in config or config[k] in (None, "")]


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (slug or "workflow")[:48].strip("-")


_UNCONFIRMED_RE = re.compile(r"\bnavig\s+([a-z][a-z0-9_-]*)")


def _assumptions(goal: str, index: IndexView) -> list[str]:
    """Capability names the text implies that the index cannot confirm."""
    out: list[str] = []
    for m in _UNCONFIRMED_RE.finditer(goal or ""):
        verb = m.group(1)
        # Only flag when NO index entry covers that verb at all.
        if not index.find(verb, kinds=["command"], limit=1):
            out.append(f"navig {verb}")
    return out


def decide(
    goal: str,
    *,
    evidence: Evidence | None = None,
    index: IndexView | None = None,
    config: Mapping[str, Any] | None = None,
) -> Decision:
    """Run the ladder. Pure function of (goal, evidence, index, config)."""
    ev = evidence or Evidence()
    view = index if index is not None else IndexView.live()
    cfg: Mapping[str, Any] = config or {}
    goal_l = (goal or "").strip().lower()
    assumptions = _assumptions(goal or "", view)

    # Candidates = ranked goal hits + EXACT command ids the evidence points at
    # (a repeated `navig x` whose command exists is reuse, never create — the
    # exact hit is scored strongest so it wins the ladder).
    goal_hits = view.find(goal_l, limit=5) if goal_l else []
    candidates: dict[str, tuple[dict, int]] = {h["id"]: (h, _score(h, goal_l)) for h in goal_hits}
    for cmd, _n in ev.repeated_commands:
        hit = view.get(f"command:{cmd}")
        if hit is not None:
            prev = candidates.get(hit["id"])
            if prev is None or prev[1] < 10:
                candidates[hit["id"]] = (hit, 10)
    for cmd, _n in ev.failing_commands:
        hit = view.get(f"command:{cmd}")
        if hit is not None:
            prev = candidates.get(hit["id"])
            if prev is None or prev[1] < 10:
                candidates[hit["id"]] = (hit, 10)

    ranked = sorted(candidates.values(), key=lambda row: -row[1])
    strong = [(h, s) for h, s in ranked if s >= MATCH_SCORE_MIN]
    wired_strong = [h for h, s in strong if _is_wired(h)]
    broken_strong = [h for h, s in strong if _is_broken(h)]
    unwired_strong = [
        h for h, s in strong if _state(h) in ("unwired", "disabled", "unknown")
    ]

    def _score_of(entry_id: str) -> int:
        row = candidates.get(entry_id)
        return row[1] if row else 0

    # 1) REUSE — one wired capability already does this.
    if wired_strong:
        top = wired_strong[0]
        missing = _missing_config(top, cfg)
        if missing:
            # Has it, but it is not usable yet → configure, never create.
            key = ", ".join(missing)
            return Decision(
                verdict="configure",
                reason=f"{top['name']} already exists but needs configuration before it can run.",
                matched=[_match_from(top, _score_of(top["id"]))],
                reused_components=[top["id"]],
                genuine_gap=f"missing config: {key}",
                plan=[f"set {k} (navig config set / vault) for {top['id']}" for k in missing],
                benefit="No new code — configure the capability NAVIG already ships.",
                complexity="low",
                verification=f"run `{_ref(top)}` and confirm it answers",
                assumptions=assumptions,
            )
        return Decision(
            verdict="reuse",
            reason=f"{top['name']} already solves this — no new capability needed.",
            matched=[_match_from(top, _score_of(top["id"]))],
            reused_components=[top["id"]],
            genuine_gap="",
            plan=[f"use `{_ref(top)}` directly"],
            artifact=None,
            benefit="Zero build cost; the capability is already installed and healthy.",
            complexity="none",
            verification=f"run `{_ref(top)}`",
            assumptions=assumptions,
        )

    # 2) CONFIGURE — the capability exists but is unwired/unavailable.
    if unwired_strong:
        top = unwired_strong[0]
        missing = _missing_config(top, cfg)
        gap = f"unwired ({_state(top)})" + (
            f"; missing config: {', '.join(missing)}" if missing else ""
        )
        return Decision(
            verdict="configure",
            reason=f"{top['name']} exists but is not available in the current setup.",
            matched=[_match_from(top, _score_of(top["id"]))],
            reused_components=[top["id"]],
            genuine_gap=gap,
            plan=[f"enable/wire {top['id']}"] + [f"set {k}" for k in missing],
            benefit="Turn on capability NAVIG already ships instead of rebuilding it.",
            complexity="low",
            verification=f"confirm {top['id']} reports state=available, then run `{_ref(top)}`",
            assumptions=assumptions,
        )

    # 3) EXTEND — a matched capability is broken: repair THAT one, nothing else.
    if broken_strong:
        top = broken_strong[0]
        return Decision(
            verdict="extend",
            reason=f"{top['name']} matches the need but is currently broken — repair it in place.",
            matched=[_match_from(top, _score_of(top["id"]))],
            reused_components=[top["id"]],
            genuine_gap=f"{top['id']} reports state=broken",
            plan=[f"diagnose and repair {top['id']} (scoped: no replacement, no unrelated changes)"],
            artifact=None,
            benefit="Restore existing capability; replacing it would discard working code.",
            complexity="medium",
            verification=f"navig doctor / `{_ref(top)}` after the repair",
            assumptions=assumptions,
        )

    # 4) COMPOSE — two or more wired capabilities cover the work; only glue is missing.
    seq_cov = _cover_sequence(ev.sequences, view)
    if len(seq_cov) >= 2:
        ids = [e["id"] for e in seq_cov]
        return Decision(
            verdict="compose",
            reason="Existing capabilities cover the workflow; only the connection between them is missing.",
            matched=[_match_from(e, 9) for e in seq_cov],
            reused_components=ids,
            genuine_gap="workflow glue (no new capability required)",
            plan=[f"wire {' → '.join(ids)} as one flow"] + [f"use `{_ref(e)}`" for e in seq_cov],
            artifact=None,
            benefit="One step instead of N; every component stays an existing, tested capability.",
            complexity="low",
            verification="run the composed flow once end-to-end",
            assumptions=assumptions,
        )

    # 5) CREATE — nothing matches AND the evidence clears the thresholds.
    best_repeat = max((n for _, n in ev.repeated_commands), default=0)
    best_fail = max((n for _, n in ev.failing_commands), default=0)
    best_seq = max((n for _, n in ev.sequences), default=0)
    goal_token_hits = len(goal_hits)
    if goal_token_hits == 0 and not ev.requested and ev.is_empty():
        return Decision(
            verdict="noop",
            reason="No goal match and no evidence — nothing worth proposing.",
            complexity="none",
            assumptions=assumptions,
        )
    if best_repeat >= REPEAT_CMD_MIN or best_fail >= FAIL_CMD_MIN or best_seq >= SEQUENCE_MIN or ev.blocked_goals:
        repeat_cmd = max(ev.repeated_commands, key=lambda row: row[1], default=("", 0))[0]
        failing_cmd = max(ev.failing_commands, key=lambda row: row[1], default=("", 0))[0]
        seq = max(ev.sequences, key=lambda row: row[1], default=((), 0))[0]
        commands = [c for c in (repeat_cmd, failing_cmd, *seq) if c]
        subject = goal_l or repeat_cmd or failing_cmd or " ".join(seq) or "workflow"
        gap = (
            f"repeated {best_repeat}×" if best_repeat >= REPEAT_CMD_MIN else ""
        ) or (f"failing {best_fail}×" if best_fail >= FAIL_CMD_MIN else "") or (
            f"sequence repeated {best_seq}×" if best_seq >= SEQUENCE_MIN else ""
        ) or "blocked goal"
        return Decision(
            verdict="create",
            reason=f"Nothing in the index covers this, and the evidence shows {gap}.",
            matched=[],
            reused_components=[],
            genuine_gap=f"no existing capability matches {subject!r}",
            plan=[
                "build a skill wrapping the proven command sequence",
                "validate with navig skill lint",
                "activate through normal skill activation",
            ],
            artifact=ArtifactSpec(
                kind="skill",
                slug=_slugify(subject),
                title=subject[:60],
                description=f"Automates the repeated workflow: {subject}",
                commands=commands,
            ),
            benefit=f"Cuts a {gap} repetition down to one invoked skill.",
            complexity="low",
            verification="navig skill lint <dir> && navig skill run <slug>",
            assumptions=assumptions,
        )

    # 6) NO-OP — evidence exists but clears nothing; suggesting anyway is noise.
    return Decision(
        verdict="noop",
        reason=(
            "Evidence below thresholds — no proposal is justified."
            if not ev.is_empty()
            else "Nothing verifiable to propose."
        ),
        complexity="none",
        assumptions=assumptions,
    )


def _score(entry: dict, query_l: str) -> int:
    """Score one entry against the goal — delegates to the index's OWN scorer
    so ``find()`` and the ladder can never disagree on what a match is."""
    from navig.creative.index import score_entry, tokenize_query

    return score_entry(entry, tokenize_query(query_l))


def _ref(entry: dict) -> str:
    inv = entry.get("how_to_invoke") or {}
    return str(inv.get("ref") or entry.get("id") or "")


def _cover_sequence(sequences: list[tuple[tuple[str, ...], int]], view: IndexView) -> list[dict]:
    """Distinct index entries covering the most-repeated evidence sequence."""
    if not sequences:
        return []
    seq, _count = max(sequences, key=lambda row: row[1])
    covered: dict[str, dict] = {}
    for cmd in seq:
        entry = view.get(f"command:{cmd}")
        if entry is None:
            # fall back to a ranked search for non-CLI steps
            hits = view.find(cmd, limit=1)
            entry = hits[0] if hits else None
        if entry is not None and _is_wired(entry) and entry["id"] not in covered:
            covered[entry["id"]] = entry
    return list(covered.values())
