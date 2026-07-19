"""
Offline CI guard for LLM mode routing — no network, no credentials, runs in the
normal `python -m pytest tests/` step.

It can't detect a provider retiring a model live (that needs `navig mode doctor`),
but it DOES fail the build if a shipped DEFAULT mode ever points at a model we've
already seen retired — the regression that let `nvidia:qwen3-coder-480b` (HTTP 410)
become the coding default and break every coding run.
"""

from __future__ import annotations

from navig.llm.liveness import RETIRED_MODELS, is_retired
from navig.llm.router import CANONICAL_MODES, LLMModeRouter


def _default_modes():
    # Empty config → the SHIPPED code defaults (what a fresh install routes to).
    return LLMModeRouter(config={}).modes


def test_no_default_mode_uses_a_retired_model():
    modes = _default_modes()
    for name in CANONICAL_MODES:
        cfg = modes.get_mode(name)
        assert cfg is not None, f"missing default mode: {name}"
        assert cfg.provider, f"mode {name} has empty provider"
        assert cfg.model, f"mode {name} has empty model"
        assert not is_retired(cfg.model), (
            f"default mode '{name}' points at RETIRED model '{cfg.model}' — "
            f"repoint it in navig/llm/router.py:LLMModesConfig"
        )
        if cfg.fallback_model:
            assert not is_retired(cfg.fallback_model), (
                f"default mode '{name}' fallback '{cfg.fallback_model}' is RETIRED"
            )


def test_is_retired_matches_known_and_ignores_live():
    assert is_retired("qwen/qwen3-coder-480b-a35b-instruct")
    assert is_retired("deepseek-ai/deepseek-r1")
    assert is_retired("qwen/qwen2.5-coder-32b-instruct")
    # live models must not be flagged
    assert not is_retired("meta/llama-3.1-70b-instruct")
    assert not is_retired("claude-opus-4-8")
    assert not is_retired("gpt-4o")
    assert not is_retired(None)
    assert not is_retired("")


def test_denylist_is_populated():
    # Guard against an accidental empty denylist silently disabling the check.
    assert len(RETIRED_MODELS) >= 3


def test_classify_probe_error():
    from navig.llm.liveness import classify_probe_error as c

    assert c(Exception("Client error '410 Gone' for url ..."))[0] == "dead"
    assert c(Exception("model reached its end of life"))[0] == "dead"
    assert c(Exception("Client error '404 Not Found'"))[0] == "dead"
    assert c(Exception("401 Unauthorized"))[0] == "auth"
    assert c(Exception("No credential for provider 'x'"))[0] == "nokey"
    assert c(Exception("The read operation timed out"))[0] == "unreachable"
    assert c(Exception("weird boom"))[0] == "error"


def test_dead_modes_filter():
    from navig.llm.liveness import dead_modes

    rows = [
        {"mode": "a", "status": "live"},
        {"mode": "b", "status": "dead"},
        {"mode": "c", "status": "auth"},
        {"mode": "d", "status": "unreachable"},
        {"mode": "e", "status": "nokey"},   # not-configured is not a break
    ]
    got = {r["mode"] for r in dead_modes(rows)}
    assert got == {"b", "c", "d"}
