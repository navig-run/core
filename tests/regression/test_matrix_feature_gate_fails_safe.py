"""A Matrix feature gate set to "false" must CLOSE, not open.

`is_feature_enabled` read its value straight from `config.yaml`, where
`navig config set` stores the argument as a STRING — and `bool("false")` is True. The
gates that default to *False* are precisely the dangerous ones:

    admin_ops             user management, server admin   (2 commands)
    registration_control  toggle open/closed registration (4 commands)
    file_sharing          upload/download                 (2 commands)

So an operator who ran the command this module's own error message prints —
`navig config set comms.matrix.features.admin_ops false` — **turned the gate on**.
34 CLI commands sit behind `@require_feature`.

Why this one is safe to coerce unilaterally, unlike `telegram.require_auth`: the
direction only ever tightens. A `"false"`-ish string now CLOSES a gate it used to
open, and every truthy spelling still opens it exactly as before — coercion cannot
open anything that was closed. `require_auth` is the mirror image (True is the safe
state), so coercing *it* would disable an auth boundary on existing installs, and
that is an owner's decision rather than a fix.
"""
from __future__ import annotations

import pytest

from navig.comms import matrix_features as mf

OFF_SPELLINGS = ["false", "False", "no", "off", "0", False]
ON_SPELLINGS = ["true", "yes", "on", "1", True]

# Gates whose default is False — the ones a string value used to flip open.
DANGEROUS = [f for f, default in mf.MATRIX_FEATURE_DEFAULTS.items() if default is False]


def _with_features(monkeypatch: pytest.MonkeyPatch, features: dict) -> None:
    monkeypatch.setattr(mf, "_get_matrix_features_config", lambda: features)


def test_the_dangerous_gates_really_do_default_to_off() -> None:
    """Anti-vacuity for everything below: if these stopped defaulting to False the
    whole fail-open shape would not exist and these tests would guard nothing."""
    assert "admin_ops" in DANGEROUS and "registration_control" in DANGEROUS


@pytest.mark.parametrize("feature", DANGEROUS)
@pytest.mark.parametrize("value", OFF_SPELLINGS)
def test_a_falsey_value_closes_a_dangerous_gate(
    monkeypatch: pytest.MonkeyPatch, feature: str, value: object
) -> None:
    _with_features(monkeypatch, {feature: value})
    assert mf.is_feature_enabled(feature) is False


@pytest.mark.parametrize("feature", DANGEROUS)
@pytest.mark.parametrize("value", ON_SPELLINGS)
def test_a_truthy_value_still_opens_the_gate(
    monkeypatch: pytest.MonkeyPatch, feature: str, value: object
) -> None:
    """The partner. Coercion must not break the documented way to ENABLE a feature —
    `navig config set comms.matrix.features.<f> true` is what the CLI itself prints."""
    _with_features(monkeypatch, {feature: value})
    assert mf.is_feature_enabled(feature) is True


def test_an_unset_feature_keeps_its_declared_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Absent config must fall back to MATRIX_FEATURE_DEFAULTS, not to a blanket
    False — that would silently disable messaging and room_management."""
    _with_features(monkeypatch, {})

    assert mf.is_feature_enabled("messaging") is True
    assert mf.is_feature_enabled("room_management") is True
    assert mf.is_feature_enabled("admin_ops") is False


def test_an_unknown_feature_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _with_features(monkeypatch, {})
    assert mf.is_feature_enabled("no_such_feature") is False


@pytest.mark.parametrize("value", OFF_SPELLINGS)
def test_the_features_listing_agrees_with_the_gate(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """`navig matrix features` renders get_all_features(). An uncoerced string made
    the listing say "enabled" for a gate that... also said enabled. Both are fixed,
    and this pins that they cannot drift apart again."""
    _with_features(monkeypatch, {"admin_ops": value})

    listed = mf.get_all_features()
    assert listed["admin_ops"] is False
    assert listed["admin_ops"] == mf.is_feature_enabled("admin_ops")


def test_the_listing_returns_real_bools_for_every_feature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is annotated `-> dict[str, bool]`; a string value made that a lie."""
    _with_features(monkeypatch, dict.fromkeys(mf.MATRIX_FEATURE_DEFAULTS, "false"))

    assert all(isinstance(v, bool) for v in mf.get_all_features().values())


@pytest.mark.parametrize("value", OFF_SPELLINGS)
def test_matrix_itself_can_be_turned_off(
    monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    """`comms.matrix.enabled` had the same raw read behind `@require_matrix`."""
    import navig.config as config_mod

    class _CM:
        def get_global_config(self) -> dict:
            return {"comms": {"matrix": {"enabled": value}}}

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _CM())
    assert mf.is_matrix_enabled() is False


def test_matrix_can_still_be_turned_on(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.config as config_mod

    class _CM:
        def get_global_config(self) -> dict:
            return {"comms": {"matrix": {"enabled": "true"}}}

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: _CM())
    assert mf.is_matrix_enabled() is True


def test_the_decorator_actually_blocks_when_the_gate_is_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The consumer. Everything above asserts a return value; this drives the real
    `@require_feature` decorator that 34 commands are wrapped in, so the tests are
    not guarding a boolean nothing reads."""
    import typer

    _with_features(monkeypatch, {"admin_ops": "false"})

    ran: list[bool] = []

    @mf.require_feature("admin_ops")
    def _dangerous_command() -> None:
        ran.append(True)

    with pytest.raises(typer.Exit) as excinfo:
        _dangerous_command()

    assert excinfo.value.exit_code == 1
    assert ran == [], "the gate let a disabled admin command run"


def test_the_decorator_lets_an_enabled_command_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The partner — a gate that always blocked would satisfy the test above and
    break every Matrix command."""
    _with_features(monkeypatch, {"admin_ops": "true"})

    ran: list[bool] = []

    @mf.require_feature("admin_ops")
    def _dangerous_command() -> None:
        ran.append(True)

    _dangerous_command()
    assert ran == [True]
