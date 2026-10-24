"""`navig doctor`'s Runtime Plugins section and its `--json` payload.

Two contracts:

* the section must not be able to look clean while a first-party plugin is missing
  (the class that hid navig-msstore from the live brain on 2026-10-20), and
* the per-plugin inventory rides `data` on a row — a key that appears only where a check
  actually supplies data, so every existing payload consumer keeps its exact shape.
"""

from __future__ import annotations

from typing import Any

import navig.commands.doctor as doctor

RECORD = {
    "package": "navig-demo",
    "plugin_id": "demo",
    "source_path": r"E:\repo\plugins\navig-demo",
    "state": "healthy",
    "activation": "verified",
    "daemon_interpreter": r"C:\venv\Scripts\pythonw.exe",
    "daemon_installed": True,
    "daemon_version": "1.0.0",
    "entry_points": {"navig.plugins": True, "navig.commands": False},
    "declares_entry_point": True,
    "package_files_present": True,
    "this_interpreter": "python",
    "this_installed": True,
    "this_version": "1.0.0",
    "repair": None,
}


def inventory(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "interpreter": "python",
        "daemon_interpreter": r"C:\venv\Scripts\pythonw.exe",
        "daemon_known": True,
        "root": r"E:\repo",
        "activation_evidence": True,
        "expected": 1,
        "counts": {"healthy": 1},
        "records": [RECORD],
    }
    base.update(over)
    return base


def test_a_missing_plugin_makes_the_section_fail(tmp_path, monkeypatch):
    from navig.plugins import runtime_inventory as module

    broken = dict(RECORD, state="not_installed", daemon_installed=False, repair="pip install -e …")
    inv = inventory(records=[broken], counts={"not_installed": 1}, activation_evidence=True)
    monkeypatch.setattr(module, "runtime_inventory", lambda *a, **k: inv)
    rows = doctor.check_runtime_inventory()
    summary = rows[0]
    assert summary[0] == doctor._WARN  # ⚠, never a green tick over a gap
    assert "not installed" in summary.detail
    assert summary.data["records"][0]["package"] == "navig-demo"


def test_all_healthy_is_green_and_carries_the_full_inventory(tmp_path, monkeypatch):
    from navig.plugins import runtime_inventory as module

    monkeypatch.setattr(module, "runtime_inventory", lambda *a, **k: inventory())
    rows = doctor.check_runtime_inventory()
    assert rows[0][0] == doctor._OK
    payload = rows[0].data
    assert payload is not None
    assert payload["daemon_known"] is True
    assert len(payload["records"]) == 1


def test_json_emits_data_only_where_a_check_supplies_it(monkeypatch):
    from navig.plugins import runtime_inventory as module

    monkeypatch.setattr(module, "runtime_inventory", lambda *a, **k: inventory())
    sections = [("Runtime Plugins", doctor.check_runtime_inventory())]
    # A plain row from another check, to prove the shape of rows without data is unchanged.
    sections.append(("Other", [doctor._check("plain row", True, "fine")]))
    payload = doctor._shape_report(sections)

    runtime_check = payload["sections"][0]["checks"][0]
    assert "data" in runtime_check and runtime_check["data"]["records"]
    plain_check = payload["sections"][1]["checks"][0]
    assert set(plain_check) == {"label", "ok", "warn", "detail"}  # no key appears by magic
    assert payload["summary"]["passed"] >= 1


def test_unreadable_runtime_is_reported_not_silently_skipped(monkeypatch):
    from navig.plugins import runtime_inventory as module

    def explode(*_a, **_k):
        raise RuntimeError("metadata unreadable")

    monkeypatch.setattr(module, "runtime_inventory", explode)
    rows = doctor.check_runtime_inventory()
    assert rows[0][0] == doctor._WARN
    assert "COULD NOT VERIFY" in rows[0].detail
