"""A mixin method's ``self._sibling`` must resolve on the live TelegramChannel.

Telegram ``/start`` replied "oops, hit an error: 'TelegramChannel' object has no attribute
'_start_system_prompt'". TelegramChannel does not inherit its mixins (runtime MRO is
``[TelegramChannel, object]``); handlers run as ``functools.partial(mixin_fn, channel)``, so
inside one ``self`` IS the channel, and a helper defined only on the mixin is an
AttributeError. ``test_telegram_channel_calls_resolve.py`` guards ``self.channel._x`` from the
callback layer; this guards the other door -- ``self._x`` from inside the mixin itself.

The scan found 27 such names across seven mixins (inline queries, spoken replies, mesh,
messaging threads, checklists, /autoheal status, /workout, /journal reflection). All are
now bound in telegram.py, so the baseline is EMPTY.
"""

from __future__ import annotations

import ast
from pathlib import Path

CHANNELS = Path(__file__).resolve().parents[2] / "navig" / "gateway" / "channels"

#: Mixin modules never handed the channel as ``self`` -- dead code, not a gap.
_NOT_DISPATCHED = {
    # Emoji reactions were removed as an action trigger; telegram.py says so and never
    # imports this module.
    "telegram_reactions.py",
}


def _unresolved() -> list[str]:
    from navig.gateway.channels.telegram import TelegramChannel

    on_channel = set(dir(TelegramChannel))
    files = [f for f in sorted(CHANNELS.glob("telegram*.py")) if f.name not in _NOT_DISPATCHED]
    mixins_seen = 0
    found: list[str] = []
    for f in files:
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for cls in tree.body:
            if not (isinstance(cls, ast.ClassDef) and cls.name.endswith("Mixin")):
                continue
            mixins_seen += 1
            defined = {
                n.name for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            } | {
                t.id
                for n in cls.body
                if isinstance(n, ast.Assign)
                for t in n.targets
                if isinstance(t, ast.Name)
            }
            for node in ast.walk(cls):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "self"
                    and node.attr in defined
                    and node.attr not in on_channel
                ):
                    found.append(f"{f.name}:{node.lineno} {cls.name}.{node.attr}")
    # Scan floor: a mis-pointed CHANNELS would otherwise pass having read nothing.
    assert mixins_seen >= 8, f"scanned only {mixins_seen} mixins -- the walk is broken"
    return found


def test_every_mixin_sibling_resolves_on_the_channel() -> None:
    missing = _unresolved()
    assert not missing, (
        "These mixin methods call a sibling the channel does not have -- an AttributeError "
        "the moment a user reaches them. Bind them in telegram.py's _MIXIN_BINDINGS:\n  "
        + "\n  ".join(missing)
    )


def test_start_prompt_works_through_the_channel() -> None:
    """The exact call /start makes. A plain ``getattr`` bind would turn the staticmethod
    into an instance method and pass the channel as ``tod``."""
    from navig.gateway.channels.telegram import TelegramChannel

    channel = object.__new__(TelegramChannel)
    prompt = channel._start_system_prompt("morning", "The user's handle is @x. ", "No context.")
    assert "It is morning." in prompt
    assert "@x" in prompt
