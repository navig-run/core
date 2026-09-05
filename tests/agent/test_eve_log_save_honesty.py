"""The evening log replied "✅ Logged" for entries it never wrote.

`_save` hand-rolled the atomic write and wrapped the whole thing in
`except Exception: logger.warning(...)`. So a failed write returned normally,
`save_shipped()` reported success, and the bot answered
**"✅ Logged: <the thing you typed>"** — while the journal on disk was unchanged
and the morning reminder had nothing to show.

Two leaks rode along: the `.tmp` file was left next to the journal on every
failure (and this module's own docstring says Windows AV/backup locks are the
*expected* failure here), and the hand-rolled writer had no fsync and no
transient-lock retry — both of which the canonical `atomic_write_json` has, and
which 29 other call sites already use.
"""

from __future__ import annotations

import json

import pytest

from navig.agent.proactive import eve_log


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    """Never touch the operator's real journal."""
    path = tmp_path / "engagement" / "eve_log.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(eve_log, "_log_path", lambda: path)
    return path


# ── a save that did not happen must not report success ────────────────────────


class TestSaveFailureIsLoud:
    def test_an_unwritable_journal_is_reported_not_swallowed(self, monkeypatch, tmp_path):
        """The discriminator, patching nothing on the module itself.

        The journal's parent is a regular FILE, so no writer can create the
        directory or open a temp file in it. The old `_save` caught that and
        returned normally — `save_shipped()` said nothing was wrong and the bot
        replied "✅ Logged". Any real failure must reach the caller.
        """
        blocker = tmp_path / "not-a-dir"
        blocker.write_text("x", encoding="utf-8")
        monkeypatch.setattr(eve_log, "_log_path", lambda: blocker / "eve_log.json")

        with pytest.raises(OSError):
            eve_log.save_shipped("shipped the auth refactor")

    def test_a_failed_write_raises(self, monkeypatch):
        def _boom(data, path, **kw):
            raise OSError("disk full")

        monkeypatch.setattr(eve_log, "atomic_write_json", _boom)

        with pytest.raises(OSError):
            eve_log.save_shipped("shipped the auth refactor")

    def test_priority_too(self, monkeypatch):
        def _boom(data, path, **kw):
            raise PermissionError("locked by antivirus")

        monkeypatch.setattr(eve_log, "atomic_write_json", _boom)

        with pytest.raises(PermissionError):
            eve_log.save_priority("ship the migration")

    def test_a_successful_save_is_readable_back(self, _isolated):
        eve_log.save_shipped("fixed the leak")
        eve_log.save_priority("ship it")

        assert eve_log.get_today()["shipped"] == "fixed the leak"
        assert eve_log.get_today()["priority"] == "ship it"
        on_disk = json.loads(_isolated.read_text(encoding="utf-8"))
        assert len(on_disk) == 1, "both writes must land in the same day's entry"


# ── it uses the canonical writer, not a hand-rolled one ───────────────────────


class TestUsesTheCanonicalWriter:
    def test_save_delegates_to_atomic_write_json(self, monkeypatch):
        """fsync + transient-lock retry + temp cleanup live there, not here."""
        seen: dict = {}

        def _capture(data, path, **kw):
            seen["data"] = data
            seen["path"] = path

        monkeypatch.setattr(eve_log, "atomic_write_json", _capture)
        eve_log.save_shipped("x")
        assert seen["data"], "nothing was handed to the writer"

    def test_no_hand_rolled_temp_file_is_left_behind(self, _isolated, monkeypatch):
        """The old writer left `<name>.tmp` next to the journal on every failure."""
        real = eve_log.atomic_write_json

        def _fail_once(data, path, **kw):
            raise OSError("transient")

        monkeypatch.setattr(eve_log, "atomic_write_json", _fail_once)
        with pytest.raises(OSError):
            eve_log.save_shipped("x")
        monkeypatch.setattr(eve_log, "atomic_write_json", real)

        leftovers = [p.name for p in _isolated.parent.iterdir() if p.name != _isolated.name]
        assert not leftovers, f"temp files left beside the journal: {leftovers}"


# ── trimming still works through the new writer ───────────────────────────────


def test_only_the_last_30_days_are_kept(_isolated, monkeypatch):
    for day in range(1, 41):
        eve_log.save_shipped(f"day {day}", date=f"2026-03-{day:02d}" if day <= 31 else f"2026-04-{day - 31:02d}")

    on_disk = json.loads(_isolated.read_text(encoding="utf-8"))
    assert len(on_disk) == eve_log._MAX_DAYS
    assert "2026-04-09" in on_disk, "the newest day must survive the trim"
