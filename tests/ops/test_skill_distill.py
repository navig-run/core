"""Tests for `navig skill distill` (T-069, plan-evidence-ledger.md).

Covers the distillation pipeline (navig.skill_distill): slice selection
(time window + explicit ids, empty-window honesty), success-path filtering
(failed → pitfalls, undone/undo/meta excluded, duplicate collapse),
conservative placeholder extraction, the non-negotiable secret sweep,
SKILL.md structural validity (asserted against the REAL `navig skill lint`),
--force semantics, --json stdout purity, the T-068 ledger-enrichment
interlock, and the agent-layer AI seam's command-fidelity gate.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_recorder(tmp_path: Path):
    from navig.operation_recorder import OperationRecorder

    return OperationRecorder(history_dir=tmp_path)


def _ts(minutes_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


def _record(
    recorder,
    command: str,
    *,
    minutes_ago: float = 5,
    status: str = "success",
    op_type: str = "local_command",
    host: str | None = None,
    error: str = "",
    exit_code: int = 0,
    tags: list[str] | None = None,
    args: dict | None = None,
    undo_data: dict | None = None,
):
    from navig.operation_recorder import (
        OperationRecord,
        OperationStatus,
        OperationType,
    )

    rec = OperationRecord(
        command=command,
        timestamp=_ts(minutes_ago),
        operation_type=OperationType(op_type),
        status=OperationStatus(status),
        host=host,
        error=error,
        exit_code=exit_code,
        tags=tags or [],
        args=args or {},
        undo_data=undo_data or {},
    )
    recorder.record(rec)
    return rec


def _distill(records, **kwargs):
    from navig.skill_distill import distill

    return distill(records, **kwargs)


# ---------------------------------------------------------------------------
# parse_duration
# ---------------------------------------------------------------------------


class TestParseDuration:
    @pytest.mark.parametrize(
        ("text", "seconds"),
        [
            ("90s", 90),
            ("30m", 1800),
            ("2h", 7200),
            ("1d", 86400),
            ("1w", 604800),
            ("1.5h", 5400),
            (" 2H ", 7200),  # case + whitespace tolerant
        ],
    )
    def test_valid_durations(self, text, seconds):
        from navig.skill_distill import parse_duration

        assert parse_duration(text).total_seconds() == seconds

    @pytest.mark.parametrize("text", ["", "abc", "2 hours", "h", "-2h", "30", "2x"])
    def test_invalid_durations_raise(self, text):
        from navig.skill_distill import parse_duration

        with pytest.raises(ValueError):
            parse_duration(text)

    def test_zero_duration_rejected(self):
        from navig.skill_distill import parse_duration

        with pytest.raises(ValueError):
            parse_duration("0h")


# ---------------------------------------------------------------------------
# slice_ledger
# ---------------------------------------------------------------------------


class TestSliceLedger:
    def test_window_includes_recent_excludes_old(self, tmp_path):
        from navig.skill_distill import slice_ledger

        rec = _make_recorder(tmp_path)
        _record(rec, "navig config set old.value 1", minutes_ago=300)
        recent = _record(rec, "navig config set new.value 2", minutes_ago=10)

        records = slice_ledger(rec, last=timedelta(hours=2))
        assert [r.id for r in records] == [recent.id]

    def test_slice_is_chronological(self, tmp_path):
        from navig.skill_distill import slice_ledger

        rec = _make_recorder(tmp_path)
        first = _record(rec, "navig host list", minutes_ago=50)
        second = _record(rec, "navig config set a 1", minutes_ago=20)

        records = slice_ledger(rec, last=timedelta(hours=2))
        assert [r.id for r in records] == [first.id, second.id]

    def test_empty_window_returns_empty(self, tmp_path):
        from navig.skill_distill import slice_ledger

        rec = _make_recorder(tmp_path)
        _record(rec, "navig config set a 1", minutes_ago=500)
        assert slice_ledger(rec, last=timedelta(minutes=5)) == []

    def test_explicit_ops_win_and_sort_chronologically(self, tmp_path):
        from navig.skill_distill import slice_ledger

        rec = _make_recorder(tmp_path)
        first = _record(rec, "navig host list", minutes_ago=50)
        second = _record(rec, "navig config set a 1", minutes_ago=20)
        _record(rec, "navig db tables", minutes_ago=10)

        records = slice_ledger(rec, op_ids=[second.id, first.id])
        assert [r.id for r in records] == [first.id, second.id]

    def test_unknown_op_id_raises(self, tmp_path):
        from navig.skill_distill import DistillError, slice_ledger

        rec = _make_recorder(tmp_path)
        with pytest.raises(DistillError, match="not found"):
            slice_ledger(rec, op_ids=["op-nope"])


# ---------------------------------------------------------------------------
# distill — success-path filtering
# ---------------------------------------------------------------------------


class TestDistillFiltering:
    def test_failed_ops_become_pitfalls_not_steps(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(
            rec,
            "navig docker restart web",
            minutes_ago=30,
            status="failed",
            error="no such container",
            exit_code=2,
        )
        _record(rec, "navig docker restart web-1", minutes_ago=20)

        result = _distill(rec.get_last_n(50)[::-1])
        assert [s.command for s in result.steps] == ["navig docker restart web-1"]
        assert len(result.pitfalls) == 1
        assert result.pitfalls[0].exit_code == 2
        assert "no such container" in result.pitfalls[0].error

    def test_undone_operations_and_their_undos_are_excluded(self, tmp_path):
        rec = _make_recorder(tmp_path)
        target = _record(
            rec,
            "navig config set ui.theme dark",
            minutes_ago=30,
            op_type="config_change",
            undo_data={"key": "ui.theme", "old_value": "light", "new_value": "dark"},
        )
        _record(
            rec,
            f"navig undo {target.id}",
            minutes_ago=20,
            op_type="config_change",
            tags=["undo"],
            args={"undo_of": target.id},
        )
        keeper = _record(rec, "navig config set app.name demo", minutes_ago=10, op_type="config_change")

        result = _distill(rec.get_last_n(50)[::-1])
        assert [s.op_ids[0] for s in result.steps] == [keeper.id]
        assert result.undone_excluded == 2  # the undone target + the undo entry

    def test_failed_undo_does_not_exclude_target(self, tmp_path):
        rec = _make_recorder(tmp_path)
        target = _record(rec, "navig config set a 1", minutes_ago=30, op_type="config_change")
        _record(
            rec,
            f"navig undo {target.id}",
            minutes_ago=20,
            status="failed",
            tags=["undo"],
            args={"undo_of": target.id},
        )

        result = _distill(rec.get_last_n(50)[::-1])
        assert [s.op_ids[0] for s in result.steps] == [target.id]

    def test_meta_distill_invocations_are_excluded(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(rec, "navig skill distill --last 2h", minutes_ago=30)
        _record(rec, "navig skills distill --last 1h --force", minutes_ago=25)
        keeper = _record(rec, "navig host list", minutes_ago=10, op_type="read_query")

        result = _distill(rec.get_last_n(50)[::-1])
        assert [s.op_ids[0] for s in result.steps] == [keeper.id]
        assert result.meta_excluded == 2

    def test_consecutive_duplicates_collapse_with_count(self, tmp_path):
        rec = _make_recorder(tmp_path)
        for i in range(3):
            _record(rec, "navig service restart nginx", minutes_ago=30 - i, op_type="service_restart")
        _record(rec, "navig host list", minutes_ago=10, op_type="read_query")

        result = _distill(rec.get_last_n(50)[::-1])
        assert len(result.steps) == 2
        assert result.steps[0].count == 3
        assert len(result.steps[0].op_ids) == 3
        assert result.collapsed == 2

    def test_read_queries_stay_in_the_recipe(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(rec, "navig host list", minutes_ago=20, op_type="read_query")
        result = _distill(rec.get_last_n(50)[::-1])
        assert result.steps[0].reversibility == "none"

    def test_empty_slice_raises_honestly(self):
        from navig.skill_distill import DistillError

        with pytest.raises(DistillError, match="nothing to distill"):
            _distill([])

    def test_all_failed_slice_raises_honestly(self, tmp_path):
        from navig.skill_distill import DistillError

        rec = _make_recorder(tmp_path)
        _record(rec, "navig docker restart web", minutes_ago=20, status="failed", error="boom")

        with pytest.raises(DistillError, match="none form a successful path"):
            _distill(rec.get_last_n(50)[::-1])


# ---------------------------------------------------------------------------
# sanitize_command — placeholders + the secret sweep
# ---------------------------------------------------------------------------


class TestSanitizeCommand:
    def test_host_becomes_placeholder(self):
        from navig.skill_distill import sanitize_command

        text, found = sanitize_command("navig run uptime web-1", host="web-1")
        assert text == "navig run uptime <host>"
        assert found["<host>"] == "web-1"

    def test_windows_home_user_segment(self):
        from navig.skill_distill import sanitize_command

        text, found = sanitize_command(r"navig file add C:\Users\alice\notes.txt /srv/notes.txt")
        assert r"C:\Users\<user>\notes.txt" in text
        assert "alice" not in text
        assert found["<user>"] == "alice"

    def test_posix_home_user_segment(self):
        from navig.skill_distill import sanitize_command

        text, found = sanitize_command("navig file get /home/bob/app.log ./app.log")
        assert "/home/<user>/app.log" in text
        assert found["<user>"] == "bob"

    def test_email_and_ip_placeholders(self):
        from navig.skill_distill import sanitize_command

        text, found = sanitize_command("navig notify ops@example.com from 10.0.0.15")
        assert "<email>" in text and "<ip>" in text
        assert found["<email>"] == "ops@example.com"
        assert found["<ip>"] == "10.0.0.15"

    def test_secret_flag_value_is_swallowed(self):
        from navig.skill_distill import sanitize_command

        text, found = sanitize_command("navig db connect --password hunter2 --name prod")
        assert "hunter2" not in text
        assert "--password <secret>" in text
        assert found["<secret>"] == ""  # secrets never keep an example

    def test_sensitive_key_value_pair_is_swallowed(self):
        from navig.skill_distill import sanitize_command

        # The whole KEY=value token collapses to <secret> (the record-time
        # pattern sweep eats key+value together — safe, never the value).
        text, _ = sanitize_command("navig deploy --env API_TOKEN=abc123def")
        assert "abc123def" not in text
        assert "<secret>" in text

    def test_sensitive_dotted_key_value_never_leaks_value(self):
        from navig.skill_distill import sanitize_command

        text, _ = sanitize_command("navig deploy --set email.password=hunter2")
        assert "hunter2" not in text
        assert "<secret>" in text

    def test_redacted_marker_becomes_secret_placeholder(self):
        from navig.skill_distill import sanitize_command

        text, _ = sanitize_command("navig config set email.password ***REDACTED***")
        assert "***REDACTED***" not in text
        assert text.endswith("<secret>")

    def test_known_secret_pattern_is_redacted_then_placeholdered(self):
        from navig.skill_distill import sanitize_command

        token = "sk_live_" + "a1" * 12
        text, _ = sanitize_command(f"navig pay charge --key {token}")
        assert token not in text
        assert "<secret>" in text

    def test_opaque_long_token_is_swallowed(self):
        from navig.skill_distill import sanitize_command

        token = "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6"  # 32 chars, letters+digits
        text, _ = sanitize_command(f"navig web fetch --session {token}")
        assert token not in text
        assert "<secret>" in text

    def test_operation_ids_and_slugs_stay_literal(self):
        from navig.skill_distill import sanitize_command

        cmd = "navig undo op-20260716120000-abcd1234"
        text, found = sanitize_command(cmd)
        assert text == cmd
        assert found == {}

    def test_plain_command_untouched(self):
        from navig.skill_distill import sanitize_command

        cmd = "navig host list"
        assert sanitize_command(cmd) == (cmd, {})


# ---------------------------------------------------------------------------
# render_skill_md — structural validity per the authoring standard
# ---------------------------------------------------------------------------


def _rich_result(tmp_path):
    rec = _make_recorder(tmp_path)
    _record(rec, "navig host list", minutes_ago=40, op_type="read_query")
    _record(
        rec,
        "navig config set ui.theme dark",
        minutes_ago=30,
        op_type="config_change",
        undo_data={"key": "ui.theme", "old_value": "light", "new_value": "dark"},
    )
    _record(
        rec,
        "navig db connect --password hunter2",
        minutes_ago=25,
        status="failed",
        error="access denied for token sk_live_" + "a1" * 12,
        exit_code=1,
    )
    _record(
        rec,
        "navig file remove /srv/old.log --host web-1",
        minutes_ago=20,
        op_type="file_delete",
        host="web-1",
    )
    return _distill(rec.get_last_n(50)[::-1], window_label="last 2h")


class TestRenderSkillMd:
    def test_frontmatter_parses_with_required_fields(self, tmp_path):
        import yaml

        from navig.skill_distill import render_skill_md

        md = render_skill_md(_rich_result(tmp_path))
        assert md.startswith("---")
        fm = yaml.safe_load(md.split("---", 2)[1])
        assert fm["name"]
        assert len(fm["description"]) >= 40
        assert "when the user" in fm["description"].lower()
        assert fm["safety"] in {"safe", "elevated", "destructive"}

    def test_safety_is_worst_step_label(self, tmp_path):
        result = _rich_result(tmp_path)  # includes a red file_delete step
        assert result.safety == "destructive"

    def test_safety_elevated_for_yellow_only(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(rec, "navig service restart nginx", minutes_ago=10, op_type="service_restart")
        assert _distill(rec.get_last_n(10)[::-1]).safety == "elevated"

    def test_safety_safe_for_reads_and_greens(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(rec, "navig host list", minutes_ago=20, op_type="read_query")
        _record(
            rec,
            "navig config set a 1",
            minutes_ago=10,
            op_type="config_change",
            undo_data={"key": "a", "old_value": None, "new_value": "1"},
        )
        assert _distill(rec.get_last_n(10)[::-1]).safety == "safe"

    def test_steps_numbered_in_order_with_danger_annotations(self, tmp_path):
        from navig.skill_distill import render_skill_md

        md = render_skill_md(_rich_result(tmp_path))
        steps = [ln for ln in md.splitlines() if ln[:2].rstrip(".").isdigit()]
        assert steps[0].startswith("1. `navig host list`")
        assert "irreversible (red)" in steps[-1]

    def test_pitfalls_rendered_with_redacted_error(self, tmp_path):
        from navig.skill_distill import render_skill_md

        md = render_skill_md(_rich_result(tmp_path))
        assert "## Pitfalls observed" in md
        assert "hunter2" not in md
        assert "sk_live_a1" not in md  # error text swept too

    def test_placeholder_table_never_shows_secret_example(self, tmp_path):
        from navig.skill_distill import render_skill_md

        md = render_skill_md(_rich_result(tmp_path))
        assert "| `<host>` | `web-1` |" in md
        assert "| `<secret>` | _(secret — value never stored)_ |" in md

    def test_provenance_counts_present(self, tmp_path):
        from navig.skill_distill import render_skill_md

        result = _rich_result(tmp_path)
        md = render_skill_md(result)
        assert "## Provenance" in md
        assert f"{result.total_records} operation(s) in slice" in md

    def test_description_never_contains_frontmatter_terminator(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(rec, "navig run echo --- weird", minutes_ago=10)
        result = _distill(rec.get_last_n(10)[::-1])
        assert "---" not in result.description

    def test_derived_name_reflects_resources(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(rec, "navig config set a 1", minutes_ago=20, op_type="config_change")
        _record(rec, "navig host use web", minutes_ago=10, op_type="host_switch")
        result = _distill(rec.get_last_n(10)[::-1])
        assert result.slug == "config-host-workflow"

    def test_explicit_name_is_slugified(self, tmp_path):
        rec = _make_recorder(tmp_path)
        _record(rec, "navig host list", minutes_ago=10, op_type="read_query")
        result = _distill(rec.get_last_n(10)[::-1], name="My Deploy Flow!")
        assert result.slug == "my-deploy-flow"


# ---------------------------------------------------------------------------
# CLI — navig skill distill
# ---------------------------------------------------------------------------


class TestDistillCli:
    def _invoke(self, args, recorder, obj=None):
        from typer.testing import CliRunner

        from navig.commands.skills import skills_app

        with patch("navig.operation_recorder.get_operation_recorder", return_value=recorder):
            return CliRunner().invoke(skills_app, args, obj=obj if obj is not None else {})

    def _seed(self, tmp_path):
        rec = _make_recorder(tmp_path / "hist")
        _record(rec, "navig config set ui.theme dark", minutes_ago=30, op_type="config_change")
        _record(rec, "navig docker restart web", minutes_ago=20, status="failed", error="boom", exit_code=2)
        _record(rec, "navig docker restart web-1", minutes_ago=10, op_type="docker_command")
        return rec

    def test_writes_skill_and_prints_lint_nudge(self, tmp_path):
        rec = self._seed(tmp_path)
        out = tmp_path / "skills"
        result = self._invoke(["distill", "--last", "2h", "--name", "demo-flow", "--out", str(out)], rec)
        assert result.exit_code == 0, result.output
        skill_file = out / "demo-flow" / "SKILL.md"
        assert skill_file.is_file()
        assert "navig skill lint" in result.output

    def test_refuses_overwrite_without_force(self, tmp_path):
        rec = self._seed(tmp_path)
        out = tmp_path / "skills"
        first = self._invoke(["distill", "--name", "demo-flow", "--out", str(out)], rec)
        assert first.exit_code == 0
        second = self._invoke(["distill", "--name", "demo-flow", "--out", str(out)], rec)
        assert second.exit_code == 1
        assert "--force" in second.output

    def test_force_overwrites(self, tmp_path):
        rec = self._seed(tmp_path)
        out = tmp_path / "skills"
        assert self._invoke(["distill", "--name", "demo-flow", "--out", str(out)], rec).exit_code == 0
        result = self._invoke(["distill", "--name", "demo-flow", "--out", str(out), "--force"], rec)
        assert result.exit_code == 0

    def test_default_output_is_the_user_skill_store(self, tmp_path, monkeypatch):
        rec = self._seed(tmp_path)
        store = tmp_path / "store"
        monkeypatch.setenv("NAVIG_STORE_DIR", str(store))
        result = self._invoke(["distill", "--name", "demo-flow"], rec)
        assert result.exit_code == 0, result.output
        assert (store / "skills" / "demo-flow" / "SKILL.md").is_file()

    def test_json_stdout_is_one_pure_document(self, tmp_path):
        rec = self._seed(tmp_path)
        out = tmp_path / "skills"
        result = self._invoke(
            ["distill", "--name", "demo-flow", "--out", str(out), "--json"], rec
        )
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output)  # whole stdout = exactly one document
        assert payload["path"].endswith("SKILL.md")
        assert payload["counts"]["steps"] == 2
        assert payload["counts"]["pitfalls"] == 1
        assert payload["safety"] == "elevated"

    def test_empty_window_is_honest_and_exits_1(self, tmp_path):
        rec = _make_recorder(tmp_path / "hist")
        result = self._invoke(["distill", "--last", "1h"], rec)
        assert result.exit_code == 1
        assert "nothing to distill" in result.output

    def test_empty_window_json_is_an_error_document(self, tmp_path):
        rec = _make_recorder(tmp_path / "hist")
        result = self._invoke(["distill", "--last", "1h", "--json"], rec)
        assert result.exit_code == 1
        payload = json.loads(result.output)
        assert "nothing to distill" in payload["error"]

    def test_invalid_duration_fails_cleanly(self, tmp_path):
        rec = _make_recorder(tmp_path / "hist")
        result = self._invoke(["distill", "--last", "2 fortnights"], rec)
        assert result.exit_code == 1
        assert "invalid duration" in result.output

    def test_ops_flag_selects_exactly_those_operations(self, tmp_path):
        rec = self._seed(tmp_path)
        target = _record(rec, "navig host list", minutes_ago=5, op_type="read_query")
        out = tmp_path / "skills"
        result = self._invoke(
            ["distill", "--ops", target.id, "--name", "one-op", "--out", str(out)], rec
        )
        assert result.exit_code == 0, result.output
        md = (out / "one-op" / "SKILL.md").read_text(encoding="utf-8")
        assert "1. `navig host list`" in md
        assert "docker restart" not in md

    def test_emitted_skill_passes_navig_skill_lint(self, tmp_path):
        from typer.testing import CliRunner

        from navig.commands.skills import skills_app

        rec = self._seed(tmp_path)
        out = tmp_path / "skills"
        assert self._invoke(["distill", "--name", "demo-flow", "--out", str(out)], rec).exit_code == 0

        lint = CliRunner().invoke(skills_app, ["lint", str(out / "demo-flow")], obj={})
        assert lint.exit_code == 0, lint.output
        assert "meets the NAVIG authoring standard" in lint.output

    def test_enriches_the_inflight_middleware_record(self, tmp_path):
        """The T-068 interlock: the distill invocation's own ledger line is
        claimed, typed file_create, and carries green undo_data — so the
        draft itself is undoable via `navig undo`."""
        rec = self._seed(tmp_path)
        out = tmp_path / "skills"
        inflight = rec.start_operation(command="navig skill distill --last 2h --name demo-flow")
        obj = {"_operation_record": inflight, "_operation_start": 0.0}
        result = self._invoke(
            ["distill", "--name", "demo-flow", "--out", str(out)], rec, obj=obj
        )
        assert result.exit_code == 0, result.output
        assert "_operation_record" not in obj  # claimed, not left for atexit

        entry = rec.get_operation(inflight.id)
        assert entry is not None
        assert entry.operation_type.value == "file_create"
        assert entry.undo_data["file_path"].endswith("SKILL.md")
        assert entry.reversibility == "green"

    def test_ai_unavailable_fails_with_clean_hint(self, tmp_path):
        from navig.agent.skill_distiller import SkillDraftUnavailableError

        rec = self._seed(tmp_path)
        out = tmp_path / "skills"
        with patch(
            "navig.agent.skill_distiller.draft_distilled_skill",
            side_effect=SkillDraftUnavailableError("No AI backend is configured or reachable"),
        ):
            result = self._invoke(
                ["distill", "--name", "demo-flow", "--out", str(out), "--ai"], rec
            )
        assert result.exit_code == 1
        assert "No AI backend" in result.output
        assert not (out / "demo-flow" / "SKILL.md").exists()  # nothing written on refusal


# ---------------------------------------------------------------------------
# AI seam — navig.agent.skill_distiller (no real LLM; llm_generate patched)
# ---------------------------------------------------------------------------


class TestAiSeam:
    def _deterministic_md(self, tmp_path) -> str:
        from navig.skill_distill import render_skill_md

        return render_skill_md(_rich_result(tmp_path))

    def test_frontmatter_survives_verbatim(self, tmp_path):
        from navig.agent.skill_distiller import draft_distilled_skill

        md = self._deterministic_md(tmp_path)
        body = md.split("---", 2)[2]
        with patch("navig.llm.generate.llm_generate", return_value="Intro prose.\n" + body):
            out = draft_distilled_skill(md)
        assert out.split("---", 2)[1] == md.split("---", 2)[1]

    def test_dropped_command_is_refused(self, tmp_path):
        from navig.agent.skill_distiller import draft_distilled_skill

        md = self._deterministic_md(tmp_path)
        with patch("navig.llm.generate.llm_generate", return_value="## Steps\n\nAll rewritten, no commands."):
            with pytest.raises(RuntimeError, match="dropped or altered"):
                draft_distilled_skill(md)

    def test_whole_response_fence_is_stripped(self, tmp_path):
        from navig.agent.skill_distiller import draft_distilled_skill

        md = self._deterministic_md(tmp_path)
        body = md.split("---", 2)[2].strip()
        with patch("navig.llm.generate.llm_generate", return_value=f"```markdown\n{body}\n```"):
            out = draft_distilled_skill(md)
        assert "```markdown" not in out

    def test_empty_response_is_refused(self, tmp_path):
        from navig.agent.skill_distiller import draft_distilled_skill

        md = self._deterministic_md(tmp_path)
        with patch("navig.llm.generate.llm_generate", return_value=""):
            with pytest.raises(RuntimeError, match="empty"):
                draft_distilled_skill(md)
