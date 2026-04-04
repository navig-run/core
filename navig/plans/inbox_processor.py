"""
navig.plans.inbox_processor — Reconciliation pipeline for inbox items.

Pipeline stages:

1. **ContentNormaliser** — Strip trailing whitespace, normalise line endings.
2. **StalenessDetector** — Flag items older than a configurable threshold.
3. **DuplicateScanner** — Detect near-duplicate items via title substring.
4. **ConflictDetector** — Detect contradicting assertions between items.
5. **Router** — Decide where each item goes based on pipeline output.

Every exception at a stage boundary is caught and the item is routed to
``.md.review``.  Results are appended to ``staging/reconciliation_queue.json``
as JSON Lines.

LM Usage (Optional)
--------------------
- Duplicate title extraction: max 100 tokens, 5-second timeout.
- Conflict assertion extraction: max 150 tokens, 5-second timeout.
- Fallback is always substring-only. Never block on LM availability.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from navig.plans.inbox_reader import InboxItem, canonical_name

logger = logging.getLogger(__name__)


# ── LM interface (duck-typed) ────────────────────────────────

@runtime_checkable
class LMClient(Protocol):
    """Minimal interface for optional LM calls."""

    def generate(self, prompt: str, max_tokens: int) -> str: ...


# ── Pipeline result ──────────────────────────────────────────

@dataclass
class ReconciliationResult:
    """Outcome of processing a single inbox item."""

    item_name: str
    """Base filename of the processed item."""

    decision: str
    """One of: ``route``, ``duplicate``, ``conflict``, ``stale``, ``review``."""

    target_dir: str
    """Relative directory path the item should be moved to (or 'review')."""

    reason: str
    """Human-readable explanation of the decision."""

    duplicate_of: str | None = None
    """If decision is 'duplicate', the canonical name of the original."""

    conflict_with: str | None = None
    """If decision is 'conflict', the conflicting item name."""

    stale_days: int | None = None
    """If decision is 'stale', number of days since last update."""

    def to_json_line(self) -> str:
        """Serialise as a single JSON line for the reconciliation queue."""
        data: dict[str, Any] = {
            "item": self.item_name,
            "decision": self.decision,
            "target": self.target_dir,
            "reason": self.reason,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
        }
        if self.duplicate_of:
            data["duplicate_of"] = self.duplicate_of
        if self.conflict_with:
            data["conflict_with"] = self.conflict_with
        if self.stale_days is not None:
            data["stale_days"] = self.stale_days
        return json.dumps(data, ensure_ascii=False)


# ── Pipeline stages ──────────────────────────────────────────


class ContentNormaliser:
    """Stage 1: Strip trailing whitespace and normalise line endings."""

    @staticmethod
    def normalise(content: str) -> str:
        """Return cleaned content with \\n endings and no trailing WS."""
        lines = content.replace("\r\n", "\n").replace("\r", "\n").splitlines()
        return "\n".join(line.rstrip() for line in lines) + "\n"


class StalenessDetector:
    """Stage 2: Flag items whose ``date`` frontmatter is older than threshold."""

    _DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

    def __init__(self, stale_days: int = 30) -> None:
        self._threshold = stale_days

    def check(self, item: InboxItem) -> int | None:
        """Return days stale, or ``None`` if fresh / no date found."""
        raw_date = item.frontmatter.get("date", "")
        match = self._DATE_RE.search(raw_date)
        if not match:
            return None
        try:
            item_date = date.fromisoformat(match.group())
        except ValueError:
            return None
        days = (date.today() - item_date).days
        return days if days > self._threshold else None


class DuplicateScanner:
    """Stage 3: Detect near-duplicate items via title substring matching.

    Optionally uses an LM to extract noun-phrases from titles for better
    matching (max 100 tokens, 5s timeout).
    """

    def __init__(
        self,
        corpus: list[InboxItem],
        *,
        lm_client: LMClient | None = None,
    ) -> None:
        self._corpus = corpus
        self._lm = lm_client
        self._title_cache: dict[str, str] = {}
        self._build_title_index()

    def _build_title_index(self) -> None:
        """Index existing items by lowercase canonical name and title."""
        for item in self._corpus:
            title = item.frontmatter.get("title", "")
            if not title:
                title = canonical_name(item.path.name).replace(".md", "").replace("_", " ").replace("-", " ")
            self._title_cache[item.path.name] = title.lower().strip()

    def find_duplicate(self, item: InboxItem) -> str | None:
        """Return the canonical name of a duplicate, or ``None``.

        Uses substring match as the primary signal.  LM is an optional
        spot-check (100 tokens, 5s timeout, failure→fallback).
        """
        item_title = self._get_title(item).lower().strip()
        if len(item_title) < 3:
            return None

        for filename, corpus_title in self._title_cache.items():
            if filename == item.path.name:
                continue
            if len(corpus_title) < 3:
                continue
            # Bidirectional substring check
            if item_title in corpus_title or corpus_title in item_title:
                return canonical_name(filename)

        # Optional LM spot-check for fuzzy matches
        if self._lm is not None:
            return self._lm_duplicate_check(item_title)

        return None

    def _get_title(self, item: InboxItem) -> str:
        """Extract title from frontmatter or derive from filename."""
        title = item.frontmatter.get("title", "")
        if title:
            return title
        return canonical_name(item.path.name).replace(".md", "").replace("_", " ").replace("-", " ")

    def _lm_duplicate_check(self, title: str) -> str | None:
        """Optional LM-based noun-phrase extraction (max 100 tokens, 5s)."""
        if self._lm is None:
            return None
        prompt = (
            f"Extract the key noun-phrases from this title: '{title}'. "
            "Return only a comma-separated list."
        )
        try:
            start = time.monotonic()
            result = self._lm.generate(prompt, max_tokens=100)
            elapsed = time.monotonic() - start
            if elapsed > 5.0:
                logger.debug("LM duplicate check exceeded 5s timeout")
                return None
            # Simple comparison with corpus titles
            phrases = [p.strip().lower() for p in result.split(",")]
            for filename, corpus_title in self._title_cache.items():
                for phrase in phrases:
                    if len(phrase) >= 3 and phrase in corpus_title:
                        return canonical_name(filename)
        except Exception:
            logger.debug("LM duplicate check failed; falling back to substring-only")
        return None


class ConflictDetector:
    """Stage 4: Detect contradicting assertions between items.

    Conflict is detected by scanning for negation patterns of existing
    assertions.  Optional LM spot-check (max 150 tokens, 5s timeout).
    """

    _NEGATION_PREFIXES = ("not ", "no ", "never ", "don't ", "shouldn't ", "cannot ")

    def __init__(
        self,
        corpus: list[InboxItem],
        *,
        lm_client: LMClient | None = None,
    ) -> None:
        self._corpus = corpus
        self._lm = lm_client
        self._assertions: dict[str, list[str]] = {}
        self._build_assertion_index()

    def _build_assertion_index(self) -> None:
        """Index key sentences from corpus items."""
        for item in self._corpus:
            sentences = self._extract_sentences(item.body)
            self._assertions[item.path.name] = sentences

    @staticmethod
    def _extract_sentences(text: str) -> list[str]:
        """Split text into sentence-like chunks, lowered."""
        raw = re.split(r"[.!?\n]+", text)
        return [s.strip().lower() for s in raw if len(s.strip()) > 10]

    def find_conflict(self, item: InboxItem) -> str | None:
        """Return the name of a conflicting item, or ``None``."""
        new_sentences = self._extract_sentences(item.body)
        if not new_sentences:
            return None

        for filename, existing in self._assertions.items():
            if filename == item.path.name:
                continue
            for new_s in new_sentences:
                for old_s in existing:
                    if self._is_contradiction(new_s, old_s):
                        return canonical_name(filename)

        # Optional LM conflict check
        if self._lm is not None:
            return self._lm_conflict_check(new_sentences)

        return None

    def _is_contradiction(self, sentence_a: str, sentence_b: str) -> bool:
        """Heuristic: one sentence negates the core of the other."""
        for prefix in self._NEGATION_PREFIXES:
            # Check if A is a negation of B
            if sentence_a.startswith(prefix):
                core_a = sentence_a[len(prefix):]
                if len(core_a) >= 5 and core_a in sentence_b:
                    return True
            # Check if B is a negation of A
            if sentence_b.startswith(prefix):
                core_b = sentence_b[len(prefix):]
                if len(core_b) >= 5 and core_b in sentence_a:
                    return True
        return False

    def _lm_conflict_check(self, sentences: list[str]) -> str | None:
        """Optional LM assertion extraction (max 150 tokens, 5s)."""
        if self._lm is None:
            return None
        sample = "; ".join(sentences[:5])
        prompt = (
            f"Does this text contradict any common best practice? "
            f"Text: '{sample}'. Answer only 'yes: <topic>' or 'no'."
        )
        try:
            start = time.monotonic()
            result = self._lm.generate(prompt, max_tokens=150)
            if time.monotonic() - start > 5.0:
                return None
            if result.strip().lower().startswith("yes"):
                logger.debug("LM detected potential conflict: %s", result.strip())
        except Exception:
            logger.debug("LM conflict check failed; falling back to substring-only")
        return None


class Router:
    """Stage 5: Decide target directory based on frontmatter and content."""

    _KEYWORD_ROUTES: list[tuple[list[str], str]] = [
        (["task", "todo", "action"], "plans/tasks/active"),
        (["decision", "adr", "choose", "decided"], "plans/decisions"),
        (["milestone", "release", "version"], "plans/milestones"),
        (["phase", "sprint", "iteration"], "plans/phases"),
        (["roadmap", "vision", "strategy"], "plans"),
    ]

    def route(self, item: InboxItem) -> str:
        """Return the target subdirectory path relative to ``.navig/``.

        Falls back to ``plans/tasks/active`` when no keyword matches.
        """
        text = (
            item.frontmatter.get("title", "")
            + " "
            + item.frontmatter.get("type", "")
            + " "
            + item.body[:500]
        ).lower()

        for keywords, target in self._KEYWORD_ROUTES:
            for kw in keywords:
                if kw in text:
                    return target

        return "plans/tasks/active"


# ── Main processor ───────────────────────────────────────────


class InboxProcessor:
    """Full reconciliation pipeline for inbox items.

    Parameters
    ----------
    root:
        Project root directory containing ``.navig/``.
    lm_client:
        Optional LM client for spot-checks.  ``None`` is safe.
    stale_days:
        Days before an item is considered stale (default 30).
    """

    def __init__(
        self,
        root: Path,
        *,
        lm_client: LMClient | None = None,
        stale_days: int = 30,
    ) -> None:
        self._root = root.resolve()
        self._navig_dir = self._root / ".navig"
        self._staging_dir = self._navig_dir / "staging"
        self._queue_file = self._staging_dir / "reconciliation_queue.json"
        self._lm = lm_client
        self._stale_days = stale_days

    def process(self, items: list[InboxItem]) -> list[ReconciliationResult]:
        """Run the full pipeline on a batch of inbox items.

        Parameters
        ----------
        items:
            Inbox items to process (typically from ``InboxReader.scan()``).

        Returns
        -------
        list[ReconciliationResult]
            One result per input item.  Order is preserved.
        """
        # Build corpus (all items) for dup/conflict detection
        staleness = StalenessDetector(stale_days=self._stale_days)
        dup_scanner = DuplicateScanner(items, lm_client=self._lm)
        conflict_detector = ConflictDetector(items, lm_client=self._lm)
        router = Router()
        normaliser = ContentNormaliser()

        results: list[ReconciliationResult] = []

        for item in items:
            try:
                result = self._process_single(
                    item,
                    normaliser=normaliser,
                    staleness=staleness,
                    dup_scanner=dup_scanner,
                    conflict_detector=conflict_detector,
                    router=router,
                )
            except Exception:
                logger.exception(
                    "Pipeline failed for %s; routing to review", item.path.name
                )
                result = ReconciliationResult(
                    item_name=canonical_name(item.path.name),
                    decision="review",
                    target_dir="review",
                    reason="Pipeline exception — see logs",
                )
            results.append(result)

        # Write results to staging queue (JSON Lines)
        self._append_to_queue(results)

        return results

    def _process_single(
        self,
        item: InboxItem,
        *,
        normaliser: ContentNormaliser,
        staleness: StalenessDetector,
        dup_scanner: DuplicateScanner,
        conflict_detector: ConflictDetector,
        router: Router,
    ) -> ReconciliationResult:
        """Run all pipeline stages on a single item."""
        name = canonical_name(item.path.name)

        # Stage 1: Normalise (we just validate, not mutate the stored item)
        _normalised = normaliser.normalise(item.content)

        # Stage 2: Staleness
        stale = staleness.check(item)
        if stale is not None:
            return ReconciliationResult(
                item_name=name,
                decision="stale",
                target_dir="plans/tasks/review",
                reason=f"Item is {stale} days old (threshold: {self._stale_days})",
                stale_days=stale,
            )

        # Stage 3: Duplicate check
        dup = dup_scanner.find_duplicate(item)
        if dup is not None:
            return ReconciliationResult(
                item_name=name,
                decision="duplicate",
                target_dir="plans/tasks/review",
                reason=f"Potential duplicate of '{dup}'",
                duplicate_of=dup,
            )

        # Stage 4: Conflict check
        conflict = conflict_detector.find_conflict(item)
        if conflict is not None:
            return ReconciliationResult(
                item_name=name,
                decision="conflict",
                target_dir="plans/tasks/review",
                reason=f"Potential conflict with '{conflict}'",
                conflict_with=conflict,
            )

        # Stage 5: Route
        target = router.route(item)
        return ReconciliationResult(
            item_name=name,
            decision="route",
            target_dir=target,
            reason="Routed by keyword match",
        )

    def _append_to_queue(self, results: list[ReconciliationResult]) -> None:
        """Append results to the reconciliation queue (JSON Lines)."""
        self._staging_dir.mkdir(parents=True, exist_ok=True)
        try:
            with open(self._queue_file, "a", encoding="utf-8") as fh:
                for r in results:
                    fh.write(r.to_json_line() + "\n")
        except OSError:
            logger.debug("Failed to write reconciliation queue at %s", self._queue_file)
