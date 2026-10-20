"""The preset model — one shape for every NAVIG AI tool.

A preset is a reusable, versioned configuration for one tool (Text, Image,
Audio generation, Audio enhancement, Video, Speech — a reusable voice profile). Common metadata (id, name, layer,
scope, instructions, variables…) is shared; tool-specific settings live in a
typed ``params`` dict validated against :data:`PARAM_SCHEMAS`, so an image
preset can never smuggle an audio setting and a typo is a validation error,
not a silently ignored key.

``layer`` decides how presets compose (see :mod:`navig.presets.resolve`):
``context`` (what the work is about) → ``style`` (how it should look/sound) →
``format`` (the shape of the output) → ``config`` (operation settings). The
user's own request always sits above every layer.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

FORMAT_VERSION = 1

TOOLS = ("text", "image", "video", "audio.generate", "audio.enhance", "speech")
LAYERS = ("context", "style", "format", "config")
SCOPES = ("builtin", "plugin", "user", "project", "space")
# Where a preset came from. ``imported`` presets are untrusted: their instruction
# text is screened and quoted, never run as authority (see resolve._screen).
SOURCES = ("builtin", "plugin", "user", "project", "space", "imported")

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,119}$")
_VAR_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class PresetError(ValueError):
    """A preset failed validation. The message names the field."""


# ── Typed tool params ────────────────────────────────────────────────
# (type, constraint). type ∈ str | int | number | bool | enum | list.
# constraint: enum → allowed values; int/number → (min, max); str → max length.

# Speech engines a voice profile may name (navig_audio.voice.tts.TTSProvider values +
# "auto"). A test keeps this list equal to the engine's enum.
SPEECH_PROVIDERS = ["auto", "edge", "mimo", "elevenlabs", "openai", "google_cloud", "deepgram", "openrouter", "local"]

_ASPECTS = ["1:1", "16:9", "9:16", "4:3", "3:4", "3:2", "2:3", "21:9"]

PARAM_SCHEMAS: dict[str, dict[str, tuple[str, Any]]] = {
    "text": {
        "kind": ("enum", ["draft", "article", "caption", "brief", "social"]),
        "language": ("str", 40),
        "voice": ("str", 400),
        "genre": ("str", 200),
        "audience": ("str", 200),
        "max_words": ("int", (1, 20000)),
        "temperature": ("number", (0.0, 2.0)),
    },
    "image": {
        "art_direction": ("str", 1200),
        "palette": ("list", 12),
        "mood": ("str", 300),
        "lighting": ("str", 300),
        "composition": ("str", 300),
        "aspect": ("enum", _ASPECTS),
        "quality": ("enum", ["low", "medium", "high", "standard", "hd"]),
        "style": ("enum", ["vivid", "natural"]),
        "negative": ("str", 1200),
        "seed": ("int", (0, 2**31 - 1)),
    },
    "video": {
        "art_direction": ("str", 1200),
        "aspect": ("enum", _ASPECTS),
        "duration_s": ("number", (1.0, 120.0)),
        "negative": ("str", 1200),
    },
    "audio.generate": {
        "kind": ("enum", ["music", "sfx", "tts"]),
        "duration_s": ("number", (0.5, 300.0)),
        "bpm": ("int", (40, 240)),
        "voice": ("str", 200),
        "negative": ("str", 600),
    },
    "audio.enhance": {
        "mode": ("enum", ["preserve", "studio", "cinematic", "custom"]),
        "category": ("enum", ["auto", "speech", "sfx", "music", "ambience", "mixed"]),
        "target_lufs": ("number", (-40.0, -6.0)),
        "true_peak_db": ("number", (-6.0, 0.0)),
        "denoise": ("enum", ["off", "auto", "light", "strong"]),
        "declip": ("bool", None),
        "declick": ("bool", None),
        "dehum": ("enum", ["off", "auto", "50", "60"]),
        "trim_silence": ("bool", None),
        "eq": ("enum", ["off", "gentle", "adaptive"]),
        "compress": ("enum", ["off", "gentle", "moderate"]),
        "stereo_width": ("number", (0.0, 2.0)),
        "highpass_hz": ("number", (0.0, 300.0)),   # rumble / handling noise below this
        "lowpass_hz": ("number", (2000.0, 22000.0)),  # hiss / band-limited sources above this
        "deess": ("enum", ["off", "light", "strong"]),       # harsh S sounds
        "deplosive": ("bool", None),                          # P/B pops (dynamic low-band cut)
        "breath_control": ("enum", ["off", "light", "strong"]),  # breaths and room between phrases
        "space": ("enum", ["off", "room", "hall"]),
        "engine": ("enum", ["auto", "local", "deepfilter", "elevenlabs"]),
        "allow_cloud": ("bool", None),
    },
    # A voice profile — reusable across Dock, CLI, OS. Only the fields its provider really
    # implements take effect; navig_audio.voice.profiles reports the rest as ignored.
    "speech": {
        "provider": ("enum", SPEECH_PROVIDERS),
        "model": ("str", 80),
        "voice": ("str", 128),
        "lang": ("enum", ["auto", "fr", "ru", "en"]),
        "speed": ("number", (0.5, 2.0)),
        "pitch_hz": ("int", (-100, 100)),
        "stability": ("number", (0.0, 1.0)),
        "similarity": ("number", (0.0, 1.0)),
        "style": ("str", 300),
        # "term=how to say it" pairs, applied before synthesis (case-insensitive, whole word)
        "pronounce": ("list", 60),
    },
}


def validate_params(tool: str, params: Any) -> dict[str, Any]:
    """Return ``params`` coerced to its schema, or raise :class:`PresetError`."""
    if tool not in PARAM_SCHEMAS:
        raise PresetError(f"tool: unknown tool {tool!r} (expected one of {', '.join(TOOLS)})")
    if params in (None, ""):
        return {}
    if not isinstance(params, dict):
        raise PresetError("params: must be a mapping")
    schema = PARAM_SCHEMAS[tool]
    out: dict[str, Any] = {}
    for key, val in params.items():
        if key not in schema:
            raise PresetError(f"params.{key}: not a {tool} setting (allowed: {', '.join(sorted(schema))})")
        if val is None:
            continue
        kind, rule = schema[key]
        out[key] = _coerce(f"params.{key}", kind, rule, val)
    return out


def _coerce(name: str, kind: str, rule: Any, val: Any) -> Any:
    if kind == "enum":
        s = str(val)
        if s not in rule:
            raise PresetError(f"{name}: {s!r} is not one of {', '.join(rule)}")
        return s
    if kind == "bool":
        if isinstance(val, bool):
            return val
        from navig.core.coerce import coerce_bool

        return coerce_bool(val, default=False)
    if kind in ("int", "number"):
        try:
            num = int(val) if kind == "int" else float(val)
        except (TypeError, ValueError):
            raise PresetError(f"{name}: expected a number, got {val!r}") from None
        if isinstance(val, bool):
            raise PresetError(f"{name}: expected a number, got a boolean")
        lo, hi = rule
        if not lo <= num <= hi:
            raise PresetError(f"{name}: {num} is outside {lo}…{hi}")
        return num
    if kind == "list":
        items = val if isinstance(val, list) else [v for v in re.split(r"[,\n]+", str(val)) if v.strip()]
        items = [str(v).strip() for v in items if str(v).strip()]
        if len(items) > rule:
            raise PresetError(f"{name}: at most {rule} items")
        return items
    s = str(val)
    if len(s) > rule:
        raise PresetError(f"{name}: longer than {rule} characters")
    return s


# ── The preset ───────────────────────────────────────────────────────


@dataclass
class PresetVariable:
    key: str
    label: str = ""
    default: str = ""
    required: bool = False


@dataclass
class Preset:
    id: str
    name: str
    tool: str
    layer: str = "style"
    description: str = ""
    category: str = ""
    tags: list[str] = field(default_factory=list)
    # Reusable guidance added to the model's instructions (text) or to the style
    # clause (image/video/audio). Never the user's request itself.
    instructions: str = ""
    # Optional output template; ``{{task}}`` marks where the user's request goes.
    template: str = ""
    variables: list[PresetVariable] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    # Optional provider preference — a hint, never a requirement.
    provider: str = ""
    model: str = ""
    # Things to avoid (text) — rendered as a constraint list.
    avoid: list[str] = field(default_factory=list)
    # Relative paths (inside the space/project root) to approved context files
    # (text) or reference images (image). Resolved and budgeted at use time.
    references: list[str] = field(default_factory=list)
    version: int = 1
    format_version: int = FORMAT_VERSION
    scope: str = "user"
    source: str = "user"
    source_path: str = ""
    # For a user override of a file preset: the id it overrides is the same id;
    # ``overrides`` records the file preset's version that was edited.
    overrides: int | None = None
    favorite: bool = False
    archived: bool = False
    created_at: str = ""
    updated_at: str = ""

    @property
    def trusted(self) -> bool:
        return self.source != "imported"

    @property
    def editable(self) -> bool:
        return self.source in ("user", "imported")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trusted"] = self.trusted
        d["editable"] = self.editable
        return d

    def to_portable(self) -> dict[str, Any]:
        """The shareable form: no timestamps, usage, favorites or local paths."""
        d = asdict(self)
        for k in ("source", "source_path", "favorite", "archived", "created_at", "updated_at", "overrides", "scope"):
            d.pop(k, None)
        return d


def preset_from_dict(raw: Any, *, source: str = "user", scope: str | None = None, source_path: str = "") -> Preset:
    """Build and validate a :class:`Preset` from a mapping (YAML/JSON/API body)."""
    if not isinstance(raw, dict):
        raise PresetError("preset: must be a mapping")
    fmt = int(raw.get("format_version") or FORMAT_VERSION)
    if fmt > FORMAT_VERSION:
        raise PresetError(f"format_version: {fmt} is newer than this NAVIG understands ({FORMAT_VERSION}); update NAVIG")
    pid = str(raw.get("id") or "").strip().lower()
    if not _ID_RE.match(pid):
        raise PresetError(f"id: {pid!r} must be a lowercase slug (a-z, 0-9, . _ / -)")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise PresetError("name: required")
    tool = str(raw.get("tool") or "").strip()
    if tool not in TOOLS:
        raise PresetError(f"tool: {tool!r} is not one of {', '.join(TOOLS)}")
    layer = str(raw.get("layer") or ("config" if tool == "audio.enhance" else "style")).strip()
    if layer not in LAYERS:
        raise PresetError(f"layer: {layer!r} is not one of {', '.join(LAYERS)}")
    if source not in SOURCES:
        raise PresetError(f"source: {source!r} is not one of {', '.join(SOURCES)}")
    scope = scope or (source if source in SCOPES else "user")
    variables = []
    for v in raw.get("variables") or []:
        if isinstance(v, str):
            v = {"key": v}
        if not isinstance(v, dict) or not _VAR_KEY_RE.match(str(v.get("key") or "")):
            raise PresetError(f"variables: bad entry {v!r} (each needs a key like 'character')")
        variables.append(
            PresetVariable(
                key=str(v["key"]),
                label=str(v.get("label") or ""),
                default=str(v.get("default") or ""),
                required=bool(v.get("required", False)),
            )
        )
    references = [str(r).strip() for r in raw.get("references") or [] if str(r).strip()]
    for r in references:
        _check_reference(r)
    return Preset(
        id=pid,
        name=name[:120],
        tool=tool,
        layer=layer,
        description=str(raw.get("description") or "")[:600],
        category=str(raw.get("category") or "")[:60],
        tags=[str(t).strip().lower() for t in raw.get("tags") or [] if str(t).strip()][:20],
        instructions=_limit("instructions", raw.get("instructions"), 12000),
        template=_limit("template", raw.get("template"), 6000),
        variables=variables,
        params=validate_params(tool, raw.get("params")),
        provider=str(raw.get("provider") or "")[:60],
        model=str(raw.get("model") or "")[:120],
        avoid=[str(a).strip() for a in raw.get("avoid") or [] if str(a).strip()][:40],
        references=references[:32],
        version=max(1, int(raw.get("version") or 1)),
        format_version=FORMAT_VERSION,
        scope=scope,
        source=source,
        source_path=source_path,
        favorite=bool(raw.get("favorite", False)),
    )


def _limit(name: str, val: Any, n: int) -> str:
    s = str(val or "")
    if len(s) > n:
        raise PresetError(f"{name}: longer than {n} characters")
    return s


def _check_reference(ref: str) -> None:
    """References are relative paths inside a root — never absolute, never ``..``."""
    p = ref.replace("\\", "/")
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or p.startswith("~"):
        raise PresetError(f"references: {ref!r} must be relative to the space/project root")
    if any(part == ".." for part in p.split("/")):
        raise PresetError(f"references: {ref!r} may not leave the root ('..')")
