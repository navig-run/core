"""Tests for navig.commands.body — the CLI over the metrics.csv body record.

Guards the ways a tracking CLI misleads:
  - reporting success for a measurement it refused to record;
  - accepting a mis-keyed weight into a record nobody can reconstruct;
  - emitting narration into a --json payload, so the consumer's parser breaks;
  - rendering an unknown trend as 0.0, which reads as "no change".

Hermetic: every invocation passes --space tmp_path, so nothing touches the
operator's real health space.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from navig.commands.body import body_app
from navig.spaces import body_metrics as bm

runner = CliRunner()

HEADER = "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n"


@pytest.fixture
def space(tmp_path):
    (tmp_path / "metrics.csv").write_text(HEADER, encoding="utf-8")
    return tmp_path


def _run(space, *args):
    return runner.invoke(body_app, [*args, "--space", str(space)])


def _row(space, day):
    _, rows = bm.read_metrics(space / "metrics.csv")
    return next((r for r in rows if r["date"] == day), None)


# ── log ───────────────────────────────────────────────────────────────────────


def test_log_records_a_weight(space):
    today = date.today().isoformat()
    result = _run(space, "log", "--weight", "88.2")
    assert result.exit_code == 0
    assert _row(space, today)["weight_kg"] == "88.2"


def test_log_records_several_metrics_at_once(space):
    today = date.today().isoformat()
    result = _run(space, "log", "--weight", "88.2", "--sleep", "7.5", "--mood", "8")
    assert result.exit_code == 0
    row = _row(space, today)
    assert (row["weight_kg"], row["sleep_hours"], row["mood_1_10"]) == ("88.2", "7.5", "8")


def test_log_with_nothing_to_record_fails_rather_than_claiming_success(space):
    result = _run(space, "log")
    assert result.exit_code == 1


def test_log_rejects_a_mood_outside_the_scale(space):
    assert _run(space, "log", "--mood", "11").exit_code != 0
    assert _row(space, date.today().isoformat()) is None


def test_log_blocks_an_implausible_jump_and_records_nothing(space):
    """A mis-keyed weight must not enter a record that cannot be reconstructed."""
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bm.upsert(space / "metrics.csv", yesterday, {"weight_kg": "88"})

    result = _run(space, "log", "--weight", "188")
    assert result.exit_code == 1
    assert _row(space, date.today().isoformat()) is None


def test_force_records_the_jump_deliberately(space):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bm.upsert(space / "metrics.csv", yesterday, {"weight_kg": "88"})

    result = _run(space, "log", "--weight", "94", "--force")
    assert result.exit_code == 0
    assert _row(space, date.today().isoformat())["weight_kg"] == "94"


def test_first_ever_weight_is_never_blocked_as_a_jump(space):
    result = _run(space, "log", "--weight", "88.2")
    assert result.exit_code == 0


def test_log_accepts_a_date(space):
    result = _run(space, "log", "--weight", "88.2", "--date", "2026-08-01")
    assert result.exit_code == 0
    assert _row(space, "2026-08-01")["weight_kg"] == "88.2"


def test_log_rejects_a_malformed_date(space):
    assert _run(space, "log", "--weight", "88", "--date", "01/08/2026").exit_code != 0


# ── --json contract ───────────────────────────────────────────────────────────


def test_log_json_is_exactly_one_parseable_document(space):
    result = _run(space, "log", "--weight", "88.2", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # raises if narration leaked into stdout
    assert payload["ok"] is True
    assert payload["recorded"]["weight_kg"] == "88.2"


def test_json_reports_a_refusal_as_ok_false(space):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    bm.upsert(space / "metrics.csv", yesterday, {"weight_kg": "88"})
    result = _run(space, "log", "--weight", "188", "--json")
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["needs_confirmation"] is True


def test_today_json_shape(space):
    _run(space, "log", "--weight", "88.2")
    payload = json.loads(_run(space, "today", "--json").stdout)
    assert payload["ok"] is True
    assert payload["week"]["latest"] == 88.2


def test_trend_json_shape(space):
    _run(space, "log", "--weight", "88.2")
    payload = json.loads(_run(space, "trend", "--json").stdout)
    assert payload["ok"] is True
    assert payload["recorded"] == 1


# ── views ─────────────────────────────────────────────────────────────────────


def test_today_on_an_empty_space_says_so_without_crashing(tmp_path):
    result = runner.invoke(body_app, ["today", "--space", str(tmp_path)])
    assert result.exit_code == 0


def test_today_shows_the_recorded_weight(space):
    _run(space, "log", "--weight", "88.2")
    result = _run(space, "today")
    assert result.exit_code == 0
    assert "88.2" in result.stdout


def test_trend_with_no_data_does_not_pretend_to_have_any(space):
    result = _run(space, "trend")
    assert result.exit_code == 0
    assert "No weight_kg recorded" in result.stdout


# ── the clinician line — neutral, and only when it applies ────────────────────


def _seed_two_weeks(space, previous_kg, current_kg):
    path = space / "metrics.csv"
    end = date.today()
    for i in range(7):
        bm.upsert(path, (end - timedelta(days=13 - i)).isoformat(),
                  {"weight_kg": str(previous_kg)})
    for i in range(7):
        bm.upsert(path, (end - timedelta(days=6 - i)).isoformat(),
                  {"weight_kg": str(current_kg)})


def test_fast_loss_surfaces_a_neutral_appointment_line(space):
    _seed_two_weeks(space, 92, 88)          # -4 kg in a week
    result = _run(space, "trend")
    assert "next appointment" in result.stdout


def test_fast_loss_line_does_not_diagnose_or_advise(space):
    """The space forbids diagnosing, prescribing, or suggesting a dose."""
    _seed_two_weeks(space, 92, 88)
    out = _run(space, "trend").stdout.lower()
    assert "next appointment" in out  # the line IS present — we are checking its wording
    for forbidden in ("dose", "diagnos", "you should stop", "reduce your"):
        assert forbidden not in out, f"clinical language leaked: {forbidden!r}"


def test_ordinary_loss_does_not_trigger_the_line(space):
    _seed_two_weeks(space, 88.4, 88.0)      # -0.4 kg in a week
    assert "next appointment" not in _run(space, "trend").stdout


def test_gaining_weight_never_triggers_the_loss_line(space):
    _seed_two_weeks(space, 88, 92)
    assert "next appointment" not in _run(space, "trend").stdout


# ── export ────────────────────────────────────────────────────────────────────


def test_export_writes_a_dated_summary(space):
    _run(space, "log", "--weight", "88.2")
    result = _run(space, "export")
    assert result.exit_code == 0
    written = list((space / "out").glob("body-*.md"))
    assert len(written) == 1
    text = written[0].read_text(encoding="utf-8")
    assert "88.2" in text
    assert "Not a medical document" in text


def test_export_honours_an_explicit_destination(space, tmp_path):
    _run(space, "log", "--weight", "88.2")
    target = tmp_path / "elsewhere" / "report.md"
    assert _run(space, "export", "--out", str(target)).exit_code == 0
    assert target.exists()


def test_export_never_prints_a_unitless_dash_with_a_unit(space):
    """`— kg` is not an unknown measurement, it is a nonsense one.

    This document is read at an appointment, so an absent number must read as
    absent — and say WHY, rather than leaving a dash the reader has to interpret.
    """
    _run(space, "log", "--weight", "88.2")          # one reading, so no prior window
    _run(space, "export")
    text = next((space / "out").glob("body-*.md")).read_text(encoding="utf-8")
    assert "— kg" not in text
    assert "no earlier window" in text


def test_export_header_and_table_agree_on_formatting(space):
    """123 in the table and 123.0 in the header is one number rendered two ways."""
    _run(space, "log", "--weight", "123")
    _run(space, "export")
    text = next((space / "out").glob("body-*.md")).read_text(encoding="utf-8")
    assert "**123 kg**" in text
    assert "123.0" not in text
