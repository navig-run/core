"""Acceptance — 40 golden utterances route deterministically with ZERO LLM (plan §3.3).

The fixture authored by the router owner (``core/tests/reflex/golden_utterances.json``) is
authoritative when present; the independent fallback below keeps the same assertions when
it is absent. Every row asserts: right tool, the documented slots, the resolved target(s)
and blast kind, and the LLM path provably never called or imported.
"""

from __future__ import annotations

import json
import subprocess
import sys

import _support as S
import pytest

pytestmark = pytest.mark.integration

GOLDEN_FIXTURE = S.CORE_ROOT / "tests" / "reflex" / "golden_utterances.json"

#: Independent fallback (utterance, tool, slots, targets, blast) — used only without the fixture.
INDEPENDENT_GOLDEN: list[dict] = [
    {"text": "turn off the desk lamp", "tool": "home.off", "slots": {"device": "desk lamp"},
     "targets": ["govee:desk-lamp"], "blast": "single"},
    {"text": "turn on the desk lamp", "tool": "home.on", "slots": {"device": "desk lamp"},
     "targets": ["govee:desk-lamp"], "blast": "single"},
    {"text": "toggle the kitchen light", "tool": "home.toggle", "slots": {"device": "kitchen light"},
     "targets": ["ha:kitchen-light"], "blast": "single"},
    {"text": "set the desk lamp brightness to 60", "tool": "home.set",
     "slots": {"device": "desk lamp", "value": "60"}, "targets": ["govee:desk-lamp"], "blast": "single"},
    {"text": "set the desk lamp color to blue", "tool": "home.set",
     "slots": {"device": "desk lamp", "value": "blue"}, "targets": ["govee:desk-lamp"], "blast": "single"},
    {"text": "what is the status of the desk lamp", "tool": "home.status",
     "slots": {"device": "desk lamp"}, "targets": ["govee:desk-lamp"], "blast": "single"},
    {"text": "turn off everything", "tool": "home.off", "slots": {"device": "everything"},
     "targets": ["govee:desk-lamp", "ha:kitchen-light", "ha:living-room-ac"], "blast": "batch"},
    {"text": "включи лампу", "tool": "home.on", "slots": {"device": "лампу"}, "targets": [], "blast": "single"},
    {"text": "выключи свет на кухне", "tool": "home.off", "slots": {"device": "свет"},
     "targets": ["ha:kitchen-light"], "blast": "single"},
    {"text": "поставь яркость 40", "tool": "home.set", "slots": {"value": "40"}, "targets": [], "blast": "single"},
    {"text": "turn on the tv", "tool": "tv.power", "slots": {}, "targets": [], "blast": "single"},
    {"text": "turn the tv volume up", "tool": "tv.volume", "slots": {}, "targets": [], "blast": "single"},
    {"text": "play something on the tv", "tool": "tv.play", "slots": {}, "targets": [], "blast": "single"},
    {"text": "assistant status", "tool": "assistant.status", "slots": {}, "targets": [], "blast": "single"},
    {"text": "list campaigns", "tool": "campaigns.status", "slots": {}, "targets": [], "blast": "single"},
]


def _load_rows() -> list[dict]:
    if not GOLDEN_FIXTURE.exists():
        return [dict(row) for row in INDEPENDENT_GOLDEN]
    try:
        data = json.loads(GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        pytest.fail(f"golden fixture is not valid JSON ({GOLDEN_FIXTURE}): {exc}", pytrace=False)
    raw = data.get("utterances", data) if isinstance(data, dict) else data
    if not isinstance(raw, list) or not raw:
        pytest.fail(f"golden fixture has no utterance list: {GOLDEN_FIXTURE}", pytrace=False)

    rows: list[dict] = []
    for item in raw:
        if not isinstance(item, dict):
            pytest.fail(f"golden fixture row is not an object: {item!r}", pytrace=False)
        text = item.get("text") or item.get("utterance") or item.get("query")
        tool = item.get("tool") or item.get("expected_tool")
        if not text or not tool:
            pytest.fail(f"golden fixture row lacks text/tool: {item!r}", pytrace=False)
        slots = item.get("slots") or item.get("expected_slots") or {}
        targets = item.get("targets") or item.get("expected_targets") or []
        if isinstance(targets, str):
            targets = [targets]
        rows.append(
            {
                "text": str(text),
                "tool": str(tool),
                "slots": dict(slots) if isinstance(slots, dict) else {},
                "targets": [str(target) for target in targets],
                "blast": str(item.get("blast") or item.get("blast_radius") or "single"),
            }
        )
    return rows


def _try_json(text: str) -> dict | None:
    """Tolerant JSON extraction for the per-utterance loop (failures are collected)."""
    start = text.find("{")
    if start < 0:
        return None
    try:
        payload, _end = json.JSONDecoder().raw_decode(text[start:])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _slot_present(plan: dict, name: str, expected, text: str, targets_present: bool) -> bool:
    """A slot is present as a slot entry, or proven by the resolved target/value it produces."""
    slots = plan.get("slots")
    if isinstance(slots, dict) and name in slots:
        return True
    if isinstance(slots, (list, tuple)):
        for item in slots:
            if isinstance(item, dict) and str(item.get("name")) == name:
                return True
            if isinstance(item, str) and item == name:
                return True
    if name == "device" and targets_present:
        return True
    if name == "on":
        # tv.power binds --on / no --on; the argv is the resolved proof of the slot.
        return ("--on" in text) is bool(expected)
    return str(expected).lower() in text


@pytest.fixture
def llm_guard(monkeypatch):
    """Fail hard if the deterministic path touches any LLM entry point."""
    import navig.commands.ai as ai_mod
    import navig.llm.routing.router as router_mod

    def boom(*_args, **_kwargs):
        raise AssertionError(
            "LLM path called while routing a golden utterance — deterministic preclassify "
            "must not call ask_ai / UnifiedRouter.route (plan §3.3)"
        )

    monkeypatch.setattr(ai_mod, "ask_ai", boom)
    monkeypatch.setattr(router_mod.UnifiedRouter, "route", boom)


def test_golden_table_covers_every_documented_tool():
    rows = _load_rows()
    assert len(rows) >= 40, f"the golden suite needs >=40 utterances, got {len(rows)}"
    covered = {row["tool"] for row in rows}
    assert S.EXPECTED_TOOLS <= covered, (
        f"the golden table must cover every shipped tool; missing: {sorted(S.EXPECTED_TOOLS - covered)}"
    )


def test_golden_utterances_route_tool_and_slots_with_zero_llm(fake_home, capsys, llm_guard):
    failures: list[str] = []

    for row in _load_rows():
        text = row["text"]
        exit_code, stdout, stderr = S.invoke_cli(["ask", text, "--plan", "--json"], capsys)
        output = S.combined(stdout, stderr)
        if exit_code != 0:
            failures.append(f"{text!r}: exit {exit_code}; {output.strip()[:300]}")
            continue
        envelope = _try_json(output)
        if envelope is None:
            failures.append(f"{text!r}: no JSON Answer envelope; {output.strip()[:300]}")
            continue
        plan = envelope.get("plan") if isinstance(envelope, dict) else None
        if not isinstance(plan, dict) or not plan:
            failures.append(f"{text!r}: no plan in the Answer envelope")
            continue
        if plan.get("tool") != row["tool"]:
            failures.append(f"{text!r}: tool {plan.get('tool')!r} != expected {row['tool']!r}")
            continue

        plan_text = S.plan_text(plan)
        targets = row["targets"]
        targets_present = all(target.lower() in plan_text for target in targets) if targets else True
        if not targets_present:
            failures.append(f"{text!r}: resolved targets {targets} missing from the plan")
            continue

        needed = [name for name in row["slots"] if not _slot_present(
            plan, name, row["slots"][name], plan_text, targets_present)]
        if needed:
            failures.append(f"{text!r}: slots {needed} not reported; plan={plan_text[:400]}")
            continue

        blast = (plan.get("blast_radius") or {})
        blind = plan.get("read_only") is True or plan.get("destructive") is False and blast.get("kind") == "read"
        if row["blast"] == "batch":
            if blast.get("kind") != "batch":
                failures.append(f"{text!r}: must be a batch plan, got blast={blast!r}")
                continue
            if len(targets) > 1 and int(blast.get("count", 0)) < len(targets):
                failures.append(
                    f"{text!r}: batch count {blast.get('count')} < {len(targets)} fixture targets"
                )
                continue
        elif row["blast"] == "read":
            if not (plan.get("read_only") is True or blast.get("kind") == "read"):
                failures.append(f"{text!r}: read-only plan expected, got blast={blast!r}")
                continue
        elif blind:
            failures.append(f"{text!r}: a mutating tool must not be marked read-only: {plan}")
            continue
        elif blast.get("kind") not in (None, "single"):
            failures.append(f"{text!r}: single-target blast expected, got kind={blast.get('kind')!r}")
            continue

        if plan.get("profile") != "fast":
            failures.append(f"{text!r}: profile {plan.get('profile')!r} (default must be fast)")
        S.token_of(plan)

    assert not failures, "golden-utterance failures:\n" + "\n".join(failures)
    assert fake_home.mutations == [], (
        f"--plan on golden utterances must never mutate, got {fake_home.mutations!r}"
    )


def test_preclassify_import_does_not_pull_the_agent_or_llm_modules():
    S.require_path("core/navig/reflex/preclassify.py")
    code = (
        "import json, sys\n"
        "import navig.reflex.preclassify\n"
        "print(json.dumps({\n"
        "  'agent': 'navig.agent' in sys.modules,\n"
        "  'llm': 'navig.llm.routing.router' in sys.modules,\n"
        "  'ai': 'navig.commands.ai' in sys.modules,\n"
        "}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(S.CORE_ROOT),
        capture_output=True,
        encoding="utf-8",
        timeout=120,
    )
    assert completed.returncode == 0, (
        f"importing navig.reflex.preclassify failed: {completed.stdout[-800:]}\n{completed.stderr[-1200:]}"
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    assert payload == {"agent": False, "llm": False, "ai": False}, (
        "the deterministic preclassify path must not import the agent/LLM machinery "
        f"(plan §3.3): {payload}"
    )
