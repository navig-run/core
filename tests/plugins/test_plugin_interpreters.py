"""`navig.plugins.interpreters` — navig in the OTHER Pythons on this machine.

Measured 2026-10-03: navig.exe and the daemon ran on 3.13 while the repo guard's hooks
imported navig under the PATH `python` (3.14). Repairing 3.13 left 3.14 with three
records below core's floor, and no check looked past the Python it ran in.

Every test here fakes the probe — no real interpreter is ever launched.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from navig.plugins import interpreters as itp

DIST = "navig-demo"
OTHER = r"C:\Py\Other\python.exe"


def _checkout(tmp_path: Path, *, source: str = "0.2.0", floor: str = "0.2.0") -> Path:
    root = tmp_path / "repo"
    (root / "core").mkdir(parents=True)
    plugin = root / "plugins" / DIST
    (plugin / "navig_demo").mkdir(parents=True)
    (plugin / "pyproject.toml").write_text(
        f'[project]\nname = "{DIST}"\nversion = "{source}"\n', encoding="utf-8"
    )
    (root / "core" / "pyproject.toml").write_text(
        '[project]\nname = "navig"\nversion = "3.0.0"\n'
        f'dependencies = ["{DIST}>={floor}"]\n\n'
        f'[tool.uv.sources]\n{DIST} = {{ path = "../plugins/{DIST}", editable = true }}\n',
        encoding="utf-8",
    )
    return root


# An editable record must point at a folder that EXISTS, or the dead-install check
# (rightly) flags it. This test directory is real on every machine.
EDITABLE = json.dumps({"url": Path(__file__).parent.as_uri(), "dir_info": {"editable": True}})
PLAIN = json.dumps({"url": "file:///C:/wheels/x.whl", "archive_info": {}})


def _probe_returning(monkeypatch, by_exe: dict[str, dict | None]) -> None:
    monkeypatch.setattr(itp, "_probe", lambda exe: by_exe.get(exe))


def _audit(root, monkeypatch, record, *, executable=OTHER):
    data = {"__executable__": executable, "navig": ["3.0.0", EDITABLE], DIST.lower(): record}
    _probe_returning(monkeypatch, {OTHER: data})
    return itp.audit_other_interpreters(root, interpreters=[OTHER])


# ── verdicts ─────────────────────────────────────────────────────────────────


def test_a_current_editable_install_is_ok(tmp_path, monkeypatch):
    (a,) = _audit(_checkout(tmp_path), monkeypatch, ["0.2.0", EDITABLE])
    assert a.ok and a.executable == OTHER


def test_an_old_record_is_stale_and_named_below_the_floor(tmp_path, monkeypatch):
    (a,) = _audit(_checkout(tmp_path), monkeypatch, ["0.1.0", EDITABLE])
    assert a.stale == ((DIST, "0.1.0", "0.2.0"),)
    assert a.below_floor == (DIST,)
    assert a.fix_paths == (f"plugins/{DIST}",)


def test_stale_within_the_floor_is_not_below_it(tmp_path, monkeypatch):
    root = _checkout(tmp_path, source="0.3.0", floor="0.1.0")
    (a,) = _audit(root, monkeypatch, ["0.2.0", EDITABLE])
    assert a.stale and a.below_floor == ()


def test_a_plain_install_of_a_declared_source_is_shadowed(tmp_path, monkeypatch):
    (a,) = _audit(_checkout(tmp_path), monkeypatch, ["0.2.0", PLAIN])
    assert a.shadowed == (DIST,) and not a.ok


# ── which interpreters count ─────────────────────────────────────────────────


def test_an_interpreter_without_navig_is_left_out(tmp_path, monkeypatch):
    _probe_returning(monkeypatch, {OTHER: {"__executable__": OTHER}})
    assert itp.audit_other_interpreters(_checkout(tmp_path), interpreters=[OTHER]) == []


def test_an_interpreter_that_fails_the_probe_is_left_out(tmp_path, monkeypatch):
    _probe_returning(monkeypatch, {OTHER: None})
    assert itp.audit_other_interpreters(_checkout(tmp_path), interpreters=[OTHER]) == []


def test_aliases_of_one_install_are_audited_once(tmp_path, monkeypatch):
    """WindowsApps python.EXE / python3.EXE start the same install as `py -3.14`."""
    data = {"__executable__": OTHER, "navig": ["3.0.0", EDITABLE]}
    aliases = [OTHER, r"C:\WindowsApps\python.EXE", r"C:\WindowsApps\python3.EXE"]
    _probe_returning(monkeypatch, {exe: dict(data) for exe in aliases})
    audits = itp.audit_other_interpreters(_checkout(tmp_path), interpreters=aliases)
    assert [a.executable for a in audits] == [OTHER]


def test_an_alias_of_the_running_interpreter_is_not_an_other(tmp_path, monkeypatch):
    alias = r"C:\WindowsApps\python.EXE"
    _probe_returning(
        monkeypatch, {alias: {"__executable__": sys.executable, "navig": ["3.0.0", EDITABLE]}}
    )
    assert itp.audit_other_interpreters(_checkout(tmp_path), interpreters=[alias]) == []


def test_nothing_to_compare_against_outside_a_checkout(tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    assert itp.audit_other_interpreters(bare, interpreters=[OTHER]) is None


# ── discovery ────────────────────────────────────────────────────────────────


def test_py_launcher_listing_is_parsed(monkeypatch, tmp_path):
    a = tmp_path / "a" / "python.exe"
    b = tmp_path / "b" / "python.exe"
    for exe in (a, b):
        exe.parent.mkdir(parents=True)
        exe.write_text("", encoding="utf-8")
    listing = f" -V:3.14 *        {a}\n -V:3.13          {b}\n -V:3.10          {tmp_path}/gone.exe\n"
    monkeypatch.setattr(itp.os, "name", "nt")
    monkeypatch.setattr(itp.shutil, "which", lambda n: "py" if n == "py" else str(a))
    monkeypatch.setattr(
        itp.subprocess,
        "run",
        lambda *args, **kw: subprocess.CompletedProcess(args, 0, stdout=listing, stderr=""),
    )
    # the default marker is stripped, a missing exe is dropped, PATH's copy is deduped
    assert itp.list_interpreters() == [str(a), str(b)]


# ── the doctor row ───────────────────────────────────────────────────────────


@pytest.fixture
def _row(monkeypatch):
    from navig.commands import doctor

    def render(audit):
        monkeypatch.setattr(itp, "audit_other_interpreters", lambda: [audit])
        (row,) = doctor.check_other_interpreters()
        return row, doctor

    return render


def test_below_the_floor_is_an_error_with_that_interpreters_own_fix(_row):
    row, doctor = _row(itp.InterpreterAudit(
        executable=OTHER, stale=((DIST, "0.1.0", "0.2.0"),), below_floor=(DIST,),
        fix_paths=(f"plugins/{DIST}",),
    ))
    assert row[0] == doctor._ERR
    assert f"{OTHER} -m pip install -e plugins/{DIST}" in row.detail


def test_stale_within_the_floor_is_a_warning(_row):
    row, doctor = _row(itp.InterpreterAudit(
        executable=OTHER, stale=((DIST, "0.2.0", "0.3.0"),), fix_paths=(f"plugins/{DIST}",),
    ))
    assert row[0] == doctor._WARN


def test_a_current_interpreter_is_a_tick(_row):
    row, doctor = _row(itp.InterpreterAudit(executable=OTHER))
    assert row[0] == doctor._OK and row[1] is True


def test_a_dead_install_in_another_python_is_found(tmp_path, monkeypatch):
    """Not declared anywhere — a retired package is exactly what nothing declares."""
    gone = json.dumps({"url": (tmp_path / "gone").as_uri(), "dir_info": {"editable": True}})
    data = {"__executable__": OTHER, "navig": ["3.0.0", EDITABLE], "navig-paperwork": ["0.1.0", gone]}
    _probe_returning(monkeypatch, {OTHER: data})
    (a,) = itp.audit_other_interpreters(_checkout(tmp_path), interpreters=[OTHER])
    assert [d for d, _ in a.dead] == ["navig-paperwork"] and not a.ok


def test_a_dead_install_is_an_error_whose_fix_uninstalls_it(_row):
    row, doctor = _row(itp.InterpreterAudit(executable=OTHER, dead=(("navig-paperwork", r"E:\gone"),)))
    assert row[0] == doctor._ERR
    assert f"{OTHER} -m pip uninstall -y navig-paperwork" in row.detail
    assert "pip install -e" not in row.detail, "nothing to reinstall when only a record is dead"
