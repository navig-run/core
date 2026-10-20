"""Two plugins cannot silently fight over one `navig <verb>`.

Entry-point discovery used to be ``out[ep.name] = ep``: whichever distribution importlib
listed LAST took the command, with no trace — so which plugin answered `navig audio`
could change with install order. Ownership is now deterministic and reported.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from navig.cli import registration as R
from navig.commands import doctor


def _ep(name: str, value: str, dist: str | None) -> SimpleNamespace:
    return SimpleNamespace(name=name, value=value, dist=SimpleNamespace(name=dist) if dist else None)


@pytest.fixture(autouse=True)
def _fresh_ownership(monkeypatch):
    monkeypatch.setattr(R, "_COMMAND_OWNERSHIP", {"collisions": {}, "shadowed": {}, "error": None})
    monkeypatch.setattr(R, "_ENTRY_POINT_CMDS", None)


class _Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def test_two_plugins_one_verb_is_a_recorded_collision_with_a_stable_winner() -> None:
    a = _ep("audio", "navig_audio.commands.audio:audio_app", "navig-audio")
    b = _ep("audio", "navig_sound.cli:app", "navig-sound")
    log, h = logging.getLogger(R.__name__), _Collect()
    log.addHandler(h)
    try:
        first = R._resolve_command_owners([a, b])
        second = R._resolve_command_owners([b, a])  # importlib order must not matter
    finally:
        log.removeHandler(h)
    assert first["audio"] is a and second["audio"] is a
    assert R._COMMAND_OWNERSHIP["collisions"] == {"audio": ["navig-audio", "navig-sound"]}
    assert any("navig-audio" in m and "navig-sound" in m for m in h.messages), h.messages


def test_the_same_declaration_seen_twice_is_one_owner_not_a_collision() -> None:
    a = _ep("video", "navig_video.commands.video:video_app", "navig-video")
    twin = _ep("video", "navig_video.commands.video:video_app", "navig-video")
    out = R._resolve_command_owners([a, twin])
    assert out["video"] is a
    assert R._COMMAND_OWNERSHIP["collisions"] == {}


def test_a_plugin_declaring_a_core_verb_is_reported_as_shadowed() -> None:
    core_name = next(iter(R._EXTERNAL_CMD_MAP))
    R._resolve_command_owners([_ep(core_name, "rogue.cli:app", "navig-rogue")])
    assert R._COMMAND_OWNERSHIP["shadowed"] == {core_name: "navig-rogue"}


def test_owner_falls_back_to_the_module_when_the_dist_is_unknown() -> None:
    R._resolve_command_owners([_ep("x", "pkg_one.cli:app", None), _ep("x", "pkg_two.cli:app", None)])
    assert R._COMMAND_OWNERSHIP["collisions"] == {"x": ["pkg_one", "pkg_two"]}


def test_report_on_this_install_has_no_collisions() -> None:
    """The real environment: first-party plugins must not collide with each other."""
    rep = R.command_ownership_report()
    assert rep["error"] is None
    assert rep["collisions"] == {}, rep["collisions"]


# ── navig doctor ──────────────────────────────────────────────────────────────


def _report(monkeypatch, **fields):
    base = {"commands": 3, "collisions": {}, "shadowed": {}, "error": None}
    base.update(fields)
    monkeypatch.setattr(R, "command_ownership_report", lambda: base)


def test_doctor_is_green_only_when_every_verb_has_one_owner(monkeypatch) -> None:
    _report(monkeypatch)
    [(icon, ok, text)] = doctor.check_command_ownership()
    assert ok is True and "3 plugin command(s)" in text


def test_doctor_warns_on_a_collision_naming_both_plugins(monkeypatch) -> None:
    _report(monkeypatch, collisions={"audio": ["navig-audio", "navig-sound"]})
    [(icon, ok, text)] = doctor.check_command_ownership()
    assert ok is False and icon == doctor._WARN
    assert "navig-audio" in text and "navig-sound" in text


def test_doctor_is_never_green_when_discovery_failed(monkeypatch) -> None:
    _report(monkeypatch, error="metadata unreadable")
    [(icon, ok, _text)] = doctor.check_command_ownership()
    assert ok is False and icon == doctor._WARN


def test_doctor_reports_a_shadowed_core_verb(monkeypatch) -> None:
    _report(monkeypatch, shadowed={"generate": "navig-rogue"})
    [(_icon, ok, text)] = doctor.check_command_ownership()
    assert ok is False and "navig-rogue" in text
