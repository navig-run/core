"""How a ``navig <command>`` typed in chat reads back as a card.

Every ``/cli``-style answer — and every "navig doctor", "navig host list" the operator
types — ends up here. It used to be a wall of raw text (or one fluent sentence that
hid the output entirely). The card keeps the output where it belongs — a monospace
block you can read — and states the two facts that decide what to do next: did it
work, and how long did it take. The buttons re-run it in place instead of forcing a
retype.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from navig.telegram.card_doc import Button, Card, Code, Para, PullQuote, esc

_FALLBACK = {
    "cli.card.title": "Command",
    "cli.card.ok": "answered in {ms} ms",
    "cli.card.failed": "exited with code {code}",
    "cli.card.failure_note": "The last lines hold the reason — the output is exactly as the command printed it.",
    "cli.card.truncated": "Showing the last {lines} lines of {total}.",
    "cli.card.empty": "The command printed nothing.",
    "cli.card.nooutput": "no output",
    "cli.card.hint": "Re-run it here, or ask what the output means.",
    "cli.card.run": "🔄 Run again",
    "cli.card.explain": "🧠 Explain",
    "cli.card.close": "✖ Close",
}

_EXIT = re.compile(r"Command exited with code:?\s*(-?\d+)")
MAX_LINES = 28
MAX_CALLBACK = 60


def _label(key: str, fallback: str = "") -> str:
    text = ""
    try:
        from navig.core.i18n import t

        text = str(t(key) or "")
    except Exception:  # noqa: BLE001 - a missing catalog must not cost the card
        text = ""
    default = fallback or _FALLBACK.get(key, key)
    return default if text in ("", key) else text


def exit_code(output: str) -> int | None:
    """The CLI writes ``Command exited with code: N``; a card must not guess."""
    match = _EXIT.search(output or "")
    return int(match.group(1)) if match else None


def tail_lines(text: str, limit: int = MAX_LINES) -> tuple[str, int]:
    """``(shown, total)`` — keep the END, because a failure explains itself last."""
    lines = (text or "").rstrip().splitlines()
    total = len(lines)
    if total <= limit:
        return "\n".join(lines), total
    return "\n".join(lines[-limit:]), total


def _callback(action: str, command: str) -> str:
    encoded = base64.urlsafe_b64encode(command.encode("utf-8")).decode("ascii").rstrip("=")
    return f"cli:{action}:{encoded}"


def parse_callback(data: str) -> tuple[str, str] | None:
    """``cli:run:<b64>`` -> ``("run", "navig status")``; anything else -> ``None``.

    ``validate=True`` matters: without it a mangled payload decodes to ``b""`` and the
    card would "re-run" an empty command instead of reporting a broken button.
    """
    parts = (data or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "cli":
        return None
    action, encoded = parts[1], parts[2]
    if action not in {"run", "explain", "close"}:
        return None
    if action == "close":
        return action, ""
    if not encoded:
        return None
    padding = "=" * (-len(encoded) % 4)
    try:
        command = base64.b64decode(encoded + padding, altchars=b"-_", validate=True).decode("utf-8")
    except Exception:  # noqa: BLE001 — a mangled payload is not a command
        return None
    if not command.strip():
        return None
    return action, command


def card(
    command: str,
    output: str,
    *,
    duration_ms: int | None = None,
    summary: str = "",
    show_explain: bool = True,
) -> Card:
    """The command card: title names it, the sheet states the outcome, the body is output."""
    body, total = tail_lines(output)
    code = exit_code(output)
    ok = code in (None, 0)

    if ok:
        outcome = (
            _label("cli.card.ok", "answered in {ms} ms").replace("{ms}", str(duration_ms))
            if duration_ms is not None
            else ""
        )
    else:
        outcome = _label("cli.card.failed", "exited with code {code}").replace("{code}", str(code))
    head = Card(f"⌨️ navig {command}".strip(), meta=outcome)
    if not ok:
        head.add(
            PullQuote(
                _label(
                    "cli.card.failure_note",
                    "The last lines hold the reason — the output is exactly as the command printed it.",
                )
            )
        )
    if summary:
        head.add(Para(esc(summary)))

    if body.strip():
        head.add(Code(body, language="text"))
    else:
        head.add(Para(_label("cli.card.empty", "The command printed nothing."), style="italic"))
    if total > MAX_LINES:
        head.add(
            Para(
                _label("cli.card.truncated", "Showing the last {lines} lines of {total}.")
                .replace("{lines}", str(MAX_LINES))
                .replace("{total}", str(total)),
                style="italic",
            )
        )

    if len(_callback("run", command)) <= MAX_CALLBACK:
        row = [
            Button(
                _label("cli.card.run", "🔄 Run again"),
                data=_callback("run", command),
                style="primary",
            )
        ]
        if show_explain and body.strip():
            row.append(Button(_label("cli.card.explain", "🧠 Explain"), data=_callback("explain", command)))
        row.append(Button(_label("cli.card.close", "✖ Close"), data="cli:close:"))
        head.buttons(*row)
    head.footer = _label("cli.card.hint", "Re-run it here, or ask what the output means.")
    return head


def build(
    command: str,
    output: str,
    *,
    duration_ms: int | None = None,
    summary: str = "",
    show_explain: bool = True,
) -> tuple[str, str, list[list[dict]] | None]:
    """``(classic_html, rich_html, keyboard)`` for one command's answer."""
    head = card(
        command,
        output,
        duration_ms=duration_ms,
        summary=summary,
        show_explain=show_explain,
    )
    return head.classic_html(), head.rich_html(), head.keyboard()


def classify(output: str) -> dict[str, Any]:
    """Small facts a caller (or a test) wants without re-parsing the text."""
    code = exit_code(output)
    body, total = tail_lines(output)
    return {"ok": code in (None, 0), "exit_code": code, "lines": total, "shown": body}


__all__ = [
    "MAX_LINES",
    "build",
    "card",
    "classify",
    "exit_code",
    "parse_callback",
    "tail_lines",
]
