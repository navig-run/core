"""Deterministic pre-classification: golden routing, values, ambiguity, fail-closed.

The golden table is the acceptance contract: every utterance must resolve to the
named tool with the expected slot values, and the whole table must run with **zero
LLM involvement** — asserted statically (the reflex modules import no model module)
and dynamically (the LLM entry points are patched to raise and are never called).
"""

from __future__ import annotations

import ast
import json
import time
from pathlib import Path

import pytest

from navig.reflex.manifest import default_registry
from navig.reflex.preclassify import classify, normalize, parse_number, parse_value
from navig.reflex.resolve import StaticResolver

REFLEX = Path(__file__).resolve().parents[2] / "navig" / "reflex"
LLM_ROOTS = ("navig.ai", "navig.llm", "navig.agent")
GOLDEN_PATH = Path(__file__).with_name("golden_utterances.json")

#: Loaded at import time so ``parametrize`` can name each utterance.
GOLDEN: list[dict] = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))["utterances"]


def _assert_golden(router, entry: dict) -> None:
    finding = router.classify(entry["text"])
    assert finding is not None, f"no route for {entry['text']!r}"
    assert finding.tool.name == entry["tool"], (
        f"{entry['text']!r} routed to {finding.tool.name}, expected {entry['tool']}"
    )
    for name, expected in (entry.get("slots") or {}).items():
        assert finding.slots.get(name) == expected, (
            f"{entry['text']!r}: slot {name!r} = {finding.slots.get(name)!r}, "
            f"expected {expected!r}"
        )
    assert [target.id for target in finding.targets] == entry.get("targets", []), (
        f"{entry['text']!r} targets {[t.id for t in finding.targets]}"
    )
    assert finding.blast_radius.get("kind") == entry.get("blast", "single"), (
        f"{entry['text']!r} blast {finding.blast_radius.get('kind')}"
    )
    assert not finding.needs_slot, f"{entry['text']!r} unexpectedly needs a slot"


def test_golden_table_has_at_least_forty_utterances() -> None:
    assert len(GOLDEN) >= 40
    assert any(not entry["text"].isascii() for entry in GOLDEN), "expected Russian input"
    assert any(entry["text"].isascii() for entry in GOLDEN), "expected English input"


@pytest.mark.parametrize("entry", GOLDEN, ids=[entry["text"] for entry in GOLDEN])
def test_golden_utterance_routes_to_the_right_tool(router, entry: dict) -> None:
    _assert_golden(router, entry)


def test_golden_utterances_run_with_zero_llm_calls(
    monkeypatch, fixture_resolver: StaticResolver
) -> None:
    """Patch the only two LLM entry points to raise; the whole table still routes."""
    calls = {"assistant": 0, "router": 0}

    async def _boom_route(*_args, **_kwargs):
        calls["router"] += 1
        raise AssertionError("UnifiedRouter.route was called from the deterministic path")

    def _boom_ask(*_args, **_kwargs):
        calls["assistant"] += 1
        raise AssertionError("AIAssistant.ask was called from the deterministic path")

    from navig.ai import AIAssistant
    from navig.llm.routing.router import UnifiedRouter

    monkeypatch.setattr(AIAssistant, "ask", _boom_ask)
    monkeypatch.setattr(UnifiedRouter, "route", _boom_route)

    registry = default_registry()
    for entry in GOLDEN:
        finding = classify(entry["text"], registry, fixture_resolver)
        assert finding is not None
        assert finding.tool.name == entry["tool"]
    assert calls == {"assistant": 0, "router": 0}


def test_reflex_modules_import_no_llm_module() -> None:
    """Static proof: no file under core/navig/reflex imports navig.ai/llm/agent."""
    offenders: list[str] = []
    for path in sorted(REFLEX.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(name == root or name.startswith(root + ".") for root in LLM_ROOTS):
                    offenders.append(f"{path.name}: {name}")
    assert not offenders, "reflex imported an LLM module: " + ", ".join(offenders)


# ── normalisation + typed values ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Turn  OFF   the lamp!", "turn off the lamp"),
        ("выключи ЛАМПУ, пожалуйста", "выключи лампу"),
        ("set the living-room light", "set the living room light"),
        ("поставь яркость", "поставь яркость"),
        ("please turn on the lamp", "turn on the lamp"),
    ],
)
def test_normalize_folds_case_punctuation_and_politeness(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("40", 40),
        ("40%", 40),
        ("сорок", 40),
        ("двадцать пять", 25),
        ("twenty five", 25),
        ("пятьдесят", 50),
        ("half", 50),
        ("max", 100),
        ("2,5", 2.5),
    ],
)
def test_parse_number_understands_digits_and_words(raw: str, expected: float) -> None:
    assert parse_number(raw) == expected


@pytest.mark.parametrize(
    ("capability", "raw", "expected"),
    [
        ("brightness", "40", 40),
        ("brightness", "150", 100),
        ("brightness", "0", 1),
        ("color", "red", "#ff0000"),
        ("color", "красный", "#ff0000"),
        ("color", "#00ff00", "#00ff00"),
        ("color", "00ff00", "#00ff00"),
        ("color_temp", "2700", 2700),
        ("color_temp", "27", 2700),
        ("color_temp", "20000", 12000),
        ("target_temp", "22.5", 22.5),
    ],
)
def test_parse_value_is_typed_for_the_cli(capability: str, raw: str, expected) -> None:
    assert parse_value(capability, raw) == expected


# ── ambiguity, capability contract, scopes ──────────────────────────────────


def test_ambiguous_device_returns_a_slot_request_not_a_plan() -> None:
    """Two lamps, one spoken "lamp": the router asks, it never guesses."""
    endpoints = [
        {
            "id": "govee:desk-lamp",
            "name": "desk lamp",
            "room": "office",
            "type": "devices.types.light",
            "capabilities": [{"type": "devices.capabilities.on_off"}],
        },
        {
            "id": "govee:floor-lamp",
            "name": "floor lamp",
            "room": "office",
            "type": "devices.types.light",
            "capabilities": [{"type": "devices.capabilities.on_off"}],
        },
    ]
    finding = classify("turn off the lamp", default_registry(), StaticResolver(endpoints))
    assert finding is not None
    assert finding.needs_slot == "device"
    assert finding.targets == ()
    labels = {item["label"] for item in finding.candidates}
    assert labels == {"desk lamp", "floor lamp"}


def test_a_capability_the_device_does_not_declare_is_refused(
    fixture_resolver: StaticResolver,
) -> None:
    """Failed contract filter → slot request, never a plan for an unsupported action."""
    finding = classify(
        "set the kitchen light color to blue", default_registry(), fixture_resolver
    )
    assert finding is not None
    assert finding.needs_slot == "capability"
    assert finding.targets == ()
    assert finding.candidates[0]["id"] == "ha:kitchen-light"


def test_unknown_utterance_returns_none(fixture_resolver: StaticResolver) -> None:
    registry = default_registry()
    for text in ("restart nginx on prod", "hello from reflex", "summarise this log"):
        assert classify(text, registry, fixture_resolver) is None


def test_explicit_total_is_a_batch_even_with_one_target() -> None:
    endpoints = [
        {
            "id": "govee:desk-lamp",
            "name": "desk lamp",
            "room": "office",
            "type": "devices.types.light",
            "capabilities": [{"type": "devices.capabilities.on_off"}],
        }
    ]
    finding = classify("turn off everything", default_registry(), StaticResolver(endpoints))
    assert finding is not None
    assert finding.blast_radius["kind"] == "batch"
    assert finding.destructive is True


def test_read_only_scope_is_not_destructive(fixture_resolver: StaticResolver) -> None:
    finding = classify("home status", default_registry(), fixture_resolver)
    assert finding is not None
    assert finding.blast_radius["kind"] == "read"
    assert finding.destructive is False


def test_a_named_device_that_does_not_match_is_never_guessed_onto_the_only_endpoint(
    fixture_resolver: StaticResolver,
) -> None:
    """Fail closed: "the aquarium" must not switch the desk lamp just because it matched one."""
    finding = classify("turn off the aquarium", default_registry(), fixture_resolver)
    assert finding is not None
    assert finding.needs_slot == "device"
    assert finding.targets == ()
    assert {item["label"] for item in finding.candidates} == {
        "Desk lamp",
        "Living-room AC",
        "Kitchen light",
        "Bedroom sensor",
        "Loft humidifier",
    }


def test_russian_device_nouns_resolve_against_an_english_named_inventory(
    fixture_resolver: StaticResolver,
) -> None:
    """The public-path regression: RU lexicon → English ``devices.yaml`` names.

    ``лампу`` must reach ``Desk lamp`` and not ``Kitchen light`` (lamp ≠ light); the room
    ``на кухне`` must reach ``room: kitchen``; ``кондиционер`` must reach ``Living-room AC``.
    """
    lamp = classify("включи лампу", default_registry(), fixture_resolver)
    assert lamp is not None and not lamp.needs_slot
    assert [target.id for target in lamp.targets] == ["govee:desk-lamp"]

    kitchen = classify("выключи свет на кухне", default_registry(), fixture_resolver)
    assert kitchen is not None and not kitchen.needs_slot
    assert [target.id for target in kitchen.targets] == ["ha:kitchen-light"]

    conditioner = classify("погаси кондиционер", default_registry(), fixture_resolver)
    assert conditioner is not None and not conditioner.needs_slot
    assert [target.id for target in conditioner.targets] == ["ha:living-room-ac"]

    office = classify("зажги свет в кабинете", default_registry(), fixture_resolver)
    assert office is not None and not office.needs_slot
    assert [target.id for target in office.targets] == ["govee:desk-lamp"]


def test_a_room_scope_uses_room_endpoints_only(fixture_resolver: StaticResolver) -> None:
    finding = classify("выключи свет на кухне", default_registry(), fixture_resolver)
    assert finding is not None
    assert finding.slots["room"] == "кухне"
    assert [target.id for target in finding.targets] == ["ha:kitchen-light"]


def test_ranking_keeps_alternatives_for_reporting_only(
    fixture_resolver: StaticResolver,
) -> None:
    """A second matching tool is reported as an alternative, never executed."""
    finding = classify("switch the desk lamp", default_registry(), fixture_resolver)
    assert finding is not None
    assert finding.tool.name == "home.toggle"
    for alternative in finding.alternatives:
        assert alternative["tool"] != finding.tool.name


def test_classification_is_fast(fixture_resolver: StaticResolver) -> None:
    """The deterministic path must stay interactive (the CLI calls it on every ask)."""
    registry = default_registry()
    started = time.perf_counter()
    for entry in GOLDEN:
        classify(entry["text"], registry, fixture_resolver)
    elapsed = time.perf_counter() - started
    assert elapsed < 2.0, f"golden table took {elapsed:.2f}s (target < 2s for {len(GOLDEN)})"
