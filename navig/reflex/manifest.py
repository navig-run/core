"""The NAVIG Reflex tool manifest — one registry, projected from the shipped commands.

A tool is a *binding*, never a new implementation: it names an existing CLI path
(the router refuses to bind to a command the command manifest does not contain),
declares the slots it accepts, and carries the deterministic patterns that
recognise it. There is deliberately no fourth command table here — the validation
in :func:`validate_bindings` reads ``navig.registry.manifest.build_public_manifest``
so a tool can only point at something ``navig --help`` already publishes.

The shape follows the shipped ``navig_voice.SkillManifest`` contract (mandatory
description, typed slots, ``requires_commit``), extended with the fields the plan
froze: ``intents``, ``risk``, ``context_providers``, ``enabled``, ``command``,
``patterns`` and ``read_only``.

No LLM import, no network, no plugin import at module scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from navig.reflex.errors import ManifestError

#: The only risk vocabulary. ``destructive`` is reserved for batches and anything
#: that hits more than one target: those are plan-only in every profile.
RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "destructive")

#: Approval vocabulary mapping — the router reuses ``navig.tools.approval``'s
#: ``safety_level`` words instead of inventing a second danger language.
RISK_TO_SAFETY: dict[str, str] = {
    "low": "safe",
    "medium": "moderate",
    "high": "dangerous",
    "destructive": "dangerous",
}

#: Risk ordering for the ranker: lower is preferred on an otherwise equal match.
RISK_ORDER: dict[str, int] = {name: index for index, name in enumerate(RISK_LEVELS)}


@dataclass(frozen=True)
class SlotSpec:
    """One typed slot a tool accepts."""

    name: str
    type: str
    required: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "type": self.type, "required": self.required}


@dataclass(frozen=True)
class ToolManifest:
    """One registered action: description is mandatory, exactly like a skill manifest."""

    name: str
    description: str
    command: tuple[str, ...] = ()
    intents: tuple[str, ...] = ()
    slots: tuple[SlotSpec, ...] = ()
    requires_commit: tuple[str, ...] = ()
    risk: str = "low"
    context_providers: tuple[str, ...] = ()
    enabled: bool = True
    patterns: tuple[str, ...] = ()
    read_only: bool = False

    def as_dict(self) -> dict[str, Any]:
        if not self.description.strip():
            raise ManifestError(f"tool {self.name!r} has an empty description")
        if self.risk not in RISK_LEVELS:
            raise ManifestError(
                f"tool {self.name!r} declares risk {self.risk!r}; "
                f"expected one of {', '.join(RISK_LEVELS)}"
            )
        return {
            "name": self.name,
            "description": self.description,
            "command": list(self.command),
            "intents": list(self.intents),
            "slots": [slot.as_dict() for slot in self.slots],
            "requiresCommit": list(self.requires_commit),
            "risk": self.risk,
            "contextProviders": list(self.context_providers),
            "enabled": self.enabled,
            "patterns": list(self.patterns),
            "readOnly": self.read_only,
        }


class ToolRegistry:
    """Every registered tool plus the intent → tool table."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolManifest] = {}
        self._intents: dict[str, str] = {}

    def register(self, tool: ToolManifest) -> ToolManifest:
        tool.as_dict()  # validates description + risk eagerly, at registration
        if not tool.command:
            raise ManifestError(f"tool {tool.name!r} has no command binding")
        if tool.name in self._tools:
            raise ManifestError(f"tool {tool.name!r} is already registered")
        for intent in tool.intents:
            claimed = self._intents.get(intent)
            if claimed is not None:
                raise ManifestError(
                    f"intent {intent!r} is claimed by {claimed!r} and {tool.name!r}"
                )
        self._tools[tool.name] = tool
        for intent in tool.intents:
            self._intents[intent] = tool.name
        return tool

    def get(self, name: str) -> ToolManifest | None:
        return self._tools.get(str(name))

    def for_intent(self, intent: str) -> ToolManifest | None:
        name = self._intents.get(str(intent))
        return self._tools.get(name) if name else None

    def all(self) -> tuple[ToolManifest, ...]:
        return tuple(self._tools.values())

    def enabled(self) -> tuple[ToolManifest, ...]:
        return tuple(tool for tool in self._tools.values() if tool.enabled)

    def manifests(self) -> list[dict[str, Any]]:
        return [tool.as_dict() for tool in self._tools.values()]

    def validate_bindings(self, known_paths: Iterable[str]) -> None:
        """Every tool's ``command`` must exist in the command manifest.

        ``known_paths`` are the manifest's paths (``"navig home off"``). A binding that
        is not there is a programming error, not a runtime condition: the router would
        shell a command that does not exist, so registration fails loudly instead.
        """
        known = {str(path).strip() for path in known_paths}
        unknown = [
            f"{tool.name} → navig {' '.join(tool.command)}"
            for tool in self._tools.values()
            if f"navig {' '.join(tool.command)}" not in known
        ]
        if unknown:
            raise ManifestError(
                "tool command binding(s) not present in the command manifest: "
                + "; ".join(sorted(unknown)),
                "add the command (or fix the tool's command tuple) before shipping",
            )


# ── The shipped tool set (10 tools, deliberately small) ─────────────────────
#
# Patterns are matched against text normalised by ``navig.reflex.preclassify.normalize``
# (lowercase, ё→е, accents folded, punctuation and hyphens → spaces). Named groups:
# ``device``, ``room``, ``value``, ``capability``, ``on``, ``direction``.

_DEV = r"(?P<device>.+?)"

_TOOL_TABLE: tuple[ToolManifest, ...] = (
    ToolManifest(
        name="home.on",
        description="Turn one home device (or a room / the house) on.",
        command=("home", "on"),
        intents=("home.on", "device.on"),
        slots=(SlotSpec("device", "device"), SlotSpec("room", "room")),
        requires_commit=("home on",),
        risk="low",
        context_providers=("endpoints",),
        patterns=(
            r"^(?:turn|switch|power)\s+(?:on|up)\s+(?:the\s+)?" + _DEV + r"$",
            r"^(?:turn|switch|power)\s+(?:the\s+)?" + _DEV + r"\s+(?:on|up)$",
            r"^(?:включи|включить|зажги|вруби|подними)\s+" + _DEV + r"$",
        ),
    ),
    ToolManifest(
        name="home.off",
        description="Turn one home device (or a room / the house) off.",
        command=("home", "off"),
        intents=("home.off", "device.off"),
        slots=(SlotSpec("device", "device"), SlotSpec("room", "room")),
        requires_commit=("home off",),
        risk="low",
        context_providers=("endpoints",),
        patterns=(
            r"^(?:turn|switch|power)\s+(?:off|down)\s+(?:the\s+)?" + _DEV + r"$",
            r"^(?:turn|switch|power)\s+(?:the\s+)?" + _DEV + r"\s+(?:off|down|out)$",
            r"^(?:выключи|выключить|погаси|отключи|выруби|опусти)\s+" + _DEV + r"$",
            r"^all\s+(?:the\s+)?(?:lights?|lamps?|devices?)\s+(?:off|out)$",
        ),
    ),
    ToolManifest(
        name="home.toggle",
        description="Flip one home device's power state.",
        command=("home", "toggle"),
        intents=("home.toggle", "device.toggle"),
        slots=(SlotSpec("device", "device"), SlotSpec("room", "room")),
        requires_commit=("home toggle",),
        risk="low",
        context_providers=("endpoints",),
        patterns=(
            r"^(?:toggle|flip)\s+(?:the\s+)?" + _DEV + r"$",
            r"^(?:switch)\s+(?:the\s+)?" + _DEV + r"$",
            r"^(?:переключи|переключить|переверни)\s+" + _DEV + r"$",
        ),
    ),
    ToolManifest(
        name="home.set",
        description="Set a home device attribute: brightness, colour, colour temperature, "
        "target temperature, mode or fan speed.",
        command=("home", "set"),
        intents=("home.set", "device.set"),
        slots=(
            SlotSpec("device", "device"),
            SlotSpec("room", "room"),
            SlotSpec("capability", "capability"),
            SlotSpec("value", "value", required=True),
        ),
        requires_commit=("home set",),
        risk="low",
        context_providers=("endpoints",),
        patterns=(
            r"^(?:set|change|adjust)\s+(?:the\s+)?"
            r"(?P<device>(?:(?!bright|colou?r|temperature|temp|mode|fan|speed).)+?)\s+"
            r"(?:to\s+)?(?P<capability>colour temperature|color temperature|colour temp|"
            r"color temp|brightness|bright|colour|color|temperature|temp|mode|fan|speed)"
            r"\s*(?:to|at|=)?\s*(?P<value>.+)$",
            r"^(?:dim|brighten)\s+(?:the\s+)?" + _DEV + r"\s+to\s+(?P<value>.+)$",
            r"^(?:поставь|установи|задай|сделай|убавь|прибавь)\s+"
            r"(?P<device>(?:(?!яркость|ярче|цвет|температуру|режим|вентилятор).)+?)\s*"
            r"(?P<capability>цветовую температуру|цвет|яркость|ярче|температуру|"
            r"режим|вентилятор)\s*(?:на|до|в)?\s*(?P<value>.+)$",
            r"^(?:поставь|установи|задай)\s+"
            r"(?P<capability>цветовую температуру|цвет|яркость|температуру|режим|вентилятор)"
            r"\s*(?:на|до|в)?\s*(?P<value>.+)$",
        ),
    ),
    ToolManifest(
        name="home.status",
        description="Read the live state of one home device, a room, or every device.",
        command=("home", "status"),
        intents=("home.status", "device.status"),
        slots=(SlotSpec("device", "device"), SlotSpec("room", "room")),
        risk="low",
        context_providers=("endpoints",),
        read_only=True,
        patterns=(
            r"^(?:what is|whats|what's|how is)\s+(?:the\s+)?(?:status|state)\s+(?:of\s+)?"
            r"(?:the\s+)?" + _DEV + r"$",
            r"^(?:status|state)\s+(?:of\s+)?(?:the\s+)?" + _DEV + r"$",
            r"^(?:is|are)\s+(?:the\s+)?" + _DEV + r"\s+(?:on|off)$",
            r"^(?:home|devices)\s+(?:status|state)$",
            r"^(?:какой|какое|как)\s+(?:статус|состояние)\s+(?:у\s+)?" + _DEV + r"$",
            r"^(?:статус|состояние)\s+(?:у\s+)?" + _DEV + r"$",
            r"^(?:проверь|покажи)\s+(?:статус|состояние)\s+(?:у\s+)?" + _DEV + r"$",
            r"^(?:статус|состояние)\s+(?:дома|устройств)$",
        ),
    ),
    ToolManifest(
        name="tv.power",
        description="Toggle the TV's power (or wake it).",
        command=("tv", "power"),
        intents=("tv.power",),
        slots=(SlotSpec("on", "bool"),),
        requires_commit=("tv power",),
        risk="low",
        context_providers=("tv",),
        patterns=(
            r"^(?:turn|switch|power)\s+(?P<on>on|off)\s+(?:the\s+)?"
            r"(?:tv|television|telly)$",
            r"^(?:turn|switch|power)\s+(?:the\s+)?(?:tv|television|telly)\s+(?P<on>on|off)$",
            r"^(?:tv|television|telly)\s+(?P<on>on|off)$",
            r"^(?:включи|выключи|вруби|отключи)\s+(?:телевизор|тв)$",
        ),
    ),
    ToolManifest(
        name="tv.volume",
        description="Change the TV volume (up/down) or mute it.",
        command=("tv", "volume"),
        intents=("tv.volume",),
        slots=(SlotSpec("direction", "direction", required=True),),
        requires_commit=("tv volume",),
        risk="low",
        context_providers=("tv",),
        patterns=(
            r"^(?:turn\s+)?(?:the\s+)?volume\s+(?P<direction>up|down)$",
            r"^(?:volume|sound)\s+(?P<direction>up|down)$",
            r"^(?P<direction>mute)$",
            r"^(?:сделай\s+|звук\s+)?(?P<direction>громче|тише)$",
            r"^прибавь\s+(?:звук|громкость)$",
            r"^убавь\s+(?:звук|громкость)$",
            r"^(?P<direction>выключи звук)$",
        ),
    ),
    ToolManifest(
        name="tv.play",
        description="Play or pause on the TV.",
        command=("tv", "play"),
        intents=("tv.play",),
        requires_commit=("tv play",),
        risk="low",
        context_providers=("tv",),
        patterns=(
            r"^(?:press\s+)?play(?:\s*(?:/|\s)?\s*pause)?$",
            r"^(?:resume)$",
            r"^(?:плей|воспроизведи|играй)$",
        ),
    ),
    ToolManifest(
        name="assistant.status",
        description="Read the voice assistant's own health (providers, TTS, skills).",
        command=("assistant", "doctor"),
        intents=("assistant.status",),
        risk="low",
        context_providers=("assistant",),
        read_only=True,
        patterns=(
            r"^(?:assistant|voice)\s+(?:status|doctor)$",
            r"^(?:is\s+)?(?:the\s+)?assistant\s+(?:ok|healthy|up)$",
            r"^(?:проверь|статус)\s+(?:ассистента|голосового ассистента|assistant)$",
        ),
    ),
    ToolManifest(
        name="campaigns.status",
        description="List campaign progress — read-only; campaigns stay the tasks store's job.",
        command=("tasks", "campaigns", "list"),
        intents=("campaigns.status",),
        risk="low",
        context_providers=("tasks",),
        read_only=True,
        patterns=(
            r"^(?:campaigns?|tasks)\s+(?:status|list)$",
            r"^(?:show|list)\s+(?:the\s+)?campaigns?$",
            r"^(?:покажи|список)\s+(?:кампании|кампаний|задания)$",
            r"^(?:кампании|задания)\s*(?:список|статус)$",
        ),
    ),
    ToolManifest(
        name="coordination.inbox",
        description="List pending coordination messages (the durable inter-agent/inter-session "
        "mailbox) for the operator, or for a named session/agent participant.",
        command=("coordination", "inbox"),
        intents=("coordination.inbox",),
        slots=(SlotSpec("recipient", "participant"),),
        risk="low",
        context_providers=("coordination",),
        read_only=True,
        patterns=(
            r"^(?:what is|whats)\s+(?:waiting|pending|next|queued)\s*(?:for me)?$",
            r"^(?:check|show|list|read)\s+(?:my\s+)?(?:coordination\s+)?(?:inbox|messages|handoffs)$",
            r"^(?:any|new|pending)\s+(?:messages|handoffs|coordination)$",
            r"^(?:что|какие|сколько)\s+(?:ждёт|ожидает|висит|сообщений)\s*(?:меня)?$",
            r"^(?:входящие|сообщения|координация)\s*(?:список|статус)?$",
        ),
    ),
)


def build_registry(tools: Iterable[ToolManifest] | None = None) -> ToolRegistry:
    """Build a registry; defaults to the shipped 10-tool set."""
    registry = ToolRegistry()
    for tool in tools if tools is not None else _TOOL_TABLE:
        registry.register(tool)
    return registry


_registry: ToolRegistry | None = None


def default_registry() -> ToolRegistry:
    """The process-wide shipped registry (built once; registration is validated eagerly)."""
    global _registry
    if _registry is None:
        _registry = build_registry()
    return _registry


def known_command_paths() -> set[str]:
    """Every published command path, from the ONE command manifest.

    Imported lazily: ``build_public_manifest`` walks the live Typer tree and loads
    shipped plugins, which is far too heavy for the ``navig ask`` fast path — it is
    only called by :func:`validate_against_command_manifest` (tests, doctor-style
    checks, release validation).
    """
    from navig.registry.manifest import build_public_manifest  # noqa: PLC0415

    manifest = build_public_manifest()
    return {str(entry.get("path", "")).strip() for entry in manifest.get("commands", [])}


def validate_against_command_manifest(registry: ToolRegistry | None = None) -> None:
    """Fail unless every registered tool binds to a command that really exists."""
    (registry or default_registry()).validate_bindings(known_command_paths())


__all__ = [
    "RISK_LEVELS",
    "RISK_ORDER",
    "RISK_TO_SAFETY",
    "SlotSpec",
    "ToolManifest",
    "ToolRegistry",
    "build_registry",
    "default_registry",
    "known_command_paths",
    "validate_against_command_manifest",
]
