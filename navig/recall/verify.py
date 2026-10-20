"""Implementation verification — a separate, re-runnable step (never part of extraction).

For concept records (idea · plan · architecture · feature from extraction) in a project with a
git repository, look for the record's identifiers in the code:

* identifiers = backticked tokens, snake/kebab/CamelCase words and dotted names in the
  title/aliases (≥ 5 chars, not common words);
* ``git grep -l -F`` per identifier (bounded), split into code and test hits.

Outcome (``impl``): code + test hits → ``likely``; code only → ``partial``; nothing found for
≥ 2 searchable identifiers → ``not_found``; otherwise ``unknown``. ``verified`` is never set
here — absence of evidence is not evidence of absence, and presence of a name is not proof
of behaviour. Audit-imported records keep their audit verdict.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.recall.model import Record

_IDENT = re.compile(r"`([^`]{4,80})`|\b([a-z]+(?:[_-][a-z0-9]+)+|[A-Z][a-z]+(?:[A-Z][a-z0-9]+)+|[a-z]+\.[a-z_.]+)\b")
_STOP = {"navig", "recall", "should", "would", "could", "about", "their", "there", "which", "where"}
_MAX_IDENTS = 4
_TIMEOUT = 20


@dataclass
class VerifyResult:
    impl: str
    identifiers: list[str]
    code_hits: list[str] = field(default_factory=list)
    test_hits: list[str] = field(default_factory=list)


def identifiers(rec: Record) -> list[str]:
    out: list[str] = []
    for text in [rec.title, *rec.aliases]:
        for m in _IDENT.finditer(text):
            tok = (m.group(1) or m.group(2) or "").strip()
            if len(tok) >= 5 and tok.casefold() not in _STOP and tok not in out:
                out.append(tok)
    return out[:_MAX_IDENTS]


def _grep(root: Path, token: str) -> list[str]:
    try:
        cp = subprocess.run(["git", "-C", str(root), "grep", "-l", "-F", "-I", "--", token],
                            capture_output=True, text=True, timeout=_TIMEOUT, encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [ln for ln in cp.stdout.splitlines() if ln.strip()][:50] if cp.returncode == 0 else []


def verify_record(rec: Record, root: Path) -> VerifyResult:
    idents = identifiers(rec)
    res = VerifyResult(impl="unknown", identifiers=idents)
    for tok in idents:
        for path in _grep(root, tok):
            low = path.replace("\\", "/").casefold()
            if low.endswith((".md", ".txt", ".json", ".jsonl")) or "/.navig/" in f"/{low}" or "changelog" in low:
                continue  # docs and plans repeat names; they prove nothing about code
            bucket = res.test_hits if ("/tests/" in f"/{low}" or "/test_" in f"/{low}" or ".test." in low) else res.code_hits
            if path not in bucket:
                bucket.append(path)
    if res.code_hits and res.test_hits:
        res.impl = "likely"
    elif res.code_hits:
        res.impl = "partial"
    elif len(idents) >= 2:
        res.impl = "not_found"
    return res


def verify_project(ingestor: Any, project: str, root: Path, *, limit: int = 500) -> dict[str, int]:
    """Verify concept records of *project* against the repo at *root*; writes impl + an event."""
    from navig.recall.vault import make_event  # noqa: PLC0415
    from navig.store.base import _utcnow  # noqa: PLC0415

    store = ingestor.store
    counts: dict[str, int] = {}
    events = []
    for rtype in ("idea", "plan", "architecture", "feature"):
        for rec in store.list_records(rtype=rtype, project=project, limit=limit):
            if "audit" in rec.tags:
                continue
            full = store.get_record(rec.id)
            if full is None:
                continue
            res = verify_record(full, root)
            counts[res.impl] = counts.get(res.impl, 0) + 1
            if res.impl == full.impl:
                continue
            events.append(make_event(full.id, "verified", _utcnow(), {
                "from": full.impl, "to": res.impl, "identifiers": res.identifiers,
                "code": res.code_hits[:5], "tests": res.test_hits[:5]}))
            full.impl = res.impl
            full.attrs["verify"] = {"identifiers": res.identifiers, "code": res.code_hits[:10],
                                    "tests": res.test_hits[:10]}
            path = ingestor.vault.write(full, existing=store.record_path(full.id))
            store.upsert_record(full, path=str(path))
    ingestor.vault.append_events(store.add_events(events))
    return counts
