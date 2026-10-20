"""Regression: GET /api/deck/persona stalled the daemon for 4–18 s.

``list_personas()`` resolves each persona ~3 times, and every resolve called
``plugin_capability_dirs("personas")`` → an uncached entry-point package scan
(``metadata.entry_points`` + ``find_spec`` per plugin, ~0.3 s). The scan is now
memoised per process and dropped only by the install/uninstall hooks.
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def counted_scan(monkeypatch):
    from navig.plugins import package as pkg_mod

    calls: list[int] = []
    real = pkg_mod._scan_entry_point_package_roots

    def _counting():
        calls.append(1)
        return real()

    monkeypatch.setattr(pkg_mod, "_scan_entry_point_package_roots", _counting)
    pkg_mod.invalidate_plugin_discovery_cache()
    yield calls
    pkg_mod.invalidate_plugin_discovery_cache()


def test_repeated_list_personas_scans_packages_once(counted_scan):
    from navig.personas.manager import list_personas

    first = list_personas()
    for _ in range(3):
        assert [p.name for p in list_personas()] == [p.name for p in first]
    assert first, "no personas discovered — the scan floor would be vacuous"
    assert len(counted_scan) == 1


def test_invalidation_hooks_force_a_rescan(counted_scan):
    from navig.modules.registry import reset_entry_points
    from navig.plugins import package as pkg_mod

    pkg_mod.plugin_capability_dirs("personas")
    pkg_mod.plugin_capability_dirs("skills")
    assert len(counted_scan) == 1

    reset_entry_points()  # what the Bay/deck/store installers fire after a pip install
    pkg_mod.plugin_capability_dirs("personas")
    assert len(counted_scan) == 2

    from navig.plugins import host as host_mod

    host_mod._invalidate_discovery()  # PluginHost.install/uninstall
    pkg_mod.plugin_capability_dirs("personas")
    assert len(counted_scan) == 3


def test_disable_toggle_stays_live_without_rescan(tmp_path, monkeypatch, counted_scan):
    from navig.plugins import package as pkg_mod

    cap = tmp_path / "fake_pkg" / "personas"
    cap.mkdir(parents=True)
    monkeypatch.setattr(
        pkg_mod, "_scan_entry_point_package_roots",
        lambda: (counted_scan.append(1), [("fake-plugin", tmp_path / "fake_pkg")])[1],
    )
    pkg_mod.invalidate_plugin_discovery_cache()
    monkeypatch.setattr(pkg_mod, "disabled_plugin_ids", lambda: set())
    assert pkg_mod.entry_point_plugin_capability_dirs("personas") == [cap]
    monkeypatch.setattr(pkg_mod, "disabled_plugin_ids", lambda: {"fake-plugin"})
    assert pkg_mod.entry_point_plugin_capability_dirs("personas") == []
    assert len(counted_scan) == 1
