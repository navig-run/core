"""Tests for navig.spaces.body_metrics — the metrics.csv body record.

Regression guards for the ways a body record silently loses data:
  - a failed read degrading to ``[]`` and the next write erasing the history;
  - a non-atomic write leaving a half-file after a crash;
  - a partial update blanking the columns it was not asked to touch;
  - a hand-added column being dropped on the next machine write;
  - a decimal COMMA ("87,4") being read as a different number, or rejected.

All tests are hermetic: every path is under tmp_path.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from navig.spaces import body_metrics as bm

HEADER = "date,weight_kg,body_fat_pct,resting_hr,sleep_hours,steps,mood_1_10,hrv,notes\n"


@pytest.fixture
def metrics(tmp_path):
    """An isolated metrics.csv seeded with one hand-written row."""
    path = tmp_path / "metrics.csv"
    path.write_text(
        HEADER + '2026-04-12,,,,7.5,,7,,"energy:7; morning check-in"\n',
        encoding="utf-8",
    )
    return path


# ── parsing ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("87.4", 87.4),
        ("87,4", 87.4),          # decimal comma — France and Russia both write this
        ("87.4 kg", 87.4),
        ("87,4 кг", 87.4),
        ("  88  ", 88.0),
        ("100", 100.0),
    ],
)
def test_parse_weight_accepts_real_input(text, expected):
    assert bm.parse_weight(text) == pytest.approx(expected)


@pytest.mark.parametrize("text", ["", "abc", "874", "8", "87.4.5", "-87", "kg"])
def test_parse_weight_rejects_rather_than_guessing(text):
    """A fabricated number in a body record is worse than a gap in it."""
    with pytest.raises(bm.WeightParseError):
        bm.parse_weight(text)


def test_comma_and_dot_are_the_same_number():
    assert bm.parse_weight("87,4") == bm.parse_weight("87.4")


def test_implausible_jump_only_flags_a_real_jump():
    assert bm.is_implausible_jump(95.0, 87.0) is True
    assert bm.is_implausible_jump(87.5, 87.0) is False
    # No previous reading is not a jump — the first weigh-in must not be blocked.
    assert bm.is_implausible_jump(87.0, None) is False


# ── read / write integrity ────────────────────────────────────────────────────


def test_missing_file_reads_as_empty_not_as_error(tmp_path):
    fieldnames, rows = bm.read_metrics(tmp_path / "nope.csv")
    assert rows == []
    assert fieldnames == bm.CANONICAL_FIELDS


def test_unreadable_file_raises_instead_of_looking_empty(metrics):
    """The whole point: 'unreadable' must never be indistinguishable from 'empty'.

    A read that quietly returns [] feeds a write that erases the record.
    """
    metrics.write_text("this is not,a valid metrics file\n", encoding="utf-8")
    with pytest.raises(bm.MetricsReadError):
        bm.read_metrics(metrics)


def test_write_refuses_to_shrink_the_record(metrics):
    for i in range(5):
        bm.upsert(metrics, f"2026-05-0{i + 1}", {"weight_kg": "88"})
    fieldnames, rows = bm.read_metrics(metrics)
    assert len(rows) == 6

    with pytest.raises(bm.MetricsReadError, match="refusing to write"):
        bm.write_metrics(metrics, fieldnames, rows[:3])

    # and the file is untouched by the refusal
    assert len(bm.read_metrics(metrics)[1]) == 6


def test_shrink_is_allowed_when_explicitly_intended(metrics):
    bm.upsert(metrics, "2026-05-01", {"weight_kg": "88"})
    fieldnames, rows = bm.read_metrics(metrics)
    bm.write_metrics(metrics, fieldnames, rows[:1], allow_shrink=True)
    assert len(bm.read_metrics(metrics)[1]) == 1


def test_write_is_atomic_no_temp_files_left_behind(metrics):
    bm.upsert(metrics, "2026-05-01", {"weight_kg": "88"})
    leftovers = [p.name for p in metrics.parent.iterdir() if p.name != "metrics.csv"]
    assert leftovers == []


# ── upsert semantics ──────────────────────────────────────────────────────────


def test_upsert_is_idempotent_by_date(metrics):
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88.2"})
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88.2"})
    rows = [r for r in bm.read_metrics(metrics)[1] if r["date"] == "2026-09-05"]
    assert len(rows) == 1


def test_upsert_returns_what_it_replaced(metrics):
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88.2"})
    previous = bm.upsert(metrics, "2026-09-05", {"weight_kg": "87.9"})
    assert previous == {"weight_kg": "88.2"}


def test_partial_upsert_does_not_blank_the_other_columns(metrics):
    """Logging a mood in the evening must not erase the morning's weight."""
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88.2"})
    bm.upsert(metrics, "2026-09-05", {"mood_1_10": "8"})
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == "2026-09-05")
    assert row["weight_kg"] == "88.2"
    assert row["mood_1_10"] == "8"


def test_hand_written_row_survives_a_machine_write(metrics):
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88.2"})
    row = next(r for r in bm.read_metrics(metrics)[1] if r["date"] == "2026-04-12")
    assert row["notes"] == "energy:7; morning check-in"
    assert row["sleep_hours"] == "7.5"


def test_unknown_column_added_by_hand_is_preserved(tmp_path):
    """A column this module has never heard of must not be silently dropped."""
    path = tmp_path / "metrics.csv"
    path.write_text("date,weight_kg,waist_cm\n2026-09-01,88,95\n", encoding="utf-8")
    bm.upsert(path, "2026-09-02", {"weight_kg": "87.8"})
    fieldnames, rows = bm.read_metrics(path)
    assert "waist_cm" in fieldnames
    assert next(r for r in rows if r["date"] == "2026-09-01")["waist_cm"] == "95"


def test_upsert_grows_the_header_for_a_new_field(metrics):
    bm.upsert(metrics, "2026-09-05", {"waist_cm": "95"})
    assert "waist_cm" in bm.read_metrics(metrics)[0]


def test_rows_stay_sorted_by_date(metrics):
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88"})
    bm.upsert(metrics, "2026-05-01", {"weight_kg": "90"})
    dates = [r["date"] for r in bm.read_metrics(metrics)[1]]
    assert dates == sorted(dates)


# ── analytics ─────────────────────────────────────────────────────────────────


@pytest.fixture
def week(tmp_path):
    """Fourteen days of weight, newest 2026-09-05, so trend has both windows."""
    path = tmp_path / "metrics.csv"
    path.write_text(HEADER, encoding="utf-8")
    end = date(2026, 9, 5)
    # previous week averages 89.0, current week averages 88.0
    for i in range(7):
        bm.upsert(path, (end - timedelta(days=13 - i)).isoformat(), {"weight_kg": "89"})
    for i in range(7):
        bm.upsert(path, (end - timedelta(days=6 - i)).isoformat(), {"weight_kg": "88"})
    return path


def test_moving_average_over_the_window(week):
    assert bm.moving_average(week, days=7, ending=date(2026, 9, 5)) == 88.0


def test_trend_compares_against_the_preceding_window(week):
    assert bm.trend(week, days=7, ending=date(2026, 9, 5)) == -1.0


def test_trend_is_none_not_zero_when_there_is_no_prior_window(metrics):
    """An unknown trend rendered as 0.0 reads as 'no change' — a different claim."""
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88"})
    assert bm.trend(metrics, days=7, ending=date(2026, 9, 5)) is None


def test_logged_ratio_counts_days_not_rows(week):
    assert bm.logged_ratio(week, days=7, ending=date(2026, 9, 5)) == (7, 7)


def test_latest_returns_the_most_recent_reading(week):
    day, value = bm.latest(week)
    assert (day, value) == (date(2026, 9, 5), 88.0)


def test_blank_and_junk_values_are_skipped_not_counted_as_zero(metrics):
    """The seeded April row has an EMPTY weight; it must not become 0.0 kg."""
    bm.upsert(metrics, "2026-09-05", {"weight_kg": "88"})
    assert [v for _, v in bm.series(metrics)] == [88.0]


def test_sparkline_is_flat_rather_than_dividing_by_zero():
    assert len(bm.sparkline([88.0, 88.0, 88.0])) == 3
    assert bm.sparkline([]) == ""
    assert len(bm.sparkline([87.0, 88.0, 89.0])) == 3


def test_summarize_shape(week):
    s = bm.summarize(week, ending=date(2026, 9, 5))
    assert s["latest"] == 88.0
    assert s["average"] == 88.0
    assert s["trend"] == -1.0
    assert s["recorded"] == 7
    assert len(s["points"]) == 7


# ── path resolution ───────────────────────────────────────────────────────────


def test_explicit_space_wins(tmp_path):
    assert bm.metrics_path(str(tmp_path)) == tmp_path / "metrics.csv"


def test_explicit_csv_path_is_used_verbatim(tmp_path):
    target = tmp_path / "elsewhere.csv"
    assert bm.metrics_path(str(target)) == target


def test_invocation_dir_wins_when_it_already_holds_the_file(tmp_path, monkeypatch):
    (tmp_path / "metrics.csv").write_text(HEADER, encoding="utf-8")
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    assert bm.metrics_path() == tmp_path / "metrics.csv"
