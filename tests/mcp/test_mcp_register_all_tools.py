"""register_all_tools must not let one failing bundle drop the rest.

If a bundle's ``register()`` raised, every bundle after it in the list was silently
skipped — its tools never registered. Ordering ``connectors`` last was only a
partial mitigation; a failure in ``desktop``/``cdp``/``windows`` still dropped
everything downstream.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from navig.mcp.tools import connectors, inventory, register_all_tools


def test_failing_bundle_does_not_block_later_bundles(monkeypatch):
    def boom(server):
        raise RuntimeError("bundle boom")

    connectors_spy = MagicMock()
    monkeypatch.setattr(inventory, "register", boom)  # the FIRST bundle raises
    monkeypatch.setattr(connectors, "register", connectors_spy)  # the LAST bundle

    register_all_tools(MagicMock())  # must NOT raise despite inventory failing

    # A bundle after the failing one still registered.
    connectors_spy.assert_called_once()


def test_all_bundles_run_when_none_fail(monkeypatch):
    connectors_spy = MagicMock()
    monkeypatch.setattr(connectors, "register", connectors_spy)

    register_all_tools(MagicMock())

    connectors_spy.assert_called_once()
