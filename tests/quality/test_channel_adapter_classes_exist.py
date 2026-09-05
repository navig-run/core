"""A channel's declared `adapter_class` must name a class that exists and can load.

`ChannelMeta.adapter_class` is a **string**, resolved at runtime by
``ChannelRegistry.get_adapter`` via ``getattr(module, meta.adapter_class)``. Nothing
type-checks a string, so three of the five declared adapters named classes that had
never been written:

    telegram   declared=TelegramChannelAdapter    actual=TelegramChannel
    whatsapp   declared=WhatsAppChannelAdapter    actual=WhatsAppChannel
    discord    declared=DiscordChannelAdapter     actual=DiscordChannel

`get_adapter` caught the AttributeError and set the channel's status to **ERROR** —
so asking the registry for the flagship channel reported *the channel* as broken when
only the missing wrapper was. Measured before the fix; Matrix and SMS were fine.

This is the same class as the module-attribute guard in `scripts/check_module_attrs.py`
(`<module>.<name>` that does not exist), which cannot see it: the name lives in a
string, not in an attribute access.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

from navig.gateway.channels.registry import DEFAULT_CHANNEL_META

_DECLARED = [
    (channel_id.value, meta.module_path, meta.adapter_class)
    for channel_id, meta in DEFAULT_CHANNEL_META.items()
    if meta.module_path and meta.adapter_class
]


def test_some_channel_actually_declares_an_adapter() -> None:
    """Anti-vacuity: every test below is parametrised over `_DECLARED`. If the list
    empties — because someone sets every `adapter_class` to None — the suite would go
    green having checked nothing."""
    assert _DECLARED, "no channel declares an adapter_class; this guard is inert"


@pytest.mark.parametrize("channel,module_path,adapter_class", _DECLARED)
def test_the_declared_adapter_class_exists(
    channel: str, module_path: str, adapter_class: str
) -> None:
    module = importlib.import_module(module_path)

    assert hasattr(module, adapter_class), (
        f"channel {channel!r} declares adapter_class={adapter_class!r}, which does not "
        f"exist in {module_path}. `get_adapter()` will catch the AttributeError and "
        f"mark this channel ERROR — reporting a working channel as broken."
    )


@pytest.mark.parametrize("channel,module_path,adapter_class", _DECLARED)
def test_the_declared_adapter_takes_no_required_arguments(
    channel: str, module_path: str, adapter_class: str
) -> None:
    """`get_adapter` calls `adapter_cls()` with no arguments. A class that needs them
    (every `*Channel` class here does — `TelegramChannel` requires a bot token) raises
    TypeError, so pointing `adapter_class` at one would trade a caught AttributeError
    for an uncaught crash in the caller."""
    module = importlib.import_module(module_path)
    adapter_cls = getattr(module, adapter_class)

    signature = inspect.signature(adapter_cls)
    required = [
        name
        for name, param in signature.parameters.items()
        if param.default is inspect.Parameter.empty
        and param.kind
        not in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
    ]

    assert not required, (
        f"channel {channel!r} declares adapter_class={adapter_class!r}, but it requires "
        f"{required} — `get_adapter()` constructs it as `adapter_cls()`."
    )


@pytest.mark.parametrize("channel,module_path,adapter_class", _DECLARED)
def test_the_registry_can_actually_build_it(
    channel: str, module_path: str, adapter_class: str
) -> None:
    """The end-to-end check. The two above explain *why* a load fails; this one is the
    behaviour a caller sees, and it is what the original bug broke."""
    from navig.gateway.channels.registry import get_channel_registry

    registry = get_channel_registry()

    assert registry.get_adapter(channel) is not None, (
        f"registry.get_adapter({channel!r}) returned None despite declaring an adapter"
    )


def test_a_channel_without_an_adapter_is_not_marked_broken() -> None:
    """The other half. Declaring no adapter is legitimate — those channels are reached
    through the gateway's live instances — and must not flip the channel to ERROR the
    way a missing class did."""
    from navig.gateway.channels.registry import ChannelStatus, get_channel_registry

    registry = get_channel_registry()
    undeclared = [
        channel_id.value
        for channel_id, meta in DEFAULT_CHANNEL_META.items()
        if not meta.adapter_class
    ]
    assert undeclared, "expected at least one channel with no registry adapter"

    for channel in undeclared:
        assert registry.get_adapter(channel) is None
        meta = registry.get_channel(registry.normalize_channel_id(channel))
        if meta is not None:
            assert meta.status is not ChannelStatus.ERROR, (
                f"{channel!r} has no adapter wrapper — that is not an error state"
            )
