"""Shared media contract — the vocabulary every media facet (image / video / audio /
text) and the core generation engine agree on.

Phase 3.0 of the media modularization: instead of four plugins each cloning the
generation orchestrator, core keeps ONE engine and exposes a typed contract + a
**generator registry**. Each facet plugin registers its per-modality backend via
:func:`register_generator`; the engine dispatches through :func:`get_generator`
(falling back to the built-in backends when no facet is registered).

This replaces the previously bare-string modality vocabulary
(``("image", "video", "audio")``) so a typo is an error, not a silent wrong path.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MediaModality(str, Enum):
    """The kinds of media a facet can generate. ``str`` mixin → JSON/DB friendly."""

    IMAGE = "image"
    VIDEO = "video"
    AUDIO = "audio"
    TEXT = "text"

    @classmethod
    def coerce(cls, value: "MediaModality | str") -> "MediaModality":
        """Normalize a string/enum to a MediaModality, raising on an unknown value."""
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value).lower())
        except ValueError as exc:
            valid = ", ".join(m.value for m in cls)
            raise ValueError(f"unknown media modality {value!r}; expected one of: {valid}") from exc


@dataclass
class MediaAsset:
    """A generated media asset — the shared row shape across facets + the engine.

    Mirrors the ``generated_media`` store row so plugins never hand-roll dicts for
    the same data. ``status``: ``generated`` (staged) | ``kept`` | ``rejected``.
    """

    id: str
    modality: MediaModality
    status: str = "generated"
    group_id: str | None = None
    prompt: str = ""
    provider: str | None = None
    model: str | None = None
    seed: int | None = None
    params: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    path: str | None = None
    license: str | None = None
    space: str | None = None


# ── generator backend registry ──────────────────────────────────────────────
# A backend runs a provider for one modality, writing files into ``out_dir`` and
# returning the provider's result objects (same contract as the engine's built-in
# per-modality blocks). Facet plugins call register_generator() at import/boot.
GeneratorBackend = Callable[..., Awaitable[list[Any]]]

_GENERATORS: dict[MediaModality, GeneratorBackend] = {}


def register_generator(modality: MediaModality | str, backend: GeneratorBackend) -> None:
    """Register (or override) the generation backend for *modality*."""
    _GENERATORS[MediaModality.coerce(modality)] = backend


def get_generator(modality: MediaModality | str) -> GeneratorBackend | None:
    """The registered backend for *modality*, or None (engine falls back to built-in)."""
    return _GENERATORS.get(MediaModality.coerce(modality))


def registered_modalities() -> list[MediaModality]:
    """Modalities that currently have a facet-registered backend."""
    return list(_GENERATORS)
