"""Regression tests for:

* Per-session conversation history (the CRITICAL cross-user leak): every Telegram
  chat used to share ~/.navig/history/default.jsonl because the agent was built
  without history=. Distinct session keys must map to distinct history files.
* The debug X-ray renderer producing valid, escaped, collapsible Markdown that the
  answer's md_to_html pass renders correctly.
* The deep path staying legible when the model answers WITHOUT tools — the progress
  bubble must be created on the first "thinking" beat, not only on a tool step.
"""

from unittest.mock import AsyncMock

import pytest

from navig.agent.conv.status_event import StatusEvent
from navig.gateway.channels.telegram_html import md_to_html
from navig.gateway.channels.telegram_progress import TelegramProgressRenderer


def _event(etype, **meta):
    from datetime import datetime

    return StatusEvent(
        type=etype, task_id="t1", message="", timestamp=datetime(2026, 1, 1),
        step_index=meta.pop("step_index", None), metadata=meta,
    )


class _FakeChannel:
    def __init__(self):
        self.send_message = AsyncMock(return_value={"message_id": 77})
        self.edit_message = AsyncMock(return_value={"ok": True})
        self.delete_message = AsyncMock(return_value=True)


class TestHistoryIsolation:
    def test_distinct_sessions_get_distinct_history_files(self, tmp_path, monkeypatch):
        # Isolate the history dir.
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        from navig.agent.conv.history import ConversationHistory

        a = ConversationHistory(user_id="telegram:user:111")
        b = ConversationHistory(user_id="telegram:user:222")
        # The path is derived from a sanitised user_id, so the two must differ.
        assert a._jsonl_path != b._jsonl_path
        assert "111" in a._jsonl_path.name
        assert "222" in b._jsonl_path.name

    def test_session_key_sanitised_into_filename(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        from navig.agent.conv.history import ConversationHistory

        h = ConversationHistory(user_id="telegram:group:-100/../x")
        # No path traversal survives into the filename.
        assert ".." not in h._jsonl_path.name
        assert "/" not in h._jsonl_path.name


class TestDebugBlockMarkdown:
    def _renderer(self):
        r = TelegramProgressRenderer(
            None, 1, own_bubble=True, debug=True,
            send_info={"mode": "REASON", "depth": "deep", "confidence": 0.9, "language": "en"},
        )
        r._task_info = {
            "tier": "research", "provider": "anthropic", "model": "claude-opus-4-8",
            "toolsets": ["research", "browser"], "effort": "HIGH",
        }
        r._steps = [
            {"kind": "step", "tool": "search", "args": "query=fight club",
             "index": 1, "state": "done", "duration_ms": 320},
        ]
        r._done_info = {"turns": 2, "tools_run": 1, "total_tokens": 4200, "cost_usd": 0.021}
        return r

    def test_debug_block_renders_to_expandable_blockquotes(self):
        md = self._renderer().build_debug_block_md()
        html = md_to_html("**Fight Club** (1999)" + md)
        # Answer bold survives, and each section is a collapsible quote.
        assert "<b>Fight Club</b>" in html
        assert html.count("<blockquote expandable>") >= 3
        assert "<code>search</code>" in html
        assert "claude-opus-4-8" in html

    def test_non_debug_renderer_emits_no_block(self):
        r = TelegramProgressRenderer(None, 1, own_bubble=True, debug=False)
        assert r.build_debug_block_md() == ""


@pytest.mark.asyncio
class TestNoToolDeepStaysLegible:
    async def test_thinking_creates_bubble_for_deep_path(self):
        # A deep answer with NO tool call must still show a status bubble, driven
        # by the "thinking" beat — otherwise the user sees 20-40s of silence.
        ch = _FakeChannel()
        r = TelegramProgressRenderer(ch, 1, own_bubble=True, debug=False)
        await r.on_event(_event("task_start", model="claude-opus-4-8"))
        assert ch.send_message.await_count == 0, "task_start alone must not send"
        await r.on_event(_event("thinking", step_index=1))
        assert r.message_id == 77
        assert ch.send_message.await_count == 1
        # The bubble shows a quiet thinking line.
        sent_text = ch.send_message.await_args.args[1]
        assert "Thinking" in sent_text

    async def test_thinking_does_not_create_bubble_when_not_own_bubble(self):
        # quick+debug: the streamed answer owns the bubble, so thinking must NOT
        # create a competing one.
        ch = _FakeChannel()
        r = TelegramProgressRenderer(ch, 1, own_bubble=False, debug=True)
        await r.on_event(_event("thinking", step_index=1))
        assert ch.send_message.await_count == 0
        assert r.message_id is None
