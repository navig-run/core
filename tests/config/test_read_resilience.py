"""A transient READ failure must not look like an empty file.

The write side of yaml_io already survives Windows' transient locks (an antivirus or
backup agent briefly holding the file) by retrying with back-off. The read side did not
— and every writer replaces config.yaml via ``os.replace``, so a reader that opens the
file *during* that replace can get a PermissionError / sharing violation.

That asymmetry is what produced the empty config: one blip → ``_load_global_config``
returned ``{}`` → the next save wrote that emptiness back over every setting the user
had. The wipe itself is now refused (test_config_wipe_guard), but the trigger has to go
too — otherwise the daemon still spends a whole boot pretending you have no settings.
"""

from __future__ import annotations

import builtins
from pathlib import Path

import pytest
import yaml

from navig.core import yaml_io

POPULATED = {"deck": {"api_key": "navig_the_installs_identity"}, "telegram": {"enabled": True}}


class _Flaky:
    """Counters for a patched Path.read_text (the function itself must be a plain
    function, or Python will not bind it as a method)."""

    def __init__(self, fail_times: int):
        self.remaining = fail_times
        self.calls = 0


def _patch_flaky_read(monkeypatch, fail_times: int, exc: Exception) -> _Flaky:
    """Make Path.read_text fail the first *fail_times* calls with *exc*.

    Mirrors what Windows raises when a reader opens a file mid-``os.replace``.
    """
    state = _Flaky(fail_times)
    real = Path.read_text

    def flaky(self, *a, **kw):
        state.calls += 1
        if state.remaining > 0:
            state.remaining -= 1
            raise exc
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky)
    return state


@pytest.fixture
def config_file(tmp_path) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(POPULATED), encoding="utf-8")
    return p


# ── the retry itself ─────────────────────────────────────────────────────────


def test_a_transient_lock_is_retried_not_swallowed(monkeypatch, config_file):
    """THE trigger. A sharing violation during another process's os.replace() must be
    ridden out, exactly as the write side already does."""
    flaky = _patch_flaky_read(monkeypatch, 2, PermissionError("[WinError 32] in use"))

    content = yaml_io.read_text_retrying(config_file)

    assert yaml.safe_load(content) == POPULATED, "the read must succeed once the lock clears"
    assert flaky.calls == 3, "it must actually retry, not succeed by luck on the first call"


def test_a_persistent_failure_RAISES_rather_than_reporting_emptiness(monkeypatch, config_file):
    """The whole disaster began by turning 'I cannot read this' into 'this is empty'.
    A failure that survives the retries must propagate, never masquerade as no-content."""
    _patch_flaky_read(monkeypatch, 99, PermissionError("locked"))

    with pytest.raises(OSError):
        yaml_io.read_text_retrying(config_file)


def test_safe_load_yaml_survives_a_transient_lock(monkeypatch, config_file):
    _patch_flaky_read(monkeypatch, 2, PermissionError("[WinError 32]"))

    assert yaml_io.safe_load_yaml(config_file) == POPULATED


def test_genuinely_invalid_yaml_is_still_None_and_is_not_retried(monkeypatch, tmp_path):
    """A syntax error is not transient — retrying it would just be slow. Only OS-level
    failures get the back-off."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("deck: {api_key: 'unterminated\n  bad: [", encoding="utf-8")

    calls = {"n": 0}
    real = Path.read_text

    def counting(slf, *a, **kw):
        calls["n"] += 1
        return real(slf, *a, **kw)

    monkeypatch.setattr(Path, "read_text", counting)

    assert yaml_io.safe_load_yaml(bad) is None
    assert calls["n"] == 1, "invalid YAML must not be retried"


# ── the config path that actually wiped ──────────────────────────────────────


def test_the_global_config_load_rides_out_a_transient_lock(monkeypatch, tmp_path):
    """End-to-end: the exact read that returned {} and got written back over everything.

    The lock is injected at BOTH read primitives on purpose. The old code read the file
    with a bare ``open()``; the fixed code goes through ``Path.read_text``. Patching only
    one of them would let this test pass against the broken code simply because the fault
    was never injected into the path it actually uses — a green test proving nothing.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(POPULATED), encoding="utf-8")

    state = _Flaky(2)
    real_read_text, real_open = Path.read_text, builtins.open
    exc = PermissionError("[WinError 32] file in use by another process")

    def _blip():
        """Fail the first 2 reads OF THIS FILE, then let it through."""
        if state.remaining > 0:
            state.remaining -= 1
            state.calls += 1
            raise exc

    def flaky_read_text(self, *a, **kw):
        if Path(self) == cfg_path:
            _blip()
        return real_read_text(self, *a, **kw)

    def flaky_open(file, *a, **kw):
        try:
            if Path(file) == cfg_path:
                _blip()
        except TypeError:  # a file-descriptor / non-path argument
            pass
        return real_open(file, *a, **kw)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    monkeypatch.setattr(builtins, "open", flaky_open)

    from navig.config import ConfigManager

    loaded = ConfigManager()._load_global_config(validate=False)

    assert state.calls > 0, "the fault must actually have been injected"
    assert loaded.get("deck", {}).get("api_key") == "navig_the_installs_identity", (
        "a transient lock must not present as an empty config"
    )


# ── load_yaml_for_update: the shared read-modify-write guard ──────────────────
#
# safe_load_yaml() collapses missing / empty / unreadable all to None, so any caller
# doing `safe_load_yaml(p) or {}` then writing back destroys a locked file. This is the
# one helper the direct callers (action, bridge, onboarding) delegate to: a genuinely
# blank/absent file is a fresh {}, but a file with content that won't read/parse is a
# REFUSAL (ConfigReadError), never a blank slate.


def test_load_for_update_missing_file_returns_default(tmp_path):
    assert yaml_io.load_yaml_for_update(tmp_path / "nope.yaml") == {}
    assert yaml_io.load_yaml_for_update(tmp_path / "nope.yaml", default={"seed": 1}) == {"seed": 1}


def test_load_for_update_empty_file_returns_default(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("", encoding="utf-8")
    assert yaml_io.load_yaml_for_update(p) == {}


def test_load_for_update_comments_only_is_blank_not_refused(tmp_path):
    """The size>0 heuristic would falsely REFUSE this; parsing correctly sees it as blank.
    A comments-only config has no data to protect, so returning {} is right."""
    p = tmp_path / "config.yaml"
    p.write_text("# just a comment\n\n", encoding="utf-8")
    assert yaml_io.load_yaml_for_update(p) == {}


def test_load_for_update_populated_file_returns_the_mapping(config_file):
    assert yaml_io.load_yaml_for_update(config_file) == POPULATED


def test_load_for_update_survives_a_transient_lock(monkeypatch, config_file):
    """A blip is retried underneath, not turned into a refusal."""
    _patch_flaky_read(monkeypatch, 2, PermissionError("[WinError 32]"))
    assert yaml_io.load_yaml_for_update(config_file) == POPULATED


def test_load_for_update_REFUSES_a_persistently_unreadable_file(monkeypatch, config_file):
    """THE guard: a lock that survives the retries must raise, never return {} for the
    caller to write back over real content."""
    _patch_flaky_read(monkeypatch, 99, PermissionError("locked"))
    with pytest.raises(yaml_io.ConfigReadError):
        yaml_io.load_yaml_for_update(config_file)


def test_load_for_update_REFUSES_invalid_yaml(tmp_path):
    """A file with bytes that won't parse is corrupt, not empty — never overwrite it."""
    p = tmp_path / "config.yaml"
    p.write_text("deck: {api_key: 'unterminated\n  bad: [", encoding="utf-8")
    with pytest.raises(yaml_io.ConfigReadError):
        yaml_io.load_yaml_for_update(p)


def test_load_for_update_REFUSES_a_non_mapping(tmp_path):
    """Valid YAML that isn't a dict (a list/scalar) is a corrupt config; a read-modify-
    write over it would destroy it, so refuse."""
    p = tmp_path / "config.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(yaml_io.ConfigReadError):
        yaml_io.load_yaml_for_update(p)
