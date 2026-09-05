"""Regression: `navig update`'s post-update doctor step must surface real warnings.

`_step_doctor` used to `from navig.commands.doctor import run_doctor_checks` — a
name that has never existed. Every run therefore hit the bare ``except`` and
reported "no issues", so a broken config after an update looked perfectly
healthy. The fix consumes the real programmatic seam, ``collect_report`` (the
exact checks ``navig doctor --json`` runs), and surfaces every non-ok row.
"""

from __future__ import annotations

import navig.commands.doctor as doctor
from navig.commands.update import _step_doctor


def _report(sections):
    """Shape a minimal report matching doctor.collect_report's contract."""
    return {
        "ok": all(c["ok"] for _n, checks in sections for c in checks),
        "sections": [
            {"name": name, "ok": all(c["ok"] for c in checks), "checks": checks}
            for name, checks in sections
        ],
        "summary": {"passed": 0, "warnings": 0, "failed": 0},
        "version": "0.0.0",
        "generated_at": "2026-01-01T00:00:00+00:00",
    }


def test_step_doctor_surfaces_warn_and_fail_rows(monkeypatch):
    """Both ⚠ warn and ✗ fail rows are collected; ✓ ok rows are ignored."""
    captured = {}

    def fake_collect_report(*args, **kwargs):
        captured.update(kwargs)
        return _report(
            [
                (
                    "Config",
                    [
                        {"label": "config.yaml", "ok": True, "warn": False, "detail": "loaded"},
                        {"label": "deck.api_key", "ok": False, "warn": True, "detail": "missing"},
                    ],
                ),
                (
                    "Gateway",
                    [
                        {"label": "gateway", "ok": False, "warn": False, "detail": "unreachable"},
                    ],
                ),
            ]
        )

    monkeypatch.setattr(doctor, "collect_report", fake_collect_report)

    result = _step_doctor()

    assert result.label == "Config doctor"
    assert result.ok is True  # the step itself never fails — it only reports
    assert result.warnings == ["deck.api_key: missing", "gateway: unreachable"]
    assert result.note == "2 warning(s)"
    # skip_deps: the update step already refreshed the package; re-probing pip
    # here would be redundant and slow.
    assert captured.get("skip_deps") is True


def test_step_doctor_reports_clean_when_all_ok(monkeypatch):
    def fake_collect_report(*args, **kwargs):
        return _report(
            [("Config", [{"label": "config.yaml", "ok": True, "warn": False, "detail": "loaded"}])]
        )

    monkeypatch.setattr(doctor, "collect_report", fake_collect_report)

    result = _step_doctor()

    assert result.warnings == []
    assert result.note == "no issues"


def test_step_doctor_is_best_effort_on_failure(monkeypatch):
    """A doctor that raises must not break the update flow."""

    def boom(*args, **kwargs):
        raise RuntimeError("doctor exploded")

    monkeypatch.setattr(doctor, "collect_report", boom)

    result = _step_doctor()

    assert result.ok is True
    assert result.note == "no issues"


def test_step_doctor_skips_named_sections(monkeypatch):
    """A section named in skip_sections is dropped from the warnings — the update flow uses
    this to suppress the transiently-misleading "Daemon" freshness row right before it
    restarts the daemon (a "STALE — Restart to load: navig update" warning DURING a navig
    update is self-contradictory)."""

    def fake_collect_report(*args, **kwargs):
        return _report(
            [
                (
                    "Daemon",
                    [{"label": "Daemon freshness", "ok": False, "warn": True,
                      "detail": "STALE — restart to load"}],
                ),
                (
                    "Config",
                    [{"label": "config.yaml", "ok": False, "warn": True, "detail": "broken"}],
                ),
            ]
        )

    monkeypatch.setattr(doctor, "collect_report", fake_collect_report)

    # Default: both surfaced.
    assert _step_doctor().warnings == [
        "Daemon freshness: STALE — restart to load",
        "config.yaml: broken",
    ]
    # skip "Daemon": only the Config warning remains; freshness is suppressed.
    skipped = _step_doctor(skip_sections={"Daemon"})
    assert skipped.warnings == ["config.yaml: broken"]
    assert not any("Daemon" in w for w in skipped.warnings)


def test_daemon_section_name_matches_the_update_skip():
    """update.py suppresses the freshness row mid-reload by skipping the section named
    'Daemon'. Pin that name so renaming the doctor section can't silently un-skip it (which
    would make `navig update` cry 'STALE — run navig update' again)."""
    import inspect

    src = inspect.getsource(doctor._collect_sections)
    assert '"Daemon"' in src, (
        "doctor's freshness section must be named 'Daemon' — update.py's _step_doctor "
        "skip_sections={'Daemon'} depends on it"
    )


def test_the_real_seam_exists_not_the_phantom():
    """Pin the fix to the real API: collect_report is the seam; the old
    run_doctor_checks name was never real and must not creep back."""
    assert callable(doctor.collect_report)
    assert not hasattr(doctor, "run_doctor_checks")
