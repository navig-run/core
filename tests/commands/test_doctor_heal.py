"""``navig doctor --heal [--dry-run] [--json]`` — the CLI face of observe→repair.

Contract under test:

- ``--heal`` collects the structured report, lists every failing check with its
  mapped remediation (or "no automatic remediation"), executes SAFE ones,
  re-collects, and prints before/after;
- ``--heal --dry-run`` executes nothing at all;
- report-only (safe=False) remediations are never executed, dry-run or not;
- ``--heal --json`` emits exactly one parseable JSON document;
- exit code parity: 0 only when the FINAL report is fully green;
- ``--dry-run`` without ``--heal`` is a usage error;
- plain ``navig doctor`` / ``navig doctor --json`` behaviour is unchanged by
  the collect_report() extraction (the shared seam).
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from navig.commands import doctor
from navig.commands.doctor import _check, doctor_app
from navig.selfheal import doctor_remediation as dr
from tests.commands._doctor_checks import stub_all_checks as _stub_all_checks

runner = CliRunner()

# The house table style is tested at a real terminal width (the default
# captured-console width of 80 truncates the wrappable Result column away).
_WIDE = {"COLUMNS": "160"}







def _no_settle(monkeypatch):
    monkeypatch.setattr(dr, "_SETTLE_SECONDS", 0)


# ── the shared seam itself ────────────────────────────────────────────────────


def test_collect_report_returns_the_exact_json_payload(monkeypatch):
    """collect_report() is the same dict --json prints — one seam, two callers."""
    _stub_all_checks(
        monkeypatch,
        check_config=[_check("Config file", True, "valid YAML")],
        check_gateway=[_check("Gateway", False, "no response", warn=True)],
    )

    report = doctor.collect_report()
    cli_payload = json.loads(runner.invoke(doctor_app, ["--json"]).stdout)

    # Identical except the generation timestamp.
    report.pop("generated_at")
    cli_payload.pop("generated_at")
    assert report == cli_payload


# ── --heal --dry-run ──────────────────────────────────────────────────────────


def test_heal_dry_run_lists_failing_checks_and_executes_nothing(monkeypatch):
    _stub_all_checks(
        monkeypatch,
        check_gateway=[_check("Gateway", False, "No response at 127.0.0.1:1", warn=True)],
        check_storage=[_check("Disk Space", False, "only 0.5GB free")],
    )
    _no_settle(monkeypatch)
    calls: list[str] = []
    monkeypatch.setitem(dr._ACTIONS, "start_daemon", lambda: calls.append("ran") or (True, "ok"))

    result = runner.invoke(doctor_app, ["--heal", "--dry-run"], env=_WIDE)

    assert calls == []  # nothing executed
    assert "Gateway" in result.stdout
    assert "Disk Space" in result.stdout
    assert "no automatic remediation" in result.stdout
    assert result.exit_code == 1  # the install is still failing


def test_dry_run_without_heal_is_a_usage_error(monkeypatch):
    _stub_all_checks(monkeypatch)
    result = runner.invoke(doctor_app, ["--dry-run"])
    assert result.exit_code != 0
    assert "--heal" in result.output


# ── --heal executes safe fixes and re-collects ───────────────────────────────


def test_heal_executes_safe_fix_recollects_and_reports_before_after(monkeypatch):
    """The full loop: failing gateway → start-daemon runs → after-report green."""
    _no_settle(monkeypatch)
    _stub_all_checks(
        monkeypatch,
        check_gateway=[_check("Gateway", False, "No response", warn=True)],
    )

    def _fake_start():
        # The "fix": subsequent collection sees a healthy gateway.
        monkeypatch.setattr(
            doctor, "check_gateway", lambda *a, **k: [_check("Gateway", True, "Responding")]
        )
        return True, "daemon running (pid=7)"

    monkeypatch.setitem(dr._ACTIONS, "start_daemon", _fake_start)

    result = runner.invoke(doctor_app, ["--heal"], env=_WIDE)

    assert "before:" in result.stdout and "after:" in result.stdout
    assert "healed" in result.stdout
    assert result.exit_code == 0  # the FINAL report is green


def test_heal_that_does_not_fix_reports_still_failing_and_exits_1(monkeypatch):
    _no_settle(monkeypatch)
    _stub_all_checks(
        monkeypatch,
        check_gateway=[_check("Gateway", False, "No response", warn=True)],
    )
    monkeypatch.setitem(dr._ACTIONS, "start_daemon", lambda: (False, "refused"))

    result = runner.invoke(doctor_app, ["--heal"], env=_WIDE)

    assert "still failing" in result.stdout or "failed" in result.stdout
    assert result.exit_code == 1


def test_unsafe_remediation_stays_report_only_without_dry_run(monkeypatch):
    """A wedged event processor prescribes a restart but never performs one."""
    _no_settle(monkeypatch)
    _stub_all_checks(
        monkeypatch,
        check_event_processor=[_check("Event processor", False, "NOT RUNNING — events piling up")],
    )
    calls: list[str] = []
    monkeypatch.setitem(dr._ACTIONS, "start_daemon", lambda: calls.append("ran") or (True, "ok"))

    result = runner.invoke(doctor_app, ["--heal"], env=_WIDE)

    assert calls == []
    assert "report-only" in result.stdout
    assert "navig service restart" in result.stdout
    assert result.exit_code == 1


def test_all_green_heal_has_nothing_to_do(monkeypatch):
    _stub_all_checks(monkeypatch, check_config=[_check("Config file", True, "valid")])
    result = runner.invoke(doctor_app, ["--heal"], env=_WIDE)
    assert "nothing to heal" in result.stdout.lower()
    assert result.exit_code == 0


# ── --heal --json ─────────────────────────────────────────────────────────────


def test_heal_json_is_one_parseable_document_with_actions_and_reports(monkeypatch):
    _no_settle(monkeypatch)
    _stub_all_checks(
        monkeypatch,
        check_gateway=[_check("Gateway", False, "No response", warn=True)],
    )

    def _fake_start():
        monkeypatch.setattr(
            doctor, "check_gateway", lambda *a, **k: [_check("Gateway", True, "Responding")]
        )
        return True, "daemon running"

    monkeypatch.setitem(dr._ACTIONS, "start_daemon", _fake_start)

    result = runner.invoke(doctor_app, ["--heal", "--json"])
    payload = json.loads(result.stdout)  # stdout is nothing but the document

    assert set(payload) == {"ok", "dry_run", "actions", "before", "after", "report"}
    assert payload["dry_run"] is False
    assert payload["ok"] is True
    (action,) = payload["actions"]
    assert action["label"] == "Gateway"
    assert action["remediation"] == "start-daemon"
    assert action["safe"] is True
    assert action["executed"] is True
    assert action["healed"] is True
    # before failing, after green — both are the plain summary triples.
    assert payload["before"]["warnings"] == 1
    assert payload["after"]["failed"] == 0 and payload["after"]["warnings"] == 0
    # the full final report rides along for downstream consumers.
    assert payload["report"]["ok"] is True
    assert result.exit_code == 0


def test_heal_json_stays_parseable_when_the_action_narrates(monkeypatch):
    """The one above passes with a SILENT stub, which is not what real actions do:
    `_start_daemon` prints on every path ("Daemon already running…", "Starting…",
    "Daemon started"). `execute()` used to run UNGUARDED between two quiet=-guarded
    collect_report() calls, so that narration landed on stdout ahead of the JSON and
    `json.loads` failed — exactly on the runs where healing actually did something."""
    _no_settle(monkeypatch)
    _stub_all_checks(
        monkeypatch,
        check_gateway=[_check("Gateway", False, "No response", warn=True)],
    )

    def _noisy_start():
        from navig import console_helper as ch

        ch.info("Starting NAVIG daemon…")  # what the real remediation does
        monkeypatch.setattr(
            doctor, "check_gateway", lambda *a, **k: [_check("Gateway", True, "Responding")]
        )
        return True, "daemon running"

    monkeypatch.setitem(dr._ACTIONS, "start_daemon", _noisy_start)

    result = runner.invoke(doctor_app, ["--heal", "--json"])

    payload = json.loads(result.stdout)  # pre-fix: JSONDecodeError
    assert payload["ok"] is True
    assert payload["actions"][0]["executed"] is True
    assert "Starting NAVIG daemon" not in result.stdout  # narration was diverted


def test_heal_json_dry_run_has_null_after(monkeypatch):
    _no_settle(monkeypatch)
    _stub_all_checks(
        monkeypatch,
        check_gateway=[_check("Gateway", False, "No response", warn=True)],
    )

    result = runner.invoke(doctor_app, ["--heal", "--json", "--dry-run"])
    payload = json.loads(result.stdout)

    assert payload["dry_run"] is True
    assert payload["after"] is None
    assert payload["actions"][0]["executed"] is False
    assert result.exit_code == 1
