"""Retention sweep — the production caller the two dead cleanup helpers were missing.

Before this module, `cleanup_old_sessions` and `cleanup_spillover` existed with documented
retention windows and NO caller: a live install's ``sessions/`` reached 5,395 files (oldest
June) and ``tmp/tool_spillover`` kept files past its 1-hour TTL.

The owner's rule is **delete → trash, never unlink**, so these tests pin the recoverability
properties, not just "it prunes":

* pruned records land in ``<trash>/retention-<stamp>/`` (source gone, copy present) with a
  ``manifest.json`` mapping originals → trash paths;
* a record is pruned only when its AGE SIGNALS ARE ALL OLD (recorded last_active AND the
  newest companion mtime);
* a session loaded in memory / passed as ``keep`` survives;
* an unreadable record survives (its age cannot be verified);
* a non-session ``*.json`` in the sessions dir survives (the sweep only owns session records);
* a failed move leaves the source untouched and is reported as such, never counted;
* the sweep records an incident with its counts and logs a one-line summary;
* the sweep is inert under pytest unless explicitly forced, and inert when disabled;
* windows are clamped so a 0/negative config value can never mean "prune everything".
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from navig.agent import retention
from navig.agent.retention import (
    RetentionReport,
    new_trash_root,
    run_retention,
    sweep_gateway_sessions,
)
from navig.agent.session_store import cleanup_old_sessions

WINDOW_DAYS = 30


class _Config(dict):
    """Minimal config stand-in: the knob reader only needs ``get(key, default)``."""

    def get(self, key, default=None):  # noqa: D102 - dict-like, explicit for clarity
        return dict.get(self, key, default)


@pytest.fixture(autouse=True)
def _isolated_trash(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """No sweep in this module may ever write into the operator's real trash."""
    monkeypatch.setenv("NAVIG_TRASH_DIR", str(tmp_path / "operator-trash"))
    monkeypatch.delenv(retention.FORCE_ENV, raising=False)
    yield


def _age(path: Path, days: float) -> None:
    stamp = time.time() - days * 86_400
    os.utime(path, (stamp, stamp))


def _pair(directory: Path, sid: str, *, active_days: float, file_days: float | None = None):
    """Create an agent-store session pair whose recorded/managed age is *active_days*."""
    meta = directory / f"{sid}.meta.json"
    jsonl = directory / f"{sid}.jsonl"
    meta.write_text(
        json.dumps(
            {
                "session_id": sid,
                "last_active": time.time() - active_days * 86_400,
                "turn_count": 1,
            }
        ),
        encoding="utf-8",
    )
    jsonl.write_text('{"role":"user","content":"x"}\n', encoding="utf-8")
    _age(meta, active_days if file_days is None else file_days)
    _age(jsonl, active_days if file_days is None else file_days)
    return meta, jsonl


def _single(directory: Path, key: str, *, days: float) -> Path:
    """Create a gateway SessionManager-style ``<key>.json`` session aged *days*."""
    path = directory / f"{key}.json"
    path.write_text(json.dumps({"key": key, "messages": []}), encoding="utf-8")
    _age(path, days)
    return path


# ── helper-level behaviour ────────────────────────────────────────────────────


def test_old_session_pairs_are_moved_to_trash_and_recoverable(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    trash = tmp_path / "trash"
    moved: dict[str, str] = {}
    old_meta, old_jsonl = _pair(sessions, "old", active_days=WINDOW_DAYS + 10)
    fresh_meta, fresh_jsonl = _pair(sessions, "fresh", active_days=1)

    removed = cleanup_old_sessions(
        WINDOW_DAYS, base_dir=sessions, trash_root=trash, moved=moved
    )

    assert removed == 1
    assert not old_meta.exists() and not old_jsonl.exists(), "source records must be moved out"
    assert (trash / "old.meta.json").is_file()
    assert (trash / "old.jsonl").is_file()
    assert str(old_meta) in moved and str(old_jsonl) in moved
    assert fresh_meta.exists() and fresh_jsonl.exists()


def test_a_session_being_written_right_now_is_not_pruned(tmp_path: Path) -> None:
    """The recorded timestamp may be stale; a moving file means the session is ACTIVE."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    meta, jsonl = _pair(
        sessions, "active", active_days=WINDOW_DAYS + 10, file_days=WINDOW_DAYS + 10
    )
    _age(meta, 0)  # the meta was just refreshed by an append
    _age(jsonl, 0)

    removed = cleanup_old_sessions(
        WINDOW_DAYS, base_dir=sessions, trash_root=tmp_path / "trash"
    )
    assert removed == 0
    assert meta.exists() and jsonl.exists()


def test_keep_ids_are_never_pruned(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    meta, jsonl = _pair(sessions, "pinned", active_days=WINDOW_DAYS + 10)

    removed = cleanup_old_sessions(
        WINDOW_DAYS, base_dir=sessions, keep=["pinned"], trash_root=tmp_path / "trash"
    )
    assert removed == 0
    assert meta.exists() and jsonl.exists()


def test_unreadable_meta_is_skipped_not_moved(tmp_path: Path) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    broken = sessions / "broken.meta.json"
    broken.write_text("{not json", encoding="utf-8")
    _age(broken, WINDOW_DAYS + 10)

    assert cleanup_old_sessions(
        WINDOW_DAYS, base_dir=sessions, trash_root=tmp_path / "trash"
    ) == 0
    assert broken.exists(), "a record we cannot read must never be moved"


def test_gateway_singles_move_to_trash_but_agent_sidecars_and_other_json_stay(
    tmp_path: Path,
) -> None:
    trash = tmp_path / "trash"
    stale = _single(tmp_path, "telegram_1", days=WINDOW_DAYS + 10)
    fresh = _single(tmp_path, "telegram_2", days=1)
    sidecar = _single(tmp_path, "agent_1.meta", days=WINDOW_DAYS + 10)  # *.meta.json
    unrelated = tmp_path / "notes.json"
    unrelated.write_text('{"not": "a session"}', encoding="utf-8")
    _age(unrelated, WINDOW_DAYS + 10)

    removed = sweep_gateway_sessions(
        tmp_path, max_age_days=WINDOW_DAYS, trash_root=trash
    )

    assert removed == 1
    assert not stale.exists()
    assert (trash / stale.name).is_file()
    assert fresh.exists()
    assert sidecar.exists(), "agent-store *.meta.json is cleanup_old_sessions' business"
    assert unrelated.exists(), "a non-session *.json is not ours to prune"


def test_active_gateway_session_is_protected_by_raw_and_sanitized_key(tmp_path: Path) -> None:
    sanitized = _single(tmp_path, "telegram_123", days=WINDOW_DAYS + 10)
    raw = _single(tmp_path, "main_agent", days=WINDOW_DAYS + 10)

    removed = sweep_gateway_sessions(
        tmp_path,
        max_age_days=WINDOW_DAYS,
        keep_keys=["telegram:123", "main/agent"],
        trash_root=tmp_path / "trash",
    )

    assert removed == 0
    assert sanitized.exists() and raw.exists()


def test_a_failed_move_leaves_the_record_and_is_reported(tmp_path: Path, monkeypatch) -> None:
    """A locked/undeliverable record must stay put and never be counted as pruned."""
    stale = _single(tmp_path, "locked", days=WINDOW_DAYS + 10)

    import navig.core.trash as trash_mod

    monkeypatch.setattr(trash_mod, "move_to_trash", lambda path, root: None)
    report = RetentionReport(trash_dir=str(tmp_path / "trash"))
    removed = sweep_gateway_sessions(
        tmp_path, max_age_days=WINDOW_DAYS, trash_root=tmp_path / "trash", report=report
    )

    assert removed == 0
    assert report.failed == 1
    assert stale.exists()


def test_partial_pair_move_is_counted_and_the_failed_file_is_reported(
    tmp_path: Path, monkeypatch
) -> None:
    """P3: if only one half of a pair moves, the session is pruned but the failure is visible."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _meta, jsonl = _pair(sessions, "old", active_days=WINDOW_DAYS + 10)

    import navig.core.trash as trash_mod

    real_move = trash_mod.move_to_trash

    def _flaky(path, root):
        if path.suffix == ".jsonl":
            return None  # e.g. the handle is still open
        return real_move(path, root)

    monkeypatch.setattr(trash_mod, "move_to_trash", _flaky)

    failed: list[str] = []
    removed = cleanup_old_sessions(
        WINDOW_DAYS,
        base_dir=sessions,
        trash_root=tmp_path / "trash",
        failed=failed,
    )

    assert removed == 1
    assert failed == [str(jsonl)]
    assert (tmp_path / "trash" / "old.meta.json").is_file()
    assert jsonl.exists(), "the failed file stays exactly where it was"


def test_run_retention_reports_partial_move_failures(tmp_path: Path, monkeypatch) -> None:
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _pair(sessions, "old", active_days=WINDOW_DAYS + 10)

    import navig.core.trash as trash_mod

    real_move = trash_mod.move_to_trash

    def _flaky(path, root):
        if path.suffix == ".jsonl":
            return None
        return real_move(path, root)

    monkeypatch.setattr(trash_mod, "move_to_trash", _flaky)

    report = run_retention(
        sessions_dir=sessions,
        gateway_sessions_dir=sessions,
        trash_root=tmp_path / "trash",
        config=_Config(),
        force=True,
    )

    assert report.sessions_removed == 1
    assert report.failed == 1
    assert "could not be moved" in report.summary()


def test_pytest_guard_makes_the_sweep_a_no_op_unless_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(retention.FORCE_ENV, raising=False)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    meta, _jsonl = _pair(sessions, "old", active_days=WINDOW_DAYS + 10)
    trash = tmp_path / "trash"

    report = run_retention(
        sessions_dir=sessions,
        gateway_sessions_dir=sessions,
        trash_root=trash,
        config=_Config(),
    )
    assert report == RetentionReport(skipped_reason="pytest")
    assert meta.exists(), "a test run must never prune operator history"
    assert not trash.exists()

    forced = run_retention(
        sessions_dir=sessions,
        gateway_sessions_dir=sessions,
        trash_root=trash,
        config=_Config(),
        force=True,
    )
    assert forced.sessions_removed == 1
    assert not meta.exists()
    assert (trash / "old.meta.json").is_file()


def test_env_force_switch_works_like_the_keyword(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(retention.FORCE_ENV, "1")
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _pair(sessions, "old", active_days=WINDOW_DAYS + 10)

    report = run_retention(
        sessions_dir=sessions,
        gateway_sessions_dir=sessions,
        trash_root=tmp_path / "trash",
        config=_Config(),
    )

    assert report.sessions_removed == 1


def test_disabled_knob_does_no_filesystem_work(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv(retention.FORCE_ENV, raising=False)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    _pair(sessions, "old", active_days=WINDOW_DAYS + 10)
    stale_spill = tmp_path / "spill" / "s.txt"
    stale_spill.parent.mkdir()
    stale_spill.write_text("x", encoding="utf-8")
    _age(stale_spill, 100)
    trash = tmp_path / "trash"

    report = run_retention(
        sessions_dir=sessions,
        gateway_sessions_dir=sessions,
        spillover_dir=stale_spill.parent,
        trash_root=trash,
        config=_Config({"retention.enabled": "false"}),
        force=True,
    )

    assert report.removed == 0
    assert report.skipped_reason
    assert stale_spill.exists()
    assert not trash.exists()


def test_window_is_clamped_so_zero_cannot_mean_prune_everything(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(retention.FORCE_ENV, raising=False)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    twelve_hours_old = _single(sessions, "recent", days=0.5)

    report = run_retention(
        sessions_dir=sessions,
        gateway_sessions_dir=sessions,
        trash_root=tmp_path / "trash",
        config=_Config({"retention.sessions_days": 0}),
        force=True,
    )

    assert report.sessions_removed == 0
    assert twelve_hours_old.exists(), "0/negative windows clamp to 1 day, never to 'all'"


def test_spillover_ttl_is_enforced_and_files_are_recoverable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(retention.FORCE_ENV, raising=False)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    spill = tmp_path / "spill"
    spill.mkdir()
    stale = spill / "old.txt"
    fresh = spill / "new.txt"
    for path, days in ((stale, 40), (fresh, 1)):
        path.write_text("x", encoding="utf-8")
        _age(path, days)
    trash = tmp_path / "trash"

    report = run_retention(
        sessions_dir=sessions,
        gateway_sessions_dir=sessions,
        spillover_dir=spill,
        trash_root=trash,
        config=_Config({"retention.spillover_days": 14}),
        force=True,
    )

    assert report.spillover_removed == 1
    assert not stale.exists()
    assert (trash / "old.txt").is_file(), "pruned spillover must be recoverable"
    assert fresh.exists()


def test_end_to_end_sweep_covers_both_formats_trash_manifest_and_incident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.delenv(retention.FORCE_ENV, raising=False)
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    agent_meta, agent_jsonl = _pair(sessions, "agent_old", active_days=WINDOW_DAYS + 10)
    _pair(sessions, "agent_fresh", active_days=1)
    stale_gateway = _single(sessions, "telegram_old", days=WINDOW_DAYS + 10)
    _single(sessions, "telegram_fresh", days=1)
    pinned = _single(sessions, "heartbeat_active", days=WINDOW_DAYS + 10)
    spill = tmp_path / "spill"
    spill.mkdir()
    stale_spill = spill / "s.txt"
    stale_spill.write_text("x", encoding="utf-8")
    _age(stale_spill, 40)
    trash = tmp_path / "trash"

    recorded: list[tuple[str, dict]] = []

    import navig.core.incidents as incidents_mod

    monkeypatch.setattr(
        incidents_mod, "record", lambda event, **data: recorded.append((event, data))
    )

    with caplog.at_level("INFO", logger="navig.agent.retention"):
        report = run_retention(
            sessions_dir=sessions,
            gateway_sessions_dir=sessions,
            spillover_dir=spill,
            trash_root=trash,
            config=_Config(),
            keep_session_keys=["heartbeat_active"],
            force=True,
        )

    assert report.sessions_removed == 2  # one agent pair + one gateway single
    assert report.spillover_removed == 1
    assert str(report.trash_dir) == str(trash)

    # Sources are gone from the live dirs, every record is present in the trash.
    assert not agent_meta.exists() and not agent_jsonl.exists()
    assert not stale_gateway.exists() and not stale_spill.exists()
    assert (trash / "agent_old.meta.json").is_file()
    assert (trash / "agent_old.jsonl").is_file()
    assert (trash / stale_gateway.name).is_file()
    assert (trash / "s.txt").is_file()
    assert pinned.exists(), "a protected session must survive the sweep"

    # Manifest records the originals so a recovery is mechanical.
    manifest = json.loads((trash / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sessions"] == 2 and manifest["spillover"] == 1
    assert any(str(agent_meta) == original for original in manifest["files"])

    # Incident carries the counts; one INFO summary names the trash dir.
    assert recorded and recorded[0][0] == incidents_mod.RETENTION_SWEPT
    assert recorded[0][1]["sessions"] == 2
    assert recorded[0][1]["spillover"] == 1
    assert any("moved 2 session file(s)" in message for message in caplog.messages)


def test_default_trash_root_is_the_config_trash_dir(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "brain"))
    monkeypatch.setenv("NAVIG_TRASH_DIR", str(tmp_path / "custom-trash"))

    root = new_trash_root(now=1_700_000_000)

    expected_stamp = time.strftime("%Y%m%dT%H%M%S", time.localtime(1_700_000_000))
    assert root.parent == tmp_path / "custom-trash"
    assert root.name == f"{retention.TRASH_PREFIX}{expected_stamp}"
