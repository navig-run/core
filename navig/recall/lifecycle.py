"""Delivery lifecycle — deterministic derivation of ``Record.lifecycle``. Zero LLM calls.

``status`` answers "was this decision approved?"; ``lifecycle`` answers "did the change
actually reach users?" — separate axes, so neither is repurposed. Rules, in order
(table-tested in ``tests/recall/test_recall_provenance.py``):

1. Explicit outcome events (from a release/deploy record, a changelog import, an
   explicit failure) → ``deployed`` · ``abandoned`` · ``failed`` — **observed**.
2. Supersession (``status=superseded`` or an incoming ``supersedes`` link) →
   ``superseded`` — **observed**.
3. A record claiming the work is built (``status`` implemented/verified) that
   verification contradicts (``impl=not_found``) → ``failed`` — **inferred**.
4. Otherwise the furthest *observed progress*, mirroring the mission's ordering:
   ``verified`` (impl — set only by verify.py or an imported audit) → ``merged``
   (git ancestor of the default branch) → ``committed`` (git sees the sha; **observed**,
   or **inferred** when git cannot confirm the link) → ``implemented_uncommitted``
   (sessions touched files, nothing committed — **inferred**) → ``proposed``
   (proposal statuses — **inferred**) → ``unknown``.

Attribution law enforced here: an assistant claim (``status_basis=assistant``) can never
produce an **observed** lifecycle by itself — every observed verdict cites git or an
event. Merged never implies deployed; implemented never implies verified.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from navig.recall import gitprov

# The mission's lifecycle vocabulary, re-exported for callers that import this module.
from navig.recall.model import (
    LIFECYCLES,  # noqa: F401
    Record,
)

BASIS_OBSERVED = "observed"
BASIS_INFERRED = "inferred"
BASIS_UNKNOWN = "unknown"

#: Outcome events (explicit evidence only — never inferred from status).
_EVENT_LIFECYCLE = {
    "deployed": "deployed", "deploy": "deployed", "release": "deployed", "released": "deployed",
    "abandoned": "abandoned", "abandon": "abandoned",
    "failed": "failed", "failure": "failed",
}
#: Statuses that only ever say "a proposal exists".
_PROPOSAL_STATUSES = frozenset({"proposed", "exploring", "unresolved", "deferred"})
#: Statuses that CLAIM the work exists in the product (for the contradiction rule).
_BUILT_CLAIM_STATUSES = frozenset({"implemented", "verified"})


class GitProbe:
    """Cached git verdicts for one repository: ``(commit_meta, merged)`` per sha.

    ``root=None`` (or a folder git cannot answer for) degrades to all-``None`` verdicts —
    derivation then marks commits *inferred*, never observed, and never fabricates.
    """

    def __init__(self, root: str | Path | None):
        self.root = gitprov.toplevel(root) if root else None
        self.branch = gitprov.default_branch(self.root) if self.root else None
        self._seen: dict[str, tuple[dict[str, Any] | None, bool | None]] = {}

    @property
    def available(self) -> bool:
        return bool(self.root)

    def examine(self, sha: str) -> tuple[dict[str, Any] | None, bool | None]:
        """(metadata or None, merged-into-default or None) — cached per sha."""
        if sha not in self._seen:
            meta = gitprov.commit_meta(self.root, sha) if self.root else None
            merged = None
            if meta is not None:
                if self.branch:
                    merged = gitprov.is_ancestor(self.root, meta["sha"], self.branch)
                # meta exists but is_ancestor failed → merged stays None (cannot confirm)
            self._seen[sha] = (meta, merged)
        return self._seen[sha]

    def rows(self, project: str = "shared") -> list[dict[str, Any]]:
        """Every examined observation as a ``commits``-cache row (metadata only)."""
        out = []
        for sha, (meta, merged) in self._seen.items():
            if meta is None:
                continue
            out.append({**meta, "sha": sha, "project": project, "branch": self.branch or "",
                        "merged": {True: "yes", False: "no"}.get(merged, "unknown"),
                        "git_root": self.root or "", "observed_at": _utc_now()})
        return out


def _utc_now() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def derive(rec: Record, *, commits: list[str], touched: bool, events: list[dict[str, Any]],
           superseded: bool, probe: GitProbe | None) -> tuple[str, str]:
    """Pure derivation: (lifecycle, basis). No I/O except through *probe* (may be None)."""
    # 1. Explicit outcomes — observed, they cite an event or an imported release record.
    for ev in events:
        life = _EVENT_LIFECYCLE.get(str(ev.get("kind") or ""))
        if life:
            return life, BASIS_OBSERVED
    # 2. Supersession.
    if superseded or rec.status == "superseded":
        return "superseded", BASIS_OBSERVED
    # 3. Claim-built-but-verification-says-absent.
    if rec.status in _BUILT_CLAIM_STATUSES and rec.impl == "not_found":
        return "failed", BASIS_INFERRED
    # 4. Furthest observed progress.
    if rec.impl == "verified":
        return "verified", BASIS_OBSERVED
    if commits:
        if probe is None or not probe.available:
            return "committed", BASIS_INFERRED  # a transcript saw the commit; git cannot confirm
        any_seen = False
        for key in commits:
            sha = key[len("commit:"):]
            meta, merged = probe.examine(sha)
            if merged:
                return "merged", BASIS_OBSERVED
            if meta is not None:
                any_seen = True
        return ("committed", BASIS_OBSERVED) if any_seen else ("committed", BASIS_INFERRED)
    if touched:
        return "implemented_uncommitted", BASIS_INFERRED
    if rec.status in _PROPOSAL_STATUSES:
        return "proposed", BASIS_INFERRED
    return "unknown", BASIS_UNKNOWN


def _patch_vault(vault: Any, path: str, lifecycle: str, basis: str) -> bool:
    """Update lifecycle in the VAULT file (read fresh — never from the index — so an
    operator's body edit is preserved). False when the file is missing or unreadable."""
    from navig.recall import vault as vault_mod  # noqa: PLC0415

    p = Path(path)
    try:
        rec = vault_mod.parse(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    rec.lifecycle = lifecycle
    rec.attrs = {**rec.attrs, "lifecycle_basis": basis}
    try:
        vault.write(rec, existing=path)
    except OSError:
        return False
    return True


def refresh(store: Any, *, project: str | None = None, root: str | Path | None = None,
            vault: Any = None, limit: int = 2000) -> dict[str, Any]:
    """Re-derive lifecycle for records (project-scoped, bounded, deterministic, no LLM).

    Reads git through a per-run :class:`GitProbe`, writes changed rows via
    ``store.set_lifecycle`` and mirrors them into the vault so ``reindex`` reproduces
    the same values. Returns ``{"checked", "changed", "git"}``.
    """
    recs = store.list_records(project=project, limit=limit)
    if not recs:
        return {"checked": 0, "changed": 0, "git": "unused"}

    links_map = store.record_session_links([r.id for r in recs])
    probe = GitProbe(root)
    project_of: dict[str, str] = {}
    changed = 0
    for rec in recs:
        sess = links_map.get(rec.id, {})
        for key in sess.get("committed") or []:
            project_of.setdefault(key[len("commit:"):], rec.project)
        superseded = rec.status == "superseded" or any(
            r.get("dir") == "in" and r.get("rel") == "supersedes" for r in store.related(rec.id))
        life, basis = derive(
            rec,
            commits=list(sess.get("committed") or []),
            touched=bool(sess.get("touched")),
            events=store.events_for(rec.id),
            superseded=superseded,
            probe=probe,
        )
        if life != rec.lifecycle or basis != rec.attrs.get("lifecycle_basis"):
            if store.set_lifecycle(rec.id, life, basis):
                changed += 1
            if vault is not None:
                _patch_vault(vault, store.record_path(rec.id), life, basis)
    probe_rows = probe.rows()
    for row in probe_rows:
        row["project"] = project_of.get(row["sha"], row["project"])
    if probe_rows:
        store.upsert_commits(probe_rows)
    return {"checked": len(recs), "changed": changed,
            "git": "ok" if probe.available else "unavailable"}
