from navig.perf.profiler import detect_regressions
import pytest

pytestmark = pytest.mark.integration


def test_detect_regressions_handles_missing_ts_key():
    samples = [
        {"cmd": "db list", "elapsed_ms": 100},
        {"cmd": "db list", "ts": 2, "elapsed_ms": 150, "top_fns": [{"fn": "slow_fn"}]},
    ]

    regressions = detect_regressions(samples)
    assert len(regressions) == 1
    assert regressions[0]["cmd"] == "db list"


def test_detect_regressions_ignores_non_dict_rows():
    samples = [
        "bad-row",
        {"cmd": "host list", "ts": 1, "elapsed_ms": 50},
        {"cmd": "host list", "ts": 2, "elapsed_ms": 75},
    ]

    regressions = detect_regressions(samples)
    assert len(regressions) == 1
    assert regressions[0]["cmd"] == "host list"
