"""The business-message log line must not carry the message body.

`~/.navig/logs/gateway.log` is a plaintext file that gets tailed during debugging,
pasted into issues, and read by agents. This line logged 50 characters of EVERY
private business message at INFO, so the log accumulated a running transcript of
the operator's conversations with third parties — people who never consented to it
and are not the operator.

Seen on the operator's machine 2026-09-04 while reading logs for an unrelated
outage: real personal conversation content, in the clear, on disk.

It bought nothing diagnostically. The text is already persisted on purpose by
`upsert_message` (that IS the Telegram catalog feature, and the right place to read
it). `integrations/telegram_voice_bot.py` sets the house precedent: log the LENGTH.

Kept at INFO: chat / sender / owner / is_owner — the routing facts the line exists
for, none of which is message content.
"""

from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2] / "navig" / "telegram" / "business.py"


def _business_log_calls() -> list[ast.Call]:
    tree = ast.parse(_SRC.read_text(encoding="utf-8"))
    out: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = node.func.value
        if not (isinstance(base, ast.Name) and base.id == "logger"):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant):
            continue
        fmt = node.args[0].value
        if isinstance(fmt, str) and "business message:" in fmt:
            out.append(node)
    return out


def test_the_line_exists_at_all() -> None:
    """Anti-vacuity floor: if the call is renamed away, the assertions below would
    pass while checking nothing."""
    assert _business_log_calls(), (
        "no logger call with 'business message:' found in business.py — this guard "
        "is checking nothing; re-point it at the current line"
    )


def test_the_message_body_is_not_logged() -> None:
    for call in _business_log_calls():
        fmt = call.args[0].value
        assert "text=" not in fmt, (
            "the business-message log line interpolates the message body into "
            f"gateway.log — that is a plaintext transcript of private third-party "
            f"conversations on disk. Log len(text) instead. fmt={fmt!r}"
        )
        # The body must not be handed over BARE. `len(text)` is fine and is the
        # point of the fix, so check the top-level argument shape rather than
        # walking the tree — a walk sees the `text` inside `len(text)` and would
        # reject the correct code.
        bare = [
            a.id for a in call.args[1:] if isinstance(a, ast.Name) and a.id == "text"
        ]
        assert not bare, (
            "`text` is passed bare to the business-message logger; pass len(text) "
            "so the body never reaches the log file"
        )


def test_the_routing_metadata_is_still_logged() -> None:
    """The line must keep its diagnostic value — this is not "delete the log"."""
    fmt = _business_log_calls()[0].args[0].value
    for field in ("chat=", "from=", "owner=", "is_owner="):
        assert field in fmt, f"{field} disappeared from the business log line: {fmt!r}"
