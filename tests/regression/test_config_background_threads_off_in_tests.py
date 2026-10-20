"""A config load starts no background thread inside the test suite.

``ConfigManager._load_global_config_cached`` starts daemon threads — a shadow re-read of
config.yaml (fast path) and deferred schema validation (slow path) — that outlive the call.
In a test process they ran on into LATER tests: the shadow re-read's ``config_load_failed``
incident landed in ``test_store_read_failure_is_not_a_wipe``'s exact assertion under
``-n auto``. ``core/tests/conftest.py`` turns them off; production keeps them.
"""

from __future__ import annotations

import threading

import navig.config as navig_config


def _threads_started_by_two_loads(monkeypatch, tmp_path) -> list[str]:
    """Slow path (no cache yet), then fast path (cache written by the first).

    Records every Thread.start() by name — deterministic, unlike counting live threads,
    which a quick daemon thread may already have left.
    """
    started: list[str] = []
    real_start = threading.Thread.start

    def _start(self):
        started.append(self.name)
        return real_start(self)

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    (tmp_path / "config.yaml").write_text("gateway:\n  port: 1\n", encoding="utf-8")
    monkeypatch.setattr(threading.Thread, "start", _start)
    cm = navig_config.ConfigManager()
    cm._load_global_config_cached()
    assert (tmp_path / ".config_cache.pkl").exists(), "the slow path must write the cache"
    cm._load_global_config_cached()
    return [n for n in started if n.startswith("navig-config-")]


def test_the_suite_runs_with_background_threads_off():
    assert navig_config.BACKGROUND_THREADS_ENABLED is False


def test_no_background_thread_from_a_config_load_in_tests(monkeypatch, tmp_path):
    assert _threads_started_by_two_loads(monkeypatch, tmp_path) == []


def test_production_still_starts_both(monkeypatch, tmp_path):
    monkeypatch.setattr(navig_config, "BACKGROUND_THREADS_ENABLED", True)
    started = _threads_started_by_two_loads(monkeypatch, tmp_path)
    assert sorted(started) == ["navig-config-shadow", "navig-config-validate"]
