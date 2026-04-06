from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

Status = Literal[
    "stable",
    "beta",
    "experimental",
    "deprecated",
    "hidden",
    "internal",
]


@dataclass(frozen=True)
class DeprecationInfo:
    since: str
    remove_after: str
    replaced_by: str
    note: str = ""


@dataclass(frozen=True)
class CommandMeta:
    summary: str
    status: Status
    since: str
    tags: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    deprecated: DeprecationInfo | None = None


_REGISTRY: dict[str, CommandMeta] = {}
_BY_CALLBACK: dict[Callable[..., Any], CommandMeta] = {}
_META_ATTR = "__navig_command_meta__"


def command_meta(**kwargs: Any):
    """Decorator that registers metadata for a Typer command handler."""
    deprecated_raw = kwargs.pop("deprecated", None)
    deprecated_obj = DeprecationInfo(**deprecated_raw) if deprecated_raw else None
    meta = CommandMeta(**kwargs, deprecated=deprecated_obj)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        setattr(fn, _META_ATTR, meta)
        _REGISTRY[fn.__qualname__] = meta
        _BY_CALLBACK[fn] = meta
        return fn

    return decorator


def get_registry() -> dict[str, CommandMeta]:
    return dict(_REGISTRY)


def get_meta_for_callback(callback: Callable[..., Any] | None) -> CommandMeta | None:
    if callback is None:
        return None

    if callback in _BY_CALLBACK:
        return _BY_CALLBACK[callback]

    direct = getattr(callback, _META_ATTR, None)
    if isinstance(direct, CommandMeta):
        return direct

    wrapped = getattr(callback, "__wrapped__", None)
    if wrapped is not None:
        wrapped_meta = getattr(wrapped, _META_ATTR, None)
        if isinstance(wrapped_meta, CommandMeta):
            return wrapped_meta

    return None
