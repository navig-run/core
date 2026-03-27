"""
navig.inbox.classifier — LLM-based inbox classifier with BM25 keyword fallback.

Classifies inbox items into routing categories:
  wiki/knowledge    → .navig/wiki/knowledge/
  wiki/technical    → .navig/wiki/technical/
  hub/tasks         → .navig/wiki/hub/tasks/
  hub/roadmap       → .navig/wiki/hub/roadmap/
  hub/changelog     → .navig/wiki/hub/changelog/
  external/business → .navig/wiki/external/business/
  external/marketing→ .navig/wiki/external/marketing/
  archive           → .navig/wiki/archive/
  ignore            → not routed

BM25 keyword fallback requires no dependencies and is used when
offline or when LLM is not configured.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# ── Category keyword map ─────────────────────────────────────

_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "wiki/technical": [
        "api",
        "endpoint",
        "function",
        "class",
        "method",
        "algorithm",
        "architecture",
        "design",
        "database",
        "schema",
        "migration",
        "docker",
        "nginx",
        "deployment",
        "server",
        "service",
        "protocol",
        "troubleshoot",
        "debug",
        "error",
        "fix",
        "patch",
        "issue",
        "decision",
        "adr",
        "why",
        "trade-off",
        "refactor",
    ],
    "wiki/knowledge": [
        "concept",
        "definition",
        "guide",
        "tutorial",
        "how to",
        "howto",
        "explanation",
        "overview",
        "introduction",
        "primer",
        "domain",
        "terminology",
        "glossary",
        "example",
        "resource",
        "reference",
        "link",
        "research",
    ],
    "hub/tasks": [
        "task",
        "todo",
        "to-do",
        "in progress",
        "done",
        "blocked",
        "sprint",
        "backlog",
        "next action",
        "follow up",
        "followup",
        "priority",
        "assign",
        "ticket",
    ],
    "hub/roadmap": [
        "roadmap",
        "milestone",
        "release",
        "version",
        "plan",
        "q1",
        "q2",
        "q3",
        "q4",
        "2025",
        "2026",
        "2027",
        "phase",
        "objective",
        "goal",
        "strategy",
    ],
    "hub/changelog": [
        "changelog",
        "release notes",
        "released",
        "shipped",
        "v0.",
        "v1.",
        "v2.",
        "fixed",
        "added",
        "changed",
        "deprecated",
        "removed",
        "security",
        "breaking change",
    ],
    "external/business": [
        "investor",
        "pitch",
        "roi",
        "revenue",
        "growth",
        "market",
        "acquisition",
        "valuation",
        "equity",
        "fundraise",
        "deck",
        "executive",
        "stakeholder",
    ],
    "external/marketing": [
        "campaign",
        "social media",
        "twitter",
        "linkedin",
        "newsletter",
        "copywriting",
        "brand",
        "landing page",
        "seo",
        "content calendar",
        "press",
        "announcement",
        "launch",
    ],
    "archive": [
        "old",
        "outdated",
        "deprecated",
        "obsolete",
        "legacy",
        "archive",
        "historical",
        "past version",
    ],
}


# ── BM25 implementation ───────────────────────────────────────


class _BM25:
    """Minimal BM25 scorer over category keyword documents."""

    K1 = 1.5
    B = 0.75

    def __init__(self, corpus: dict[str, list[str]]) -> None:
        self.labels: list[str] = list(corpus.keys())
        self.docs: list[list[str]] = [v for v in corpus.values()]

        # Build IDF and freq maps
        N = len(self.docs)
        df: dict[str, int] = {}
        for doc in self.docs:
            for term in set(doc):
                df[term] = df.get(term, 0) + 1

        self.idf: dict[str, float] = {}
        for term, freq in df.items():
            self.idf[term] = math.log((N - freq + 0.5) / (freq + 0.5) + 1)

        avdl = sum(len(d) for d in self.docs) / N if N else 1.0
        self.avdl = avdl

    def score(self, query_terms: list[str], doc_idx: int) -> float:
        doc = self.docs[doc_idx]
        dl = len(doc)
        tf_map: dict[str, int] = {}
        for t in doc:
            tf_map[t] = tf_map.get(t, 0) + 1

        score = 0.0
        for term in set(query_terms):
            if term not in self.idf:
                continue
            tf = tf_map.get(term, 0)
            norm_tf = (tf * (self.K1 + 1)) / (tf + self.K1 * (1 - self.B + self.B * dl / self.avdl))
            score += self.idf[term] * norm_tf
        return score

    def rank(self, query_terms: list[str]) -> list[tuple[str, float]]:
        scored = [(self.labels[i], self.score(query_terms, i)) for i in range(len(self.labels))]
        scored.sort(key=lambda x: x[1], reverse=True)
        return scored


_SCORER = _BM25(_CATEGORY_KEYWORDS)


# ── Result dataclass ─────────────────────────────────────────


@dataclass
class ClassifyResult:
    category: str
    confidence: float  # 0.0 – 1.0
    alternatives: list[tuple[str, float]] = field(default_factory=list)
    method: str = "bm25"  # "bm25" | "llm"
    explanation: str = ""


# ── Classifier ───────────────────────────────────────────────


class Classifier:
    """
    Classify an inbox item into a routing category.

    Args:
        use_llm: Attempt LLM classification first; fall back to BM25 on
                 any failure (no LLM configured, offline, timeout).
        llm_timeout: Seconds to wait for LLM response before falling back.
    """

    def __init__(self, use_llm: bool = False, llm_timeout: float = 8.0) -> None:
        self.use_llm = use_llm
        self.llm_timeout = llm_timeout

    def classify(
        self,
        content: str,
        filename: str = "",
        extra_context: str | None = None,
    ) -> ClassifyResult:
        """
        Classify text content (or filename) into a routing category.

        Parameters
        ----------
        content:
            Raw text content of the file.  May be empty for URL items.
        filename:
            Original filename — used as additional signal.
        extra_context:
            Optional extra metadata string (e.g. URL domain).
        """
        if self.use_llm:
            try:
                return self._classify_llm(content, filename, extra_context)
            except Exception:
                pass  # fall through to BM25
        return self._classify_bm25(content, filename)

    # ── BM25 path ─────────────────────────────────────────

    def _classify_bm25(self, content: str, filename: str) -> ClassifyResult:
        combined = f"{filename} {content}".lower()
        tokens = re.findall(r"[a-z0-9'/-]+", combined)
        # Expand multi-word keyword matches
        bigrams = [tokens[i] + " " + tokens[i + 1] for i in range(len(tokens) - 1)]
        all_terms = tokens + bigrams

        ranked = _SCORER.rank(all_terms)
        if not ranked:
            return ClassifyResult(
                category="archive",
                confidence=0.0,
                method="bm25",
                explanation="No signal found; defaulting to archive",
            )

        top_label, top_score = ranked[0]
        total = sum(abs(s) for _, s in ranked)
        confidence = min((top_score / total) if total > 0 else 0.0, 1.0)

        alts = [(label, round(score, 4)) for label, score in ranked[1:4]]
        return ClassifyResult(
            category=top_label if top_score > 0.01 else "archive",
            confidence=round(confidence, 4),
            alternatives=alts,
            method="bm25",
            explanation=f"BM25 top score={top_score:.3f}",
        )

    # ── LLM path ──────────────────────────────────────────

    def _classify_llm(
        self,
        content: str,
        filename: str,
        extra_context: str | None,
    ) -> ClassifyResult:
        from navig.llm_generate import generate_text

        categories = list(_CATEGORY_KEYWORDS.keys()) + ["ignore"]
        categories_fmt = "\n".join(f"  - {c}" for c in categories)
        snippet = (content[:1200] + "…") if len(content) > 1200 else content
        extra = f"\nExtra context: {extra_context}" if extra_context else ""

        prompt = (
            f"You are a document classifier for the NAVIG inbox router.\n"
            f"Classify the following document into exactly ONE of these categories:\n"
            f"{categories_fmt}\n\n"
            f"Respond ONLY with a JSON object: "
            f'{{"category": "<category>", "confidence": 0.0-1.0, "explanation": "<short>"}}\n\n'
            f"Filename: {filename}{extra}\n\nContent:\n{snippet}"
        )

        import json as _json

        text = generate_text(prompt, timeout=self.llm_timeout)
        # Extract JSON from response
        match = re.search(r"\{[^{}]+\}", text, re.DOTALL)
        if not match:
            raise ValueError("LLM response did not contain JSON")

        data = _json.loads(match.group())
        cat = data.get("category", "archive")
        if cat not in categories:
            cat = "archive"
        return ClassifyResult(
            category=cat,
            confidence=float(data.get("confidence", 0.5)),
            method="llm",
            explanation=data.get("explanation", ""),
        )
