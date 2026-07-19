"""Regression: an installed formation must actually load.

`navig install <formation>` writes to store_dir()/formations/<id> (the user
content store — commands/install.py:_dest_for), but the loader used to scan only
builtin_store_dir()/formations + config_dir()/formations — never store_dir(). So
an installed formation landed in a directory nothing read and silently never
loaded. The loader now also scans store_dir()/formations.
"""

from __future__ import annotations

import json

import navig.formations.loader as loader_mod
import navig.platform.paths as paths_mod
import navig.plugins.package as plugin_pkg_mod


def _isolate(monkeypatch, tmp_path):
    store = tmp_path / "store"
    cfg = tmp_path / "cfg"
    builtin = tmp_path / "builtin"
    for d in (store / "formations", cfg / "formations", builtin / "formations"):
        d.mkdir(parents=True)
    monkeypatch.setattr(loader_mod, "config_dir", lambda: cfg)
    monkeypatch.setattr(paths_mod, "store_dir", lambda: store)
    monkeypatch.setattr(paths_mod, "builtin_store_dir", lambda: builtin)
    monkeypatch.setattr(plugin_pkg_mod, "plugin_capability_dirs", lambda cap: [])
    loader_mod.clear_formations_roots()
    return store, cfg


def test_store_dir_formations_is_a_root(monkeypatch, tmp_path):
    store, cfg = _isolate(monkeypatch, tmp_path)
    try:
        roots = loader_mod._get_formations_roots()
    finally:
        loader_mod.clear_formations_roots()
    assert store / "formations" in roots  # the fix: the `navig install` target is scanned
    assert cfg / "formations" in roots  # legacy dir still scanned


def test_installed_formation_is_discovered(monkeypatch, tmp_path):
    store, _cfg = _isolate(monkeypatch, tmp_path)
    fdir = store / "formations" / "my-council"
    fdir.mkdir()
    (fdir / "formation.json").write_text(json.dumps({"id": "my-council"}), encoding="utf-8")
    try:
        found = loader_mod.discover_formations()
    finally:
        loader_mod.clear_formations_roots()
    assert "my-council" in found
    assert found["my-council"] == fdir
