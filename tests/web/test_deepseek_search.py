"""Contract tests for the DeepSeek Search runner (``_search_deepseek``).

The endpoint contract was verified live 2026-10-20 (POST /v1/tools/search,
Bearer auth, query 1-128 chars, count 1-50, results with title/url/snippet/
summary/date). These tests pin the mapping and every failure shape with mocked
HTTP — no network, no key material.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from navig.tools.web import _search_deepseek

pytestmark = pytest.mark.integration

ENDPOINT = "https://api.deepseek.com/v1/tools/search"


class _Resp:
    def __init__(self, status: int, payload: Any = None, text: str = "") -> None:
        self.status_code = status
        self._payload = payload
        self.text = text if text else (json.dumps(payload) if payload is not None else "")
        self.headers = {"content-type": "application/json"}

    def json(self) -> Any:
        if self._payload is None:
            raise ValueError("no json body")
        return self._payload


def _capture_post(monkeypatch, response: _Resp | Exception) -> dict:
    capture: dict[str, Any] = {}

    def fake(url, json=None, timeout=None, headers=None, **kwargs):
        capture["url"] = url
        capture["json"] = json
        capture["timeout"] = timeout
        capture["headers"] = headers or {}
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr("navig.tools.web.requests.post", fake)
    return capture


def _results(*items: dict) -> dict:
    return {"results": list(items)}


def test_success_maps_results_with_snippet_summary_and_date(monkeypatch):
    capture = _capture_post(
        monkeypatch,
        _Resp(200, _results(
            {"title": "T1", "url": "https://a.example/x", "snippet": "S1", "date": "2026-10-01T00:00:00+00:00"},
            {"title": "T2", "url": "https://b.example/y", "summary": "SUM2"},
            {"title": "no-url", "snippet": "dropped"},  # url-less entries are dropped
        )),
    )

    result = _search_deepseek("query", "sk-test", count=5)

    assert result.success is True
    assert result.provider == "deepseek_search"
    assert len(result.results) == 2
    assert result.results[0].title == "T1"
    assert result.results[0].snippet == "S1"
    assert result.results[0].age == "2026-10-01T00:00:00+00:00"
    # Falls back to `summary` when snippet is absent.
    assert result.results[1].snippet == "SUM2"
    # Request shape: endpoint, Bearer auth, need_content off.
    assert capture["url"] == ENDPOINT
    assert capture["headers"]["Authorization"] == "Bearer sk-test"
    assert capture["json"]["need_content"] is False
    assert capture["json"]["query"] == "query"
    assert capture["json"]["count"] == 5


def test_count_is_clamped_to_the_documented_range(monkeypatch):
    high = _capture_post(monkeypatch, _Resp(200, _results({"title": "t", "url": "https://u"})))
    _search_deepseek("q", "k", count=99)
    assert high["json"]["count"] == 50

    low = _capture_post(monkeypatch, _Resp(200, _results({"title": "t", "url": "https://u"})))
    _search_deepseek("q", "k", count=0)
    assert low["json"]["count"] == 1


def test_query_is_truncated_to_128_chars(monkeypatch):
    capture = _capture_post(
        monkeypatch, _Resp(200, _results({"title": "t", "url": "https://u"}))
    )

    _search_deepseek("x" * 500, "k", count=3)

    assert len(capture["json"]["query"]) == 128


def test_empty_query_fails_without_a_network_call(monkeypatch):
    capture = _capture_post(monkeypatch, _Resp(200, _results()))

    result = _search_deepseek("   ", "k")

    assert result.success is False
    assert result.provider == "deepseek_search"
    assert "non-empty query" in (result.error or "")
    assert "url" not in capture  # requests.post was never reached


def test_empty_results_is_failure_so_auto_can_cascade(monkeypatch):
    _capture_post(monkeypatch, _Resp(200, {"results": []}))

    result = _search_deepseek("nothing matches this", "k")

    assert result.success is False
    assert "returned no results" in (result.error or "")


def test_401_reports_an_invalid_key(monkeypatch):
    _capture_post(monkeypatch, _Resp(401, {"error": "unauthorized"}))

    result = _search_deepseek("q", "sk-bad")

    assert result.success is False
    assert "invalid or expired" in (result.error or "")


@pytest.mark.parametrize("status", [429, 500, 404])
def test_non_200_reports_status_and_body_truncation(monkeypatch, status):
    _capture_post(monkeypatch, _Resp(status, None, text="x" * 1000))

    result = _search_deepseek("q", "k")

    assert result.success is False
    assert f"DeepSeek Search API error {status}" in (result.error or "")
    assert len(result.error or "") < 700  # body truncated to 500


def test_transport_exception_is_a_failure_not_a_crash(monkeypatch):
    _capture_post(monkeypatch, ConnectionError("network down"))

    result = _search_deepseek("q", "k")

    assert result.success is False
    assert "DeepSeek Search failed" in (result.error or "")
