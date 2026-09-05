"""`navig vault` must not report a failure and exit 0.

The secrets surface is the worst place for a phantom success: `vault disable X`
on a typo'd id printed "not found" and exited 0, so a script that revokes a
credential before rotating a key believed the revoke had happened. The module
already used `typer.Exit(1)` for exactly this condition in four other commands.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from navig.commands.vault import vault_app

pytestmark = pytest.mark.integration

runner = CliRunner()


def _vault(**behaviour):
    v = MagicMock()
    for name, value in behaviour.items():
        getattr(v, name).return_value = value
    return v


@pytest.mark.parametrize(
    ("argv", "method"),
    [
        (["disable", "nope"], "disable"),
        (["enable", "nope"], "enable"),
    ],
)
def test_missing_credential_exits_non_zero(argv, method):
    """The store said "no such credential" — the command must agree."""
    with patch("navig.commands.vault._vault_mod.get_vault", return_value=_vault(**{method: False})):
        res = runner.invoke(vault_app, argv)
    assert res.exit_code == 1, res.output
    assert "not found" in res.output


def test_clone_of_a_missing_source_exits_non_zero():
    with patch("navig.commands.vault._vault_mod.get_vault", return_value=_vault(clone=None)):
        res = runner.invoke(vault_app, ["clone", "nope", "other-profile"])
    assert res.exit_code == 1, res.output
    assert "not found" in res.output


@pytest.mark.parametrize(
    ("argv", "method", "value"),
    [
        (["disable", "ok-id"], "disable", True),
        (["enable", "ok-id"], "enable", True),
    ],
)
def test_the_success_path_still_exits_zero(argv, method, value):
    """Anti-vacuity: `raise Exit(1)` unconditionally would satisfy the tests above."""
    with patch("navig.commands.vault._vault_mod.get_vault", return_value=_vault(**{method: value})):
        res = runner.invoke(vault_app, argv)
    assert res.exit_code == 0, res.output


# ── the post-add courtesy test reports its outcome ────────────────────────


def _result(success: bool):
    r = MagicMock()
    r.success = success
    r.message = "checked"
    r.details = None
    r.tested_at = None
    return r


@pytest.mark.parametrize("success", [True, False])
def test_run_test_by_id_reports_whether_validation_passed(success):
    """It used to return None for BOTH outcomes, so the caller learned nothing."""
    from navig.commands.vault import _run_test_by_id

    v = MagicMock()
    v.test.return_value = _result(success)
    v.get_by_id.return_value = MagicMock(id="cred-1")

    with patch("navig.commands.vault._vault_mod.get_vault", return_value=v):
        assert _run_test_by_id("cred-1") is success


def test_run_test_by_id_reports_false_when_the_test_raises():
    """An exception is a failed check, not an absent one — and it must not escape.

    It runs inside `vault add`'s `except Exception`, so raising would be caught
    there and relabelled "Failed to add credential" for a credential that WAS
    saved. Reporting by return value is the contract.
    """
    from navig.commands.vault import _run_test_by_id

    v = MagicMock()
    v.test.side_effect = RuntimeError("provider unreachable")

    with patch("navig.commands.vault._vault_mod.get_vault", return_value=v):
        assert _run_test_by_id("cred-1") is False


def test_validation_metadata_is_written_by_one_shared_helper():
    """`_run_test_by_id` and `vault test` both persist the outcome; two verbatim
    copies of that block drift the moment one learns a new field."""
    from navig.commands.vault import _record_validation_metadata

    v = MagicMock()
    _record_validation_metadata(v, MagicMock(id="cred-9"), _result(False))

    v.update.assert_called_once()
    meta = v.update.call_args.kwargs["metadata"]
    assert meta["validation_success"] is False
    assert meta["validation_message"] == "checked"

    # A missing credential is a no-op, not an AttributeError on `.id`.
    v2 = MagicMock()
    _record_validation_metadata(v2, None, _result(True))
    v2.update.assert_not_called()
