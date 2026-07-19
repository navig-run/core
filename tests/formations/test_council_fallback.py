"""Council provider fallback (credential-class failures only).

When a routed provider fails with a credential/config-class error
(``nokey``/``unreachable`` per ``navig.llm.liveness.classify_probe_error``),
the engine retries that call ONCE via the default-provider route and marks
the outcome honestly:

* per-agent calls — ``provider_fallback`` + ``fallback_provider`` on the
  response, mirrored onto the ``agent_response`` event;
* the final synthesis call — ``synthesis_fallback`` +
  ``synthesis_fallback_provider`` on the run result.

Non-credential errors keep the original behavior: the call fails, the run
continues. A fallback that also fails preserves the ORIGINAL error. A failed
synthesis never presents its error string as the council's decision: the
result carries a distinct ``synthesis_error`` field, ``final_decision``
becomes an operator-facing sentence, and the ``done`` event is badged with
``synthesis_error: true``.
"""

from __future__ import annotations

import json
import threading

import navig.formations.council as council_mod
from navig.formations.council import run_council
from navig.formations.types import AgentSpec, Formation

_SECRET = "SECRET-SOUL-PROMPT-NEVER-ON-THE-WIRE"

_NOKEY_MSG = (
    "No credential configured for provider 'anthropic'. "
    "Connect one with `navig connect`, set the env var, or run: navig vault set anthropic"
)


def _agent(agent_id: str, name: str, role: str) -> AgentSpec:
    return AgentSpec(
        id=agent_id,
        name=name,
        role=role,
        traits=["focused"],
        personality="direct",
        scope=[role.lower()],
        system_prompt=f"{_SECRET} You are {name}, the {role}. " + "x" * 100,
    )


def _formation() -> Formation:
    agents = {
        "alpha": _agent("alpha", "Alpha", "Architect"),
        "beta": _agent("beta", "Beta", "Operator"),
    }
    return Formation(
        id="squad",
        name="Squad",
        version="1.0.0",
        description="fixture",
        agents=["alpha", "beta"],
        default_agent="alpha",
        loaded_agents=agents,
    )


class _LogRecorder:
    """Minimal stand-in for the module logger (the navig hierarchy does not
    propagate to the pytest root handler, so caplog can't see it)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def _fmt(self, msg: str, args: tuple) -> str:
        return msg % args if args else msg

    def warning(self, msg, *args, **kwargs):
        with self._lock:
            self.warnings.append(self._fmt(str(msg), args))

    def error(self, msg, *args, **kwargs):
        with self._lock:
            self.errors.append(self._fmt(str(msg), args))

    def info(self, *args, **kwargs):
        pass

    def debug(self, *args, **kwargs):
        pass


def test_credential_failure_falls_back_once_marks_response_and_event(monkeypatch):
    """nokey on the primary → ONE retry via the default route, response +
    event marked, mandated warning logged once per agent."""
    calls: list[str | None] = []
    lock = threading.Lock()

    def fake_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        with lock:
            calls.append(model)
        if model is None:
            raise ValueError(_NOKEY_MSG)
        return "Recovered on the substitute provider, and it held.\nCONFIDENCE: 0.7"

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)
    monkeypatch.setattr(
        council_mod, "_fallback_route", lambda failed: ("ollama", "ollama:qwen2.5:7b-instruct")
    )

    events: list[dict] = []
    result = run_council(
        _formation(), "Ship it?", rounds=1, timeout_per_agent=5, on_event=events.append
    )

    responses = result["rounds"][0]["responses"]
    assert len(responses) == 2
    for r in responses:
        assert r["provider_fallback"] is True
        assert r["fallback_provider"] == "ollama"
        assert "Recovered on the substitute provider" in r["response"]
        assert r["confidence"] == 0.7

    # Exactly one pinned retry per call site: 2 agents + 1 synthesis, never more.
    assert calls.count("ollama:qwen2.5:7b-instruct") == 3
    # The synthesis call was rescued too — marked on the result, not an agent.
    assert result["synthesis_fallback"] is True
    assert result["synthesis_fallback_provider"] == "ollama"
    assert "synthesis_error" not in result

    # Event payload carries the flag — and never credentials/system prompts.
    chips = [e for e in events if e["type"] == "agent_response"]
    assert len(chips) == 2
    for chip in chips:
        assert chip["provider_fallback"] is True
        assert chip["fallback_provider"] == "ollama"
    assert _SECRET not in json.dumps(events)
    assert "credential" not in json.dumps(events)

    # The mandated warning, exactly once per agent + once for synthesis.
    fallback_warnings = [w for w in rec.warnings if "falling back to the default provider" in w]
    assert (
        "[COUNCIL] Agent 'alpha': provider 'anthropic' has no credential — "
        "falling back to the default provider" in fallback_warnings
    )
    assert (
        "[COUNCIL] Agent 'beta': provider 'anthropic' has no credential — "
        "falling back to the default provider" in fallback_warnings
    )
    assert (
        "[COUNCIL] Synthesis: provider 'anthropic' has no credential — "
        "falling back to the default provider" in fallback_warnings
    )
    assert len(fallback_warnings) == 3


def test_fallback_warning_logged_once_per_agent_per_run(monkeypatch):
    """A 3-round run with a broken pinned provider warns once per agent."""

    def fake_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        if model is None:
            raise ValueError(_NOKEY_MSG)
        return "Substitute answer.\nCONFIDENCE: 0.6"

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)
    monkeypatch.setattr(council_mod, "_fallback_route", lambda failed: ("ollama", "ollama:qwen3"))

    result = run_council(_formation(), "Ship it?", rounds=3, timeout_per_agent=5)

    assert len(result["rounds"]) == 3
    agent_warnings = [
        w for w in rec.warnings if "Agent '" in w and "falling back to the default provider" in w
    ]
    assert len(agent_warnings) == 2  # one per agent, not one per agent per round
    # Synthesis runs once per run, so it warns at most once.
    synthesis_warnings = [
        w for w in rec.warnings if "Synthesis:" in w and "falling back to the default provider" in w
    ]
    assert len(synthesis_warnings) == 1
    # ...but every round's responses are still marked (the fallback ran each time).
    for rnd in result["rounds"]:
        assert all(r.get("provider_fallback") is True for r in rnd["responses"])


def test_no_pin_retry_uses_plain_seam(monkeypatch):
    """When the default route differs from the failed provider, the retry goes
    through the plain seam — no ``model=`` kwarg (backward-compatible fakes)."""
    seen: dict[str, int] = {}
    lock = threading.Lock()

    def fake_ai(prompt: str, system_prompt: str | None = None) -> str:
        # Deliberately NO ``model`` parameter: a pinned retry would TypeError.
        with lock:
            seen[prompt] = seen.get(prompt, 0) + 1
            attempt = seen[prompt]
        if attempt == 1:
            raise ValueError(_NOKEY_MSG)
        return "Second attempt answered.\nCONFIDENCE: 0.6"

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)
    monkeypatch.setattr(council_mod, "_fallback_route", lambda failed: ("openrouter", None))

    result = run_council(_formation(), "Ship it?", rounds=1, timeout_per_agent=5)

    responses = result["rounds"][0]["responses"]
    for r in responses:
        assert r["provider_fallback"] is True
        assert r["fallback_provider"] == "openrouter"
        assert "Second attempt answered" in r["response"]
    # The synthesis retry rode the same plain (no-pin) seam.
    assert result["synthesis_fallback"] is True
    assert result["synthesis_fallback_provider"] == "openrouter"
    assert "Second attempt answered" in result["final_decision"]


def test_non_credential_failure_does_not_fall_back(monkeypatch):
    """A content/rate-limit-class error keeps today's behavior: no retry,
    the agent fails, the run continues."""
    calls: list[str | None] = []
    lock = threading.Lock()

    def fake_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        with lock:
            calls.append(model)
        raise RuntimeError("429 too many requests")

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)

    result = run_council(_formation(), "Ship it?", rounds=1, timeout_per_agent=5)

    responses = result["rounds"][0]["responses"]
    assert len(responses) == 2
    for r in responses:
        assert r["response"] == "[ERROR: 429 too many requests]"
        assert "provider_fallback" not in r
        assert "fallback_provider" not in r
    # 2 agent primaries + 1 synthesis — no retries anywhere.
    assert len(calls) == 3
    assert all(m is None for m in calls)
    assert not [w for w in rec.warnings if "falling back" in w]
    # The original error class is surfaced, not swallowed.
    assert any("429 too many requests" in e for e in rec.errors)


def test_fallback_also_failing_keeps_original_error_and_run_continues(monkeypatch):
    """Primary nokey + failing fallback → the agent fails with the ORIGINAL
    error; the deliberation still completes."""
    calls: list[str | None] = []
    lock = threading.Lock()

    def fake_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        with lock:
            calls.append(model)
        if model is None:
            raise ValueError(_NOKEY_MSG)
        raise ConnectionError("connect timeout to localhost:11434")

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)
    monkeypatch.setattr(council_mod, "_fallback_route", lambda failed: ("ollama", "ollama:qwen3"))

    events: list[dict] = []
    result = run_council(
        _formation(), "Ship it?", rounds=1, timeout_per_agent=5, on_event=events.append
    )

    responses = result["rounds"][0]["responses"]
    for r in responses:
        assert r["response"] == f"[ERROR: {_NOKEY_MSG}]"  # original error, not the retry's
        assert "provider_fallback" not in r
    # One retry per call site happened (and failed): 2 agents + 1 synthesis —
    # exactly once each, never a loop.
    assert calls.count("ollama:qwen3") == 3
    # The run still completed end to end — with the honest synthesis failure
    # shape, never the raw error masquerading as the decision.
    assert len(result["rounds"]) == 1
    assert result["synthesis_error"] == _NOKEY_MSG
    assert result["final_decision"].startswith("Synthesis failed (")
    assert "[ERROR" not in result["final_decision"]
    assert [e["type"] for e in events][-1] == "done"
    assert events[-1]["synthesis_error"] is True
    assert any("fallback also failed" in e for e in rec.errors)
    chips = [e for e in events if e["type"] == "agent_response"]
    assert all("provider_fallback" not in c for c in chips)


def test_credential_status_uses_shared_liveness_classifier():
    """Regression tie to navig.llm.liveness: the engine classifies with the
    same rules `navig mode doctor` uses."""
    assert council_mod._credential_status(ValueError(_NOKEY_MSG)) == "nokey"
    assert council_mod._credential_status(ConnectionError("endpoint unreachable")) == "unreachable"
    assert council_mod._credential_status(RuntimeError("401 unauthorized")) == "auth"
    assert council_mod._credential_status(RuntimeError("model gone: 404")) == "dead"
    assert council_mod._credential_status(RuntimeError("some content policy thing")) == "error"


def test_fallback_route_prefers_mode_fallback_when_default_is_the_failed_provider(monkeypatch):
    """_fallback_route: default != failed → plain retry; default == failed →
    the mode's configured fallback pair; introspection failure → (None, None)."""
    import navig.llm.router as router_mod

    class _Cfg:
        provider = "anthropic"
        model = "claude-opus-4-8"
        mode = "big_tasks"

    monkeypatch.setattr(router_mod, "resolve_llm", lambda *a, **k: _Cfg())
    monkeypatch.setattr(
        router_mod, "get_mode_fallback_spec", lambda mode, **k: "ollama:qwen2.5:7b-instruct"
    )

    # Default route already differs from the failed provider → no pin needed.
    assert council_mod._fallback_route("nvidia") == ("anthropic", None)
    # Default route IS the failed provider → hop to the mode fallback.
    assert council_mod._fallback_route("anthropic") == ("ollama", "ollama:qwen2.5:7b-instruct")

    # No usable fallback (or same provider) → plain no-pin retry.
    monkeypatch.setattr(router_mod, "get_mode_fallback_spec", lambda mode, **k: None)
    assert council_mod._fallback_route("anthropic") == ("anthropic", None)

    # Introspection blowing up must never break the run.
    def _boom(*a, **k):
        raise RuntimeError("router unavailable")

    monkeypatch.setattr(router_mod, "resolve_llm", _boom)
    assert council_mod._fallback_route("anthropic") == (None, None)


# ── Synthesis fallback + honest failure state (the final-decision call) ───────

_SYNTH_MARKER = "The team just discussed"  # only the synthesis prompt says this


def test_synthesis_credential_failure_falls_back_once_and_marks_result(monkeypatch):
    """nokey during synthesis only → ONE pinned retry, a REAL decision text,
    result marked ``synthesis_fallback`` (+ provider), mandated warning logged
    once — and no error badge anywhere (the run was rescued)."""
    synth_calls: list[str | None] = []
    lock = threading.Lock()

    def fake_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        if _SYNTH_MARKER in prompt:
            with lock:
                synth_calls.append(model)
            if model is None:
                raise ValueError(_NOKEY_MSG)
            return "Ship it — the team agreed the seams hold. Next step: tag the release."
        return "Agent answer.\nCONFIDENCE: 0.8"

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)
    monkeypatch.setattr(council_mod, "_fallback_route", lambda failed: ("ollama", "ollama:qwen3"))

    events: list[dict] = []
    result = run_council(
        _formation(), "Ship it?", rounds=1, timeout_per_agent=5, on_event=events.append
    )

    # The rescued decision is the real text — marked, never an error string.
    assert result["final_decision"].startswith("Ship it — the team agreed")
    assert result["synthesis_fallback"] is True
    assert result["synthesis_fallback_provider"] == "ollama"
    assert "synthesis_error" not in result
    assert synth_calls == [None, "ollama:qwen3"]  # exactly one retry, never a loop

    # The mandated warning, exactly once (agents never failed → never warned).
    fallback_warnings = [w for w in rec.warnings if "falling back to the default provider" in w]
    assert fallback_warnings == [
        "[COUNCIL] Synthesis: provider 'anthropic' has no credential — "
        "falling back to the default provider"
    ]

    # A rescued synthesis is NOT an error — the done event carries no badge.
    assert events[-1]["type"] == "done"
    assert "synthesis_error" not in events[-1]


def test_synthesis_fallback_also_failing_yields_honest_failure_state(monkeypatch):
    """Synthesis nokey + failing fallback → ``synthesis_error`` carries the
    ORIGINAL error, ``final_decision`` is the operator-facing sentence (never
    the raw bracketed error), the run completes, and the terminal ``done``
    event is badged ``synthesis_error: true``."""
    synth_calls: list[str | None] = []
    lock = threading.Lock()

    def fake_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        if _SYNTH_MARKER in prompt:
            with lock:
                synth_calls.append(model)
            if model is None:
                raise ValueError(_NOKEY_MSG)
            raise ConnectionError("connect timeout to localhost:11434")
        return "Agent answer.\nCONFIDENCE: 0.8"

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)
    monkeypatch.setattr(council_mod, "_fallback_route", lambda failed: ("ollama", "ollama:qwen3"))

    events: list[dict] = []
    result = run_council(
        _formation(), "Ship it?", rounds=1, timeout_per_agent=5, on_event=events.append
    )

    # Honest failure shape: distinct error field + operator-facing sentence.
    assert result["synthesis_error"] == _NOKEY_MSG  # the ORIGINAL error, not the retry's
    assert result["final_decision"].startswith("Synthesis failed (")
    assert result["final_decision"].endswith("— see the individual agent responses above.")
    assert "[ERROR" not in result["final_decision"]
    assert "synthesis_fallback" not in result
    assert synth_calls == [None, "ollama:qwen3"]  # exactly one retry

    # The run still completed end to end; the terminal event badges the failure.
    assert len(result["rounds"]) == 1
    assert all(r["confidence"] == 0.8 for r in result["rounds"][0]["responses"])
    assert events[-1]["type"] == "done"
    assert events[-1]["synthesis_error"] is True
    assert any(
        "Synthesis failed (nokey) and the default-provider fallback also failed" in e
        for e in rec.errors
    )


def test_non_credential_synthesis_error_no_fallback_same_honest_shape(monkeypatch):
    """A content/rate-limit-class synthesis error: NO retry, no warning — but
    the result still gets the honest failure shape, never a bracketed error."""
    synth_calls: list[str | None] = []
    lock = threading.Lock()

    def fake_ai(prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        if _SYNTH_MARKER in prompt:
            with lock:
                synth_calls.append(model)
            raise RuntimeError("429 too many requests")
        return "Agent answer.\nCONFIDENCE: 0.8"

    rec = _LogRecorder()
    monkeypatch.setattr(council_mod, "_ask_ai", fake_ai)
    monkeypatch.setattr(council_mod, "logger", rec)

    events: list[dict] = []
    result = run_council(
        _formation(), "Ship it?", rounds=1, timeout_per_agent=5, on_event=events.append
    )

    assert synth_calls == [None]  # no retry for non-credential errors
    assert not [w for w in rec.warnings if "falling back" in w]
    assert result["synthesis_error"] == "429 too many requests"
    assert result["final_decision"] == (
        "Synthesis failed (429 too many requests) — see the individual agent responses above."
    )
    assert "synthesis_fallback" not in result
    assert events[-1]["type"] == "done"
    assert events[-1]["synthesis_error"] is True
    # The original error class is surfaced in the log, not swallowed.
    assert any("Final decision synthesis failed: 429 too many requests" in e for e in rec.errors)
