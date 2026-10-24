"""Deterministic pre-classification — no model, no network, no plugin import.

An utterance becomes a :class:`Preclassification` (tool + typed slots + resolved
targets + blast radius) or ``None``. The algorithm is the one the shipped voice
plugin proved (``navig_voice/nlu.py``): normalise, fold, match declared per-tool
patterns, extract typed slots, resolve device/room names against the endpoint
projection, then rank. Nothing here imports an AI module — the caller decides
whether an unmatched utterance falls through to the assistant.

Values are parsed into the typed shapes ``navig home set`` accepts (percent for
brightness, Kelvin for colour temperature, ``#rrggbb`` for colour), including
English/Russian number words, so the router never shells a command with text the
provider would reject.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable

from navig.reflex.manifest import RISK_ORDER, ToolManifest, ToolRegistry
from navig.reflex.resolve import (
    SCOPE_LIGHT_WORDS,
    SCOPE_TOTAL_WORDS,
    EndpointResolver,
    EndpointView,
    summarize,
)

# ── normalisation ────────────────────────────────────────────────────────────

_PUNCTUATION = re.compile(r"[!?;,\"«»„“”()\[\]{}…•·/\\]")
_DETERMINERS = frozenset(
    {"the", "a", "an", "my", "our", "мой", "мою", "моя", "наш", "наша", "наше"}
)
_SCOPE_WORDS = SCOPE_TOTAL_WORDS
_LIGHT_WORDS = SCOPE_LIGHT_WORDS
_LIGHT_STEMS = frozenset({"light", "lamp", "ламп"})

_ROOM_PREPOSITION = re.compile(r"\s+(?:in|в|во|на)\s+(?:the\s+)?(?P<room>[^,]+)$")

#: Spoken-concept aliases: a Russian noun (already stemmed, or its folded form) means the
#: same *thing* as the English word operators put in device ids/names. The inventory is
#: the operator's own (`devices.yaml`: ``desk-lamp``, ``Kitchen light``, ``living room``),
#: so a Russian-speaking operator must still reach it — this maps the spoken concept, not
#: a specific utterance, and it is symmetric (an English speaker reaches a Russian-named
#: inventory through the same table).
_CONCEPT_WORDS: dict[str, tuple[str, ...]] = {
    "ламп": ("lamp",),
    "лампа": ("lamp",),
    "лампы": ("lamp",),
    "свет": ("light",),
    "света": ("light",),
    "люстр": ("chandelier", "light"),
    "освещение": ("light",),
    "кондиционер": ("ac", "air", "conditioner"),
    "кондицион": ("ac",),
    "сплит": ("ac",),
    "телевизор": ("tv", "television"),
    "тв": ("tv",),
    "настольн": ("desk",),
    "кухонн": ("kitchen",),
    "гостин": ("living", "room"),
    "спальн": ("bedroom",),
    "увлажнитель": ("humidifier",),
    "датчик": ("sensor",),
    "сенсор": ("sensor",),
    "выключатель": ("switch",),
    "розетка": ("plug", "socket"),
}

#: Spoken room (stemmed/plain) → canonical room concept as it appears in an inventory.
_ROOM_CONCEPTS: dict[str, str] = {
    "кухн": "kitchen",
    "кухня": "kitchen",
    "кабинет": "office",
    "кабинете": "office",
    "офис": "office",
    "гостин": "living room",
    "гостиная": "living room",
    "зал": "living room",
    "спальн": "bedroom",
    "спальня": "bedroom",
    "ванн": "bathroom",
    "ванная": "bathroom",
    "прихож": "hallway",
    "коридор": "hallway",
    "детск": "nursery",
}

_RU_ENDINGS: tuple[str, ...] = (
    "иями", "ями", "ами", "ыми", "ими", "ого", "его", "ому", "ему",
    "ая", "яя", "ое", "ее", "ые", "ие", "ую", "юю", "ой", "ей", "ый", "ий",
    "ах", "ях", "ом", "ем", "ам", "ям", "ы", "и", "а", "я", "е", "у", "ю", "о",
)


def normalize(text: str) -> str:
    """Lowercase, accent-folded, punctuation-free form used for matching.

    ``ё`` folds to ``е`` on purpose (both spellings are normal Russian), hyphens and
    apostrophes become spaces so ``living-room`` and ``living room`` compare equal,
    and a leading/trailing politeness word is dropped rather than becoming a device.
    A decimal separator between digits is preserved (``22.5`` stays one token), so a
    temperature is not silently truncated to its integer part.
    """
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\u00a0", " ").replace("\u2009", " ").replace("\u202f", " ")
    for apostrophe in ("'", "’", "‘", "ʼ", "`", "´"):
        value = value.replace(apostrophe, " ")
    value = re.sub(r"(?<=\d)[.,](?=\d)", "\x00", value)
    value = _PUNCTUATION.sub(" ", value)
    value = value.replace("-", " ").replace("—", " ").replace("–", " ")
    value = value.replace(".", " ")
    value = _fold_case(value)
    value = " ".join(value.split()).strip()
    for word in ("please", "пожалуйста"):
        if value.startswith(word + " "):
            value = value[len(word) + 1 :]
        if value.endswith(" " + word):
            value = value[: -len(word) - 1]
    return " ".join(value.replace("\x00", ".").split()).strip()


def _fold_case(text: str) -> str:
    """Accent-free lowercase; Cyrillic case only (NFKD would rename ``й``)."""
    out: list[str] = []
    for character in text:
        codepoint = ord(character)
        if 0x0400 <= codepoint <= 0x04FF:
            out.append("е" if character.casefold() == "ё" else character.casefold())
            continue
        decomposed = unicodedata.normalize("NFKD", character)
        base = "".join(char for char in decomposed if not unicodedata.combining(char))
        out.append(base.casefold())
    return "".join(out)


def _stem(token: str) -> str:
    """A tolerant stem for name/room comparison (``лампу``/``лампа`` → ``ламп``)."""
    if not token:
        return ""
    if re.search(r"[а-я]", token):
        for ending in _RU_ENDINGS:
            if token.endswith(ending) and len(token) - len(ending) >= 4:
                return token[: -len(ending)]
        return token
    if token.endswith("ies") and len(token) > 5:
        return token[:-3] + "y"
    if token.endswith(("ses", "xes", "ches", "shes")) and len(token) > 5:
        return token[:-2]
    if token.endswith("s") and not token.endswith("ss") and len(token) > 3:
        return token[:-1]
    return token


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(word for word in normalize(text).split() if word and word not in _DETERMINERS)


def _concept_variants(token: str) -> set[str]:
    """The set of words *token* may stand for: itself, its stem, and its concept aliases."""
    variants = {token}
    stem = _stem(token)
    if stem:
        variants.add(stem)
    for key in (token, stem):
        for concept in _CONCEPT_WORDS.get(key, ()):
            variants.update(concept.split())
    return variants


def _field_variants(text: str) -> set[str]:
    variants: set[str] = set()
    for token in _tokens(text):
        variants |= _concept_variants(token)
    return variants


def _room_variants(text: str) -> set[str]:
    """Words a spoken OR inventory room name may stand for, including room concepts."""
    variants: set[str] = set()
    for token in _tokens(text):
        variants |= _concept_variants(token)
        stem = _stem(token)
        for key in (token, stem):
            concept = _ROOM_CONCEPTS.get(key)
            if concept:
                variants.update(concept.split())
    return variants


def _field_texts(endpoint: EndpointView) -> tuple[str, ...]:
    return (endpoint.id, endpoint.local_id, endpoint.name, *endpoint.aliases)


# ── number / value parsing ───────────────────────────────────────────────────

_EN_SMALL: dict[str, int] = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19,
}
_EN_TENS: dict[str, int] = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
_RU_SMALL: dict[str, int] = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
}
_RU_TENS: dict[str, int] = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_NUMBER_WORDS: dict[str, int] = {**_EN_SMALL, **_EN_TENS, **_RU_SMALL, **_RU_TENS}
_RELATIVE_WORDS: dict[str, int] = {
    "half": 50, "половина": 50, "половину": 50,
    "max": 100, "maximum": 100, "full": 100, "максимум": 100, "максимальную": 100,
    "min": 1, "minimum": 1, "минимум": 1,
}

COLOUR_NAMES: dict[str, str] = {
    "red": "#ff0000",
    "красный": "#ff0000",
    "красную": "#ff0000",
    "green": "#00ff00",
    "зелёный": "#00ff00",
    "зеленый": "#00ff00",
    "blue": "#0000ff",
    "синий": "#0000ff",
    "синюю": "#0000ff",
    "white": "#ffffff",
    "белый": "#ffffff",
    "warm white": "#ffd7a0",
    "тёплый белый": "#ffd7a0",
    "теплый белый": "#ffd7a0",
    "cool white": "#cfe8ff",
    "холодный белый": "#cfe8ff",
    "orange": "#ff8000",
    "оранжевый": "#ff8000",
    "yellow": "#ffd700",
    "жёлтый": "#ffd700",
    "желтый": "#ffd700",
    "purple": "#8000ff",
    "фиолетовый": "#8000ff",
    "pink": "#ff69b4",
    "розовый": "#ff69b4",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "gold": "#ffd700",
}

#: Capability words → the canonical ``navig home set`` key.
CAPABILITY_KEYS: dict[str, str] = {
    "brightness": "brightness",
    "bright": "brightness",
    "яркость": "brightness",
    "ярче": "brightness",
    "color": "color",
    "colour": "color",
    "цвет": "color",
    "color temperature": "color_temp",
    "colour temperature": "color_temp",
    "color temp": "color_temp",
    "colour temp": "color_temp",
    "temp": "color_temp",
    "цветовую температуру": "color_temp",
    "temperature": "target_temp",
    "температуру": "target_temp",
    "mode": "mode",
    "режим": "mode",
    "fan": "fan",
    "fan speed": "fan",
    "speed": "fan",
    "вентилятор": "fan",
}

_NUMBER_PATTERN = re.compile(r"-?\d+(?:[.,]\d+)?")


def parse_number(raw: str) -> float | int | None:
    """Parse ``40``, ``40%``, ``сорок``, ``twenty five``, ``half`` into a number."""
    text = normalize(raw)
    if not text:
        return None
    match = _NUMBER_PATTERN.search(text)
    if match:
        number = float(match.group(0).replace(",", "."))
        return int(number) if number == int(number) else number
    total = 0
    found = False
    for word in text.split():
        if word in _RELATIVE_WORDS:
            return _RELATIVE_WORDS[word]
        if word in _NUMBER_WORDS:
            total += _NUMBER_WORDS[word]
            found = True
        elif word in {"hundred", "сто"}:
            total = (total or 1) * 100
            found = True
    return total if found else None


def _parse_colour(raw: str) -> str | None:
    text = normalize(raw)
    for name, hex_value in COLOUR_NAMES.items():
        if name in text:
            return hex_value
    cleaned = text.replace(" ", "").lstrip("#")
    if re.fullmatch(r"[0-9a-f]{3}|[0-9a-f]{6}", cleaned):
        return f"#{cleaned}"
    return None


def parse_value(capability: str, raw: str) -> Any:
    """Parse a spoken value into the typed value ``navig home set`` expects."""
    if capability == "brightness":
        number = parse_number(raw)
        return None if number is None else max(1, min(100, int(number)))
    if capability == "color":
        return _parse_colour(raw)
    if capability == "color_temp":
        number = parse_number(raw)
        if number is None:
            return None
        if number < 1000:  # "27" for 2700 K is a common spoken shorthand
            number = int(number * 100)
        return max(1000, min(12000, int(number)))
    if capability == "target_temp":
        number = parse_number(raw)
        return None if number is None else round(float(number), 1)
    return normalize(raw) or None


# ── results ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Target:
    """One resolved endpoint target."""

    id: str
    label: str
    room: str = ""
    capabilities: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "room": self.room,
            "capabilities": list(self.capabilities),
        }


@dataclass
class Preclassification:
    """What the deterministic parser found for one utterance."""

    tool: ToolManifest
    slots: dict[str, Any] = field(default_factory=dict)
    targets: tuple[Target, ...] = ()
    blast_radius: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    needs_slot: str = ""
    candidates: tuple[dict[str, Any], ...] = ()
    alternatives: tuple[dict[str, Any], ...] = ()
    normalized: str = ""
    score: tuple[Any, ...] = ()

    @property
    def destructive(self) -> bool:
        return self.blast_radius.get("kind") == "batch"

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool.name,
            "slots": dict(self.slots),
            "targets": [target.as_dict() for target in self.targets],
            "blast_radius": dict(self.blast_radius),
            "risk": self.tool.risk,
            "read_only": self.tool.read_only,
            "confidence": round(self.confidence, 3),
            "needs_slot": self.needs_slot,
            "candidates": [dict(item) for item in self.candidates],
            "alternatives": [dict(item) for item in self.alternatives],
        }


# ── matching ─────────────────────────────────────────────────────────────────


def _match_endpoints(
    query: str, endpoints: Iterable[EndpointView]
) -> tuple[list[EndpointView], int]:
    """Resolve a spoken name; returns (matches, tier). A lower tier is stronger.

    Matching is concept-aware: a Russian spoken noun may stand for the English word in
    the operator's device name (``лампу`` → ``lamp`` → ``desk-lamp``), through
    :func:`_concept_variants` on both sides. The tiers stay strict — exact, prefix,
    every-token-mapped, substring — and a query that matches several endpoints stays
    ambiguous (the caller asks instead of guessing).
    """
    wanted = normalize(query)
    tokens = _tokens(wanted)
    if not wanted or not tokens:
        return [], 0
    wanted_variants = [_concept_variants(token) for token in tokens]
    exact: list[EndpointView] = []
    prefix: list[EndpointView] = []
    contained: list[EndpointView] = []
    substring: list[EndpointView] = []
    for endpoint in endpoints:
        fields = [normalize(value) for value in _field_texts(endpoint)]
        field_union: set[str] = set()
        for value in _field_texts(endpoint):
            field_union |= _field_variants(value)
        if wanted in fields:
            exact.append(endpoint)
            continue
        if any(
            len(field) >= 4
            and len(wanted) >= 4
            and (field.startswith(wanted) or wanted.startswith(field))
            for field in fields
        ):
            prefix.append(endpoint)
            continue
        if all(variants & field_union for variants in wanted_variants):
            contained.append(endpoint)
            continue
        if any(len(wanted) >= 4 and wanted in field for field in fields):
            substring.append(endpoint)
    for tier, found in enumerate((exact, prefix, contained, substring), start=1):
        if found:
            return found, tier
    return [], 0


def _scope_kind(device_text: str) -> str:
    """``all`` / ``lights`` when the "device" is really a scope, else ``""``."""
    tokens = [token for token in _tokens(device_text)]
    if not tokens:
        return ""
    if not all(token in _SCOPE_WORDS or token in _LIGHT_WORDS for token in tokens):
        return ""
    if any(token in _LIGHT_WORDS for token in tokens):
        return "lights"
    return "all"


def _explicit_total(device_text: str) -> bool:
    """True for "all"/"everything"/"все" — an explicit whole-scope request."""
    return any(token in _SCOPE_WORDS for token in _tokens(device_text))


def _capability_filter(capability: str) -> tuple[str, str]:
    """Spoken capability → (wire capability type suffix, wire instance).

    Brightness and target temperature are both ``range`` on the wire; the instance is
    what keeps "яркость" from resolving to a thermostat.
    """
    if capability == "color":
        return ("color_setting", "")
    if capability == "color_temp":
        return ("color_setting", "temperature_k")
    if capability == "brightness":
        return ("range", "brightness")
    if capability == "target_temp":
        return ("range", "temperature")
    if capability == "mode":
        return ("mode", "mode")
    if capability == "fan":
        return ("mode", "fan_speed")
    return ("", "")


def _supports(endpoint: EndpointView, capability_filter: tuple[str, str]) -> bool:
    return endpoint.supports(capability_filter[0], capability_filter[1])


def _resolve_room(room_text: str, endpoints: Iterable[EndpointView]) -> tuple[str, list[str]]:
    """Return (canonical room, candidate rooms) for a spoken room.

    Concept-aware in both directions, so ``на кухне`` reaches a room stored as
    ``kitchen`` and ``in the kitchen`` reaches ``Кухня``.
    """
    wanted = normalize(room_text)
    rooms: dict[str, str] = {}
    for endpoint in endpoints:
        if endpoint.room:
            rooms.setdefault(normalize(endpoint.room), endpoint.room)
    if not wanted:
        return "", sorted(rooms.values())
    if wanted in rooms:
        return rooms[wanted], []
    for normalized_room, original in rooms.items():
        if len(wanted) >= 4 and (
            normalized_room.startswith(wanted) or wanted.startswith(normalized_room)
        ):
            return original, []
    wanted_stem = _stem(wanted.replace(" ", ""))
    for normalized_room, original in rooms.items():
        room_stem = _stem(normalized_room.replace(" ", ""))
        if len(wanted_stem) >= 4 and room_stem.startswith(wanted_stem[:4]):
            return original, []
    wanted_variants = _room_variants(wanted)
    for normalized_room, original in rooms.items():
        if wanted_variants & _room_variants(normalized_room):
            return original, []
    return "", sorted(rooms.values())


def _target(endpoint: EndpointView) -> Target:
    return Target(
        id=endpoint.id,
        label=endpoint.name or endpoint.local_id,
        room=endpoint.room,
        capabilities=endpoint.capabilities,
    )


def _candidate(endpoint: EndpointView) -> dict[str, Any]:
    """A slot-request candidate: the stable id plus the human label the operator picks."""
    return {
        "id": endpoint.id,
        "label": endpoint.name or endpoint.local_id,
        "room": endpoint.room,
        "capabilities": list(endpoint.capabilities),
    }


def _power_capable(endpoint: EndpointView) -> bool:
    return not endpoint.capabilities or "on_off" in endpoint.capabilities


def _targets_for(
    tool: ToolManifest, slots: dict[str, Any], endpoints: list[EndpointView]
) -> tuple[tuple[Target, ...], str, tuple[dict[str, Any], ...]]:
    """Resolve device/room slots into targets. Returns (targets, needs_slot, candidates)."""
    slot_names = {slot.name for slot in tool.slots}
    if "device" not in slot_names and "room" not in slot_names:
        return (), "", ()  # a whole-device verb (TV/status tools): no target to resolve
    room_text = str(slots.get("room_text") or "")
    room = ""
    if room_text:
        room, room_candidates = _resolve_room(room_text, endpoints)
        if not room:
            return (), "room", tuple({"room": name} for name in room_candidates)
    pool = list(endpoints)
    if room:
        pool = [item for item in pool if normalize(item.room) == normalize(room)]

    device_text = str(slots.get("device_text") or "").strip()
    scope = _scope_kind(device_text) if device_text else ""
    if scope:
        targets = [
            item for item in pool if (scope != "lights" or item.is_light) and _power_capable(item)
        ]
        if not targets:
            return (), "device", tuple(_candidate(item) for item in pool)
        return tuple(_target(item) for item in targets), "", ()

    capability = slots.get("capability")
    capability_filter = (
        _capability_filter(capability) if isinstance(capability, str) else ("", "")
    )

    if device_text:
        matches, _tier = _match_endpoints(device_text, pool)
        if len(matches) == 1:
            chosen = matches[0]
            if not _supports(chosen, capability_filter):
                return (
                    (),
                    "capability",
                    (
                        {
                            "id": chosen.id,
                            "label": chosen.name or chosen.local_id,
                            "capabilities": list(chosen.capabilities),
                        },
                    ),
                )
            return (_target(chosen),), "", ()
        if len(matches) > 1:
            return (), "device", tuple(_candidate(item) for item in matches)

    if room:
        targets = [item for item in pool if _supports(item, capability_filter)]
        if targets:
            return tuple(_target(item) for item in targets), "", ()

    if tool.read_only and not device_text:
        return tuple(_target(item) for item in pool), "", ()

    # Nothing was named: a capability names the set of devices that can do it, and ALL of
    # them are the target — one dimmable light is a single-target plan, a houseful of
    # dimmable lights is a batch (plan-only, destructive). A NAMED device that did not
    # match is never guessed onto the only endpoint — that would act on the wrong device;
    # it becomes a slot request with the near matches as candidates.
    if not device_text:
        narrow = [item for item in pool if _supports(item, capability_filter)]
        if narrow:
            return tuple(_target(item) for item in narrow), "", ()
        return (), "device", tuple(_candidate(item) for item in pool[:5])
    return (), "device", tuple(_candidate(item) for item in pool[:5])


def _infer_slots(
    tool: ToolManifest, match: re.Match[str], text: str, slots: dict[str, Any]
) -> None:
    """Fill pattern groups plus the values a pattern cannot always capture."""
    for name, value in match.groupdict().items():
        if value is None or not str(value).strip():
            continue
        slots[f"{name}_text"] = str(value).strip()
    if tool.name in {"home.on", "home.off", "home.toggle"} and "device_text" not in slots:
        # The scope-only wordings ("all lights off") carry no device group; recover the
        # scope so the whole inventory is never switched just because a slot is absent.
        if any(word in text for word in ("light", "lamp", "свет", "ламп")):
            slots["device_text"] = "lights"
        elif any(word in text for word in ("everything", "devices", "all", "все", "всё")):
            slots["device_text"] = "everything"
    if tool.name == "tv.power" and "on_text" not in slots:
        slots["on"] = "выключи" not in text and "отключи" not in text
    if tool.name == "tv.volume" and "direction_text" not in slots:
        if "прибавь" in text or "громче" in text:
            slots["direction"] = "up"
        elif "убавь" in text or "тише" in text:
            slots["direction"] = "down"
    if tool.name == "home.set":
        capability = CAPABILITY_KEYS.get(
            normalize(str(slots.get("capability_text") or "brightness")), "brightness"
        )
        slots["capability"] = capability
        raw_value = str(slots.get("value_text") or "")
        slots["value"] = parse_value(capability, raw_value)
        slots["value_raw"] = raw_value


def _clean_slots(slots: dict[str, Any]) -> dict[str, Any]:
    """Drop the internal ``*_text`` carriers, keep the plan's slot vocabulary."""
    cleaned: dict[str, Any] = {}
    for name in ("device", "room", "value", "capability", "on", "direction", "recipient"):
        if name in slots:
            cleaned[name] = slots[name]
    if "device_text" in slots:
        cleaned["device"] = str(slots["device_text"])
    if "room_text" in slots:
        cleaned["room"] = str(slots["room_text"])
    if "on_text" in slots:
        cleaned["on"] = normalize(str(slots["on_text"])) in {"on", "up"}
    if "direction_text" in slots:
        direction = normalize(str(slots["direction_text"]))
        cleaned["direction"] = {"громче": "up", "тише": "down"}.get(
            direction, "mute" if "выключи" in direction else direction
        )
    if "recipient_text" in slots:
        cleaned["recipient"] = normalize(str(slots["recipient_text"]))
    return {name: value for name, value in cleaned.items() if value is not None}


def _blast_radius(
    targets: tuple[Target, ...], *, read_only: bool, total: bool
) -> dict[str, Any]:
    """Describe what a plan would touch. Only a batch is ``destructive``.

    ``total`` means the utterance named the whole set ("all"/"everything"/"все"); it
    is a batch even when it currently resolves to one device. More than one resolved
    target is always a batch. Read-only tools are not "destructive" at all.
    """
    listed = [item.as_dict() for item in targets]
    if read_only:
        return {"kind": "read", "count": len(targets), "targets": listed}
    if total or len(targets) > 1:
        return {"kind": "batch", "count": len(targets), "targets": listed}
    return {"kind": "single", "count": len(targets), "targets": listed}


def _slot_score(
    tool: ToolManifest, slots: dict[str, Any], targets: tuple[Target, ...]
) -> tuple[int, int]:
    """(required slots present, all slots present) — the first rank factors."""
    required = 0
    present = 0
    for slot in tool.slots:
        has_value = slots.get(slot.name) not in (None, "", ())
        if slot.name == "device" and targets:
            has_value = True
        if slot.required and has_value:
            required += 1
        if has_value:
            present += 1
    return required, present


def classify(
    text: str,
    registry: ToolRegistry,
    resolver: EndpointResolver | None = None,
) -> Preclassification | None:
    """Match *text* to at most one tool plan. ``None`` means "not a routed action".

    Fail closed, in two phases:

    1. **contract** — every pattern match is checked against the contract: the tool is
       enabled at registration, its required slots resolve, and (for a set) the target
       declares the requested capability. A candidate that fails is never ranked, so a
       cheaper/lower-risk false match cannot win past an unresolved target.
    2. **rank** — survivors only, deterministically: required slots present → all slots
       present → specificity (matched length, then pattern length) → lower risk →
       tool name. The full ordered survivors list is kept as ``alternatives[]`` for
       reporting; it is **never** auto-executed (fallback is classification-time only).

    A recognised verb whose target does not resolve is returned as a *slot request*
    (``needs_slot`` + candidates) instead of a plan — the operator picks the device.
    """
    normalized = normalize(text)
    if not normalized:
        return None
    endpoints = [summarize(item) for item in (resolver.list() if resolver else [])]
    attempts: list[tuple[str, str]] = [(normalized, "")]
    room_probe = _ROOM_PREPOSITION.search(normalized)
    if room_probe:
        stripped = normalized[: room_probe.start()].strip()
        if stripped and stripped != normalized:
            attempts.append((stripped, room_probe.group("room").strip()))
    survivors: dict[str, Preclassification] = {}
    rejected: list[Preclassification] = []
    for candidate_text, room_text in attempts:
        for tool in registry.enabled():
            for pattern in tool.patterns:
                try:
                    match = re.match(pattern, candidate_text)
                except re.error:  # a malformed pattern is a manifest bug, not a crash
                    continue
                if match is None:
                    continue
                slots: dict[str, Any] = {"room_text": room_text} if room_text else {}
                _infer_slots(tool, match, candidate_text, slots)
                targets, needs_slot, candidates = _targets_for(tool, slots, endpoints)
                cleaned = _clean_slots(slots)
                for slot in tool.slots:
                    if slot.required and cleaned.get(slot.name) in (None, "", ()):
                        needs_slot = needs_slot or slot.name
                        break
                scope = _scope_kind(str(cleaned.get("device") or ""))
                total = _explicit_total(str(cleaned.get("device") or ""))
                required, present = _slot_score(tool, cleaned, targets)
                rank = (
                    required,
                    present,
                    len(match.group(0)),
                    -RISK_ORDER.get(tool.risk, 0),
                    -len(pattern),
                    tool.name,
                )
                confidence = 0.5 if needs_slot else (
                    1.0 if match.group(0) == candidate_text else 0.9
                )
                if not needs_slot and (len(targets) > 1 or scope):
                    confidence = min(confidence, 0.95)
                finding = Preclassification(
                    tool=tool,
                    slots=cleaned,
                    targets=targets,
                    blast_radius=_blast_radius(
                        targets, read_only=tool.read_only, total=total
                    ),
                    confidence=confidence,
                    needs_slot=needs_slot,
                    candidates=candidates,
                    normalized=normalized,
                    score=rank,
                )
                if needs_slot:
                    rejected.append(finding)
                    continue
                previous = survivors.get(tool.name)
                if previous is None or rank > previous.score:
                    survivors[tool.name] = finding
    if survivors:
        ranked = sorted(survivors.values(), key=lambda item: item.score, reverse=True)
        selected = ranked[0]
        selected.alternatives = tuple(
            {
                "tool": item.tool.name,
                "slots": item.slots,
                "confidence": item.confidence,
                "targets": [target.as_dict() for target in item.targets],
            }
            for item in ranked[1:]
        )
        return selected
    if rejected:
        # One best slot request only; the candidate list is reported, never planned.
        rejected.sort(key=lambda item: (-len(item.normalized), item.tool.name))
        return rejected[0]
    return None


__all__ = [
    "CAPABILITY_KEYS",
    "COLOUR_NAMES",
    "Preclassification",
    "Target",
    "classify",
    "normalize",
    "parse_number",
    "parse_value",
]
