"""A failed READ of servers.json must never become a destructive WRITE.

The same defect as `test_trigger_read_failure_is_not_a_wipe`, in a second store —
which is what makes it a class rather than a one-off. `_load_servers` reset
`self.servers = {}` on ANY failure, and every mutating verb is
load -> mutate -> `_save_servers()`, so one transient lock (an antivirus holding the
file, a read landing mid-replace) followed by `navig mcp install|enable|disable`
wrote an empty store over every configured server.

`_save_servers` carried the write-side half too: it printed the error and returned
None while all four callers went straight on to `ch.success("... installed")`, so a
failed write reported a server that was never stored. Both halves are the same
contract — do not claim a change that is not on disk, and never let a failed read
decide what gets written.

Neither existing guard could see it: `test_command_exit_honesty` bans
`ch.error`-then-return inside `navig/commands/` and this file is `navig/mcp_manager.py`;
`test_no_config_wipe_pattern` is data-flow aware but scoped to ONE function, while
here the taint crosses two methods through `self.servers`.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


def _mgr(tmp_path):
    from navig.mcp_manager import MCPManager

    return MCPManager(config_dir=tmp_path)


_CONFIG = {
    "alpha": {"type": "npm", "package": "a", "command": "npx", "args": [], "enabled": True},
    "beta": {"type": "npm", "package": "b", "command": "npx", "args": [], "enabled": True},
}


def _seed(tmp_path):
    """Write a populated servers.json and return (path, its exact bytes)."""
    mgr = _mgr(tmp_path)
    path = mgr.servers_file
    path.write_text(json.dumps(_CONFIG, indent=2), encoding="utf-8")
    return path, path.read_text(encoding="utf-8")


def test_a_transient_read_lock_does_not_wipe_the_servers(tmp_path, monkeypatch):
    """Patched at `read_text_retrying` — the seam every json read funnels through.

    NOT at `builtins.open`: `Path.read_text` calls `io.open`, so patching builtins
    silently fails to intercept and the probe passes while testing nothing.
    """
    from navig.core import json_io

    path, original = _seed(tmp_path)

    def _locked(*_a, **_kw):
        raise PermissionError(13, "The process cannot access the file")

    monkeypatch.setattr(json_io, "read_text_retrying", _locked)

    mgr = _mgr(tmp_path)
    assert mgr.disable_server("alpha") is False, (
        "reported a change against a store it could not read"
    )
    assert path.read_text(encoding="utf-8") == original, (
        "one transient lock destroyed every configured MCP server"
    )


def test_a_failed_write_is_not_reported_as_success(tmp_path, monkeypatch):
    """The write-side half: `mcp enable` claimed a server it never persisted."""
    _seed(tmp_path)
    mgr = _mgr(tmp_path)

    from navig.core import yaml_io

    def _fail(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr(yaml_io, "atomic_write_text", _fail)
    monkeypatch.setattr("navig.mcp_manager.atomic_write_text", _fail)

    assert mgr.enable_server("alpha") is False, (
        "a server whose config never reached disk was reported as enabled"
    )


# ── anti-vacuity: returning False always would satisfy both tests above ───


def test_a_normal_change_still_persists(tmp_path):
    _seed(tmp_path)
    mgr = _mgr(tmp_path)
    assert mgr.disable_server("alpha") is True

    reread = _mgr(tmp_path)
    assert set(reread.servers) == {"alpha", "beta"}, "an existing server was dropped"
    assert reread.servers["alpha"].is_enabled() is False, "the change did not persist"
    assert reread.servers["beta"].is_enabled() is True, "an unrelated server was altered"


def test_a_fresh_install_with_no_file_is_not_a_read_failure(tmp_path):
    """Absent is not unreadable — a first run must still be able to write."""
    mgr = _mgr(tmp_path)
    assert not mgr.servers_file.exists()
    assert mgr._save_servers() is True, "a first run must still be able to write"
    assert mgr.servers_file.exists()
