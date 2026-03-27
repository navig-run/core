from __future__ import annotations

from pathlib import Path

import click
import pytest

from navig.commands import init as init_mod


def test_legacy_documents_migration_moves_files_and_removes_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "home" / ".navig"
    source_dir = tmp_path / "home" / "Documents" / ".navig"
    log_dir = tmp_path / "logs"

    (source_dir / "ai_context").mkdir(parents=True)
    (source_dir / "ai_context" / "state.json").write_text('{"ok": true}', encoding="utf-8")

    monkeypatch.setattr(init_mod, "_legacy_documents_config_dir", lambda: source_dir)
    monkeypatch.setattr(init_mod, "_local_log_dir", lambda: log_dir)

    target_dir.mkdir(parents=True, exist_ok=True)
    init_mod._migrate_legacy_documents_dir(target_dir)

    assert (target_dir / "ai_context" / "state.json").read_text(encoding="utf-8") == '{"ok": true}'
    assert not source_dir.exists()
    assert (log_dir / "init.log").exists()


def test_legacy_documents_migration_conflict_keeps_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target_dir = tmp_path / "home" / ".navig"
    source_dir = tmp_path / "home" / "Documents" / ".navig"
    log_dir = tmp_path / "logs"

    (source_dir / "ai_context").mkdir(parents=True)
    (target_dir / "ai_context").mkdir(parents=True)
    (source_dir / "ai_context" / "state.json").write_text('{"value": 1}', encoding="utf-8")
    (target_dir / "ai_context" / "state.json").write_text('{"value": 2}', encoding="utf-8")

    monkeypatch.setattr(init_mod, "_legacy_documents_config_dir", lambda: source_dir)
    monkeypatch.setattr(init_mod, "_local_log_dir", lambda: log_dir)

    with pytest.raises(click.exceptions.Exit):
        init_mod._migrate_legacy_documents_dir(target_dir)

    assert source_dir.exists()
    assert (source_dir / "ai_context" / "state.json").exists()
    assert "legacy migration failed" in (log_dir / "init.log").read_text(encoding="utf-8")


def test_run_init_aborts_cleanly_on_directory_permission_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(init_mod, "_DEFAULT_NAVIG_DIR", tmp_path / ".navig")
    monkeypatch.setattr(init_mod, "_write_init_log", lambda _message: None)

    def _raise_permission_error() -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(init_mod, "_ensure_dirs", _raise_permission_error)

    with pytest.raises(click.exceptions.Exit):
        init_mod.run_init(dry_run=True, no_genesis=True, name="test-node")


def test_windows_runtime_layout_migration_flattens_nested_platformdirs_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_root = tmp_path / "local" / "navig" / "NAVIG"
    canonical_logs = tmp_path / "local" / "navig" / "logs"
    canonical_state = tmp_path / "local" / "navig" / "state"
    canonical_cache = tmp_path / "local" / "navig" / "cache"
    log_dir = tmp_path / "logs"

    (legacy_root / "Logs").mkdir(parents=True)
    (legacy_root / "memory").mkdir(parents=True)
    (legacy_root / "Cache").mkdir(parents=True)
    (legacy_root / "Logs" / "debug.log").write_text("debug", encoding="utf-8")
    (legacy_root / "memory" / "key_facts.db").write_text("facts", encoding="utf-8")
    (legacy_root / "Cache" / "tmp.json").write_text("cache", encoding="utf-8")

    monkeypatch.setattr(init_mod, "_legacy_windows_platformdirs_root", lambda: legacy_root)
    monkeypatch.setattr(init_mod, "_local_log_dir", lambda: canonical_logs)
    monkeypatch.setattr(init_mod, "_local_state_dir", lambda: canonical_state)
    monkeypatch.setattr(init_mod, "_cache_dir", lambda: canonical_cache)
    monkeypatch.setattr(init_mod, "_write_init_log", lambda _message: None)

    init_mod._migrate_legacy_windows_runtime_layout()

    assert (canonical_logs / "debug.log").read_text(encoding="utf-8") == "debug"
    assert (canonical_state / "memory" / "key_facts.db").read_text(encoding="utf-8") == "facts"
    assert (canonical_cache / "tmp.json").read_text(encoding="utf-8") == "cache"
    assert not legacy_root.exists()


def test_windows_runtime_layout_migration_appends_conflicting_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    legacy_root = tmp_path / "local" / "navig" / "NAVIG"
    canonical_logs = tmp_path / "local" / "navig" / "logs"
    canonical_state = tmp_path / "local" / "navig" / "state"
    canonical_cache = tmp_path / "local" / "navig" / "cache"

    (legacy_root / "Logs").mkdir(parents=True)
    canonical_logs.mkdir(parents=True, exist_ok=True)
    (legacy_root / "Logs" / "debug.log").write_text("new log", encoding="utf-8")
    (canonical_logs / "debug.log").write_text("old log", encoding="utf-8")

    monkeypatch.setattr(init_mod, "_legacy_windows_platformdirs_root", lambda: legacy_root)
    monkeypatch.setattr(init_mod, "_local_log_dir", lambda: canonical_logs)
    monkeypatch.setattr(init_mod, "_local_state_dir", lambda: canonical_state)
    monkeypatch.setattr(init_mod, "_cache_dir", lambda: canonical_cache)
    monkeypatch.setattr(init_mod, "_write_init_log", lambda _message: None)

    init_mod._migrate_legacy_windows_runtime_layout()

    merged = (canonical_logs / "debug.log").read_text(encoding="utf-8")
    assert "old log" in merged
    assert "new log" in merged
