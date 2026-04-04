"""Tests for post-init deferred integration guidance.

Validates that deferred integration entries include a short description
explaining what each integration enables, as required by the issue.
"""

from __future__ import annotations

import io
import sys

from navig.onboarding.engine import EngineState, StepRecord
from navig.onboarding.runner import (
    _deferred_integration_commands,
    _print_verification_dashboard,
)


def _make_state(*step_specs: tuple[str, str]) -> EngineState:
    """Build an EngineState with steps from (id, status) pairs."""
    state = EngineState()
    for step_id, status in step_specs:
        state.steps.append(
            StepRecord(
                id=step_id,
                title=step_id.replace("-", " ").title(),
                status=status,
                completed_at="",
                duration_ms=0,
                output={},
            )
        )
    return state


def _optional_tiers(*step_ids: str) -> dict[str, str]:
    """Return a tier mapping marking all given step IDs as 'optional'."""
    return {step_id: "optional" for step_id in step_ids}


class TestDeferredIntegrationCommands:
    def test_skipped_optional_steps_are_deferred(self) -> None:
        state = _make_state(
            ("matrix", "skipped"),
            ("email", "skipped"),
            ("social-networks", "skipped"),
            ("telegram-bot", "skipped"),
        )
        tiers = _optional_tiers("matrix", "email", "social-networks", "telegram-bot")

        result = _deferred_integration_commands(state, tiers)

        assert len(result) == 4
        commands = [cmd for cmd, _ in result]
        assert "navig matrix setup" in commands
        assert "navig email setup" in commands
        assert "navig social setup" in commands
        assert "navig telegram setup" in commands

    def test_failed_optional_steps_are_deferred(self) -> None:
        state = _make_state(("matrix", "failed"))
        tiers = _optional_tiers("matrix")

        result = _deferred_integration_commands(state, tiers)

        assert len(result) == 1
        assert result[0][0] == "navig matrix setup"

    def test_completed_optional_steps_are_not_deferred(self) -> None:
        state = _make_state(("matrix", "completed"))
        tiers = _optional_tiers("matrix")

        result = _deferred_integration_commands(state, tiers)

        assert result == []

    def test_non_optional_steps_are_not_deferred(self) -> None:
        state = _make_state(("matrix", "skipped"))
        # matrix is essential, not optional
        tiers = {"matrix": "essential"}

        result = _deferred_integration_commands(state, tiers)

        assert result == []

    def test_each_entry_is_tuple_with_command_and_description(self) -> None:
        state = _make_state(("matrix", "skipped"))
        tiers = _optional_tiers("matrix")

        result = _deferred_integration_commands(state, tiers)

        assert len(result) == 1
        cmd, description = result[0]
        assert cmd == "navig matrix setup"
        assert isinstance(description, str)
        assert len(description) > 0

    def test_descriptions_are_informative(self) -> None:
        """Each deferred integration must include a non-empty description."""
        state = _make_state(
            ("matrix", "skipped"),
            ("email", "skipped"),
            ("social-networks", "skipped"),
            ("telegram-bot", "skipped"),
        )
        tiers = _optional_tiers("matrix", "email", "social-networks", "telegram-bot")

        result = _deferred_integration_commands(state, tiers)

        desc_by_cmd = {cmd: desc for cmd, desc in result}
        assert desc_by_cmd["navig matrix setup"]
        assert desc_by_cmd["navig email setup"]
        assert desc_by_cmd["navig social setup"]
        assert desc_by_cmd["navig telegram setup"]

    def test_partial_deferred(self) -> None:
        state = _make_state(
            ("matrix", "completed"),
            ("email", "skipped"),
            ("social-networks", "skipped"),
        )
        tiers = _optional_tiers("matrix", "email", "social-networks")

        result = _deferred_integration_commands(state, tiers)

        commands = [cmd for cmd, _ in result]
        assert "navig matrix setup" not in commands
        assert "navig email setup" in commands
        assert "navig social setup" in commands


class TestPrintVerificationDashboard:
    def _capture_dashboard(self, state: EngineState, tiers: dict[str, str]) -> str:
        buf = io.StringIO()
        original = sys.stdout
        sys.stdout = buf
        try:
            _print_verification_dashboard(state, tiers)
        finally:
            sys.stdout = original
        return buf.getvalue()

    def test_deferred_section_shows_descriptions(self) -> None:
        state = _make_state(("matrix", "skipped"), ("email", "skipped"))
        tiers = _optional_tiers("matrix", "email")

        output = self._capture_dashboard(state, tiers)

        assert "Deferred integrations" in output
        # Each command must appear on the same line as its description
        lines_by_cmd: dict[str, str] = {}
        for line in output.splitlines():
            if "navig matrix setup" in line:
                lines_by_cmd["matrix"] = line
            if "navig email setup" in line:
                lines_by_cmd["email"] = line
        assert "matrix" in lines_by_cmd, "navig matrix setup not found in output"
        assert "email" in lines_by_cmd, "navig email setup not found in output"
        # Descriptions must appear on the same line as their commands
        assert "matrix" in lines_by_cmd["matrix"].lower() or len(lines_by_cmd["matrix"].strip()) > len("- navig matrix setup")
        assert "smtp" in lines_by_cmd["email"].lower() or "email" in lines_by_cmd["email"].lower()

    def test_no_deferred_section_when_all_completed(self) -> None:
        state = _make_state(("matrix", "completed"), ("email", "completed"))
        tiers = _optional_tiers("matrix", "email")

        output = self._capture_dashboard(state, tiers)

        assert "Deferred integrations" not in output

    def test_recommended_next_command_shown_when_steps_not_finished(self) -> None:
        state = _make_state(("ai-provider", "skipped"), ("matrix", "completed"))
        tiers = {"ai-provider": "recommended", "matrix": "optional"}

        output = self._capture_dashboard(state, tiers)

        assert "Recommended" in output
        assert "navig init --reconfigure" in output

    def test_description_appears_on_same_line_as_command(self) -> None:
        state = _make_state(("matrix", "skipped"))
        tiers = _optional_tiers("matrix")

        output = self._capture_dashboard(state, tiers)

        for line in output.splitlines():
            if "navig matrix setup" in line:
                # The description must be on the same line as the command
                assert len(line.strip()) > len("- navig matrix setup")
                break
        else:
            raise AssertionError("navig matrix setup not found in output")
