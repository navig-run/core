"""
Tests for onboarding renderer functions.

Covers:
  - render_step_success   → green ✓ marker + label + detail
  - render_step_skipped   → dim · marker + label
  - render_step_failure   → red ✗ marker + error + fix hint
  - render_step_already_done → dim · marker + "done"
  - render_step_in_progress  → dim · marker + "…"
  - _label                → maps known step IDs to short labels
  - render_progress       → pending-step line
"""

from __future__ import annotations

import pytest

from navig.onboarding import renderer
from navig.onboarding.engine import StepRecord
from navig.onboarding.renderer import (
    _label,
    _strip_ansi,
    render_progress,
    render_step_already_done,
    render_step_failure,
    render_step_in_progress,
    render_step_skipped,
    render_step_success,
)

pytestmark = pytest.mark.integration

# ── Helpers ──────────────────────────────────────────────────────────────────


def _record(
    step_id: str = "workspace-init",
    title: str = "Initialize workspace",
    status: str = "completed",
    output: dict | None = None,
    error: str | None = None,
    duration_ms: int = 42,
) -> StepRecord:
    return StepRecord(
        id=step_id,
        title=title,
        status=status,
        completed_at="2026-01-01T00:00:00",
        duration_ms=duration_ms,
        output=output or {},
        error=error,
    )


# ── _label ───────────────────────────────────────────────────────────────────


class TestLabel:
    def test_known_step_id(self) -> None:
        assert _label("workspace-init", "fallback") == "workspace"

    def test_ai_provider_label(self) -> None:
        assert _label("ai-provider", "fallback") == "ai provider"

    def test_unknown_step_id_uses_fallback(self) -> None:
        assert _label("unknown-step", "My Fallback Title") == "My Fallback Title"

    def test_fallback_truncated(self) -> None:
        long_fallback = "A" * 30
        result = _label("unknown-step", long_fallback)
        assert len(result) <= 18


# ── render_step_success ──────────────────────────────────────────────────────


class TestRenderStepSuccess:
    def test_contains_check_mark(self) -> None:
        # The renderer picks its glyph set at import time from the terminal it is writing
        # to (Unicode vs ASCII), so the assertion follows the set it actually chose — a
        # pytest run with no LANG/stdout encoding gets the ASCII "+" (2026-10-30, outpost).
        rec = _record()
        output = render_step_success(rec)
        plain = _strip_ansi(output)
        assert renderer._ICON_OK in plain

    def test_contains_label(self) -> None:
        rec = _record(step_id="ai-provider", title="Choose AI provider")
        output = render_step_success(rec)
        plain = _strip_ansi(output)
        assert "ai provider" in plain

    def test_slow_step_shows_duration(self) -> None:
        rec = _record(duration_ms=1200)
        output = render_step_success(rec)
        plain = _strip_ansi(output)
        assert "1200ms" in plain

    def test_fast_step_hides_duration(self) -> None:
        rec = _record(duration_ms=100)
        output = render_step_success(rec)
        plain = _strip_ansi(output)
        assert "100ms" not in plain


# ── render_step_skipped ──────────────────────────────────────────────────────


class TestRenderStepSkipped:
    def test_contains_dot_marker(self) -> None:
        rec = _record(status="skipped")
        output = render_step_skipped(rec)
        plain = _strip_ansi(output)
        assert renderer._ICON_DOT in plain

    def test_contains_label(self) -> None:
        rec = _record(step_id="vault-init", title="Initialize vault", status="skipped")
        output = render_step_skipped(rec)
        plain = _strip_ansi(output)
        assert "vault" in plain


# ── render_step_failure ──────────────────────────────────────────────────────


class TestRenderStepFailure:
    def test_contains_fail_marker(self) -> None:
        rec = _record(status="failed", error="Permission denied")
        output = render_step_failure(rec)
        plain = _strip_ansi(output)
        assert renderer._ICON_FAIL in plain

    def test_contains_error_text(self) -> None:
        rec = _record(status="failed", error="Permission denied")
        output = render_step_failure(rec)
        plain = _strip_ansi(output)
        assert "Permission denied" in plain

    def test_contains_fix_hint(self) -> None:
        rec = _record(step_id="configure-ssh", status="failed", error="oops")
        output = render_step_failure(rec, fix_hint="ssh-keygen -t ed25519")
        plain = _strip_ansi(output)
        assert "ssh-keygen -t ed25519" in plain

    def test_default_fix_hint(self) -> None:
        rec = _record(step_id="configure-ssh", status="failed", error="oops")
        output = render_step_failure(rec)
        plain = _strip_ansi(output)
        assert "navig init --step configure-ssh" in plain


# ── render_step_already_done ─────────────────────────────────────────────────


class TestRenderStepAlreadyDone:
    def test_contains_done_text(self) -> None:
        rec = _record()
        output = render_step_already_done(rec)
        plain = _strip_ansi(output)
        assert "done" in plain


# ── render_step_in_progress ──────────────────────────────────────────────────


class TestRenderStepInProgress:
    def test_contains_ellipsis(self) -> None:
        output = render_step_in_progress("Loading config")
        plain = _strip_ansi(output)
        assert "Loading config" in plain
        assert "…" in plain


# ── render_progress ──────────────────────────────────────────────────────────


class TestRenderProgress:
    def test_contains_label(self) -> None:
        output = render_progress(3, 10, "vault init", step_id="vault-init")
        plain = _strip_ansi(output)
        assert "vault" in plain
        assert "…" in plain


# ── the glyph decision ───────────────────────────────────────────────────────


def test_a_utf8_stdout_gets_the_unicode_set(monkeypatch):
    """The set follows the encoding actually in use — and is built from it, not guessed."""
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    assert renderer._uni() is True


def test_a_dumb_terminal_gets_ascii(monkeypatch):
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("PYTHONIOENCODING", "utf-8")
    assert renderer._uni() is False


def test_the_ascii_set_is_marked_not_coloured_ascii(monkeypatch):
    """ASCII fallbacks stay single characters — a two-char marker would break alignment."""
    assert len(renderer._ICON_OK) == 1 and len(renderer._ICON_DOT) == 1
    assert len(renderer._ICON_FAIL) == 1
