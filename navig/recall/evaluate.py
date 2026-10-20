"""Extraction quality, measured against the session audit (the gold set already on disk).

For sessions the audit analysed, the audit lists the features and ideas each one originated
(``attrs.sessions`` on the imported records). After extraction, a gold item counts as found
when some record extracted from the same session shares at least half of its title words.
That is deliberately lenient on wording and strict on provenance — the extracted record must
come from the very session the audit attributed the item to.

Also reported, because a high recall with bad provenance would be worthless:

* quote validity — verified quotes ÷ all quotes the model offered;
* attribution errors — records whose status basis is ``user`` with no operator-spoken
  evidence (must be 0);
* unquoted items — items dropped to review for lack of a verifiable quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from navig.recall.extract import ExtractOptions, ExtractReport, LLMCall, run_extraction
from navig.recall.model import USER_SPEAKERS

_W = re.compile(r"\w{3,}", re.UNICODE)
_STOPWORDS = {"the", "and", "for", "with", "from", "into", "that", "this", "not", "via", "per", "all", "new",
              "navig", "a", "an", "of", "to", "in", "on"}


def _words(text: str) -> set[str]:
    return {w for w in _W.findall(text.casefold()) if w not in _STOPWORDS}


@dataclass
class SessionScore:
    source_id: str
    gold: int
    found: int
    missed: list[str] = field(default_factory=list)


@dataclass
class EvalReport:
    sessions: list[SessionScore]
    extraction: ExtractReport
    attribution_errors: int

    @property
    def recall(self) -> float:
        g = sum(s.gold for s in self.sessions)
        return sum(s.found for s in self.sessions) / g if g else 0.0

    @property
    def quote_validity(self) -> float:
        t = self.extraction.valid_quotes + self.extraction.rejected_quotes
        return self.extraction.valid_quotes / t if t else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"recall": round(self.recall, 3), "quote_validity": round(self.quote_validity, 3),
                "attribution_errors": self.attribution_errors,
                "sessions": [s.__dict__ for s in self.sessions], "extraction": self.extraction.to_dict()}


def gold_by_session(store: Any) -> dict[str, list[str]]:
    """``cc:<session>`` → titles of audit features/ideas that session originated."""
    out: dict[str, list[str]] = {}
    for rtype in ("feature", "idea"):
        for rec in store.list_records(rtype=rtype, limit=100_000):
            if "audit" not in rec.tags:
                continue
            for sid in rec.attrs.get("sessions") or []:
                if len(str(sid)) >= 32:
                    out.setdefault(f"cc:{sid}", []).append(rec.title)
    return out


def pick_sessions(store: Any, n: int, *, min_gold: int = 3, project: str | None = None) -> list[str]:
    gold = gold_by_session(store)
    cands = []
    for sid, titles in gold.items():
        if len(titles) < min_gold:
            continue
        src = store.get_source(sid)
        if src and src["turns"] and (not project or src["project"] == project):
            cands.append((src["size"], sid))
    return [sid for _, sid in sorted(cands)[:n]]


def score(store: Any, source_ids: list[str]) -> tuple[list[SessionScore], int]:
    gold = gold_by_session(store)
    rows = store._read_all(  # noqa: SLF001 — read-only scoring query
        "SELECT r.id, r.title, r.summary, r.status_basis, e.source_id, e.speaker FROM records r "
        "JOIN evidence e ON e.record_id = r.id WHERE r.tags_json LIKE '%extracted%'")
    per_source: dict[str, list[set[str]]] = {}
    user_ev: dict[str, bool] = {}
    basis: dict[str, str] = {}
    for rid, title, summary, sb, src, speaker in rows:
        per_source.setdefault(src, []).append(_words(f"{title} {summary}"))
        user_ev[rid] = user_ev.get(rid, False) or speaker in USER_SPEAKERS
        basis[rid] = sb
    attribution_errors = sum(1 for rid, b in basis.items() if b == "user" and not user_ev[rid])
    scores = []
    for sid in source_ids:
        extracted = per_source.get(sid, [])
        found, missed = 0, []
        for title in gold.get(sid, []):
            g = _words(title)
            if g and any(len(g & e) / len(g) >= 0.5 for e in extracted):
                found += 1
            else:
                missed.append(title)
        scores.append(SessionScore(sid, len(gold.get(sid, [])), found, missed[:10]))
    return scores, attribution_errors


def evaluate(ingestor: Any, opts: ExtractOptions, *, sessions: int = 6, llm: LLMCall | None = None,
             project: str | None = None) -> EvalReport:
    ids = pick_sessions(ingestor.store, sessions, project=project)
    opts.source_ids = tuple(ids)
    opts.project = None
    rep = run_extraction(ingestor, opts, llm=llm)
    scores, errors = score(ingestor.store, ids)
    return EvalReport(scores, rep, errors)
