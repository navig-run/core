"""Commands taught to the MODEL must exist, or the agent emits actions that fail.

`Brain.DEFAULT_SYSTEM_PROMPT` is reachable from `navig agent` (commands/agent.py →
run_agent → AgentRunner → Brain), and it lists the CLI the model should drive. It taught
`navig workflow list` / `navig workflow run` five times — a group that has never been
registered; the real one is `navig flow`. Every occurrence was an instruction the model
would follow into a usage error, and nothing could catch it: the text is a string
constant, so ruff sees a valid literal and `scripts/check_module_attrs.py` resolves
attribute access, not command text.

A wrong command in a system prompt is worse than a wrong command in a help string. A
human reading help gets an error and adapts; the model has been *told* this is the way,
and will retry it.

Flags are checked too, not just the command path — `flow run --var key=value` is only
correct advice if `--var` is a real option (it is).
"""

from __future__ import annotations

import re

import click
import pytest
from typer.main import get_command

# Trailing words that are placeholders or prose, not subcommands.
_NOT_A_VERB = re.compile(r"^(?:<|\"|\[)")


@pytest.fixture(scope="module")
def root():
    from navig.cli import app
    from navig.cli.registration import _register_external_commands

    _register_external_commands(register_all=True, target_app=app)
    return get_command(app)


def _prompt() -> str:
    from navig.agent.brain import Brain

    return Brain.DEFAULT_SYSTEM_PROMPT


def _referenced() -> list[tuple[str, list[str], list[str]]]:
    """Every `navig …` reference in the prompt, as (raw, command words, flags)."""
    out = []
    for match in re.finditer(r"`navig ([^`]+)`", _prompt()):
        raw = match.group(1)
        words: list[str] = []
        flags: list[str] = []
        for token in raw.split():
            if token.startswith("--"):
                flags.append(token.split("=", 1)[0])
            elif _NOT_A_VERB.match(token) or not re.fullmatch(r"[a-z][a-z0-9-]*", token):
                break  # an argument — everything after it is not a subcommand
            else:
                words.append(token)
        if words:
            out.append((raw, words, flags))
    return out


def _resolve(root, words: list[str]):
    cur = root
    for i, word in enumerate(words):
        if not isinstance(cur, click.Group):
            return None, words[i:]
        nxt = cur.get_command(click.Context(cur), word)
        if nxt is None:
            return None, words[i:]
        cur = nxt
    return cur, []


def test_the_prompt_actually_names_commands():
    """If the extraction silently finds nothing, every assertion below is vacuous."""
    assert len(_referenced()) >= 8


def test_every_command_taught_to_the_model_exists(root) -> None:
    broken = []
    for raw, words, flags in _referenced():
        command, tail = _resolve(root, words)
        if command is None:
            broken.append(f"`navig {raw}` — no such command (`{tail[0]}`)")
            continue
        accepted: set[str] = set()
        for param in command.get_params(click.Context(command)):
            accepted.update(getattr(param, "opts", []) or [])
        rejected = [f for f in flags if f not in accepted]
        if rejected:
            broken.append(f"`navig {raw}` — command exists but rejects {rejected}")
    assert not broken, (
        "the agent system prompt teaches the model commands that do not exist:\n  "
        + "\n  ".join(broken)
    )


# ── the SHIPPED prompt files, not just the constant ──────────────────────────
#
# `Brain.DEFAULT_SYSTEM_PROMPT` was the first prompt swept, not the only one that
# reaches a model. `navig/builtin/prompts/**` ships 34 markdown prompts that are
# discoverable through `navig prompts` and fed to models, and one of them —
# `evolve/skill_designer.md`, the template that GENERATES SKILL.md files — told the
# model to emit `commands: ["navig skill invoke …"]`. The real verb is `navig skill run`,
# so every skill that generator produced documented an invocation that does not exist.
# A wrong command in a generator template does not stay in the template.
#
# Bare (unbackticked) references count here. In a prompt file the whole document is
# instruction to a model, so "navig foo bar" on its own line is a command reference in a
# way it is not inside arbitrary Python source — which is why the tree-wide sweep of
# core/navig had to require backticks and this one does not.

from pathlib import Path  # noqa: E402

_BARE_REF = re.compile(r"`?navig ([a-z][^`\n]{0,60})`?")


def _prompt_files() -> list[Path]:
    from navig.platform.paths import builtin_store_dir

    root = builtin_store_dir() / "prompts"
    if not root.exists():
        return []
    return sorted(f for f in root.rglob("*") if f.suffix in {".md", ".txt"})


def _refs_in(text: str, pattern: re.Pattern[str]):
    for match in pattern.finditer(text):
        raw = match.group(1)
        words: list[str] = []
        flags: list[str] = []
        for token in raw.split():
            if token.startswith("--"):
                flags.append(token.split("=", 1)[0])
            elif _NOT_A_VERB.match(token) or not re.fullmatch(r"[a-z][a-z0-9-]*", token):
                break
            else:
                words.append(token)
        if words:
            yield raw, words, flags


def test_builtin_prompt_files_are_present():
    """If the builtin store is missing, every assertion below is vacuous — and a wheel
    shipping no prompts is itself the defect `npm run ci:install` exists to catch."""
    assert len(_prompt_files()) >= 20


def test_no_shipped_prompt_teaches_a_command_that_does_not_exist(root) -> None:
    broken = []
    for path in _prompt_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for raw, words, flags in _refs_in(text, _BARE_REF):
            command, tail = _resolve(root, words)
            if command is None:
                broken.append(f"{path.name}: `navig {raw}` — no such command (`{tail[0]}`)")
                continue
            accepted: set[str] = set()
            for param in command.get_params(click.Context(command)):
                accepted.update(getattr(param, "opts", []) or [])
            rejected = [f for f in flags if f not in accepted]
            if rejected:
                broken.append(f"{path.name}: `navig {raw}` — rejects {rejected}")
    assert not broken, "shipped prompts teach commands that do not exist:\n  " + "\n  ".join(
        broken
    )


# ── commands the agent PLANS to execute, not just describes ──────────────────
#
# `conversational_legacy._simple_response` is the no-LLM fallback: when no provider is
# available it hand-builds a plan dict, and `{"action": "command", "params": {"cmd": …}}`
# is dispatched for real (see navig/agent/command_guard.py). It planned
# `navig workflow list` — the same dead group the system prompt named, in a second file,
# so fixing the prompt alone would have left the fallback broken.
#
# Structured data, not prose: these are `"cmd"` values in dict literals, so the AST finds
# them exactly and there is no false-positive risk to trade off.

import ast as _ast  # noqa: E402


def _planned_commands() -> list[tuple[str, int, str]]:
    from navig.agent import brain as _brain

    agent_root = Path(_brain.__file__).parent
    found = []
    for path in sorted(agent_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Dict):
                continue
            for key, value in zip(node.keys, node.values):
                if (
                    isinstance(key, _ast.Constant)
                    and key.value == "cmd"
                    and isinstance(value, _ast.Constant)
                    and isinstance(value.value, str)
                    and value.value.startswith("navig ")
                ):
                    found.append((path.name, node.lineno, value.value))
    return found


def test_a_planned_command_is_actually_present():
    """If the AST scan finds none, the assertion below is vacuous."""
    assert _planned_commands(), "no literal `cmd` plans found — has the shape moved?"


def test_every_planned_command_exists(root) -> None:
    broken = []
    for name, lineno, cmd in _planned_commands():
        words = [t for t in cmd.split()[1:] if not t.startswith("-")]
        command, tail = _resolve(root, words)
        if command is None:
            broken.append(f"{name}:{lineno} plans `{cmd}` — no such command (`{tail[0]}`)")
    assert not broken, "the agent plans commands that do not exist:\n  " + "\n  ".join(broken)
