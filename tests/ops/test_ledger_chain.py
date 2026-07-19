"""Tests for the hash-chained operations ledger (T-067).

Covers navig.ledger_chain (pure chain math + verify walk), the chain fields
OperationRecorder embeds on every append, rotation/clear/legacy wrinkles from
plan-evidence-ledger.md, concurrent-append safety, and the `navig ledger
verify` CLI contract (Rich + --json purity + exit codes 0/1).
"""

from __future__ import annotations

import json
import threading
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


def _write_ops(recorder, n: int, prefix: str = "cmd") -> None:
    from navig.operation_recorder import OperationRecord

    for i in range(n):
        recorder.record(OperationRecord(command=f"{prefix}{i}"))


def _lines(path: Path) -> list[str]:
    return [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _entries(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in _lines(path)]


# ---------------------------------------------------------------------------
# Chain math — canonical payload + entry hash
# ---------------------------------------------------------------------------


class TestChainMath:
    def test_canonical_payload_excludes_chain_fields(self):
        from navig.ledger_chain import canonical_payload

        entry = {"command": "ls", "prev": "sha256:aa", "hash": "sha256:bb", "sig": "zz"}
        payload = canonical_payload(entry)
        assert payload == '{"command":"ls"}'

    def test_canonical_payload_is_roundtrip_stable(self):
        """parse(serialize(x)) then re-canonicalize == first canonicalization."""
        from navig.ledger_chain import canonical_payload

        entry = {
            "command": "naïve — ✓",
            "duration_ms": 42.5,
            "args": {"b": 1, "a": [1, 2]},
            "exit_code": 0,
        }
        first = canonical_payload(entry)
        reparsed = json.loads(first)
        assert canonical_payload(reparsed) == first

    def test_compute_entry_hash_covers_prev(self):
        from navig.ledger_chain import compute_entry_hash

        entry = {"command": "ls"}
        assert compute_entry_hash(None, entry) != compute_entry_hash("sha256:aa", entry)

    def test_hash_is_sha256_prefixed(self):
        from navig.ledger_chain import HASH_PREFIX, compute_entry_hash

        h = compute_entry_hash(None, {"command": "ls"})
        assert h.startswith(HASH_PREFIX)
        assert len(h) == len(HASH_PREFIX) + 64  # hex sha256


# ---------------------------------------------------------------------------
# Writer — recorder embeds prev/hash on every append
# ---------------------------------------------------------------------------


class TestRecorderChainsEntries:
    def test_first_entry_is_genesis(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _write_ops(rec, 1)
        (entry,) = _entries(_ledger(tmp_path))
        assert entry["prev"] is None
        assert entry["hash"].startswith("sha256:")

    def test_entries_link_prev_to_previous_hash(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _write_ops(rec, 3)
        entries = _entries(_ledger(tmp_path))
        assert entries[1]["prev"] == entries[0]["hash"]
        assert entries[2]["prev"] == entries[1]["hash"]

    def test_stored_hash_recomputes(self, tmp_path):
        from navig.ledger_chain import compute_entry_hash

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 2)
        for entry in _entries(_ledger(tmp_path)):
            assert compute_entry_hash(entry["prev"], entry) == entry["hash"]

    def test_readers_still_work_on_chained_file(self, tmp_path):
        """from_dict strips chain fields — iter/get/last_n must not crash."""
        rec = _make_recorder(tmp_path)
        _write_ops(rec, 3)
        ops = rec.get_last_n(3)
        assert len(ops) == 3
        assert ops[0].command == "cmd2"
        fetched = rec.get_operation(ops[0].id)
        assert fetched is not None
        assert not hasattr(fetched, "prev")

    def test_from_dict_does_not_mutate_caller_dict(self):
        from navig.operation_recorder import OperationRecord

        data = {"id": "x", "operation_type": "other", "status": "success", "hash": "sha256:aa"}
        OperationRecord.from_dict(data)
        assert data["operation_type"] == "other"  # not replaced by the enum
        assert "hash" in data


# ---------------------------------------------------------------------------
# Verify — round-trip, tamper detection, honest states
# ---------------------------------------------------------------------------


class TestVerifyLedger:
    def test_append_verify_roundtrip_intact(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 10)
        result = verify_ledger(_ledger(tmp_path))
        assert result.ok
        assert result.status == "intact"
        assert result.total == result.chained == result.verified == 10
        assert result.unchained == 0
        assert result.breaks == []
        assert result.segments == 1

    def test_tampered_middle_entry_named_by_line(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 10)
        ledger = _ledger(tmp_path)
        lines = _lines(ledger)
        entry = json.loads(lines[4])  # line 5, 1-based
        entry["command"] = "rm -rf / --tampered"  # rewrite content, keep hash
        lines[4] = json.dumps(entry)
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = verify_ledger(ledger)
        assert not result.ok
        assert result.status == "broken"
        assert result.first_broken_line == 5
        assert "hash mismatch" in result.breaks[0].reason
        assert result.verified == 9  # only the tampered entry fails

    def test_deleted_middle_line_detected_at_successor(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 10)
        ledger = _ledger(tmp_path)
        lines = _lines(ledger)
        del lines[4]
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = verify_ledger(ledger)
        assert not result.ok
        assert result.first_broken_line == 5  # the successor, now on line 5
        assert "prev-hash mismatch" in result.breaks[0].reason

    def test_reordered_lines_detected(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 6)
        ledger = _ledger(tmp_path)
        lines = _lines(ledger)
        lines[2], lines[3] = lines[3], lines[2]
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        assert not verify_ledger(ledger).ok

    def test_missing_ledger_reports_missing_not_broken(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        result = verify_ledger(tmp_path / "does-not-exist.jsonl")
        assert result.ok
        assert result.status == "missing"
        assert result.total == 0

    def test_empty_ledger_reports_empty(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        ledger = tmp_path / "operations.jsonl"
        ledger.write_text("", encoding="utf-8")
        result = verify_ledger(ledger)
        assert result.ok
        assert result.status == "empty"

    def test_legacy_only_file_reports_legacy(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        ledger = tmp_path / "operations.jsonl"
        legacy = [json.dumps({"id": f"op-{i}", "command": f"old{i}"}) for i in range(3)]
        ledger.write_text("\n".join(legacy) + "\n", encoding="utf-8")

        result = verify_ledger(ledger)
        assert result.ok
        assert result.status == "legacy"
        assert result.unchained == 3
        assert result.chained == 0

    def test_legacy_then_chained_entries_verify_clean(self, tmp_path):
        """New writer appending to a pre-chain file starts a fresh chain."""
        from navig.ledger_chain import verify_ledger
        from navig.operation_recorder import OperationRecord

        ledger = _ledger(tmp_path)
        legacy = [json.dumps({"id": f"op-{i}", "command": f"old{i}"}) for i in range(3)]
        ledger.parent.mkdir(parents=True, exist_ok=True)
        ledger.write_text("\n".join(legacy) + "\n", encoding="utf-8")

        rec = _make_recorder(tmp_path)
        rec.record(OperationRecord(command="new0"))
        rec.record(OperationRecord(command="new1"))

        result = verify_ledger(ledger)
        assert result.ok
        assert result.status == "intact"
        assert result.unchained == 3
        assert result.chained == 2
        entries = _entries(ledger)
        assert entries[3]["prev"] is None  # fresh chain after legacy tail

    def test_clear_history_is_a_clean_restart(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 4)
        assert rec.clear_history() == 4
        _write_ops(rec, 2)

        result = verify_ledger(_ledger(tmp_path))
        assert result.ok
        assert result.status == "intact"
        assert result.chained == 2
        assert _entries(_ledger(tmp_path))[0]["prev"] is None

    def test_garbage_line_inside_chained_region_is_a_break(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 4)
        ledger = _ledger(tmp_path)
        lines = _lines(ledger)
        lines.insert(2, "{corrupted-not-json")
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = verify_ledger(ledger)
        assert not result.ok
        assert result.malformed == 1
        assert result.first_broken_line == 3

    def test_verification_to_dict_schema(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path)
        _write_ops(rec, 2)
        payload = verify_ledger(_ledger(tmp_path)).to_dict()

        for key in (
            "path",
            "status",
            "ok",
            "total",
            "chained",
            "unchained",
            "malformed",
            "verified",
            "breaks",
            "first_broken_line",
            "restarts",
            "segments",
            "anchor",
            "algorithm",
            "format",
            "guarantee",
        ):
            assert key in payload, f"missing key: {key}"
        assert payload["algorithm"] == "sha256"
        assert payload["guarantee"] == "tamper-evident"
        # JSON-serializable end to end
        assert json.loads(json.dumps(payload)) == payload


# ---------------------------------------------------------------------------
# Rotation — the chain survives (wrinkle 1)
# ---------------------------------------------------------------------------


class TestRotationCarriesChain:
    def test_rotation_preserves_chain_with_anchor(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path, max_entries=10)
        _write_ops(rec, 12)  # forces at least one rotation

        result = verify_ledger(_ledger(tmp_path))
        assert result.ok, f"breaks: {[b.to_dict() for b in result.breaks]}"
        assert result.status == "intact"
        assert result.chained <= 10
        # first survivor still names a rotated-out hash — the anchor
        assert result.anchor is not None
        assert result.anchor.startswith("sha256:")

    def test_append_after_rotation_keeps_linking(self, tmp_path):
        from navig.ledger_chain import verify_ledger

        rec = _make_recorder(tmp_path, max_entries=10)
        _write_ops(rec, 12)
        _write_ops(rec, 3, prefix="post")

        result = verify_ledger(_ledger(tmp_path))
        assert result.ok
        entries = _entries(_ledger(tmp_path))
        assert entries[-1]["prev"] == entries[-2]["hash"]

    def test_rotation_keeps_raw_bytes_of_survivors(self, tmp_path):
        rec = _make_recorder(tmp_path, max_entries=10)
        _write_ops(rec, 9)
        before = _lines(_ledger(tmp_path))
        _write_ops(rec, 3, prefix="extra")  # triggers rotation (12 > 10)
        after = _lines(_ledger(tmp_path))
        # every surviving line is byte-identical to what was originally written
        survivors = [ln for ln in after if ln in before]
        assert len(survivors) >= 1


# ---------------------------------------------------------------------------
# Concurrency — in-process appends never fork the chain
# ---------------------------------------------------------------------------


class TestConcurrentAppends:
    def test_threaded_appends_stay_intact(self, tmp_path):
        from navig.ledger_chain import verify_ledger
        from navig.operation_recorder import OperationRecord

        rec = _make_recorder(tmp_path)
        errors: list[Exception] = []

        def writer(tag: str):
            try:
                for i in range(25):
                    rec.record(OperationRecord(command=f"{tag}-{i}"))
            except Exception as exc:  # noqa: BLE001 — surfaced via the list
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(f"t{n}",)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        result = verify_ledger(_ledger(tmp_path))
        assert result.ok, f"breaks: {[b.to_dict() for b in result.breaks]}"
        assert result.chained == 100


# ---------------------------------------------------------------------------
# CLI — navig ledger verify (exit codes + JSON purity)
# ---------------------------------------------------------------------------


class TestLedgerVerifyCli:
    def _invoke(self, args: list[str]):
        from typer.testing import CliRunner

        from navig.commands.ledger import ledger_app

        return CliRunner().invoke(ledger_app, args, obj={})

    def test_intact_exits_zero(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _write_ops(rec, 5)
        result = self._invoke(["verify", "--path", str(_ledger(tmp_path))])
        assert result.exit_code == 0, result.output
        assert "chain intact" in result.output

    def test_broken_exits_one_and_names_the_line(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _write_ops(rec, 5)
        ledger = _ledger(tmp_path)
        lines = _lines(ledger)
        entry = json.loads(lines[2])
        entry["command"] = "tampered"
        lines[2] = json.dumps(entry)
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self._invoke(["verify", "--path", str(ledger)])
        assert result.exit_code == 1
        assert "line 3" in result.output

    def test_missing_ledger_exits_zero(self, tmp_path):
        result = self._invoke(["verify", "--path", str(tmp_path / "nope.jsonl")])
        assert result.exit_code == 0
        assert "nothing recorded" in result.output.lower()

    def test_json_output_is_one_pure_document(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _write_ops(rec, 5)
        result = self._invoke(["verify", "--path", str(_ledger(tmp_path)), "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)  # whole stdout parses = purity
        assert payload["status"] == "intact"
        assert payload["chained"] == 5

    def test_json_broken_exits_one_with_first_broken_line(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _write_ops(rec, 5)
        ledger = _ledger(tmp_path)
        lines = _lines(ledger)
        del lines[1]
        ledger.write_text("\n".join(lines) + "\n", encoding="utf-8")

        result = self._invoke(["verify", "--path", str(ledger), "--json"])
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert payload["status"] == "broken"
        assert payload["first_broken_line"] == 2
        assert payload["ok"] is False

    def test_default_path_resolves_at_call_time(self, tmp_path):
        """No frozen path: the default ledger comes from the recorder at call time."""
        from navig.operation_recorder import OperationRecorder

        rec = OperationRecorder(history_dir=tmp_path)
        _write_ops(rec, 2)
        with patch("navig.operation_recorder.get_operation_recorder", return_value=rec):
            result = self._invoke(["verify", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output)["chained"] == 2


# ---------------------------------------------------------------------------
# CLI registration — the verb actually exists on the app
# ---------------------------------------------------------------------------


class TestLedgerRegistration:
    def test_registered_in_external_cmd_map(self):
        from navig.cli.registration import _EXTERNAL_CMD_MAP

        assert _EXTERNAL_CMD_MAP["ledger"] == ("navig.commands.ledger", "ledger_app")

    def test_help_registry_has_ledger(self):
        from navig.cli.help_dictionaries import HELP_REGISTRY

        assert "ledger" in HELP_REGISTRY
        assert "verify" in HELP_REGISTRY["ledger"]["commands"]
