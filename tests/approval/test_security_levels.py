"""`approval.security_level` — the router-firewall 3 levels (high / medium / low).

One knob, three postures (owner design 2026-10-15):

- ``high``    even proven reads page; ANY timeout denies (fail-closed)
- ``medium``  the shipped default: proven reads run, writes page
- ``low``     the terminal family never pages; every other gated tool does

Pinned here: normalization (aliases, typos never kill boot and never fail
open), the ``low`` safe-pattern derivation, explicit-key precedence over the
level, and the manager-level behaviour each level actually produces.
"""

from __future__ import annotations

import time

import pytest

from navig.approval.manager import ApprovalManager
from navig.approval.policies import (
    DEFAULT_SECURITY_LEVEL,
    SHELL_TOOL_NAMES,
    SHELL_TOOL_PATTERNS,
    ApprovalLevel,
    ApprovalPolicy,
    coerce_security_level,
)

pytestmark = pytest.mark.integration


# ── normalization ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "medium"),
        ("", "medium"),
        ("HIGH", "high"),
        ("  Low ", "low"),
        ("strict", "high"),      # router/English aliases normalize…
        ("balanced", "medium"),
        ("permissive", "low"),
        ("nonsense", "medium"),  # …a typo lands on the designed default
        (42, "medium"),
    ],
)
def test_levels_normalize_and_a_typo_never_raises(raw, expected):
    assert coerce_security_level(raw) == expected


def test_a_typo_is_logged_not_swallowed(log_messages):
    # log_messages, not caplog: `navig.core.logging` sets the `navig` root to
    # propagate=False, so caplog (root-level) sees nothing from this tree.
    coerce_security_level("hi")
    assert any("security_level" in m for m in log_messages), (
        "an operator who typo'd the level must see it somewhere"
    )


# ── derivation ────────────────────────────────────────────────────────────────


def test_low_adds_the_shell_family_to_safe_without_losing_explicit_entries():
    cfg = {"approval": {"security_level": "low", "levels": {"safe": ["custom *"]}}}
    p = ApprovalPolicy.from_config(cfg)
    assert "custom *" in p.safe_patterns
    for pattern in SHELL_TOOL_PATTERNS:
        assert pattern in p.safe_patterns
    # the config dict must not be mutated by a load
    assert cfg["approval"]["levels"]["safe"] == ["custom *"]


def test_medium_and_high_leave_the_safe_list_alone():
    for level in ("medium", "high"):
        p = ApprovalPolicy.from_config({"approval": {"security_level": level}})
        assert not any(t in " ".join(p.safe_patterns) for t in ("bash_exec", "navig_run"))


def test_classification_follows_the_level():
    low = ApprovalPolicy.from_config({"approval": {"security_level": "low"}})
    assert low.classify_command("tool bash_exec") is ApprovalLevel.SAFE
    assert low.classify_command("tool db_query") is ApprovalLevel.CONFIRM, (
        "low exempts the TERMINAL only — other destructive tools still ask"
    )

    medium = ApprovalPolicy.from_config({"approval": {"security_level": "medium"}})
    assert medium.classify_command("tool bash_exec") is ApprovalLevel.CONFIRM
    assert medium.security_level == DEFAULT_SECURITY_LEVEL


def test_high_fails_closed_on_timeout_medium_follows_default_action():
    high = ApprovalPolicy(security_level="high", default_action="approve")
    assert high.fail_closed_timeout is True
    for level in ("medium", "low"):
        p = ApprovalPolicy(security_level=level, default_action="approve")
        assert p.fail_closed_timeout is False


def test_low_shell_patterns_parity_with_the_gate():
    """`low`'s exempt set IS the gate's shell family — drift is a bug."""
    from navig.tools.approval import _SHELL_TOOLS

    assert SHELL_TOOL_NAMES == _SHELL_TOOLS


# ── manager behaviour ─────────────────────────────────────────────────────────


async def test_low_approves_a_shell_command_instantly_without_prompting():
    policy = ApprovalPolicy.from_config(
        {"approval": {"security_level": "low", "timeout_seconds": 30}}
    )
    manager = ApprovalManager(policy=policy)
    started = time.monotonic()
    assert await manager.request_approval(command="tool bash_exec") is True
    assert time.monotonic() - started < 1.0, "SAFE must return without waiting"


async def test_low_still_asks_for_a_non_shell_destructive_tool():
    policy = ApprovalPolicy.from_config(
        {"approval": {"security_level": "low", "timeout_seconds": 1}}
    )
    policy.timeout_seconds = 0.05  # bypass coerce_int's 1 s floor for the test
    manager = ApprovalManager(policy=policy)
    assert await manager.request_approval(command="tool db_query") is False, (
        "no human answered — it must have ASKED (timeout → deny), not auto-approved"
    )


async def test_high_denies_a_timeout_even_when_default_action_is_approve():
    policy = ApprovalPolicy(security_level="high", default_action="approve")
    policy.timeout_seconds = 0.05
    manager = ApprovalManager(policy=policy)
    assert await manager.request_approval(command="tool bash_exec") is False


async def test_medium_timeout_still_honours_default_action_approve():
    """The control: proves high's denial above comes from the LEVEL, not the wait."""
    policy = ApprovalPolicy(security_level="medium", default_action="approve")
    policy.timeout_seconds = 0.05
    manager = ApprovalManager(policy=policy)
    assert await manager.request_approval(command="tool bash_exec") is True


# ── chat-path read bypass precedence (tools layer) ────────────────────────────


def _patch_config(monkeypatch, values: dict):
    import navig.config as cfg

    real_get = cfg.get

    def fake_get(key, default=None, *a, **kw):
        if key in values:
            return values[key]
        return real_get(key, default, *a, **kw)

    monkeypatch.setattr(cfg, "get", fake_get)


def test_read_bypass_derives_from_level_when_no_explicit_key(monkeypatch):
    from navig.tools import approval as ap

    _patch_config(monkeypatch, {"approval.security_level": "high"})
    assert ap._auto_approve_read_only() is False, "high: even proven reads page"

    _patch_config(monkeypatch, {"approval.security_level": "low"})
    assert ap._auto_approve_read_only() is True

    _patch_config(monkeypatch, {"approval.security_level": "medium"})
    assert ap._auto_approve_read_only() is True


def test_an_explicit_key_beats_the_level(monkeypatch):
    from navig.tools import approval as ap

    _patch_config(
        monkeypatch,
        {"approval.security_level": "medium", "approval.auto_approve_read_only": False},
    )
    assert ap._auto_approve_read_only() is False, "an explicit key is a decision"

    _patch_config(
        monkeypatch,
        {"approval.security_level": "high", "approval.auto_approve_read_only": True},
    )
    assert ap._auto_approve_read_only() is True


# ── end to end: the exact path a Telegram tool call takes ─────────────────────
#
# The tests above pin the policy and the manager separately. A Telegram call goes
# gate_agent_tool_call → needs_approval → gate.check → _manager_backend →
# manager.request_approval → policy.classify. Only walking the WHOLE chain proves the
# knob changes what the operator is asked.


@pytest.fixture
def bound_gate():
    from navig.tools import approval as ap

    ap.reset_approval_gate()
    yield ap
    ap.reset_approval_gate()


def _bind(ap, level: str) -> ApprovalManager:
    policy = ApprovalPolicy.from_config({"approval": {"security_level": level}})
    policy.timeout_seconds = 0.05  # nobody answers — an ASK shows up as a timeout deny
    manager = ApprovalManager(policy=policy)
    ap.bind_approval_manager(manager, None)
    return manager


async def test_low_lets_a_telegram_shell_write_through_the_whole_gate(bound_gate):
    _bind(bound_gate, "low")
    denial = await bound_gate.gate_agent_tool_call(
        "bash_exec",
        parameters={"command": "navig audio gen horror --kind music"},
        session_key="telegram:user:12345",
    )
    assert denial is None, "low: the terminal must not ask"


async def test_low_still_asks_for_a_database_call_through_the_whole_gate(bound_gate):
    _bind(bound_gate, "low")
    denial = await bound_gate.gate_agent_tool_call(
        "db_query", parameters={"sql": "DELETE FROM x"}, session_key="telegram:user:12345"
    )
    assert denial is not None and denial.startswith("[Denied"), (
        "low frees the TERMINAL only — a database write must still page"
    )


async def test_medium_asks_for_a_shell_write_through_the_whole_gate(bound_gate):
    """The control: proves low's pass above comes from the LEVEL, not the harness."""
    _bind(bound_gate, "medium")
    denial = await bound_gate.gate_agent_tool_call(
        "bash_exec",
        parameters={"command": "navig audio gen horror --kind music"},
        session_key="telegram:user:12345",
    )
    assert denial is not None and denial.startswith("[Denied")


async def test_high_asks_even_for_a_proven_read(bound_gate, monkeypatch):
    import navig.config as cfg

    real_get = cfg.get
    monkeypatch.setattr(
        cfg,
        "get",
        lambda key, default=None, *a, **kw: "high"
        if key == "approval.security_level"
        else (None if key == "approval.auto_approve_read_only" else real_get(key, default, *a, **kw)),
    )
    _bind(bound_gate, "high")
    denial = await bound_gate.gate_agent_tool_call(
        "bash_exec", parameters={"command": "git status"}, session_key="telegram:user:12345"
    )
    assert denial is not None, "high: not even a read runs unasked"
