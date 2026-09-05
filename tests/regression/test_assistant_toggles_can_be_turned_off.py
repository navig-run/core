"""Two assistant toggles could not be turned off, and nothing said which key to set.

`navig config set` stores its argument as a STRING and `bool("false")` is True, so
both of these returned True for *every* value an operator could type:

    should_auto_analyze()    gates auto-analysis in assistant_hooks + auto_detection
    requires_confirmation()  gates the destructive-operation prompt in
                             proactive_display.check_pre_execution_warnings

Neither key was documented as a `navig config set` toggle, which is why the
config-boolean guard did not cover them: its scope is deliberately "keys a user is
documented as being able to set". They are documented now, so that guard holds the
line here from this point on — the property it was built with, exercised for real
(41 documented toggles before this change, 44 after, still at zero).

Note the polarity difference, because it decides how alarming each one is:
`requires_confirmation` is fail-SAFE — an uncoerced `"false"` kept the confirmation
ON, so nothing was ever unguarded; the setting was simply inert and the operator who
turned it off kept being prompted with no way to tell why.
"""
from __future__ import annotations

import pytest

from navig.proactive_assistant import ProactiveAssistant

OFF_SPELLINGS = ["false", "False", "no", "off", "0", False]
ON_SPELLINGS = ["true", "yes", "on", "1", True]


def _assistant(**config: object) -> ProactiveAssistant:
    a = ProactiveAssistant.__new__(ProactiveAssistant)
    a.assistant_config = dict(config)
    return a


@pytest.mark.parametrize("value", OFF_SPELLINGS)
def test_auto_analysis_can_be_turned_off(value: object) -> None:
    assert _assistant(auto_analysis=value).should_auto_analyze() is False


@pytest.mark.parametrize("value", ON_SPELLINGS)
def test_auto_analysis_stays_on_when_enabled(value: object) -> None:
    """The partner — a fix that always returned False would satisfy the test above
    and silently disable auto-analysis for everyone."""
    assert _assistant(auto_analysis=value).should_auto_analyze() is True


def test_auto_analysis_defaults_to_on_when_unset() -> None:
    assert _assistant().should_auto_analyze() is True


@pytest.mark.parametrize("value", OFF_SPELLINGS)
def test_confirmation_can_be_turned_off(value: object) -> None:
    assert _assistant(confirmation_required=value).requires_confirmation() is False


@pytest.mark.parametrize("value", ON_SPELLINGS)
def test_confirmation_stays_on_when_enabled(value: object) -> None:
    """The important partner. This gate blocks DESTRUCTIVE operations, so a fix that
    made it fall to False would remove a safety prompt — the opposite of the bug."""
    assert _assistant(confirmation_required=value).requires_confirmation() is True


def test_confirmation_defaults_to_on_when_unset() -> None:
    """Absent config must mean "confirm", never "proceed"."""
    assert _assistant().requires_confirmation() is True


def test_the_destructive_gate_actually_consumes_the_toggle() -> None:
    """Anti-vacuity: the tests above assert a method's return value. If nothing reads
    it, they guard a number in a vacuum. This pins the real consumer — the branch in
    `check_pre_execution_warnings` that stops a destructive command."""
    import inspect

    from navig.proactive.proactive_display import ProactiveDisplay

    source = inspect.getsource(ProactiveDisplay.check_pre_execution_warnings)
    assert "requires_confirmation()" in source, (
        "the destructive-operation gate no longer reads requires_confirmation() — "
        "these tests would be guarding a value nothing consumes"
    )


def test_a_failed_audit_write_is_not_reported_through_a_quiet_sink(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """`log_audit` used `ch.dim` for a lost AUDIT line. An audit trail whose gaps are
    invisible is worse than no trail: it reads as a complete record."""
    from navig import console_helper as ch

    a = ProactiveAssistant.__new__(ProactiveAssistant)
    a.ai_context_dir = tmp_path

    warnings: list[str] = []
    dims: list[str] = []
    monkeypatch.setattr(ch, "warning", lambda m, *x, **k: warnings.append(m))
    monkeypatch.setattr(ch, "dim", lambda m, *x, **k: dims.append(m))

    real_open = open

    def _open(file, mode="r", *x, **k):  # noqa: ANN001
        if "assistant_audit.log" in str(file):
            raise OSError("read-only file system")
        return real_open(file, mode, *x, **k)

    monkeypatch.setattr("builtins.open", _open)
    a.log_audit("suggestion_shown", {"cmd": "navig host remove prod"})

    assert warnings, "a lost audit line was reported only through a quiet sink"
    assert "gap" in " ".join(warnings), (
        "the message must say the trail has a GAP — that is the part a reader of the "
        "log can never recover on their own"
    )


def test_a_successful_audit_write_is_silent_and_persisted(tmp_path) -> None:
    """The partner: the normal path must stay quiet and must actually write."""
    a = ProactiveAssistant.__new__(ProactiveAssistant)
    a.ai_context_dir = tmp_path

    a.log_audit("suggestion_shown", {"cmd": "navig host list"})

    body = (tmp_path / "assistant_audit.log").read_text(encoding="utf-8")
    assert "suggestion_shown" in body and "navig host list" in body
