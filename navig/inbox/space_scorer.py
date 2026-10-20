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
    *,
    text: str = "",
) -> Path | None:
    """
    Scan all sibling spaces under `spaces_root`, score each against `content_terms`,
    and return the `.navig/inbox/` path of the highest-scoring sibling above
    MIN_ATTRACTION_SCORE.

    Ignores the source space itself (to avoid self-routing).
    Returns None if no suitable destination is found.

    When *text* is given and the decision model is switched on for ``inbox_space``
    (`navig decider on`), the model picks among the same siblings — or "none" — first,
    using each space's channel names and descriptions; keyword scoring remains the
    fallback whenever it is off, unsure or unavailable.
    """
    candidates: list[tuple[Path, RoutesConfig]] = []
    for sibling_root in scan_sibling_spaces(spaces_root):
        # Skip the space the file came from
        try:
            if sibling_root.resolve() == source_space_root.resolve():
                continue
        except ValueError:
            pass  # Windows path comparison on different drives — treat as different

        config = load(sibling_root)
        if config is None:
            continue  # no routes.yaml → no scoring data, never a destination
        candidates.append((sibling_root, config))

    if text and candidates:
        decided = _decide_destination(text, candidates)
        if decided is not None:
            return decided or None  # "" = the model is sure no sibling fits

    best_path: Path | None = None
    best_score = MIN_ATTRACTION_SCORE  # must beat the threshold to qualify

    for sibling_root, config in candidates:
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


#: The decision-model option meaning "no sibling space fits".
_NONE_OPTION = "none"


def _describe_space(root: Path, config: RoutesConfig) -> str:
    parts = []
    for ch in config.channels[:8]:
        bit = ch.name or ch.id
        if ch.description:
            bit += f" — {ch.description}"
        elif ch.keywords:
            bit += f" ({', '.join(ch.keywords[:8])})"
        parts.append(bit)
    return f"space '{root.name}': " + ("; ".join(parts) if parts else "no channels described")


def _decide_destination(text: str, candidates: list[tuple[Path, RoutesConfig]]) -> Path | str | None:
    """Ask the decision model which sibling fits.

    Returns the sibling's inbox path, ``""`` when the model is confident none fits, or
    ``None`` to fall back to keyword scoring (off, unsure, unavailable, or an option
    name that cannot be told apart).
    """
    from navig.agent import decide

    if not decide.is_active("inbox_space"):
        return None
    by_option: dict[str, Path] = {}
    options: dict[str, str] = {}
    for root, config in candidates:
        name = root.name
        if name == _NONE_OPTION or name in by_option:
            return None  # ambiguous option ids — let the keyword scorer decide
        by_option[name] = root
        options[name] = _describe_space(root, config)
    options[_NONE_OPTION] = "none of these spaces fits this document; leave it where it is"
    answers = decide.decide(
        text,
        [decide.choice("space", "Which space does this document belong in?", options)],
        use="inbox_space",
    )
    if not answers or "space" not in answers:
        return None
    pick = answers["space"].answer
    if pick == _NONE_OPTION:
        logger.info("Decision model: no sibling space fits (%.0f%% sure)", answers["space"].confidence * 100)
        return ""
    root = by_option.get(pick)
    if root is None:
        return None
    logger.info(
        "Best destination (decision model): %s (%.0f%% sure)",
        root,
        answers["space"].confidence * 100,
    )
    return root / ".navig" / "inbox"


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
