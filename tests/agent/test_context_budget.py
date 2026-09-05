"""Truncation must carry its cause, and must not change the condensed contract.

Identity used to be cut silently in three places, so the model answered
confidently from a fragment of its own instructions. A budget also fails *safe*
where a keyword classifier fails *open*: shortening a source is visible, never
loading it is not.
"""

from __future__ import annotations

import pytest

from navig.agent.conv.budget import BudgetReport, apply_budget
from navig.agent.conv.soul import _MAX_SOUL_CHARS, _condense_soul


class TestCauseAttribution:
    def test_per_file_limit_is_attributed(self):
        _out, rep = apply_budget(
            [("SOUL.md", "/p", "a" * 9_000)], per_file_max=4_000, total_max=100_000
        )
        f = rep.files[0]
        assert f.truncated and f.causes == ("per-file-limit",)
        assert f.raw_chars == 9_000 and f.injected_chars == 4_000

    def test_total_limit_is_attributed(self):
        _out, rep = apply_budget(
            [("A", "/a", "a" * 900), ("B", "/b", "b" * 900)],
            per_file_max=5_000,
            total_max=1_000,
        )
        assert rep.files[0].truncated is False
        assert rep.files[1].causes == ("total-limit",)

    def test_both_causes_can_apply_to_one_file(self):
        _out, rep = apply_budget(
            [("A", "/a", "a" * 500), ("B", "/b", "b" * 9_000)],
            per_file_max=4_000,
            total_max=1_000,
        )
        assert set(rep.files[1].causes) == {"per-file-limit", "total-limit"}

    def test_removed_pct_is_reported(self):
        _out, rep = apply_budget(
            [("S", "/p", "x" * 10_000)], per_file_max=5_000, total_max=100_000
        )
        assert rep.files[0].removed_pct == 50

    def test_untruncated_file_reports_zero_removed(self):
        _out, rep = apply_budget([("S", "/p", "short")], per_file_max=100, total_max=100)
        assert rep.files[0].removed_pct == 0 and rep.has_truncation is False

    def test_empty_source_is_recorded_not_dropped(self):
        _out, rep = apply_budget([("S", "/p", "")], per_file_max=100, total_max=100)
        assert rep.files[0].raw_chars == 0 and rep.files[0].truncated is False

    def test_priority_order_is_respected(self):
        """Earlier items keep their allowance; later ones absorb the pressure."""
        out, _rep = apply_budget(
            [("first", "/a", "a" * 800), ("second", "/b", "b" * 800)],
            per_file_max=5_000,
            total_max=900,
        )
        assert out[0][0] == "first" and len(out[0][1]) == 800


class TestSignatureAndNotes:
    def test_signature_is_empty_when_clean(self):
        _out, rep = apply_budget([("S", "/p", "ok")], per_file_max=100, total_max=100)
        assert rep.signature() == "" and rep.prompt_note() == ""

    def test_signature_is_stable_for_the_same_state(self):
        args = ([("S", "/p", "x" * 9_000)],)
        _o1, r1 = apply_budget(*args, per_file_max=4_000, total_max=100_000)
        _o2, r2 = apply_budget(*args, per_file_max=4_000, total_max=100_000)
        assert r1.signature() == r2.signature() != ""

    def test_signature_is_order_independent(self):
        a = [("A", "/a", "x" * 9_000), ("B", "/b", "y" * 9_000)]
        _o1, r1 = apply_budget(a, per_file_max=4_000, total_max=100_000)
        _o2, r2 = apply_budget(list(reversed(a)), per_file_max=4_000, total_max=100_000)
        assert r1.signature() == r2.signature()

    def test_prompt_note_tells_the_model_what_it_cannot_see(self):
        _out, rep = apply_budget(
            [("SOUL.md", "/p", "x" * 9_000)], per_file_max=4_000, total_max=100_000
        )
        note = rep.prompt_note()
        assert "SOUL.md" in note and "shortened" in note
        assert "say so rather than guessing" in note

    def test_warning_lines_overflow_is_summarised(self):
        items = [(f"F{i}", f"/{i}", "x" * 9_000) for i in range(6)]
        _out, rep = apply_budget(items, per_file_max=4_000, total_max=10_000_000)
        lines = rep.warning_lines(max_files=3)
        assert len(lines) == 4 and "more truncated source" in lines[-1]

    def test_empty_report_is_safe(self):
        rep = BudgetReport()
        assert rep.has_truncation is False and rep.warning_lines() == []


class TestCondensedOutputContractPreserved:
    """The budget must not change bytes anything downstream already asserts."""

    def test_verbatim_tier_keeps_the_ellipsis_marker(self):
        raw = "y" * (_MAX_SOUL_CHARS + 500)
        out = _condense_soul(raw, True, "workspace")
        assert out.endswith("…")
        assert len(out) <= _MAX_SOUL_CHARS + 2

    def test_verbatim_tier_under_cap_is_untouched(self):
        raw = "short and sweet"
        assert _condense_soul(raw, True, "workspace") == raw

    def test_context_tier_is_a_bare_hard_cut(self):
        raw = "z" * 3_000
        out = _condense_soul(raw, False, "context")
        assert out == raw[:2000]
        assert not out.endswith("…"), "the non-rich tier never carried a marker"

    @pytest.mark.parametrize(
        "source", ["persona", "space", "folder-space", "identity", "workspace"]
    )
    def test_every_human_authored_source_is_verbatim(self, source):
        assert _condense_soul("mine", True, source) == "mine"
