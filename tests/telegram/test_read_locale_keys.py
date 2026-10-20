"""Every word the `/read` surfaces can say exists in English, Russian and French — and the
`/read` document gate is wired into `_process_update`.

Split out of ``test_read_actions`` on purpose: these tests read the locale JSON files BY PATH
(an asset, not a Python module), so editing a locale selects them in no tier unless they are
wired into the fast gate (``sourceGuardArgs``) — and a 70-test behaviour file is a poor thing
to run on every change just for three file reads. This module is the small, fast, wired part.

Three things can go wrong, one test each:

* a key used in code is missing from a locale — the owner's Russian bot answers in English, or
  with the raw key;
* the English default in code drifts from ``en.json`` — two different texts for one key, which
  one a user sees depends on whether the locale file loaded;
* a ``{placeholder}`` is renamed in one language — ``str.format`` then raises at send time, in
  the one language nobody tested.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
SOURCE = CORE / "telegram" / "read_actions.py"
LANGS = ("en", "ru", "fr")


def _locale(lang: str) -> dict[str, str]:
    return json.loads((CORE / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


def _t_calls() -> list[ast.Call]:
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_t"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]


def _used_keys() -> set[str]:
    return {call.args[0].value for call in _t_calls()}


def test_the_scan_found_the_keys_it_should():
    """Anti-vacuity: a scan that finds nothing makes every test below pass by comparing nothing."""
    used = _used_keys()
    assert len(used) >= 20, f"only {len(used)} _t() keys found in read_actions.py — the AST scan is stale"
    assert all(k.startswith("reader.tg.") for k in used)


def test_every_message_key_exists_in_english_russian_and_french():
    used = _used_keys()
    for lang in LANGS:
        missing = sorted(used - _locale(lang).keys())
        assert not missing, f"{lang}.json lacks {missing}"


def test_the_extension_card_keys_exist_in_every_language():
    """The /extensions card is what the owner reads to decide what to switch off."""
    expected = {
        "tgext.reading.label",
        "tgext.reading.description",
        "tgext.reading.about.0",
        "tgext.reading.about.1",
        "tgext.reading.about.2",
    }
    for lang in LANGS:
        missing = sorted(expected - _locale(lang).keys())
        assert not missing, f"{lang}.json lacks {missing}"


def test_the_english_defaults_in_code_match_the_english_locale():
    """The default is what a user sees if the locale file fails to load."""
    en = _locale("en")
    checked = 0
    for call in _t_calls():
        if len(call.args) < 2:
            continue
        try:
            default = ast.literal_eval(call.args[1])
        except (ValueError, SyntaxError):
            continue  # a computed default: cannot be compared statically
        assert en[call.args[0].value] == default, (
            f"en.json drifted from the code default for {call.args[0].value}"
        )
        checked += 1
    assert checked >= 15, f"only {checked} defaults compared — the scan is stale"


def test_placeholders_agree_across_languages():
    data = {lang: _locale(lang) for lang in LANGS}
    for key in sorted(_used_keys()):
        placeholders = {lang: set(re.findall(r"\{(\w+)\}", data[lang][key])) for lang in LANGS}
        assert placeholders["en"] == placeholders["ru"] == placeholders["fr"], f"{key}: {placeholders}"


def test_the_document_gate_is_in_process_update_and_runs_before_the_courrier_gate():
    """A unit-tested module nothing calls is the defect this repo keeps finding. Driving the
    thousand-line `_process_update` end to end is not practical, so pin the wiring itself."""
    tree = ast.parse((CORE / "gateway" / "channels" / "telegram.py").read_text(encoding="utf-8"))
    func = next(
        n for n in ast.walk(tree)
        if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == "_process_update"
    )
    calls: dict[str, int] = {}
    for node in ast.walk(func):
        if not isinstance(node, ast.Call):
            continue
        # `read_actions.wants_read(...)` is an attribute call; the courrier gate imports
        # `wants_intake` by name and calls it bare — collect both shapes.
        if isinstance(node.func, ast.Attribute):
            calls.setdefault(node.func.attr, node.lineno)
        elif isinstance(node.func, ast.Name):
            calls.setdefault(node.func.id, node.lineno)
    assert "wants_read" in calls and "handle_document" in calls, "the /read gate is not in _process_update"
    assert calls["wants_read"] < calls["wants_intake"], "/read must be decided BEFORE the courrier gate"

    gate = next(
        n for n in ast.walk(func)
        if isinstance(n, ast.If) and any(
            isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "wants_read" for c in ast.walk(n.test)
        )
    )
    assert isinstance(gate.body[-1], ast.Return), "after handling the file the update must stop here"
    outer = next(
        n for n in ast.walk(func)
        if isinstance(n, ast.If) and gate in n.body and "is_group" in ast.dump(n.test)
    )
    assert outer, "the gate must be a DM-only gesture"
