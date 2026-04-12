"""
navig.inbox.space_scorer — Score content terms against space routing configs.

Used to answer two questions:
  1. Should this file leave a space?
     → `check_exclude_rules(terms, config)` — returns the first matching ExcludeRule or None.

  2. Which sibling space is the best destination?
     → `find_best_destination(terms, siblings)` — scores each sibling's channel keywords
        and descriptions; returns the inbox path of the highest-scoring sibling.

Scoring is intentionally simple (linear keyword-hit counting) so it is fast,
deterministic, and transparent to users.  The user controls the keyword lists
in routes.yaml; there is no hidden ML model.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from navig.inbox.routes_loader import ExcludeRule, RoutesConfig, load, scan_sibling_spaces

logger = logging.getLogger("navig.inbox.space_scorer")

# Minimum score (0-1) a sibling must reach to be considered a valid destination.
MIN_ATTRACTION_SCORE = 0.05


def check_exclude_rules(
    content_terms: list[str],
    config: RoutesConfig,
) -> ExcludeRule | None:
    """
    Return the first ExcludeRule in `config.exclude` that fires for the given terms.
    Returns None if no rule matches.

    A rule fires when the number of unique keyword hits in `content_terms`
    reaches `rule.min_hits`.
    """
    lower_terms = {t.lower() for t in content_terms}
    for rule in config.exclude:
        hits = _count_hits(lower_terms, rule.keywords)
        if hits >= rule.min_hits:
            logger.debug(
                "Exclude rule triggered: %d/%d keyword hits — keywords=%s",
                hits,
                rule.min_hits,
                rule.keywords[:6],
            )
            return rule
    return None


def score_against_space(content_terms: list[str], config: RoutesConfig) -> float:
    """
    Return a 0–1 attraction score reflecting how well `content_terms` match
    the channels declared in `config`.

    Score = (total keyword hits across all channels) / len(content_terms)
    Capped at 1.0.  Returns 0 if config has no channels or no keywords.
    """
    if not config.channels or not content_terms:
        return 0.0

    lower_terms = {t.lower() for t in content_terms}
    total_hits = 0

    for channel in config.channels:
        # Collect all scoring vocabulary for this channel
        vocab: list[str] = list(channel.keywords)  # explicit keyword list first
        # Also mine words from the description string
        if channel.description:
            vocab.extend(_tokenize(channel.description))

        total_hits += _count_hits(lower_terms, vocab)

    # Normalise: hits per content term (capped at 1)
    raw = total_hits / len(content_terms)
    return min(raw, 1.0)


def find_best_destination(
    content_terms: list[str],
    source_space_root: Path,
    spaces_root: Path,
) -> Path | None:
    """
    Scan all sibling spaces under `spaces_root`, score each against `content_terms`,
    and return the `.navig/inbox/` path of the highest-scoring sibling above
    MIN_ATTRACTION_SCORE.

    Ignores the source space itself (to avoid self-routing).
    Returns None if no suitable destination is found.
    """
    siblings = scan_sibling_spaces(spaces_root)
    best_path: Path | None = None
    best_score = MIN_ATTRACTION_SCORE  # must beat the threshold to qualify

    for sibling_root in siblings:
        # Skip the space the file came from
        try:
            if sibling_root.resolve() == source_space_root.resolve():
                continue
        except ValueError:
            pass  # Windows path comparison on different drives — treat as different

        config = load(sibling_root)
        if config is None:
            # No routes.yaml → no scoring data. Fall back to any sibling that
            # has an inbox dir, with a minimal score.
            inbox_path = sibling_root / ".navig" / "inbox"
            if inbox_path.is_dir() and MIN_ATTRACTION_SCORE < best_score:
                continue  # already have something better
            continue

        score = score_against_space(content_terms, config)
        logger.debug(
            "Space %s scored %.3f for %d terms",
            sibling_root.name,
            score,
            len(content_terms),
        )

        if score > best_score:
            best_score = score
            best_path = sibling_root / ".navig" / "inbox"

    if best_path:
        logger.info("Best destination: %s (score=%.3f)", best_path, best_score)
    else:
        logger.debug("No sibling space reached attraction threshold %.3f", MIN_ATTRACTION_SCORE)

    return best_path


def extract_terms(text: str, max_terms: int = 200) -> list[str]:
    """
    Tokenise raw text into a deduplicated list of lowercase words ≥4 chars.
    Used to turn inbox file content into scoring terms.
    """
    tokens = re.findall(r"[a-zA-Z]{4,}", text)
    seen: set[str] = set()
    result: list[str] = []
    for tok in tokens:
        low = tok.lower()
        if low not in seen:
            seen.add(low)
            result.append(low)
        if len(result) >= max_terms:
            break
    return result


# ── Helpers ───────────────────────────────────────────────────


def _count_hits(term_set: set[str], keywords: list[str]) -> int:
    """Count how many keywords (case-insensitive) appear in `term_set`."""
    count = 0
    for kw in keywords:
        kw_low = kw.lower()
        # Exact token match or substring match for multi-word keywords
        if " " in kw_low:
            # For phrases we check if all constituent words appear in term_set
            parts = kw_low.split()
            if all(p in term_set for p in parts):
                count += 1
        elif kw_low in term_set:
            count += 1
    return count


def _tokenize(text: str) -> list[str]:
    """Split a description/channel name into lowercase words ≥3 chars."""
    return [w.lower() for w in re.findall(r"[a-zA-Z]{3,}", text)]
