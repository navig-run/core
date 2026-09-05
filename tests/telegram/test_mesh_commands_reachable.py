"""/nodes, /leader, /mesh and /switch existed as code and as nothing else.

`TelegramMeshMixin` shipped four complete handlers — peers, leader, toggle, handoff —
with **no `_SLASH_REGISTRY` entry and no dispatcher path**. `TelegramChannel`'s runtime
MRO is `[TelegramChannel, object]`, so a mixin method is only reachable if the dispatcher
resolves it by name against that mixin, and it resolved against three other mixins but
not this one.

Two things had to be true for the commands to work, and these pin both:

1. a registry entry exists for each command, naming a handler that really is on the mixin;
2. the handlers speak the dispatcher's calling convention. The two that take an argument
   declared ``args``, which the dispatcher supplies to nothing — the live one forwards
   ``text`` (the whole ``/mesh on``). Wiring them as written would have raised TypeError
   on the first press, which is the failure this file exists to make impossible.
"""

from __future__ import annotations

import inspect

import pytest

from navig.gateway.channels import telegram_commands as tc
from navig.gateway.channels.telegram_mesh import TelegramMeshMixin

pytestmark = pytest.mark.integration

MESH_COMMANDS = {
    "nodes": "_handle_mesh_nodes",
    "leader": "_handle_mesh_leader",
    "mesh": "_handle_mesh_toggle",
    "switch": "_handle_mesh_switch",
}

# What the live dispatcher in TelegramChannel is able to pass. A handler parameter
# outside this set is never filled, so a required one is a guaranteed TypeError.
DISPATCHER_KWARGS = {"chat_id", "user_id", "username", "metadata", "is_group", "text", "session"}


def _entry(command: str):
    return next((e for e in tc._SLASH_REGISTRY if e.command == command), None)


@pytest.mark.parametrize("command,handler", sorted(MESH_COMMANDS.items()))
def test_each_mesh_command_is_registered(command, handler):
    entry = _entry(command)
    assert entry is not None, f"/{command} has no _SLASH_REGISTRY entry — it cannot be dispatched"
    assert entry.handler == handler, f"/{command} points at {entry.handler!r}, not {handler!r}"


@pytest.mark.parametrize("command,handler", sorted(MESH_COMMANDS.items()))
def test_the_named_handler_resolves_the_way_the_dispatcher_resolves_it(command, handler):
    """Mirror the dispatcher's lookup: channel first, then the known mixins.

    Asserted against the mixin because that is where these live — TelegramChannel does
    not inherit it, so `getattr(channel, name)` alone finds nothing.
    """
    fn = getattr(TelegramMeshMixin, handler, None)
    assert callable(fn), f"{handler} is not on TelegramMeshMixin — the registry entry dangles"


@pytest.mark.parametrize("command,handler", sorted(MESH_COMMANDS.items()))
def test_every_parameter_can_actually_be_supplied(command, handler):
    """A required parameter the dispatcher does not know is a TypeError on first press."""
    sig = inspect.signature(getattr(TelegramMeshMixin, handler))
    for name, param in sig.parameters.items():
        if name == "self":
            continue
        if param.default is inspect.Parameter.empty:
            assert name in DISPATCHER_KWARGS, (
                f"{handler} requires {name!r}, which the dispatcher never supplies "
                f"(it can pass: {sorted(DISPATCHER_KWARGS)})"
            )


def test_the_argument_taking_handlers_read_text_not_args():
    """`/mesh on` and `/switch host` must get their argument from `text`.

    The dispatcher forwards the raw command line; `args` was supplied by nothing, so the
    argument would have been lost even if the call had somehow succeeded.
    """
    for handler in ("_handle_mesh_toggle", "_handle_mesh_switch"):
        params = inspect.signature(getattr(TelegramMeshMixin, handler)).parameters
        assert "text" in params, f"{handler} must accept `text` — the dispatcher forwards that"
        assert "args" not in params, f"{handler} still declares `args`, which nothing supplies"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("/mesh", "status"),
        ("/mesh status", "status"),
        ("/mesh on", "on"),
        ("/mesh   OFF  ", "off"),
    ],
)
def test_toggle_parses_its_action_out_of_the_raw_command(text, expected):
    """The parsing idiom every other parameterised handler uses, applied here."""
    arg = text.split(" ", 1)[1].strip() if " " in text else ""
    assert (arg.lower() or "status") == expected
