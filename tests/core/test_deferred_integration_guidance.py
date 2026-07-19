"""Tests for post-init deferred integration guidance.

Validates that when an optional integration step is skipped or fails, onboarding tells the
user how to finish it later — with a REAL command and a short description of what it enables.

These tests used to assert the commands ``navig matrix setup`` / ``navig social setup`` /
``navig email setup`` / ``navig telegram setup``. Two of those do not exist: ``matrix`` and
``social`` have no ``setup`` verb (``navig matrix setup`` exits 2, "No such command"). So the
old tests demanded that onboarding print a command that would 404 the moment a user ran it.
The source was fixed to point those integrations at ``navig init --reconfigure`` — which
revisits exactly those wizard steps — and these tests were left asserting the broken
contract. That is why they were red on ``main``.

The rewrite keys on the per-step DESCRIPTION (all four steps now share the reconfigure
command, so the description is what distinguishes them) and adds the guard that would have
caught the original hallucination: every deferred command must actually RESOLVE in the CLI.
"""

from __future__ import annotations

import io
import sys

import pytest

from navig.onboarding.engine import EngineState, StepRecord
from navig.onboarding.runner import (
    _deferred_integration_commands,
    _print_verification_dashboard,
)

pytestmark = pytest.mark.integration


def _real_command_paths() -> set[str]:
    """Every command path the CLI actually exposes, e.g. ``navig lighthouse deploy``."""
    from navig.registry.manifest import build_public_manifest

    return {c["path"] for c in build_public_manifest(validate=False)["commands"]}


def _command_resolves(command: str, real_paths: set[str]) -> bool:
    """True if ``command`` (flags stripped) is a real CLI command.

    Matches the longest leading run of non-flag tokens that is a registered path, so
    ``navig init --reconfigure`` resolves via ``navig init`` and ``navig matrix setup``
    (which is not registered at any prefix) does not.
    """
    tokens = [t for t in command.split() if not t.startswith("-")]
    return any(" ".join(tokens[:n]) in real_paths for n in range(len(tokens), 0, -1))


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
    return dict.fromkeys(step_ids, "optional")


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

        # One deferred entry per skipped optional integration, each with a real command
        # and its own description.
        assert len(result) == 4
        descriptions = " ".join(desc.lower() for _, desc in result)
        assert "matrix" in descriptions
        assert "smtp" in descriptions or "email" in descriptions
        assert "social" in descriptions
        assert "telegram" in descriptions

    def test_failed_optional_steps_are_deferred(self) -> None:
        state = _make_state(("matrix", "failed"))
        tiers = _optional_tiers("matrix")

        result = _deferred_integration_commands(state, tiers)

        assert len(result) == 1
        cmd, desc = result[0]
        # Matrix has no `setup` verb; the integration is finished by re-running the wizard.
        assert cmd == "navig init --reconfigure"
        assert "matrix" in desc.lower()

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
        assert isinstance(cmd, str) and cmd
        assert isinstance(description, str) and description

    def test_descriptions_are_informative(self) -> None:
        """Each deferred integration must carry its own non-empty, specific description."""
        state = _make_state(
            ("matrix", "skipped"),
            ("email", "skipped"),
            ("social-networks", "skipped"),
            ("telegram-bot", "skipped"),
        )
        tiers = _optional_tiers("matrix", "email", "social-networks", "telegram-bot")

        result = _deferred_integration_commands(state, tiers)

        descriptions = [desc for _, desc in result]
        assert all(desc.strip() for desc in descriptions)
        # Four distinct integrations must produce four distinct descriptions — otherwise
        # the deferred list is four identical lines telling the user nothing.
        assert len(set(descriptions)) == 4

    def test_partial_deferred(self) -> None:
        state = _make_state(
            ("matrix", "completed"),
            ("email", "skipped"),
            ("social-networks", "skipped"),
        )
        tiers = _optional_tiers("matrix", "email", "social-networks")

        result = _deferred_integration_commands(state, tiers)

        # matrix completed → not deferred; email + social skipped → deferred.
        assert len(result) == 2
        descriptions = " ".join(desc.lower() for _, desc in result)
        assert "matrix" not in descriptions
        assert ("smtp" in descriptions or "email" in descriptions)
        assert "social" in descriptions

    def test_every_deferred_command_actually_resolves(self) -> None:
        """The bug that made this file red: a deferred command that does not exist.

        `navig matrix setup` / `navig social setup` were printed as next steps and 404'd
        on sight. Onboarding must only ever hand the user a command the CLI actually has —
        so exercise every optional integration and assert each deferred command resolves in
        the real command manifest.
        """
        real_paths = _real_command_paths()
        optional_ids = ("matrix", "email", "social-networks", "telegram-bot", "lighthouse", "deck-deploy")
        state = _make_state(*[(sid, "skipped") for sid in optional_ids])
        tiers = _optional_tiers(*optional_ids)

        for cmd, _ in _deferred_integration_commands(state, tiers):
            assert _command_resolves(cmd, real_paths), (
                f"deferred command {cmd!r} does not resolve in the CLI — onboarding would "
                f"tell the user to run a command that exits 2 (No such command)."
            )


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
        # The two integrations share the reconfigure command, so they are told apart by
        # their descriptions — each must appear, on a line that also carries the command.
        lines_by_topic: dict[str, str] = {}
        for line in output.splitlines():
            if "navig init --reconfigure" not in line:
                continue
            low = line.lower()
            if "matrix" in low:
                lines_by_topic["matrix"] = line
            if "smtp" in low or "email" in low:
                lines_by_topic["email"] = line
        assert "matrix" in lines_by_topic, "matrix deferral not found in output"
        assert "email" in lines_by_topic, "email deferral not found in output"

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
            if "navig init --reconfigure" in line and "matrix" in line.lower():
                # The description rides on the same line as the command, not a bare verb.
                assert len(line.strip()) > len("- navig init --reconfigure")
                break
        else:
            raise AssertionError("matrix deferral not found on a command line in output")
