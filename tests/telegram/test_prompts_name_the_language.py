"""A prompt that says nothing about language does not get "auto" — it gets ENGLISH.

`/start` proved it on the operator's own install (`user.language: Russian`). The
handler sends an LLM greeting and then, after a long absence, an away summary.
`away_summary` ends its prompt with *"Write the summary in Russian."*; the
greeting prompt, built inline in the same handler, named no language at all. So
one command produced two messages, back to back, in two different languages —
and the English one was the greeting, the first thing the operator reads.

There are **two correct answers** and this asserts every prompt gives one of them:

* **PIN** the operator's language — `language_directive()`, for output that is
  *ours*: a greeting, a recap of your own conversation, a rewritten command
  result. Those should follow `user.language`.
* **MIRROR** the input — "in the same language as the input", for a transform
  applied to *someone else's* text. `ai_actions` is right to do this: summarising
  a French message should return French, not Russian, and `translate` takes its
  target as an argument.

Silence is the defect, so there is no exemption list — a module choosing either
correct behaviour passes, and only one that mentions neither fails.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from navig.core import i18n, language
from navig.gateway.channels import away_summary, telegram
from navig.gateway.channels.telegram_commands import TelegramCommandsMixin

NAVIG = Path(language.__file__).parent.parent
PROMPT_ROOTS = (NAVIG / "gateway" / "channels", NAVIG / "telegram")


@pytest.fixture
def pinned(monkeypatch):
    """`user.language = Russian`, the operator's real setting."""
    monkeypatch.setattr(language, "resolve_language", lambda override="": "Russian")
    return "Russian"


@pytest.fixture
def auto(monkeypatch):
    """Nothing pinned — every prompt must then mirror its input."""
    monkeypatch.setattr(language, "resolve_language", lambda override="": None)


def directives() -> dict[str, str]:
    """The language sentence each operator-facing prompt ends with."""
    return {
        "away summary": away_summary._recap_system_prompt(),
        # ⚠ The ASSEMBLED prompt, not the helper that supplies one sentence of it.
        # The first version of this test read the helper and passed with the
        # directive deleted from the prompt — the same defect one level up.
        "/start greeting": TelegramCommandsMixin._start_system_prompt(
            "morning", "The user's handle is @op. ", "There is no prior context."
        ),
        "command output": telegram._command_output_language_directive(),
    }


def test_every_prompt_names_the_pinned_language(pinned) -> None:
    """The bug, stated directly: all three must agree, not two of three."""
    missing = [name for name, text in directives().items() if pinned not in text]
    assert not missing, (
        f"these prompts do not name the operator's language ({pinned}), so the "
        f"model will answer in English: {missing}"
    )


def test_every_prompt_mirrors_the_input_when_nothing_is_pinned(auto) -> None:
    """Auto must mean "follow the content", never a silent English default."""
    missing = [name for name, text in directives().items() if "same language" not in text]
    assert not missing, f"these prompts fall silent when no language is pinned: {missing}"


def test_the_away_summary_wording_did_not_change(pinned) -> None:
    """The shared helper replaced this module's own copy of the logic. It is the
    one site that already worked, so its output is the proof the refactor changed
    nothing — byte-for-byte, both branches."""
    assert away_summary._recap_system_prompt().endswith(
        "Write the summary in Russian."
    )


def test_the_away_summary_mirror_wording_did_not_change(auto) -> None:
    assert away_summary._recap_system_prompt().endswith(
        "Write the summary in the same language the conversation is in."
    )


@pytest.mark.parametrize("code", ["en", "ru", "fr"])
def test_the_start_fallback_is_localized(code: str, monkeypatch) -> None:
    """The greeting has a fixed fallback for when the LLM is unavailable. It was
    an English literal, so the one path that runs when everything else failed was
    also the one path guaranteed to be in the wrong language.

    ⚠ `monkeypatch`, never a bare assignment. `i18n.current_language` is a MODULE
    GLOBAL: setting it directly pins the language for every later test in the same
    xdist worker. The first version of this test did exactly that and left French
    pinned, which reddened two `test_reply_actions` cases asserting English — a
    failure that looks like flake and is nothing of the kind.
    """
    monkeypatch.setattr(i18n, "current_language", lambda: code)
    i18n.shared().reset()
    try:
        text = i18n.t("start.fallback")
        assert text != "start.fallback", f"{code}.json is missing start.fallback"
        assert text.strip(), f"{code}.json has an empty start.fallback"
        if code == "ru":
            assert re.search(r"[А-Яа-я]", text), (
                f"the Russian fallback is not Russian: {text!r}"
            )
    finally:
        i18n.shared().reset()


# ── the guard ────────────────────────────────────────────────────────────────


def prompt_modules() -> list[Path]:
    """Modules that BUILD a system prompt and CALL an LLM, discovered by shape.

    Not a hand-written list: a new operator-facing prompt is covered the day it
    is written, which is the only way this class stops recurring.
    """
    found = []
    for root in PROMPT_ROOTS:
        for path in sorted(root.rglob("*.py")):
            try:
                src = path.read_text(encoding="utf-8")
                ast.parse(src)
            except (SyntaxError, UnicodeDecodeError):
                continue
            builds = '"role": "system"' in src or "system_prompt" in src
            calls = any(t in src for t in ("run_llm", "complete(", "on_message("))
            if builds and calls:
                found.append(path)
    return found


def _handles_language(src: str) -> bool:
    return "language_directive" in src or "same language" in src


def test_no_prompt_module_is_silent_about_language() -> None:
    modules = prompt_modules()
    silent = [
        p.name for p in modules if not _handles_language(p.read_text(encoding="utf-8"))
    ]
    assert not silent, (
        "these modules build an LLM prompt and never say what language to answer "
        "in — which means English, whatever the operator set. Either call "
        "`navig.core.language.language_directive()` to follow `user.language`, or "
        "say 'in the same language as the input' if the op transforms someone "
        f"else's text: {silent}"
    )


def test_the_guard_actually_found_the_modules() -> None:
    """Anti-vacuity, counting a presence. Both assertions above pass over an empty
    list, and a renamed prompt variable or a moved directory produces exactly
    that. Measured: 4 modules — three that pin, one (`ai_actions`) that mirrors."""
    modules = prompt_modules()
    names = {p.name for p in modules}
    assert len(modules) >= 4, f"only found {len(modules)} prompt modules"
    for expected in ("away_summary.py", "telegram_commands.py", "ai_actions.py"):
        assert expected in names, f"{expected} is no longer detected as a prompt module"


def test_the_guard_would_catch_a_silent_prompt(tmp_path: Path) -> None:
    """Teeth, on a fixture: a module that builds a prompt and says nothing about
    language must be judged silent, while either correct answer passes."""
    silent = 'system_prompt = "You are NAVIG."\nrun_llm([{"role": "system"}])\n'
    pins = silent + "from navig.core.language import language_directive\n"
    mirrors = silent + 'EXTRA = "in the same language as the input"\n'
    assert not _handles_language(silent), "the rule would not have caught the bug"
    assert _handles_language(pins), "pinning must be accepted"
    assert _handles_language(mirrors), "mirroring must be accepted"
