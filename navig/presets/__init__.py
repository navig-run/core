"""NAVIG Universal Presets — one typed, reusable preset system for every AI tool.

* :mod:`navig.presets.model`    — the preset shape and the per-tool typed params.
* :mod:`navig.presets.catalog`  — builtin/plugin/user/project/space files layered
  under the operator's saved rows (prompt library database, schema v2).
* :mod:`navig.presets.resolve`  — composes presets + the user's request into one
  inspectable configuration, with fixed precedence.
* :mod:`navig.presets.adapters` — maps resolved image params onto what each
  provider accepts.

No LLM calls happen here: presets only shape what is passed to the tools that
make them (``navig_sdk.ai`` for text, the media generators for the rest).
"""

from navig.presets.model import FORMAT_VERSION, LAYERS, TOOLS, Preset, PresetError

__all__ = ["FORMAT_VERSION", "LAYERS", "TOOLS", "Preset", "PresetError"]
