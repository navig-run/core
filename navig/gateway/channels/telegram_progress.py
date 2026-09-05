"""
telegram_progress — live progress + debug X-ray for the deep (research) path.

Consumes the ``StatusEvent`` stream that ``ConversationalAgent.run_agentic`` now
emits (task_start / thinking / step_start / step_done / step_failed / task_done)
and turns it into ONE Telegram message that edits in place.

Two presentations, one renderer:

* **Normal mode** — a single, quiet italic line that mutates as work happens
  ("🔎 Searching the web" → "📖 Reading wikipedia.org" → …). It owns the bubble
  the final answer is then edited into, so the user watches one message become
  the reply. No progress bar, no step list, no model footer.

* **Debug mode** — the full X-ray the operator asked for, on every message:
  what NAVIG decided on send (mode · depth · tier · model · toolsets · language),
  a live per-tool step log while it works, and the totals on finish (turns,
  tokens, cost, wall-clock, any account fallback). The block appended under the
  answer is emitted as **Markdown** (``>!`` collapsible quote, ``**bold**``,
  ``` `code` ```) so it flows through the same ``md_to_html`` the answer does —
  raw HTML would be escaped by that converter. The LIVE bubble is HTML because
  it is sent directly with ``parse_mode="HTML"``.

Everything here is best-effort: a rendering failure must never break a reply.
"""

from __future__ import annotations

import html
import logging
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

if TYPE_CHECKING:
    from navig.agent.conv.status_event import StatusEvent
    from navig.gateway.channels.telegram import TelegramChannel

logger = logging.getLogger(__name__)

# Match StatusRenderer's discipline — stay under Telegram's editMessageText rate.
_MIN_EDIT_INTERVAL: float = 0.5


def _host(url: str) -> str:
    """Bare hostname for a URL, sans www. — for a compact 'Reading <site>' line."""
    try:
        net = urlparse(url if "://" in url else f"https://{url}").netloc
        return net[4:] if net.startswith("www.") else net
    except Exception:  # noqa: BLE001
        return ""


def _url_from_summary(args_summary: str) -> str:
    """Pull the url=… value out of a redacted arg summary, if present."""
    for part in (args_summary or "").split(" · "):
        if part.startswith("url="):
            return part[4:]
    return ""


#: tool name → (glyph, present-tense verb) for the minimal one-liner.
_TOOL_FACE: dict[str, tuple[str, str]] = {
    "search": ("🔎", "Searching the web"),
    "web_fetch": ("📖", "Reading"),
    "browser_tool": ("🌐", "Browsing"),
    "wiki_search": ("📚", "Checking references"),
    "wiki_read": ("📚", "Reading references"),
    "kb_lookup": ("🗂", "Checking knowledge base"),
    "memory_read": ("🧠", "Recalling"),
    "read_file": ("📄", "Reading a file"),
    "bash_exec": ("⚙️", "Running a command"),
}


def _minimal_line(tool: str, args_summary: str) -> str:
    """The single quiet italic line shown in normal mode for the current step."""
    glyph, verb = _TOOL_FACE.get(tool, ("⚙️", "Working"))
    if tool in ("web_fetch", "browser_tool"):
        host = _host(_url_from_summary(args_summary))
        if host:
            return f"{glyph} {verb} {host}…"
    return f"{glyph} {verb}…"


class TelegramProgressRenderer:
    """Edits one Telegram message in place from a StatusEvent stream.

    Parameters
    ----------
    channel:     the live TelegramChannel (for send/edit).
    chat_id:     target chat.
    own_bubble:  when True the renderer creates and edits its own message, which
                 the caller then reuses as the answer bubble. When False it only
                 accumulates events for :meth:`build_debug_block_md` (used when
                 the answer streams into its own placeholder, e.g. quick chat).
    debug:       collect the full X-ray for the appended block.
    send_info:   Telegram-side decision block for the debug header — mode, depth,
                 confidence, language. The rest is filled from StatusEvents.
    """

    def __init__(
        self,
        channel: "TelegramChannel",
        chat_id: int,
        *,
        own_bubble: bool,
        debug: bool,
        send_info: dict[str, Any] | None = None,
    ) -> None:
        self._channel = channel
        self._chat_id = chat_id
        self.own_bubble = own_bubble
        self.debug = debug
        self._send_info = dict(send_info or {})
        self._task_info: dict[str, Any] = {}
        self._done_info: dict[str, Any] = {}
        self._steps: list[dict[str, Any]] = []
        self._current_line = "⋯"
        self.message_id: int | None = None
        self._last_edit_at: float = 0.0
        self._last_rendered: str = ""
        self._t0 = time.monotonic()

    # ── Event sink ────────────────────────────────────────────────────────────

    async def on_event(self, event: "StatusEvent") -> None:
        """Consume one StatusEvent. Never raises."""
        try:
            await self._handle(event)
        except Exception as exc:  # noqa: BLE001 — progress is never load-bearing
            logger.debug("progress on_event skipped: %s", exc)

    async def _handle(self, event: "StatusEvent") -> None:
        etype = event.type
        meta = event.metadata or {}

        if etype == "task_start":
            self._task_info = dict(meta)
            return

        if etype == "thinking":
            self._current_line = "💭 Thinking…"
            if self.debug:
                self._steps.append({"kind": "thinking", "turn": meta.get("step_index")})
            # Create the bubble on the first thinking beat for the deep path. This
            # is what keeps a tool-LESS deep answer legible: the research tier never
            # streams tokens, so a question the model answers from its own knowledge
            # (no search/fetch step ever fires) would otherwise show nothing but the
            # typing indicator for 20-40s — exactly the "silence feels broken" case.
            # own_bubble is only ever True for the deep tier, which is never fast, so
            # there is no fast answer to flash a status line at.
            if self.own_bubble:
                await self._render(force=self.message_id is None)
            return

        if etype == "step_start":
            tool = str(meta.get("tool", ""))
            self._current_line = _minimal_line(tool, str(meta.get("args_summary", "")))
            self._steps.append(
                {
                    "kind": "step",
                    "tool": tool,
                    "args": meta.get("args_summary", ""),
                    "index": meta.get("step_index"),
                    "state": "running",
                }
            )
            # First concrete step is what creates the bubble (naturally >1s in, so
            # the "only show when it takes a while" rule falls out for free).
            if self.own_bubble:
                await self._render(force=self.message_id is None)
            return

        if etype in ("step_done", "step_failed"):
            idx = meta.get("step_index")
            for step in reversed(self._steps):
                if step.get("kind") == "step" and step.get("index") == idx:
                    step["state"] = "done" if etype == "step_done" else "failed"
                    step["result"] = meta.get("result_summary", "")
                    step["error"] = meta.get("error", "")
                    step["duration_ms"] = meta.get("duration_ms")
                    break
            return

        if etype == "task_done":
            self._done_info = dict(meta)
            # No live re-render: the caller edits this very bubble into the final
            # answer next, and in debug mode appends build_debug_block_md().
            return

    # ── Live bubble (HTML, sent directly) ─────────────────────────────────────

    async def _render(self, *, force: bool = False) -> None:
        text = f"<i>{html.escape(self._current_line)}</i>"
        if not text or text == self._last_rendered:
            return
        now = time.monotonic()
        if (
            not force
            and self.message_id is not None
            and (now - self._last_edit_at) < _MIN_EDIT_INTERVAL
        ):
            return
        self._last_edit_at = now
        self._last_rendered = text
        try:
            if self.message_id is None:
                msg = await self._channel.send_message(self._chat_id, text, parse_mode="HTML")
                self.message_id = (msg or {}).get("message_id")
            else:
                await self._channel.edit_message(
                    self._chat_id, self.message_id, text, parse_mode="HTML"
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("progress render edit skipped: %s", exc)

    # ── Debug X-ray, appended under the answer (Markdown) ─────────────────────

    def build_debug_block_md(self) -> str:
        """Full X-ray as Markdown, to append under the final answer (debug only).

        Emits collapsible ``>!`` blockquotes so it never buries the reply, and
        Markdown (not HTML) so the answer's own md_to_html pass renders it too.
        """
        if not self.debug:
            return ""
        blocks: list[str] = []

        # On send — what NAVIG decided.
        si, ti = self._send_info, self._task_info
        send_rows: list[str] = []
        if si.get("mode"):
            depth = str(si.get("depth", ""))
            conf = si.get("confidence")
            if isinstance(conf, (int, float)):
                depth += f" ({conf:.2f})"
            send_rows.append(f"mode **{si['mode']}** · depth **{depth}**")
        if si.get("depth_source"):
            send_rows.append(f"depth via {si['depth_source']}")
        if ti.get("tier") or ti.get("model"):
            send_rows.append(
                f"tier **{ti.get('tier', '?')}** · "
                f"{ti.get('provider', '')}/{ti.get('model', '?')}"
            )
        if ti.get("toolsets"):
            send_rows.append("tools: " + ", ".join(str(t) for t in ti["toolsets"]))
        if ti.get("effort"):
            send_rows.append(f"effort {ti['effort']}")
        if si.get("language"):
            send_rows.append(f"lang {si['language']}")
        if send_rows:
            blocks.append(self._quote_md("🔬 Debug — on send", send_rows))

        # While responding — the step log.
        log_rows: list[str] = []
        for step in self._steps:
            if step.get("kind") == "thinking":
                log_rows.append(f"💭 think · turn {step.get('turn', '?')}")
                continue
            icon = {"running": "⋯", "done": "✓", "failed": "✗"}.get(step.get("state", ""), "•")
            dur = step.get("duration_ms")
            durs = f" · {dur}ms" if isinstance(dur, int) else ""
            row = f"{icon} `{step.get('tool', '')}`"
            if step.get("args"):
                row += f" {step['args']}"
            row += durs
            if step.get("state") == "failed" and step.get("error"):
                row += f" — {str(step['error'])[:100]}"
            log_rows.append(row)
        if log_rows:
            blocks.append(self._quote_md("🔬 Debug — steps", log_rows))

        # On finish — the totals.
        di = self._done_info
        done_rows: list[str] = []
        if di.get("turns") is not None:
            done_rows.append(f"turns {di['turns']}")
        if di.get("tools_run") is not None:
            done_rows.append(f"tools {di['tools_run']}")
        if di.get("total_tokens"):
            done_rows.append(f"{di['total_tokens']} tok")
        if di.get("cost_usd"):
            done_rows.append(f"${di['cost_usd']}")
        done_rows.append(f"{time.monotonic() - self._t0:.1f}s")
        if di.get("account_fallback"):
            done_rows.append(f"↪ {di['account_fallback']}")
        blocks.append(self._quote_md("🔬 Debug — done", [" · ".join(done_rows)]))

        return "\n\n" + "\n\n".join(blocks)

    @staticmethod
    def _quote_md(title: str, rows: list[str]) -> str:
        """One collapsible Markdown blockquote. ``>!`` on the first line forces
        an expandable quote in md_to_html."""
        lines = [f">! **{title}**"]
        lines += [f"> {r}" for r in rows]
        return "\n".join(lines)

    async def clear(self) -> None:
        """Delete the progress bubble (only when it is NOT reused as the answer)."""
        if self.message_id is not None:
            try:
                await self._channel.delete_message(self._chat_id, self.message_id)
            except Exception as exc:  # noqa: BLE001
                logger.debug("progress clear skipped: %s", exc)
            self.message_id = None
