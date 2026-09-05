"""The menu renderer must read keys the entries actually carry.

Companion to `test_start_menu_commands_resolve.py`: that one guards the *table*, this one
guards the *renderer*. Both are needed, and the split is the whole lesson — the table was
allowed to rot precisely because nothing ever rendered it successfully.

The callback handler used to do `info["description"]`. No entry in `ACTION_COMMANDS` has
ever carried that key (they hold `cmd`/`type`/`prompt`), so all 98 buttons raised KeyError
into a bare `except Exception: pass` and every one of them fell through to the generic
"Action: <id>" stub. Nothing failed loudly, so nothing was noticed.

This pins the data contract from both sides: the renderer must produce something real for
every entry that exists, and it must not depend on a key the table does not define.
"""

from __future__ import annotations

import pytest

from navig.bot.start_menu import ACTION_COMMANDS
from navig.integrations.telegram_voice_bot import _action_message

# The shape the renderer falls back to when an entry carries neither prompt nor cmd.
_STUB = "⚙️ Action: <code>{action_id}</code>"


def test_the_table_is_populated():
    """A renderer proved correct over zero entries proves nothing."""
    assert len(ACTION_COMMANDS) >= 50


@pytest.mark.parametrize("action_id", sorted(ACTION_COMMANDS))
def test_every_entry_renders_something_useful(action_id: str) -> None:
    text = _action_message(action_id, ACTION_COMMANDS[action_id])
    assert isinstance(text, str) and text.strip(), f"{action_id} rendered nothing"
    # The generic stub is the FALLBACK the KeyError used to force everything into. An
    # entry that carries a prompt or a command must render that, not the stub.
    # Compare against the exact stub, not "is the id mentioned" — `top` legitimately
    # renders `<code>/top</code>`, which contains its own id.
    spec = ACTION_COMMANDS[action_id]
    if spec.get("prompt") or spec.get("cmd"):
        assert text != _STUB.format(action_id=action_id), (
            f"{action_id} fell back to the generic stub despite carrying "
            f"{'prompt' if spec.get('prompt') else 'cmd'}: {text!r}"
        )


def test_no_entry_defines_the_key_the_renderer_used_to_read():
    """If `description` ever becomes a real key, this test should be updated rather than
    silently satisfied — it records WHY the renderer stopped reading it."""
    with_desc = [k for k, v in ACTION_COMMANDS.items() if "description" in v]
    assert not with_desc, (
        f"entries now carry 'description' ({with_desc[:3]}) — revisit the renderer, which "
        "deliberately does not read it"
    )


def test_renderer_survives_an_unknown_entry_shape():
    """A new entry shape must degrade to naming the action, never raise: this runs inside
    a Telegram callback handler, where an exception is a button that silently does nothing."""
    text = _action_message("mystery", {"type": "future-shape"})
    assert "mystery" in text


def test_slash_entries_are_not_prefixed_with_navig():
    """`{"cmd": "/status", "type": "slash"}` is a Telegram command, not a CLI one —
    rendering it as `navig /status` would be advice that cannot work."""
    text = _action_message("status", {"cmd": "/status", "type": "slash"})
    assert "/status" in text
    assert "navig /status" not in text
