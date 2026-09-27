"""OpenRouter turns are costed from OpenRouter's own published prices.

Every OpenRouter turn used to read $0.00: its ids look like
``anthropic/claude-sonnet-4.5`` and no ``PRICE_TABLE`` entry matches that shape.
Prices now come from OpenRouter's public ``/api/v1/models`` (USD per token),
cached locally and refreshed only by callers that already go online.
"""

from __future__ import annotations

import json

import pytest

from navig.agent import openrouter_prices as orp
from navig.agent.usage_tracker import UsageEvent, _lookup_price

PAYLOAD = {
    "data": [
        {"id": "anthropic/claude-sonnet-4.5",
         "pricing": {"prompt": "0.000003", "completion": "0.000015",
                     "input_cache_read": "0.0000003", "input_cache_write": "0.00000375",
                     "overrides": [{"min_prompt_tokens": 200000, "prompt": "0.000006"}]}},
        {"id": "openai/gpt-oss-20b", "pricing": {"prompt": "0.000000018", "completion": "0.00000009"}},
        {"id": "openrouter/auto", "pricing": {"prompt": "-1", "completion": "-1"}},
        {"id": "broken/no-pricing"},
        "not-a-dict",
    ]
}


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    """Never read or write the operator's real cache."""
    monkeypatch.setenv("NAVIG_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(orp, "_memo", None)
    return tmp_path


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _serve(monkeypatch, payload, status=200):
    import httpx

    calls = []
    monkeypatch.setattr(httpx, "get", lambda url, **kw: (calls.append(url), _Resp(payload, status))[1])
    return calls


# ── parse ──────────────────────────────────────────────────────────────────


def test_prices_are_converted_from_per_token_to_per_million():
    prices = orp.parse(PAYLOAD)

    assert prices["anthropic/claude-sonnet-4.5"] == (3.0, 15.0, 0.3, 3.75)
    assert prices["openai/gpt-oss-20b"] == (0.018, 0.09, 0.0, 0.0)


def test_a_negative_sentinel_is_not_a_price_and_junk_is_skipped():
    prices = orp.parse(PAYLOAD)

    assert prices["openrouter/auto"] == (0.0, 0.0, 0.0, 0.0), "-1 means 'varies', not a refund"
    assert "broken/no-pricing" not in prices
    assert len(prices) == 3


# ── refresh: atomic, and a failure never empties the cache ────────────────


def test_refresh_writes_the_cache_and_lookup_reads_it(monkeypatch):
    calls = _serve(monkeypatch, PAYLOAD)

    assert orp.refresh() == 3
    assert calls == [orp.MODELS_URL]
    assert orp.lookup("anthropic/claude-sonnet-4.5") == (3.0, 15.0, 0.3, 3.75)
    assert orp.lookup("not/listed") is None
    assert orp.age_seconds() is not None


def test_a_failed_fetch_keeps_the_existing_cache(monkeypatch):
    _serve(monkeypatch, PAYLOAD)
    orp.refresh()
    before = orp.cache_path().read_bytes()

    _serve(monkeypatch, {}, status=503)
    with pytest.raises(RuntimeError):
        orp.refresh()

    assert orp.cache_path().read_bytes() == before


def test_an_empty_price_list_never_replaces_a_real_one(monkeypatch):
    """The "failed read became a destructive write" class: an empty response
    must not wipe the prices every future turn depends on."""
    _serve(monkeypatch, PAYLOAD)
    orp.refresh()
    before = orp.cache_path().read_bytes()

    _serve(monkeypatch, {"data": []})
    with pytest.raises(ValueError):
        orp.refresh()

    assert orp.cache_path().read_bytes() == before


def test_no_cache_and_a_corrupt_cache_both_read_as_unpriced():
    assert orp.lookup("anthropic/claude-sonnet-4.5") is None
    orp.cache_path().parent.mkdir(parents=True, exist_ok=True)
    orp.cache_path().write_text("{not json", encoding="utf-8")
    assert orp.lookup("anthropic/claude-sonnet-4.5") is None


def test_a_rewritten_cache_is_re_read(monkeypatch):
    _serve(monkeypatch, PAYLOAD)
    orp.refresh()
    assert orp.lookup("openai/gpt-oss-20b")[0] == 0.018

    raw = json.loads(orp.cache_path().read_text(encoding="utf-8"))
    raw["prices"]["openai/gpt-oss-20b"] = [9, 9, 0, 0]
    import os
    import time

    orp.cache_path().write_text(json.dumps(raw), encoding="utf-8")
    t = time.time() + 5
    os.utime(orp.cache_path(), (t, t))

    assert orp.lookup("openai/gpt-oss-20b")[0] == 9.0


# ── the cost path: the PROVIDER decides, never the id's shape ─────────────


def test_an_openrouter_turn_is_costed_from_openrouters_price(monkeypatch):
    _serve(monkeypatch, PAYLOAD)
    orp.refresh()

    ev = UsageEvent(turn=1, model="anthropic/claude-sonnet-4.5", provider="openrouter",
                    prompt_tokens=1_000_000, completion_tokens=1_000_000)

    assert ev.cost_usd() == pytest.approx(18.0)


def test_the_same_id_on_a_free_host_is_not_billed_at_openrouters_price(monkeypatch):
    """`openai/gpt-oss-20b` is an OpenRouter id AND an NVIDIA and groq id —
    free tiers there. Pricing by the id's shape would bill them."""
    _serve(monkeypatch, PAYLOAD)
    orp.refresh()

    assert _lookup_price("openai/gpt-oss-20b", "openrouter")[:2] == (0.018, 0.09)
    assert _lookup_price("openai/gpt-oss-20b", "nvidia") == (0.0, 0.0, 0.0, 0.0)
    assert _lookup_price("openai/gpt-oss-20b") == (0.0, 0.0, 0.0, 0.0)


def test_an_uncached_openrouter_model_falls_back_and_never_goes_online(monkeypatch):
    """No cache → $0.00 as before. The cost path must not fetch: an agent turn
    must never stall on a price."""
    import httpx

    def _no_network(*_a, **_k):
        raise AssertionError("a cost lookup went online")

    monkeypatch.setattr(httpx, "get", _no_network)

    assert _lookup_price("anthropic/claude-sonnet-4.5", "openrouter") == (0.0, 0.0, 0.0, 0.0)
    assert _lookup_price("gpt-4o", "openrouter")[:2] == (2.50, 10.00), "table still applies"
