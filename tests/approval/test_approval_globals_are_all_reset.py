"""Every process-global in `navig.tools.approval` must be cleared by one reset.

This module keeps several globals, and `bind_approval_manager` writes into them. A test
that exercises that path installs its state for every later test in the same xdist
worker — which has already produced a real cross-test failure once
(`test_autonomous_init_and_comms` leaving a fake `ApprovalManager` that reddened an
unrelated MCP test).

`reset_approval_gate` is the containment, and it works only if it stays complete.
`_bound_audit_log` was added *after* that docstring already named the failure mode and
was still missed — six tests in `test_gate_manager_wiring.py` bind a real fake audit log
and every one of them leaked it.

So this file asserts the property mechanically rather than trusting the list: any
module-level mutable that survives a reset is a leak, whether or not anyone remembered
to name it.
"""

from __future__ import annotations

import navig.tools.approval as approval


class _Fake:
    """Stands in for a test's audit log / manager."""

    def record(self, **kwargs):  # pragma: no cover - never asserted, only leaked
        pass


def test_the_bound_audit_log_does_not_survive_a_reset() -> None:
    """The specific one that was missed."""
    approval.bind_approval_manager(object(), _Fake())
    assert approval._bound_audit_log is not None

    approval.reset_approval_gate()

    assert approval._bound_audit_log is None, (
        "A test's fake audit log outlived its test and will receive records from every "
        "later test in this xdist worker."
    )


def test_the_gate_backend_does_not_survive_a_reset() -> None:
    approval.bind_approval_manager(object(), _Fake())
    approval.reset_approval_gate()

    # A fresh gate falls back to the module default rather than the bound backend.
    assert approval.get_approval_gate().backend is approval._log_and_approve


def test_the_external_index_does_not_survive_a_reset() -> None:
    approval.record_external_tool("mcp__acme__x", mode="read", server="acme")
    assert approval.needs_approval("mcp__acme__x") is False

    approval.reset_approval_gate()

    assert approval.needs_approval("mcp__acme__x") is True


def test_self_declared_danger_does_not_survive_a_reset() -> None:
    approval.record_tool_safety("plugin_x", dangerous=True)
    assert approval.is_destructive_tool("plugin_x") is True

    approval.reset_approval_gate()

    assert approval.is_destructive_tool("plugin_x") is False


def test_no_module_global_survives_a_reset() -> None:
    """The mechanical version: derived from the module, not from a hand-kept list.

    A future global added to this module and forgotten in `reset_approval_gate` fails
    here without anyone having to remember this file exists.
    """
    approval.reset_approval_gate()

    # Populate everything a caller can populate.
    approval.bind_approval_manager(object(), _Fake())
    approval.record_external_tool(
        "mcp__acme__y", mode="action", auto_approvable=True,
        operator_pre_authorised=True, server="acme",
    )
    approval.record_tool_safety("plugin_y", dangerous=True)
    approval.get_approval_gate()

    approval.reset_approval_gate()

    dirty: list[str] = []
    for name, value in vars(approval).items():
        if name.startswith("__") or not name.startswith("_"):
            continue
        if name in {"_EXTERNAL_TOOL_PREFIX", "_DESTRUCTIVE_NAME_SUFFIXES"}:
            continue  # constants, not state
        if isinstance(value, (set, dict)) and value:
            dirty.append(f"{name} = {value!r}")
        elif isinstance(value, _Fake):
            dirty.append(f"{name} still holds a test object")

    assert not dirty, (
        "These module globals survived reset_approval_gate() and will leak into every "
        "later test in this worker:\n  " + "\n  ".join(dirty)
    )
