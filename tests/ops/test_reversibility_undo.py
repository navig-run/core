"""Tests for reversibility labels + `navig undo` + `navig ledger show` (T-068).

Covers navig.reversibility (the green/yellow/red taxonomy incl. the
sensitive-key rules), the label the recorder stamps on every appended entry,
the undo engine's safety contract (drift detection, double-undo protection,
secret refusal), the config-set capture seam, both CLI surfaces, and — the
T-067 interlock — that labeled entries and undo entries ride the hash chain
without invalidating mixed legacy+labeled ledgers.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recorder(tmp_path: Path, max_entries: int = 10_000):
    from navig.operation_recorder import OperationRecorder

    return OperationRecorder(history_dir=tmp_path, max_entries=max_entries)


def _ledger(tmp_path: Path) -> Path:
    return tmp_path / "operations.jsonl"


def _entries(path: Path) -> list[dict]:
    return [
        json.loads(ln)
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]


def _record_config_change(
    recorder,
    key: str = "log_level",
    old_value="INFO",
    new_value="DEBUG",
    old_exists: bool = True,
    **overrides,
):
    """Record a green config_change the way the config-set seam does."""
    from navig.operation_recorder import OperationRecord, OperationStatus, OperationType

    undo_data = {
        "key": key,
        "old_value": old_value,
        "old_exists": old_exists,
        "new_value": new_value,
        "scope": "global",
    }
    undo_data.update(overrides.pop("undo_data_extra", {}))
    record = OperationRecord(
        command=f"navig config set {key} {new_value}",
        operation_type=OperationType.CONFIG_CHANGE,
        status=OperationStatus.SUCCESS,
        args={"key": key},
        undo_data=undo_data,
        **overrides,
    )
    recorder.record(record)
    return record


class _StubConfigManager:
    """The narrow ConfigManager surface the engine + config seam touch."""

    def __init__(self, cfg: dict | None = None, hosts: tuple[str, ...] = ()):
        self.cfg = cfg if cfg is not None else {}
        self.hosts = set(hosts)
        self.saves = 0
        self.active_host_calls: list[str] = []

    @property
    def global_config(self) -> dict:
        return self.cfg

    def refresh_global_config(self) -> dict:
        return self.cfg

    def _save_global_config(self, cfg: dict) -> None:
        self.cfg = cfg
        self.saves += 1

    def update_global_config(self, patch_dict: dict) -> None:
        self.cfg.update(patch_dict)
        self.saves += 1

    def host_exists(self, name: str) -> bool:
        return name in self.hosts

    def set_active_host(self, host: str, local: bool = False) -> None:
        self.active_host_calls.append(host)
        self.cfg["active_host"] = host


# ---------------------------------------------------------------------------
# Taxonomy — navig.reversibility
# ---------------------------------------------------------------------------


class TestClassify:
    def test_config_change_with_undo_data_is_green(self):
        from navig.reversibility import Reversibility, classify

        label = classify("config_change", {"key": "log_level", "old_value": "INFO"})
        assert label is Reversibility.GREEN

    def test_config_change_without_undo_data_is_yellow(self):
        from navig.reversibility import Reversibility, classify

        assert classify("config_change") is Reversibility.YELLOW

    def test_red_defaults(self):
        from navig.reversibility import Reversibility, classify

        assert classify("remote_command") is Reversibility.RED
        assert classify("local_command") is Reversibility.RED
        assert classify("database_query") is Reversibility.RED
        # unknown/future type — irreversible until proven otherwise
        assert classify("quantum_flux") is Reversibility.RED

    def test_undo_tag_caps_at_yellow(self):
        """An undo entry never becomes an undo candidate (double-undo guard)."""
        from navig.reversibility import Reversibility, classify

        label = classify("config_change", {"key": "x", "old_value": 1}, tags=["undo"])
        assert label is Reversibility.YELLOW

    def test_sensitive_undo_data_caps_at_yellow(self):
        from navig.reversibility import Reversibility, classify

        label = classify("config_change", {"key": "openai_api_key", "sensitive": True})
        assert label is Reversibility.YELLOW

    def test_undo_data_on_unsupported_type_stays_yellow(self):
        """Green means `navig undo` CAN replay it — never promised elsewhere."""
        from navig.reversibility import Reversibility, classify

        assert classify("file_upload", {"remote": "/x"}) is Reversibility.YELLOW

    def test_file_ops_without_backup_are_red(self):
        from navig.reversibility import Reversibility, classify

        assert classify("file_delete") is Reversibility.RED
        assert classify("file_modify") is Reversibility.RED

    def test_sensitive_key_detection(self):
        from navig.reversibility import is_sensitive_config_key

        assert is_sensitive_config_key("openai_api_key")
        assert is_sensitive_config_key("email.password")
        assert is_sensitive_config_key("telegram.bot_token")
        assert is_sensitive_config_key("myservice.client_secret")
        assert not is_sensitive_config_key("email.smtp_host")
        assert not is_sensitive_config_key("log_level")
        assert not is_sensitive_config_key("execution.mode")


# ---------------------------------------------------------------------------
# Recorder — every appended entry carries an honest label
# ---------------------------------------------------------------------------


class TestRecorderLabels:
    def test_plain_command_is_labeled_red(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="navig run ls"))
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["reversibility"] == "red"
        assert entry["reversible"] is False

    def test_config_change_with_undo_data_is_labeled_green(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record_config_change(rec)
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["reversibility"] == "green"
        assert entry["reversible"] is True

    def test_preset_label_wins(self, tmp_path):
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="x", reversibility="yellow"))
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["reversibility"] == "yellow"

    def test_reversible_flag_is_synced_to_label(self, tmp_path):
        """A caller claiming reversible=True without undo_data gets corrected."""
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="x", reversible=True))
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["reversibility"] == "red"
        assert entry["reversible"] is False

    def test_from_dict_tolerates_future_fields(self):
        from navig.operation_recorder import OperationRecord

        data = {
            "id": "op-x",
            "operation_type": "other",
            "status": "success",
            "hash": "sha256:aa",
            "field_from_the_future": {"nested": True},
        }
        record = OperationRecord.from_dict(data)
        assert record.id == "op-x"
        assert "field_from_the_future" in data  # caller dict untouched

    def test_labeled_entries_keep_the_chain_intact(self, tmp_path):
        from navig.ledger_chain import verify_ledger
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="plain"))
        _record_config_change(rec)
        result = verify_ledger(_ledger(tmp_path))
        assert result.ok
        assert result.status == "intact"
        assert result.verified == 2

    def test_mixed_legacy_and_labeled_ledger_verifies(self, tmp_path):
        """Pre-chain, pre-label lines + new labeled entries: one intact ledger."""
        from navig.ledger_chain import verify_ledger

        ledger = _ledger(tmp_path)
        legacy = [
            json.dumps({"id": f"op-legacy-{i}", "command": f"old{i}", "status": "success"})
            for i in range(3)
        ]
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("\n".join(legacy) + "\n", encoding="utf-8")

        rec = _make_recorder(tmp_path)
        _record_config_change(rec)

        result = verify_ledger(ledger)
        assert result.ok
        assert result.status == "intact"
        assert result.unchained == 3
        assert result.chained == 1
        # legacy entries still parse through the reader (no reversibility field)
        ops = rec.get_last_n(10)
        assert len(ops) == 4
        assert ops[-1].reversibility == ""  # legacy: honestly unlabeled


# ---------------------------------------------------------------------------
# Undo engine — safety contract
# ---------------------------------------------------------------------------


class TestUndoEngine:
    def test_happy_path_restores_and_chains(self, tmp_path):
        from navig.ledger_chain import verify_ledger
        from navig.undo import (
            check_drift,
            collect_undone,
            ensure_undoable,
            perform_undo,
            recent_records,
        )

        rec = _make_recorder(tmp_path)
        target = _record_config_change(rec)
        stub = _StubConfigManager({"log_level": "DEBUG"})

        with patch("navig.config.get_config_manager", return_value=stub):
            records = recent_records(rec)
            ensure_undoable(records[0], collect_undone(records))
            check_drift(records[0])
            swapped = perform_undo(records[0])

        assert stub.cfg["log_level"] == "INFO"
        assert swapped["new_value"] == "INFO"
        assert swapped["old_value"] == "DEBUG"

        # record the undo the way the CLI does — it must ride the chain
        undo_record = rec.start_operation(
            command=f"navig undo {target.id}",
            operation_type=target.operation_type,
            args={"undo_of": target.id},
            tags=["undo"],
        )
        rec.complete_operation(undo_record, success=True, undo_data=swapped)

        result = verify_ledger(_ledger(tmp_path))
        assert result.ok and result.status == "intact"
        entries = _entries(_ledger(tmp_path))
        assert entries[-1]["args"]["undo_of"] == target.id
        assert entries[-1]["reversibility"] == "yellow"  # undo tag caps the label

    def test_drift_refusal_when_value_changed_since(self, tmp_path):
        from navig.undo import UndoRefused, check_drift, recent_records

        rec = _make_recorder(tmp_path)
        _record_config_change(rec)  # set INFO -> DEBUG
        stub = _StubConfigManager({"log_level": "TRACE"})  # changed again since

        with patch("navig.config.get_config_manager", return_value=stub):
            with pytest.raises(UndoRefused, match="changed since"):
                check_drift(recent_records(rec)[0])
        assert stub.cfg["log_level"] == "TRACE"  # untouched

    def test_drift_refusal_when_key_deleted_since(self, tmp_path):
        from navig.undo import UndoRefused, check_drift, recent_records

        rec = _make_recorder(tmp_path)
        _record_config_change(rec)
        stub = _StubConfigManager({})

        with patch("navig.config.get_config_manager", return_value=stub):
            with pytest.raises(UndoRefused, match="no longer exists"):
                check_drift(recent_records(rec)[0])

    def test_double_undo_is_refused(self, tmp_path):
        from navig.undo import UndoRefused, collect_undone, ensure_undoable, recent_records

        rec = _make_recorder(tmp_path)
        target = _record_config_change(rec)
        undo_record = rec.start_operation(
            command=f"navig undo {target.id}",
            operation_type=target.operation_type,
            args={"undo_of": target.id},
            tags=["undo"],
        )
        rec.complete_operation(undo_record, success=True)

        records = recent_records(rec)
        undone = collect_undone(records)
        target_rec = next(r for r in records if r.id == target.id)
        with pytest.raises(UndoRefused, match="already undone"):
            ensure_undoable(target_rec, undone)

    def test_failed_undo_does_not_mark_target_undone(self, tmp_path):
        from navig.undo import collect_undone, recent_records

        rec = _make_recorder(tmp_path)
        target = _record_config_change(rec)
        undo_record = rec.start_operation(
            command=f"navig undo {target.id}",
            operation_type=target.operation_type,
            args={"undo_of": target.id},
            tags=["undo"],
        )
        rec.complete_operation(undo_record, success=False, error="boom", exit_code=1)

        assert collect_undone(recent_records(rec)) == {}

    def test_sensitive_undo_data_is_refused(self, tmp_path):
        from navig.operation_recorder import OperationRecord, OperationStatus, OperationType
        from navig.undo import UndoRefused, ensure_undoable

        rec = _make_recorder(tmp_path)
        rec.record(
            OperationRecord(
                command="navig config set openrouter_api_key ***",
                operation_type=OperationType.CONFIG_CHANGE,
                status=OperationStatus.SUCCESS,
                undo_data={
                    "key": "openrouter_api_key",
                    "sensitive": True,
                    "vault_ref": "openrouter/api_key",
                },
            )
        )
        target = rec.get_last_n(1)[0]
        with pytest.raises(UndoRefused, match="secret-bearing"):
            ensure_undoable(target, {})

    def test_defense_in_depth_refuses_secret_named_keys(self, tmp_path):
        """Even a capture site that FORGOT to mark a token key is refused."""
        from navig.undo import UndoRefused, ensure_undoable, recent_records

        rec = _make_recorder(tmp_path)
        _record_config_change(rec, key="myservice.token", old_value="a", new_value="b")
        with pytest.raises(UndoRefused, match="names secret material"):
            ensure_undoable(recent_records(rec)[0], {})

    def test_yellow_refusal_includes_compensation_hint(self, tmp_path):
        from navig.operation_recorder import OperationRecord, OperationStatus, OperationType
        from navig.undo import UndoRefused, ensure_undoable

        rec = _make_recorder(tmp_path)
        rec.record(
            OperationRecord(
                command="navig tunnel run",
                operation_type=OperationType.TUNNEL_START,
                status=OperationStatus.SUCCESS,
            )
        )
        target = rec.get_last_n(1)[0]
        with pytest.raises(UndoRefused, match="yellow.*tunnel remove"):
            ensure_undoable(target, {})

    def test_restore_to_absent_removes_the_key(self, tmp_path):
        from navig.undo import perform_undo, recent_records

        rec = _make_recorder(tmp_path)
        _record_config_change(
            rec, key="brand.new", old_value=None, new_value="v1", old_exists=False
        )
        stub = _StubConfigManager({"brand": {"new": "v1"}})
        with patch("navig.config.get_config_manager", return_value=stub):
            perform_undo(recent_records(rec)[0])
        assert "new" not in stub.cfg.get("brand", {})

    def test_nested_key_restore(self, tmp_path):
        from navig.undo import check_drift, perform_undo, recent_records

        rec = _make_recorder(tmp_path)
        _record_config_change(rec, key="a.b.c", old_value="x", new_value="y")
        stub = _StubConfigManager({"a": {"b": {"c": "y"}}})
        with patch("navig.config.get_config_manager", return_value=stub):
            check_drift(recent_records(rec)[0])
            perform_undo(recent_records(rec)[0])
        assert stub.cfg["a"]["b"]["c"] == "x"

    def test_host_switch_undo(self, tmp_path):
        from navig.operation_recorder import OperationRecord, OperationStatus, OperationType
        from navig.undo import check_drift, perform_undo, recent_records

        rec = _make_recorder(tmp_path)
        rec.record(
            OperationRecord(
                command="navig config set active_host prod",
                operation_type=OperationType.HOST_SWITCH,
                status=OperationStatus.SUCCESS,
                undo_data={"previous_host": "staging", "new_host": "prod"},
            )
        )
        stub = _StubConfigManager({"active_host": "prod"}, hosts=("staging", "prod"))
        with patch("navig.config.get_config_manager", return_value=stub):
            check_drift(recent_records(rec)[0])
            perform_undo(recent_records(rec)[0])
        assert stub.active_host_calls == ["staging"]
        assert stub.cfg["active_host"] == "staging"

    def test_candidates_exclude_undo_entries_and_flag_states(self, tmp_path):
        from navig.undo import find_candidates

        rec = _make_recorder(tmp_path)
        first = _record_config_change(rec, key="alpha", old_value="1", new_value="2")
        second = _record_config_change(rec, key="beta", old_value="a", new_value="b")
        undo_record = rec.start_operation(
            command=f"navig undo {second.id}",
            operation_type=second.operation_type,
            args={"undo_of": second.id},
            tags=["undo"],
        )
        rec.complete_operation(undo_record, success=True)

        stub = _StubConfigManager({"alpha": "2", "beta": "a"})
        with patch("navig.config.get_config_manager", return_value=stub):
            cands = find_candidates(rec, limit=10)

        by_id = {c.record.id: c for c in cands}
        assert set(by_id) == {first.id, second.id}  # the undo entry itself is absent
        assert by_id[second.id].state == "undone"
        assert by_id[first.id].state == "ready"

    def test_file_modify_drift_check_uses_post_hash(self, tmp_path):
        import hashlib

        from navig.operation_recorder import OperationRecord, OperationStatus, OperationType
        from navig.undo import UndoRefused, check_drift, perform_undo, recent_records

        backup = tmp_path / "backup.txt"
        backup.write_text("before", encoding="utf-8")
        target = tmp_path / "target.txt"
        target.write_text("after", encoding="utf-8")

        rec = _make_recorder(tmp_path / "hist")
        rec.record(
            OperationRecord(
                command="edit target.txt",
                operation_type=OperationType.FILE_MODIFY,
                status=OperationStatus.SUCCESS,
                undo_data={
                    "file_history_backup": str(backup),
                    "path": str(target),
                    "after_sha256": hashlib.sha256(b"after").hexdigest(),
                },
            )
        )
        record = recent_records(rec)[0]
        check_drift(record)  # matches — allowed

        target.write_text("changed again", encoding="utf-8")
        with pytest.raises(UndoRefused, match="changed since"):
            check_drift(record)

        target.write_text("after", encoding="utf-8")
        perform_undo(record)
        assert target.read_text(encoding="utf-8") == "before"


# ---------------------------------------------------------------------------
# Config-set capture seam
# ---------------------------------------------------------------------------


class TestConfigSetCapture:
    def _set(self, tmp_path, key, value, cfg=None, hosts=()):
        from navig.commands.config import set_config

        rec = _make_recorder(tmp_path)
        stub = _StubConfigManager(cfg if cfg is not None else {}, hosts=hosts)
        with (
            patch("navig.commands.config.get_config_manager", return_value=stub),
            patch("navig.operation_recorder.get_operation_recorder", return_value=rec),
        ):
            set_config(key, value)
        return rec, stub

    def test_plain_key_captures_green_undo_data(self, tmp_path):
        rec, stub = self._set(tmp_path, "log_level", "DEBUG", cfg={"log_level": "INFO"})
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["operation_type"] == "config_change"
        assert entry["reversibility"] == "green"
        assert entry["undo_data"] == {
            "key": "log_level",
            "old_value": "INFO",
            "old_exists": True,
            "new_value": "DEBUG",
            "scope": "global",
        }
        assert stub.cfg["log_level"] == "DEBUG"

    def test_new_key_captures_old_exists_false(self, tmp_path):
        _rec, _stub = self._set(tmp_path, "execution.mode", "auto", cfg={})
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["undo_data"]["old_exists"] is False
        assert entry["undo_data"]["new_value"] == "auto"

    def test_sensitive_key_stores_no_plaintext_anywhere(self, tmp_path):
        secret = "super-secret-value-12345"
        self._set(tmp_path, "myservice.token", secret, cfg={})
        (entry,) = _entries(_ledger(tmp_path))
        line = json.dumps(entry)
        assert secret not in line  # not in command, args, undo_data — nowhere
        assert entry["command"] == "navig config set myservice.token ***"
        assert entry["undo_data"]["sensitive"] is True
        assert entry["reversibility"] == "yellow"

    def test_vault_backed_key_records_vault_ref(self):
        """The _record_config_set helper names the vault mirror for known keys."""
        from navig.commands.config import _record_config_set

        rec_holder = {}

        class _Recorder:
            def start_operation(self, **kw):
                from navig.operation_recorder import OperationRecord

                rec_holder["record"] = OperationRecord(**kw)
                return rec_holder["record"]

            def complete_operation(self, record, **kw):
                rec_holder["undo_data"] = kw.get("undo_data")
                return "op-test"

        with patch(
            "navig.operation_recorder.get_operation_recorder", return_value=_Recorder()
        ):
            _record_config_set("openrouter_api_key", None, False, "sk-or-secret")

        data = rec_holder["undo_data"]
        assert data["sensitive"] is True
        assert data["vault_ref"] == "openrouter/api_key"
        assert "sk-or-secret" not in json.dumps(data)
        assert rec_holder["record"].command == "navig config set openrouter_api_key ***"

    def test_active_host_captures_host_switch(self, tmp_path):
        rec, stub = self._set(
            tmp_path, "active_host", "prod", cfg={"active_host": "staging"}, hosts=("prod",)
        )
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["operation_type"] == "host_switch"
        assert entry["reversibility"] == "green"
        assert entry["undo_data"] == {"previous_host": "staging", "new_host": "prod"}
        assert stub.cfg["active_host"] == "prod"


# ---------------------------------------------------------------------------
# CLI — navig undo
# ---------------------------------------------------------------------------


class TestUndoCli:
    def _app(self):
        import typer

        from navig.commands.undo import undo_command

        app = typer.Typer()
        app.command("undo")(undo_command)

        # A second command prevents Typer's single-command collapse, so the
        # app routes exactly like the real CLI (`navig undo [op-id] [--flags]`).
        @app.command("noop", hidden=True)
        def _noop():  # pragma: no cover - routing ballast only
            pass

        return app

    def _invoke(self, args, recorder, stub):
        from typer.testing import CliRunner

        with (
            patch("navig.operation_recorder.get_operation_recorder", return_value=recorder),
            patch("navig.config.get_config_manager", return_value=stub),
        ):
            return CliRunner().invoke(self._app(), args, obj={})

    def test_list_json_is_one_pure_document(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record_config_change(rec)
        stub = _StubConfigManager({"log_level": "DEBUG"})
        result = self._invoke(["undo", "--list", "--json"], rec, stub)
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)  # whole stdout parses = purity
        assert payload["ready"] == 1
        assert payload["candidates"][0]["state"] == "ready"
        assert payload["candidates"][0]["would"].startswith("restore config")

    def test_undo_yes_restores_and_records(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        target = _record_config_change(rec)
        stub = _StubConfigManager({"log_level": "DEBUG"})

        result = self._invoke(["undo", "--yes"], rec, stub)
        assert result.exit_code == 0, result.output
        assert stub.cfg["log_level"] == "INFO"

        entries = _entries(_ledger(tmp_path))
        assert entries[-1]["args"]["undo_of"] == target.id
        assert "undo" in entries[-1]["tags"]
        assert entries[-1]["status"] == "success"
        assert verify_ledger(_ledger(tmp_path)).ok  # chain survives the undo

    def test_undo_twice_refuses(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record_config_change(rec)
        stub = _StubConfigManager({"log_level": "DEBUG"})

        first = self._invoke(["undo", "--yes"], rec, stub)
        assert first.exit_code == 0, first.output
        second = self._invoke(["undo", "--yes"], rec, stub)
        assert second.exit_code == 1
        assert "cannot be undone" in second.output or "already undone" in second.output

    def test_undo_refuses_on_drift(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record_config_change(rec)  # recorded INFO -> DEBUG
        stub = _StubConfigManager({"log_level": "TRACE"})  # changed again since

        result = self._invoke(["undo", "--yes"], rec, stub)
        assert result.exit_code == 1
        assert "changed since" in result.output
        assert stub.cfg["log_level"] == "TRACE"  # untouched

    def test_json_without_yes_refuses_purely(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record_config_change(rec)
        stub = _StubConfigManager({"log_level": "DEBUG"})
        result = self._invoke(["undo", "--json"], rec, stub)
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert "confirmation required" in payload["error"]
        assert stub.cfg["log_level"] == "DEBUG"  # nothing happened

    def test_nothing_to_undo_exits_one(self, tmp_path):
        rec = _make_recorder(tmp_path)
        stub = _StubConfigManager({})
        result = self._invoke(["undo", "--yes"], rec, stub)
        assert result.exit_code == 1
        assert "Nothing to undo" in result.output

    def test_explicit_op_id_targets_older_operation(self, tmp_path):
        rec = _make_recorder(tmp_path)
        first = _record_config_change(rec, key="alpha", old_value="1", new_value="2")
        _record_config_change(rec, key="beta", old_value="a", new_value="b")
        stub = _StubConfigManager({"alpha": "2", "beta": "b"})

        result = self._invoke(["undo", first.id, "--yes"], rec, stub)
        assert result.exit_code == 0, result.output
        assert stub.cfg["alpha"] == "1"
        assert stub.cfg["beta"] == "b"  # newer op untouched


# ---------------------------------------------------------------------------
# CLI — navig ledger show
# ---------------------------------------------------------------------------


class TestLedgerShowCli:
    def _invoke(self, args):
        from typer.testing import CliRunner

        from navig.commands.ledger import ledger_app

        return CliRunner().invoke(ledger_app, args, obj={})

    def _seed(self, tmp_path):
        """legacy line + red command + green config change."""
        from navig.operation_recorder import OperationRecord

        ledger = _ledger(tmp_path)
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text(
            json.dumps({"id": "op-legacy-0", "command": "old", "status": "success"}) + "\n",
            encoding="utf-8",
        )
        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="navig run ls"))
        _record_config_change(rec)
        return rec

    def test_show_json_is_one_pure_document(self, tmp_path):
        self._seed(tmp_path)
        result = self._invoke(["show", "--path", str(_ledger(tmp_path)), "--json"])
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)
        assert payload["chain"]["status"] == "intact"
        assert payload["count"] == 3
        by_chain = [e["chain"] for e in payload["entries"]]
        assert by_chain == ["legacy", "ok", "ok"]
        labels = [e["reversibility"] for e in payload["entries"]]
        assert labels[1] == "red"
        assert labels[2] == "green"

    def test_show_marks_undone_operations(self, tmp_path):
        rec = self._seed(tmp_path)
        target = rec.get_last_n(1)[0]
        undo_record = rec.start_operation(
            command=f"navig undo {target.id}",
            operation_type=target.operation_type,
            args={"undo_of": target.id},
            tags=["undo"],
        )
        rec.complete_operation(undo_record, success=True)

        result = self._invoke(["show", "--path", str(_ledger(tmp_path)), "--json"])
        payload = json.loads(result.output)
        entry = next(e for e in payload["entries"] if e["id"] == target.id)
        assert entry["undone"] is True
        assert entry["undone_by"]

    def test_show_flags_broken_lines_but_exits_zero(self, tmp_path):
        self._seed(tmp_path)
        ledger = _ledger(tmp_path)
        lines = [ln for ln in ledger.read_text(encoding="utf-8").splitlines() if ln.strip()]
        tampered = json.loads(lines[1])
        tampered["command"] = "tampered"
        lines[1] = json.dumps(tampered)
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self._invoke(["show", "--path", str(ledger), "--json"])
        assert result.exit_code == 0  # a view, not a gate
        payload = json.loads(result.output)
        assert payload["chain"]["status"] == "broken"
        assert any(e["chain"] == "broken" for e in payload["entries"])

    def test_show_table_renders_with_nudge(self, tmp_path):
        self._seed(tmp_path)
        result = self._invoke(["show", "--path", str(_ledger(tmp_path))])
        assert result.exit_code == 0, result.output
        assert "green" in result.output
        assert "navig undo" in result.output

    def test_tail_limits_entries(self, tmp_path):
        self._seed(tmp_path)
        result = self._invoke(["show", "--path", str(_ledger(tmp_path)), "--tail", "1", "--json"])
        payload = json.loads(result.output)
        assert payload["count"] == 1
        assert payload["entries"][0]["reversibility"] == "green"  # the most recent

    def test_missing_ledger_is_honest(self, tmp_path):
        result = self._invoke(["show", "--path", str(tmp_path / "nope.jsonl")])
        assert result.exit_code == 0
        assert "nothing recorded" in result.output.lower()

    def test_show_redacts_secret_material_in_commands(self, tmp_path):
        """Legacy middleware lines may carry raw argv secrets — never re-print them."""
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="navig config set api_key=sk-live-abcdef123456"))
        result = self._invoke(["show", "--path", str(_ledger(tmp_path)), "--json"])
        assert result.exit_code == 0
        assert "sk-live-abcdef123456" not in result.output


# ---------------------------------------------------------------------------
# Registration — the verbs actually exist
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_undo_registers_as_flat_command(self):
        import typer

        from navig.cli.registration import _try_register_undo

        app = typer.Typer()
        already: set[str] = set()
        _try_register_undo(app, already)
        assert "undo" in already
        assert any(c.name == "undo" for c in app.registered_commands)

    def test_help_registry_has_undo_and_ledger_show(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY

        assert "undo" in HELP_REGISTRY
        assert "show" in HELP_REGISTRY["ledger"]["commands"]

    def test_ledger_reads_are_skipped_by_the_recorder_middleware(self):
        """Observer effect: `navig ledger show` must not append to what it shows."""
        from navig.cli.middleware import _SKIP_RECORD_KEYWORDS

        assert "ledger " in _SKIP_RECORD_KEYWORDS
        # but `navig undo` IS recorded (it's a real operation)
        assert not any(kw.strip() == "undo" for kw in _SKIP_RECORD_KEYWORDS)
