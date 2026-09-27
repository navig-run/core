"""OpenRouter's own per-model prices, cached locally.

OpenRouter bills per token at a price that differs per model and changes when
the upstream vendor's does — 458 models when this was written. Hand-copying
them into ``usage_tracker.PRICE_TABLE`` would rot faster than any table here,
so they are read from OpenRouter's PUBLIC model list
(``https://openrouter.ai/api/v1/models``, no key needed), which reports each
model's price in USD per token.

Two halves, deliberately separate:

* :func:`refresh` does the network call and writes the cache. It is called only
  from places that already go online — ``navig ai models --check`` and the
  heartbeat's daily probe — never from a cost lookup, so an agent turn can never
  stall on a price.
* :func:`lookup` reads the cache (memoised by mtime). No cache → ``None``, and
  the caller falls back to ``PRICE_TABLE`` / $0.00 exactly as before.

Before this, every OpenRouter turn was costed at $0.00: its ids look like
``anthropic/claude-sonnet-4.5`` and no table entry matches that shape.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_NAME = "openrouter_prices.json"

#: (input, output, cache_read, cache_write) per 1M tokens, keyed by model id.
Prices = tuple[float, float, float, float]

_memo: tuple[float, dict[str, Prices]] | None = None
_memo_path: Path | None = None


def cache_path() -> Path:
    from navig.platform import paths

    return paths.cache_dir() / _CACHE_NAME


def _per_million(value: Any) -> float:
    """OpenRouter reports USD per TOKEN as a string; the table is per 1M.

    A negative price is OpenRouter's "varies / router-decided" sentinel
    (``openrouter/auto`` reports -1) — that is not a price, so it reads 0.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return round(v * 1_000_000, 6) if v > 0 else 0.0


def parse(payload: dict[str, Any]) -> dict[str, Prices]:
    """``/api/v1/models`` JSON → ``{id: (in, out, cache_read, cache_write)}``.

    Long-context ``overrides`` are ignored: the table stores one band, the same
    simplification every other entry makes.
    """
    out: dict[str, Prices] = {}
    for model in payload.get("data") or []:
        mid = model.get("id") if isinstance(model, dict) else None
        pricing = model.get("pricing") if isinstance(model, dict) else None
        if not mid or not isinstance(pricing, dict):
            continue
        out[mid] = (
            _per_million(pricing.get("prompt")),
            _per_million(pricing.get("completion")),
            _per_million(pricing.get("input_cache_read")),
            _per_million(pricing.get("input_cache_write")),
        )
    return out


def refresh(*, timeout: float = 20.0) -> int:
    """Fetch OpenRouter's price list and replace the cache atomically.

    Returns the number of priced models written. Raises on a network or parse
    failure — callers decide whether that matters; the existing cache is left
    untouched either way, because a failed fetch must never become an EMPTY
    price list (the "failed read became a destructive write" class).
    """
    import httpx

    resp = httpx.get(MODELS_URL, timeout=timeout)
    resp.raise_for_status()
    prices = parse(resp.json())
    if not prices:
        raise ValueError("OpenRouter returned no priced models — keeping the existing cache")
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"fetched_at": time.time(), "source": MODELS_URL,
                    "prices": {k: list(v) for k, v in prices.items()}}),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    return len(prices)


def _load() -> dict[str, Prices]:
    global _memo, _memo_path
    path = cache_path()
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    if _memo is not None and _memo_path == path and _memo[0] == mtime:
        return _memo[1]
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        prices = {k: tuple(float(x) for x in v)[:4] for k, v in (raw.get("prices") or {}).items()
                  if isinstance(v, list) and len(v) >= 4}
    except (OSError, ValueError, TypeError) as exc:
        logger.debug("openrouter price cache unreadable (%s): %s", path, exc)
        return {}
    _memo, _memo_path = (mtime, prices), path  # type: ignore[assignment]
    return prices  # type: ignore[return-value]


def lookup(model: str) -> Prices | None:
    """The cached price for an OpenRouter model id, or None."""
    return _load().get(model)


def age_seconds() -> float | None:
    """How old the cache is, or None when there is none."""
    try:
        return time.time() - cache_path().stat().st_mtime
    except OSError:
        return None
