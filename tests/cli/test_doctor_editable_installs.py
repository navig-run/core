"""doctor must name an editable install that no longer resolves.

The class, observed three times in one day (2026-10-14): a worktree's editable
install keeps mapping a module to a directory that no longer exists; a package
mid-extraction maps a directory that exists but resolves to nothing; both stay
registered and the failure surfaces far away — ``navig <verb>`` says
"entry-point command unavailable", and ``export_registry`` silently drops the
plugin's commands from the manifest (a shrink its own ``--check`` then calls
fresh).

Path existence alone misses the second case (measured: a package whose
``__init__.py`` had not been written yet — path healthy, import dead), so the
check resolves through the live import system with ``find_spec``, which executes
nothing.
"""

from __future__ import annotations

import importlib.util

import navig.commands.doctor as doc

FINDER_TMPL = (
    "from __future__ import annotations\n"
    "MAPPING: dict[str, str] = {%r: %r}\n"
    "NAMESPACES: dict[str, list[str]] = {}\n"
)


def _write_finder(tmp_path, module: str, target: str):
    """A real-shaped editable finder file on this fake site dir."""
    f = tmp_path / ("__editable___" + module + "-0.1.0_finder.py")
    f.write_text(FINDER_TMPL % (module, target), encoding="utf-8")
    return f


def test_mapping_parses_doubled_windows_paths(tmp_path, monkeypatch):
    """The finder literal holds backslash-escaped paths — literal_eval must decode them."""
    target = str(tmp_path / "navig_fake" / "navig_fake")
    _write_finder(tmp_path, "navig_fake", target)
    monkeypatch.setattr(doc.sys, "path", [str(tmp_path), *doc.sys.path])
    maps = doc._editable_mappings()
    assert ("navig_fake", target) in maps, maps


def test_a_gone_target_is_named_with_its_fix(tmp_path, monkeypatch):
    gone = str(tmp_path / "deleted-worktree" / "navig_gone")
    monkeypatch.setattr(doc, "_editable_mappings", lambda: [("navig_gone", gone)])
    (finding,) = doc._editable_findings()
    name, detail = finding
    assert name == "navig_gone"
    assert "target gone" in detail
    assert "pip install -e" in detail and "pip uninstall" in detail


def test_a_partial_package_is_named_even_though_the_path_exists(tmp_path, monkeypatch):
    """The 2026-10-14 case: directory present, resolves to nothing (no __init__)."""
    pkg = tmp_path / "navig_partial" / "navig_partial"
    pkg.mkdir(parents=True)  # exists — but find_spec will answer None
    monkeypatch.setattr(doc, "_editable_mappings", lambda: [("navig_partial", str(pkg))])
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    (finding,) = doc._editable_findings()
    assert finding[0] == "navig_partial"
    assert "no module resolves" in finding[1]
    assert "partial" in finding[1]


def test_a_raising_find_spec_is_a_finding_not_a_crash(tmp_path, monkeypatch):
    pkg = tmp_path / "navig_boom"
    pkg.mkdir()
    monkeypatch.setattr(doc, "_editable_mappings", lambda: [("navig_boom", str(pkg))])

    def boom(name):
        raise AttributeError("broken finder")

    monkeypatch.setattr(importlib.util, "find_spec", boom)
    (finding,) = doc._editable_findings()
    assert finding[0] == "navig_boom"
    assert "AttributeError" in finding[1]


def test_a_healthy_mapping_reports_one_ok_row(tmp_path, monkeypatch):
    pkg = tmp_path / "navig_ok"
    pkg.mkdir()
    monkeypatch.setattr(doc, "_editable_mappings", lambda: [("navig_ok", str(pkg))])
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    (row,) = doc.check_editable_installs()
    assert row[1] is True
    assert "all resolve" in row.detail


def test_foreign_editables_get_existence_checks_only(tmp_path, monkeypatch):
    """A third-party editable must never be probed through the import system."""
    pkg = tmp_path / "third" / "mod"
    pkg.mkdir(parents=True)
    monkeypatch.setattr(doc, "_editable_mappings", lambda: [("third_party_mod", str(pkg))])

    def must_not_run(name):
        raise AssertionError(f"find_spec must not be asked about foreign modules: {name}")

    monkeypatch.setattr(importlib.util, "find_spec", must_not_run)
    assert doc._editable_findings() == []


def test_zero_editables_is_a_healthy_row_not_a_failure(monkeypatch):
    monkeypatch.setattr(doc, "_editable_mappings", lambda: [])
    (row,) = doc.check_editable_installs()
    assert row[1] is True
    assert "regular install" in row.detail
