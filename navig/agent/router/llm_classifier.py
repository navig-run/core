"""
navig.agent.router.llm_classifier
==================================
LLM-fallback tier classifier for the NAVIG prompt router.

Purpose
-------
The rule-based classifier handles ~70–80 % of incoming prompts with high
confidence. When its ``ClassificationResult.confidence`` drops below a
configurable threshold (default 0.70) this module fires a single,
ultra-cheap LLM inference call to determine the correct ``RequestTier``.

Cache strategy
--------------
Results are stored in a module-level ``dict`` keyed by the SHA-256 hex
digest of the first 500 characters of the prompt. Entries expire after
``CACHE_TTL_SECONDS`` (1 hour). When the store reaches ``CACHE_MAX_ENTRIES``
(1 000) the oldest inserted entry is evicted before the new one is written —
Python 3.7+ dicts preserve insertion order, so ``next(iter(_cache))`` is
always the oldest key.  No external library is required.

Cost profile
------------
* Provider: cheapest available (routed by ``get_ai_client()`` singleton)
* Cost per call:  < $0.00003
* Added latency:  ~300 ms (irrelevant once cached; invisible for non-streaming)
* Tokens generated: ``max_tokens=10``, ``temperature=0``

Integration
-----------
Import and call from the routing layer::

    from navig.agent.router.llm_classifier import (
        classify_by_llm,
        should_use_llm_classifier,
    )

    result = classify_request(prompt)
    if should_use_llm_classifier(result.confidence):
        tier = await classify_by_llm(prompt)
    else:
        tier = result.tier
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------

import datetime
import hashlib
import re

from loguru import logger

from navig.agent.ai_client import get_ai_client
from navig.agent.routing_strategy import RequestTier

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

CACHE_TTL_SECONDS: int = 3_600  # 1 hour
CACHE_MAX_ENTRIES: int = 1_000  # LRU-by-age eviction at this limit
DEFAULT_CONFIDENCE_THRESHOLD: float = 0.7

# Ultra-cheap LLM call: minimal output, zero temperature for maximum determinism.
_CLASSIFIER_MAX_TOKENS: int = 10
_CLASSIFIER_TEMPERATURE: float = 0

# Tier scan priority: longest/rarest first to avoid false matches.
# e.g. "needs COMPLEX reasoning" must not short-circuit to "MEDIUM".
_TIER_SCAN_ORDER: tuple[RequestTier, ...] = (
    "REASONING",
    "AGENTIC",
    "COMPLEX",
    "MEDIUM",
    "SIMPLE",
)

# System prompt sent with every LLM classification call.
_SYSTEM_PROMPT: str = (
    "You are a prompt-complexity classifier.\n"
    "Categories:\n"
    "  SIMPLE    — factual lookup, single-step, no reasoning required\n"
    "  MEDIUM    — moderate complexity, some context, light reasoning\n"
    "  COMPLEX   — multi-step, domain knowledge, analytical depth\n"
    "  REASONING — formal logic, proofs, deep causal or mathematical chains\n"
    "  AGENTIC   — autonomous task execution, tool use, multi-turn planning\n"
    "Respond with ONLY one word from the list above. No punctuation."
)

# ---------------------------------------------------------------------------
# Cache store
# ---------------------------------------------------------------------------

_cache: dict[str, tuple[RequestTier, datetime.datetime]] = {}

# ---------------------------------------------------------------------------
# Internal helpers — cache
# ---------------------------------------------------------------------------


def _get_cache_key(prompt: str) -> str:
    """Return the SHA-256 hex digest of the first 500 characters of *prompt*.

    Truncation bounds the hashing cost and matches the truncation applied
    before the LLM call, so the same key is used throughout.
    """
    return hashlib.sha256(prompt[:500].encode("utf-8", errors="replace")).hexdigest()


def _evict_oldest() -> None:
    """Remove the oldest entry from the cache (insertion-order, dict-native).

    Safe to call on an empty cache — no-op in that case.
    """
    if not _cache:
        return
    oldest_key = next(iter(_cache))
    del _cache[oldest_key]
    logger.debug("llm_classifier: evicted oldest cache entry key={}", oldest_key[:16])


def _read_cache(key: str) -> RequestTier | None:
    """Return the cached tier for *key* if it exists and has not expired.

    Expired entries are left in place and cleaned up lazily (or evicted by
    the max-size check on the next write).  This avoids the overhead of a
    sweep on every read.

    Returns ``None`` on cache miss or TTL expiry.
    """
    entry = _cache.get(key)
    if entry is None:
        return None

    tier, inserted_at = entry
    age = (
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - inserted_at
    ).total_seconds()
    if age > CACHE_TTL_SECONDS:
        logger.debug("llm_classifier: cache TTL expired key={} age_s={:.0f}", key[:16], age)
        return None

    logger.debug("llm_classifier: cache hit key={} tier={}", key[:16], tier)
    return tier


def _write_cache(key: str, tier: RequestTier) -> None:
    """Insert *tier* into the cache under *key*.

    Evicts the oldest entry first when the store is at capacity so the
    insertion always succeeds within the ``CACHE_MAX_ENTRIES`` bound.
    """
    if len(_cache) >= CACHE_MAX_ENTRIES:
        _evict_oldest()
    _cache[key] = (
        tier,
        datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None),
    )
    logger.debug("llm_classifier: cached tier={} key={}", tier, key[:16])


# ---------------------------------------------------------------------------
# Internal helpers — LLM call
# ---------------------------------------------------------------------------


def _parse_tier(raw: str) -> RequestTier | None:
    """Extract a ``RequestTier`` literal from a raw LLM response string.

    Matching is case-insensitive and uses word-boundary anchors so that
    embedded phrases like ``"This is a MEDIUM tier prompt"`` are handled
    correctly.  Tiers are scanned in priority order (rarest first) to avoid
    short-circuiting on a common word that appears inside a rarer tier name.

    Returns ``None`` when no tier can be identified.
    """
    normalised = raw.strip().upper()
    for tier in _TIER_SCAN_ORDER:
        if re.search(rf"\b{tier}\b", normalised):
            return tier
    logger.warning("llm_classifier: _parse_tier could not extract tier from response={!r}", raw)
    return None


async def _call_llm(prompt: str) -> RequestTier:
    """Fire a single ultra-cheap LLM inference call and return a ``RequestTier``.

    Uses the ``get_ai_client()`` singleton so provider selection, credential
    management, and retries are handled by the existing infrastructure.

    The call is capped at ``max_tokens=10`` and ``temperature=0`` to minimise
    cost and maximise determinism.

    Returns ``"MEDIUM"`` on any exception or unparseable response — the router
    must never crash because of this module.
    """
    client = get_ai_client()
    truncated = prompt[:500]
    logger.debug("llm_classifier: calling LLM for tier classification ({}chars)", len(truncated))

    try:
        raw: str = await client.complete(
            truncated,
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=_CLASSIFIER_MAX_TOKENS,
            temperature=_CLASSIFIER_TEMPERATURE,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("llm_classifier: LLM call failed, defaulting to MEDIUM — {}", exc)
        return "MEDIUM"

    tier = _parse_tier(raw)
    if tier is None:
        logger.warning("llm_classifier: parse failure, defaulting to MEDIUM — raw={!r}", raw)
        return "MEDIUM"

    logger.debug("llm_classifier: LLM classified tier={} raw={!r}", tier, raw)
    return tier


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def should_use_llm_classifier(
    confidence: float,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> bool:
    """Return ``True`` when *confidence* is below *threshold*.

    Use this guard before calling :func:`classify_by_llm` to avoid
    unnecessary LLM calls when the rule-based classifier is confident::

        result = classify_request(prompt)
        if should_use_llm_classifier(result.confidence):
            tier = await classify_by_llm(prompt)
        else:
            tier = result.tier

    Args:
        confidence: Float in ``[0.0, 1.0]`` from ``ClassificationResult.confidence``.
        threshold:  Minimum confidence that does *not* require an LLM call.
                    Defaults to ``DEFAULT_CONFIDENCE_THRESHOLD`` (0.70).
    """
    return confidence < threshold


async def classify_by_llm(prompt: str) -> RequestTier:
    """Classify *prompt* into a ``RequestTier`` using a cached LLM inference call.

    Flow::

        1. Compute SHA-256 cache key from prompt[:500].
        2. Return cached tier on hit (subject to 1-hour TTL).
        3. On miss: call LLM, write to cache, return result.
        4. On any unexpected exception: log warning, return ``"MEDIUM"``.

    Concurrent calls with the same prompt before either populates the cache
    will each make an independent LLM call — acceptable given the sub-cent
    cost and the simplicity benefit over introducing async locks.

    Args:
        prompt: The user prompt to classify.  May be empty or whitespace;
                the hash and inference call still proceed normally.

    Returns:
        A ``RequestTier`` literal: ``"SIMPLE"``, ``"MEDIUM"``, ``"COMPLEX"``,
        ``"REASONING"``, or ``"AGENTIC"``.  Never raises.
    """
    try:
        key = _get_cache_key(prompt)

        cached = _read_cache(key)
        if cached is not None:
            return cached

        tier = await _call_llm(prompt)
        _write_cache(key, tier)
        return tier

    except Exception as exc:  # noqa: BLE001 — absolute last-resort guard
        logger.warning(
            "llm_classifier: unexpected error in classify_by_llm, defaulting to MEDIUM — {}",
            exc,
        )
        return "MEDIUM"
