"""Tests for the adversarial verifier (provable-trust gate)."""

from __future__ import annotations

import navig.llm_generate as L
import pytest

from navig.agent.verifier import AdversarialVerifier, VerifierConfig, _parse_verdict


@pytest.fixture
def stub_llm(monkeypatch):
    """Patch llm_generate to return a fixed verdict string."""
    def _set(text: str):
        monkeypatch.setattr(L, "llm_generate", lambda messages, **kw: text)
    return _set


class TestParseVerdict:
    def test_strict_json(self):
        v = _parse_verdict('{"safe": false, "confidence": 0.9, "reason": "rm -rf"}', "s")
        assert v and v.safe is False and v.confidence == 0.9

    def test_keyword_fallback_unsafe(self):
        v = _parse_verdict("This is clearly unsafe and destructive", "s")
        assert v and v.safe is False

    def test_unparseable_returns_none(self):
        assert _parse_verdict("", "s") is None


class TestVerdictBehaviour:
    @pytest.mark.asyncio
    async def test_unsafe_blocks(self, stub_llm):
        stub_llm('{"safe": false, "confidence": 0.9, "reason": "prod delete"}')
        v = AdversarialVerifier(VerifierConfig(enabled=True))
        verdict = await v.verify_tool_call("bash_exec", {"cmd": "rm -rf /"})
        assert verdict.safe is False

    @pytest.mark.asyncio
    async def test_safe_passes(self, stub_llm):
        stub_llm('{"safe": true, "confidence": 0.95, "reason": "ok"}')
        v = AdversarialVerifier(VerifierConfig(enabled=True, confidence_threshold=0.7))
        verdict = await v.verify_tool_call("write_file", {"path": "/tmp/a"})
        assert verdict.safe is True

    @pytest.mark.asyncio
    async def test_low_confidence_safe_is_blocked(self, stub_llm):
        stub_llm('{"safe": true, "confidence": 0.3, "reason": "unsure"}')
        v = AdversarialVerifier(VerifierConfig(enabled=True, confidence_threshold=0.7))
        verdict = await v.verify_tool_call("write_file", {})
        assert verdict.safe is False  # adversarial bias on low confidence

    @pytest.mark.asyncio
    async def test_error_fail_closed_by_default(self, monkeypatch):
        def _boom(messages, **kw):
            raise RuntimeError("provider down")

        monkeypatch.setattr(L, "llm_generate", _boom)
        v = AdversarialVerifier(VerifierConfig(enabled=True, fail_open=False))
        verdict = await v.verify_mission(type("M", (), {"title": "t", "capability": "c"})())
        assert verdict.safe is False

    @pytest.mark.asyncio
    async def test_error_fail_open_when_configured(self, monkeypatch):
        monkeypatch.setattr(L, "llm_generate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        v = AdversarialVerifier(VerifierConfig(enabled=True, fail_open=True))
        verdict = await v.verify_tool_call("write_file", {})
        assert verdict.safe is True

    def test_disabled_is_reported(self):
        assert AdversarialVerifier(VerifierConfig(enabled=False)).enabled is False
