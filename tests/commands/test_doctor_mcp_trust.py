"""`navig doctor` must show what each MCP server is actually allowed to do.

`mcp.trust.servers.<id>` decides whether a server's declared reads run unprompted and
which of its tools it may offer at all. Both are set with `navig config set`, and there
was no way to confirm either took effect.

The failure that matters is silent: an unrecognised tier falls back to `byo` with a log
line the person who typed it never sees. "Configured but not in effect" must not render
as a tidy ✓ — this file is the pin for that, and for the rule the doctor learned the
hard way: a green tick may never mean "I could not look".
"""

from __future__ import annotations

import pytest

from navig.commands import doctor


@pytest.fixture
def mcp_config(monkeypatch):
    """Drive `check_mcp_trust` from a synthetic config, never the operator's own."""
    cfg: dict = {}

    class _CM:
        @staticmethod
        def get(key, default=None):
            if key == "mcp":
                return cfg
            if key == "mcp.trust":
                return cfg.get("trust", {})
            return default

    monkeypatch.setattr("navig.config.ConfigManager", lambda *a, **k: _CM())
    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: _CM())
    return cfg


# CheckResult is a (icon, ok, line) tuple carrying .label/.detail — `ok` is index 1.
def _rows(results):
    return {r.label: r for r in results}


def test_no_servers_means_no_section(mcp_config) -> None:
    """An install that does not use MCP should not carry an empty section forever."""
    assert doctor.check_mcp_trust() == []


def test_a_default_server_reports_byo_and_all_tools(mcp_config) -> None:
    mcp_config["servers"] = {"acme": {"command": "x"}}

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert row[1] is True
    assert "byo" in row.detail
    assert "all tools" in row.detail


def test_a_vetted_server_says_so(mcp_config) -> None:
    mcp_config["servers"] = {"acme": {"command": "x"}}
    mcp_config["trust"] = {"servers": {"acme": "vetted"}}

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert row[1] is True
    assert "vetted" in row.detail


def test_a_mistyped_tier_warns_instead_of_showing_a_tick(mcp_config) -> None:
    """The whole point: this setting is being ignored, and nothing else says so."""
    mcp_config["servers"] = {"acme": {"command": "x"}}
    mcp_config["trust"] = {"servers": {"acme": "trusted"}}

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert row[1] is False, "a tier that never applied must not render as ✓"
    assert "trusted" in row.detail
    assert "byo" in row.detail


def test_a_scope_is_shown(mcp_config) -> None:
    mcp_config["servers"] = {"acme": {"command": "x"}}
    mcp_config["trust"] = {"servers": {"acme": {"tier": "vetted", "tools": ["a", "b"]}}}

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert row[1] is True
    assert "2 tool(s)" in row.detail


def test_an_empty_allowlist_warns(mcp_config) -> None:
    """Denying everything is a legal configuration and almost never the intent."""
    mcp_config["servers"] = {"acme": {"command": "x"}}
    mcp_config["trust"] = {"servers": {"acme": {"tools": []}}}

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert row[1] is False
    assert "NO tools" in row.detail


def test_every_configured_server_gets_a_row(mcp_config) -> None:
    mcp_config["servers"] = {"a": {}, "b": {}, "c": {}}

    assert set(_rows(doctor.check_mcp_trust())) == {"MCP a", "MCP b", "MCP c"}


def test_a_broken_read_warns_rather_than_ticking(monkeypatch) -> None:
    """✓ must never mean 'I could not look'."""

    def _boom(*a, **k):
        raise RuntimeError("config on fire")

    monkeypatch.setattr("navig.config.ConfigManager", _boom)

    rows = doctor.check_mcp_trust()
    assert len(rows) == 1
    assert rows[0][1] is False
    assert "on fire" in rows[0].detail


def test_the_section_is_wired_into_the_report(mcp_config, monkeypatch) -> None:
    """A check nothing calls is documentation."""
    mcp_config["servers"] = {"acme": {"command": "x"}}

    sections = dict(doctor._collect_sections(port=None))
    assert "MCP Trust" in sections
    assert any(r.label == "MCP acme" for r in sections["MCP Trust"])


# ---------------------------------------------------------------------------
# Pre-authorised tools (the two-gate release valve)
# ---------------------------------------------------------------------------


def test_pre_authorised_tools_are_always_shown(mcp_config) -> None:
    """This is the one setting that lets a WRITE happen with nobody watching."""
    mcp_config["servers"] = {"acme": {"command": "x"}}
    mcp_config["trust"] = {
        "servers": {"acme": {"tier": "vetted", "auto_approve": ["sync", "touch"]}}
    }

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert row[1] is True
    assert "2 pre-authorised" in row.detail
    assert "sync" in row.detail


def test_pre_authorising_on_a_byo_server_warns(mcp_config) -> None:
    """Gate 1 needs a vetted endpoint, so this setting does nothing — and doing nothing
    silently is exactly what this row exists to catch."""
    mcp_config["servers"] = {"acme": {"command": "x"}}
    mcp_config["trust"] = {"servers": {"acme": {"auto_approve": ["sync"]}}}

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert row[1] is False
    assert "no effect" in row.detail


def test_no_pre_authorisation_is_not_mentioned(mcp_config) -> None:
    """The common case stays quiet."""
    mcp_config["servers"] = {"acme": {"command": "x"}}

    row = _rows(doctor.check_mcp_trust())["MCP acme"]
    assert "pre-authorised" not in row.detail
