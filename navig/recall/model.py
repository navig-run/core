"""Recall data model — turns (what was said, by whom) and records (what it means).

Six separate dimensions keep "who said it" apart from "is it true":

* ``Evidence.speaker`` — who produced the quoted text (parser-assigned, deterministic)
* ``Record.endorsement`` — did the *user* endorse or reject it (user evidence only)
* ``Record.status`` + ``Record.status_basis`` — lifecycle state and which kind of evidence
  set it (``user`` · ``audit`` · ``assistant`` · ``verify``)
* ``Record.impl`` — implementation state, set only by verification or an imported audit
* ``Record.confidence`` — how sure the extraction is that the record says what the
  source says
"""

from __future__ import annotations

import base64
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

RECORD_TYPES = (
    "idea", "decision", "term", "plan", "table", "question",
    "finding", "risk", "architecture", "lore", "feature",
    "task", "snippet",  # from the chat library: open/done work items, code the person kept
)
STATUSES = (
    "proposed", "exploring", "confirmed", "approved", "rejected", "deferred",
    "superseded", "unresolved", "implemented", "verified",
)
STATUS_BASES = ("user", "audit", "assistant", "verify")
ENDORSEMENTS = ("none", "user_endorsed", "user_rejected")
IMPL_STATES = (
    "verified", "likely", "partial", "discussed_unverified", "not_found", "superseded", "unknown",
)
# Delivery lifecycle — a SEPARATE axis from status (decision approval) and impl (verification).
# A record can be an approved decision whose lifecycle is still proposed, or a confirmed idea
# that was merged but never deployed. Derived deterministically (recall/lifecycle.py); `unknown`
# until observed. Merged ≠ deployed, implemented ≠ verified, agent-claimed ≠ observed.
LIFECYCLES = (
    "proposed", "implemented_uncommitted", "committed", "merged", "verified", "deployed",
    "failed", "superseded", "abandoned", "unknown",
)
SPEAKERS = (
    "user_typed",         # what the operator actually typed
    "user_answer",        # an answer to a structured question (AskUserQuestion)
    "user_plan_verdict",  # the operator approving / declining a proposed plan
    "user_pasted",        # material the operator pasted (theirs to share, not their decision)
    "ide_selection",      # editor selection attached to a message
    "assistant",          # the model's visible reply
    "agent_report",       # a subagent / background-agent report
    "web_capture",        # content captured from a web page
    "prior_audit",        # derived analysis from an earlier session audit
    "imported_doc",       # a parsed document (legacy changelog migration), not a conversation
)
USER_SPEAKERS = frozenset({"user_typed", "user_answer", "user_plan_verdict"})


@dataclass
class Turn:
    """One attributable unit of a conversation. ``msg_id`` is unique within its source."""

    msg_id: str
    speaker: str
    text: str
    ts: str = ""
    kind: str = "text"  # text · plan · answer · verdict · report · doc
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"msg_id": self.msg_id, "speaker": self.speaker, "text": self.text,
                "ts": self.ts, "kind": self.kind, "meta": self.meta}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Turn:
        return cls(msg_id=str(d["msg_id"]), speaker=str(d["speaker"]), text=str(d.get("text", "")),
                   ts=str(d.get("ts", "")), kind=str(d.get("kind", "text")),
                   meta=dict(d.get("meta") or {}))


@dataclass
class Evidence:
    source_id: str
    msg_id: str
    speaker: str
    excerpt: str
    ts: str = ""
    # Exact pointer into a file-backed source: JSON {"lines": [start, end], "file_sha256"}.
    # Empty for transcript evidence (the archive segment is the pointer there).
    locator: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"source_id": self.source_id, "msg_id": self.msg_id, "speaker": self.speaker,
                "excerpt": self.excerpt, "ts": self.ts, "locator": self.locator}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Evidence:
        return cls(source_id=str(d.get("source_id", "")), msg_id=str(d.get("msg_id", "")),
                   speaker=str(d.get("speaker", "")), excerpt=str(d.get("excerpt", "")),
                   ts=str(d.get("ts", "")), locator=str(d.get("locator", "")))


@dataclass
class Record:
    id: str
    type: str
    title: str
    summary: str = ""
    body: str = ""
    project: str = "shared"
    status: str = "proposed"
    status_basis: str = "assistant"
    endorsement: str = "none"
    impl: str = "unknown"
    confidence: float = 0.5
    canonical_key: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    created: str = ""
    updated: str = ""
    ext_id: str = ""
    attrs: dict[str, Any] = field(default_factory=dict)
    evidence: list[Evidence] = field(default_factory=list)
    # Delivery lifecycle (LIFECYCLES). Last field on purpose: additive, so every existing
    # positional construction keeps its meaning. Basis ("observed"/"inferred"/"unknown")
    # lives in attrs["lifecycle_basis"] so it round-trips through the vault with attrs.
    lifecycle: str = "unknown"


_KEY_RE = re.compile(r"[^\w]+", re.UNICODE)


def canonical_key(text: str) -> str:
    """Lowercase, punctuation-free key used for exact-match reconciliation."""
    return _KEY_RE.sub(" ", text.casefold()).strip()[:160]


def record_id(*parts: str) -> str:
    """Stable ``rk-`` id derived from the evidence key, so re-ingest never mints a new id."""
    digest = hashlib.sha1("\x1f".join(parts).encode("utf-8")).digest()
    return "rk-" + base64.b32encode(digest).decode("ascii").lower().rstrip("=")[:12]


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
