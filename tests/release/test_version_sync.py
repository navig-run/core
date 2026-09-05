"""Regression tests for tools/_version_sync.py::maybe_sync_www.

The website version silently stopped syncing on every release because maybe_sync_www
looked for a pre-monorepo sibling `navig-www` dir (which no longer exists) instead of
the in-repo `web/www`. These lock the corrected behaviour: delegate to the canonical
Node script under web/www, and degrade non-fatally when it (or Node) is absent.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[2] / "tools" / "_version_sync.py"


def _load():
    spec = importlib.util.spec_from_file_location("navig_version_sync_under_test", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _fake_monorepo(tmp_path):
    """<tmp>/core (REPO_ROOT) + <tmp>/web/www/scripts/sync-site-version.mjs."""
    core = tmp_path / "core"
    core.mkdir()
    scripts = tmp_path / "web" / "www" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "sync-site-version.mjs").write_text("// stub\n", encoding="utf-8")
    return core


def test_maybe_sync_www_delegates_to_node_for_web_www(tmp_path, monkeypatch):
    vs = _load()
    monkeypatch.setattr(vs, "REPO_ROOT", _fake_monorepo(tmp_path))
    monkeypatch.setattr(vs.shutil, "which", lambda name: "/usr/bin/node" if name == "node" else None)

    captured: dict[str, object] = {}

    class _R:
        returncode = 0
        stdout = "Site version synced"
        stderr = ""

    def _fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _R()

    monkeypatch.setattr(vs.subprocess, "run", _fake_run)

    vs.maybe_sync_www("3.24.0", dry_run=False)

    cmd = captured["cmd"]
    assert cmd[0] == "/usr/bin/node"
    script_path = str(cmd[1]).replace("\\", "/")
    assert script_path.endswith("sync-site-version.mjs")
    assert "web/www/scripts" in script_path  # the MONOREPO path, never the dead navig-www


def test_maybe_sync_www_skips_when_no_site_dir(tmp_path, monkeypatch):
    vs = _load()
    core = tmp_path / "core"
    core.mkdir()  # no web/www and no legacy navig-www sibling
    monkeypatch.setattr(vs, "REPO_ROOT", core)

    ran = {"node": False}
    monkeypatch.setattr(vs.subprocess, "run", lambda *a, **k: ran.__setitem__("node", True))

    vs.maybe_sync_www("3.24.0", dry_run=False)  # must NOT raise (non-fatal)
    assert ran["node"] is False


def test_maybe_sync_www_dry_run_does_not_run_node(tmp_path, monkeypatch):
    vs = _load()
    monkeypatch.setattr(vs, "REPO_ROOT", _fake_monorepo(tmp_path))

    ran = {"node": False}
    monkeypatch.setattr(vs.subprocess, "run", lambda *a, **k: ran.__setitem__("node", True))

    vs.maybe_sync_www("3.24.0", dry_run=True)
    assert ran["node"] is False  # a dry-run only previews
