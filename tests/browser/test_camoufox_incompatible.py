"""`auto` must stop choosing Camoufox once its driver has crashed — and start again.

Camoufox declares an **unpinned** ``playwright`` dependency, so pip is free to pair
a Juggler protocol with a Firefox build it does not speak. When they disagree the
Node driver dies mid-navigation:

    Error: Assertion error
        at FFPage._onWebSocketOpened (...coreBundle.js:44292:9)

which takes the whole driver process down and surfaces to Python as *"Connection
closed while reading from the driver"*. Measured on the operator's machine:
playwright 1.61.0 (Juggler for firefox revision 1532) against Camoufox's Firefox
135.0.1-beta.24.

`best_login_engine()` tested only "is the package importable", so an incompatible
pair launched a browser that died on **every** login — a raw Node stack trace, a
fallback, and the cost of the launch, each time.

The fix deliberately REMEMBERS a crash rather than predicting compatibility from a
version matrix: a matrix is fragile and goes stale the moment either project
moves. The record is keyed to the exact package pair, so upgrading either one
retries Camoufox automatically — the suppression cannot outlive its cause.
"""
from __future__ import annotations

import pytest

from navig.browser import firefox as fx


@pytest.fixture
def record(tmp_path, monkeypatch):
    """Point the incompatibility record at a temp file, not the real config dir."""
    path = tmp_path / "browser" / "camoufox-incompatible.json"
    monkeypatch.setattr(fx, "_camoufox_incompat_file", lambda: path)
    monkeypatch.setattr(fx, "_engine_versions",
                        lambda: {"playwright": "1.61.0", "camoufox": "0.4.11"})
    monkeypatch.setattr(fx, "camoufox_available", lambda: True)
    return path


def test_camoufox_is_chosen_while_nothing_has_crashed(record):
    assert fx.camoufox_known_broken() is None
    assert fx.best_login_engine() == "camoufox"


def test_a_recorded_crash_takes_camoufox_out_of_auto(record):
    fx.record_camoufox_incompatible("the driver died mid-navigation")

    assert "driver died" in (fx.camoufox_known_broken() or "")
    assert fx.best_login_engine() == "firefox"


def test_upgrading_either_package_retries_camoufox(record, monkeypatch):
    """The whole point of keying on versions: the suppression must expire by
    itself when the incompatibility it describes might be gone."""
    fx.record_camoufox_incompatible("the driver died mid-navigation")
    assert fx.best_login_engine() == "firefox"

    monkeypatch.setattr(fx, "_engine_versions",
                        lambda: {"playwright": "1.62.0", "camoufox": "0.4.11"})
    assert fx.camoufox_known_broken() is None
    assert fx.best_login_engine() == "camoufox", "a playwright upgrade must re-test"

    monkeypatch.setattr(fx, "_engine_versions",
                        lambda: {"playwright": "1.61.0", "camoufox": "0.5.0"})
    assert fx.best_login_engine() == "camoufox", "a camoufox upgrade must re-test"


def test_an_unreadable_record_never_blocks_a_launch(record):
    """A health note must not become the reason nobody can log in."""
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text("{ not json", encoding="utf-8")

    assert fx.camoufox_known_broken() is None
    assert fx.best_login_engine() == "camoufox"


def test_recording_never_raises_when_the_path_is_unwritable(monkeypatch):
    def _boom():
        raise OSError("read-only filesystem")

    monkeypatch.setattr(fx, "_camoufox_incompat_file", _boom)
    fx.record_camoufox_incompatible("whatever")  # must not raise


def test_camoufox_absent_is_still_plain_firefox(record, monkeypatch):
    monkeypatch.setattr(fx, "camoufox_available", lambda: False)
    assert fx.best_login_engine() == "firefox"


def test_only_a_dead_DRIVER_records_not_a_closed_window():
    """The common case is the operator closing the login window. Disabling the
    stealthiest engine for that would be a real regression, so the recorder is
    gated on the driver-death string specifically.
    """
    from pathlib import Path

    source = Path(fx.__file__).parents[3] / "plugins" / "navig-download" / \
        "navig_download" / "commands" / "download.py"
    text = source.read_text(encoding="utf-8")
    assert 'if engine == "camoufox" and "reading from the driver" in str(exc).lower():' in text, (
        "the recorder is no longer gated on the driver-death signature — an "
        "ordinary window close would now disable camoufox"
    )
