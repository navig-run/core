"""`ConfigSingleton` degraded an unreadable config to `{}` and then persisted it.

The previous rounds of this class (45d9bebbf, bd1c68530, b46425ace) each closed a
store and each ended by saying `core/shared_config.py` "already has its own refusal".
That was asserted from the documentation, not from the file, and it was WRONG — the
documented guard lives in `ConfigManager` (`config.py`), and `ConfigSingleton` reaches
the same files without going through it.

TWO exposures, and the second is the sharper one:

* PROJECT — `_refresh_project_data` caught every failure into `self._project_data = {}`.
  `set_active_host(host, scope="project")` / `set_active_app` / `save()` then call
  `_save_project()`, which `atomic_write_yaml`s that dict over `.navig/config.yaml`.
  One transient lock and the project's whole config becomes a single key.

* GLOBAL — `_load`'s bootstrap fallback did the same to `self._global_data`, and
  `_save_global` writes `~/.navig/config.yaml` through `atomic_write_yaml` DIRECTLY.
  That is a back door around `ConfigManager._on_disk_config_is_populated`, the guard
  added specifically to stop an empty config being written over a populated one. So
  `enable_plugin()` after one blip would persist `{}` plus one key over every global
  setting.

A third defect made the project half STICKY rather than momentary: the cache keys were
assigned BEFORE the read, so after a failure the next `force=False` call matched the
same path+mtime, skipped re-reading, and kept the empty copy — for the life of a
PROCESS-WIDE SINGLETON. They are now written only after a read that succeeded.

The fault is injected at `Path.read_text` / `builtins.open`, the seams BOTH the old
and new code use. Injecting at `read_text_retrying` (which only the fixed code calls)
would give a fault the old code cannot hit — a test that proves the fix works while
never demonstrating the bug, which is how an earlier test in this class ended up
passing in both directions.
"""

from __future__ import annotations

import builtins
import pathlib

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def captured_incidents(monkeypatch):
    from navig.core import incidents

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda event, **d: seen.append((event, d)))
    return seen


def _lock_path(monkeypatch, filename):
    """Make ONE file unreadable at every seam either version might use."""
    real_read_text = pathlib.Path.read_text
    real_open = builtins.open

    def _locked_read_text(self, *a, **kw):
        if self.name == filename:
            raise PermissionError(13, "The process cannot access the file")
        return real_read_text(self, *a, **kw)

    def _locked_open(file, *a, **kw):
        if str(file).endswith(filename):
            raise PermissionError(13, "The process cannot access the file")
        return real_open(file, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", _locked_read_text)
    monkeypatch.setattr(builtins, "open", _locked_open)

    def _unlock():
        monkeypatch.setattr(pathlib.Path, "read_text", real_read_text)
        monkeypatch.setattr(builtins, "open", real_open)

    return _unlock


def _fresh_singleton(monkeypatch):
    """A real, uninitialised ConfigSingleton — it caches itself on the CLASS."""
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setattr(ConfigSingleton, "_instance", None)
    return ConfigSingleton()


def _project_dir(tmp_path, monkeypatch, body: str):
    """A project whose `.navig/config.yaml` holds real settings; cwd points at it."""
    proj = tmp_path / "proj"
    (proj / ".navig").mkdir(parents=True)
    (proj / ".navig" / "config.yaml").write_text(body, encoding="utf-8")
    monkeypatch.chdir(proj)
    return proj / ".navig" / "config.yaml"


_PROJECT_BODY = "active_host: prod\nexecution:\n  mode: safe\nnotes: keep me\n"


def test_an_unreadable_project_config_is_not_replaced_by_one_key(
    tmp_path, monkeypatch, captured_incidents
):
    cfg_path = _project_dir(tmp_path, monkeypatch, _PROJECT_BODY)

    unlock = _lock_path(monkeypatch, "config.yaml")
    singleton = _fresh_singleton(monkeypatch)
    singleton.set_active_host("staging", scope="project")
    unlock()

    assert cfg_path.read_text(encoding="utf-8") == _PROJECT_BODY, (
        "one transient lock replaced the entire project config with a single key"
    )
    assert "store_write_refused" in [e for e, _ in captured_incidents], (
        "the refusal was silent — the operator has no way to learn the write was dropped"
    )


def test_a_failed_project_read_is_retried_not_cached(tmp_path, monkeypatch):
    """The failure must not be STICKY.

    The cache keys were assigned before the read, so a failed load matched on the next
    `force=False` call and the empty copy survived for the life of the singleton.
    """
    _project_dir(tmp_path, monkeypatch, _PROJECT_BODY)

    unlock = _lock_path(monkeypatch, "config.yaml")
    singleton = _fresh_singleton(monkeypatch)
    assert singleton._project_load_failed is True
    unlock()

    # A plain refresh (force=False) must actually re-read now that the file is fine.
    singleton._refresh_project_data()
    assert singleton._project_load_failed is False, "the failure was cached, not retried"
    assert singleton._project_data.get("notes") == "keep me"


def test_project_config_still_saves_normally(tmp_path, monkeypatch, captured_incidents):
    """Anti-vacuity — refusing always would stop project config working at all."""
    cfg_path = _project_dir(tmp_path, monkeypatch, _PROJECT_BODY)

    singleton = _fresh_singleton(monkeypatch)
    singleton.set_active_host("staging", scope="project")

    body = cfg_path.read_text(encoding="utf-8")
    assert "staging" in body, "the change did not persist"
    assert "keep me" in body, "an unrelated project setting was dropped"
    assert captured_incidents == [], "a healthy save recorded an incident"


def test_a_project_config_that_is_absent_is_not_a_read_failure(tmp_path, monkeypatch):
    """Absent is not unreadable — a project with no `.navig/config.yaml` must still write."""
    proj = tmp_path / "bare"
    proj.mkdir()
    monkeypatch.chdir(proj)

    singleton = _fresh_singleton(monkeypatch)
    singleton.set_active_host("prod", scope="project")

    written = proj / ".navig" / "config.yaml"
    assert written.exists(), "a project with no config file could no longer create one"
    assert "prod" in written.read_text(encoding="utf-8")


def test_the_global_save_refuses_after_a_failed_bootstrap_read(tmp_path, monkeypatch):
    """`_save_global` writes the same file `ConfigManager` guards — directly.

    Driven at the flag rather than through the bootstrap fallback, because that branch
    only runs when ConfigManager itself is unavailable. What matters is the invariant:
    a global save while the load is known to have failed must not touch the file.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    (tmp_path / "cfg").mkdir()
    global_cfg = tmp_path / "cfg" / "config.yaml"
    original = "deck:\n  api_key: real-identity\nplugins:\n  enabled: true\n"
    global_cfg.write_text(original, encoding="utf-8")

    singleton = _fresh_singleton(monkeypatch)
    # Baseline AFTER construction: booting ConfigManager legitimately applies pending
    # config migrations and rewrites the file. Comparing across that would fail for a
    # reason that has nothing to do with the save under test.
    after_boot = global_cfg.read_text(encoding="utf-8")
    assert "real-identity" in after_boot, "the baseline itself lost the settings"

    singleton._global_load_failed = True
    singleton._global_data = {}
    singleton._save_global()

    assert global_cfg.read_text(encoding="utf-8") == after_boot, (
        "an empty global config was written over the operator's settings — the exact "
        "door ConfigManager's refusal exists to close, reopened by writing directly"
    )
