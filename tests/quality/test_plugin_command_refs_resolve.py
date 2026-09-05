"""Plugin advice must not name a verb or flag the CLI does not have.

The core guards (`tests/cli/test_doctor_fts_remedy.py`,
`tests/agent/test_system_prompt_commands_resolve.py`,
`tests/bot/test_start_menu_commands_resolve.py`) are all scoped to `core/navig`, and this
repo's most-repeated CI defect is a guard whose scope was drawn at an ADDRESS rather than
at the shape that makes something the surface. Plugins print advice to the same operator.
Sweeping them found four real defects, one of them runtime output on a blocked path:
`navig blackbox record` on a sealed blackbox said "run `navig blackbox unseal` first"
(the real form is `seal --unseal`), and three docstrings/README named
`navig generate --modality …` when `--modality` lives on the `gen` subcommand.

⚠ THE RULE IS DELIBERATELY NARROWER THAN THE CORE ONES: a reference is only checked when
its GROUP already resolves. A plugin command can be perfectly declared in source and still
be absent from this interpreter — measured twice while writing this:

  * `navig antivirus` — navig-antivirus is not installed here at all, and
  * `navig design`   — navig-text DOES declare
    `design = "navig_text.commands.design:design_app"` in its pyproject and the target
    imports fine with verbs `edit`/`check`, but the editable install's dist-info predates
    that entry point, so the command is not registered until someone reinstalls.

Flagging either would be reporting an environment fact as a code defect. Dropping the
"unknown group" class costs nothing here: all four real defects were wrong VERBS or wrong
FLAGS under a group that does resolve, which is exactly the environment-independent half.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import click
import pytest
from typer.main import get_command

_NOT_A_VERB = re.compile(r'^(?:<|"|\[|\{|\$)')
_REF = re.compile(r"`navig ([^`\n]+)`")


@pytest.fixture(scope="module")
def root():
    from navig.cli import app
    from navig.cli.registration import _register_external_commands

    _register_external_commands(register_all=True, target_app=app)
    return get_command(app)


def _repo_root() -> Path:
    # core/tests/quality/<this file> → repo root
    return Path(__file__).resolve().parents[3]


def _plugin_sources() -> list[Path]:
    """Every first-party plugin source file, including the closed `private/harbor`.

    `private/harbor` is listed explicitly for the same reason `ci-local.mjs` does it: an
    address assumption of `plugins/navig-*` has already hidden that package from the gate
    three separate times, and its bugs reach paying customers.
    """
    out: list[Path] = []
    for root_dir in (_repo_root() / "plugins", _repo_root() / "private" / "harbor"):
        if not root_dir.exists():
            continue
        for path in sorted(root_dir.rglob("*.py")):
            parts = set(path.parts)
            if "__pycache__" in parts or "tests" in parts:
                continue
            out.append(path)
    return out


def _refs(text: str):
    """(raw, command words, flags) for each `navig …` reference.

    Two parsing rules that each removed a batch of false positives when measured:
      * everything after the first flag that is not itself a flag is that flag's VALUE —
        `navig tt login --from-browser firefox` does not imply a `firefox` verb (4 hits);
      * resolution stops at a leaf Command, so trailing words are ARGUMENTS —
        `navig social connect linkedin` is `connect <network>` (16 hits).
    """
    for match in _REF.finditer(text):
        raw = match.group(1)
        words: list[str] = []
        flags: list[str] = []
        seen_flag = False
        for token in raw.split():
            if token.startswith("-"):
                seen_flag = True
                flags.append(token.split("=", 1)[0])
            elif seen_flag:
                continue
            elif _NOT_A_VERB.match(token) or not re.fullmatch(r"[a-z][a-z0-9-]*", token):
                break
            else:
                words.append(token)
        if words:
            yield raw, words, flags


def _resolve(root, words: list[str]):
    cur = root
    for i, word in enumerate(words):
        if not isinstance(cur, click.Group):
            return cur, []  # a leaf command — the rest are arguments
        nxt = cur.get_command(click.Context(cur), word)
        if nxt is None:
            return None, words[i:]
        cur = nxt
    return cur, []


def _referenced():
    for path in _plugin_sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for raw, words, flags in _refs(node.value):
                    yield path, node.lineno, raw, words, flags


def test_plugins_are_present_and_reference_commands():
    """A sweep that reads nothing looks exactly like a clean sweep."""
    assert len(_plugin_sources()) >= 100
    assert list(_referenced()), "no `navig …` references found in plugin sources"


def test_plugin_advice_uses_real_verbs_and_flags(root) -> None:
    broken: list[str] = []
    for path, lineno, raw, words, flags in _referenced():
        if _resolve(root, words[:1])[0] is None:
            continue  # group not registered in THIS interpreter — see the module docstring
        command, tail = _resolve(root, words)
        if command is None:
            broken.append(f"{path.name}:{lineno} `navig {raw}` — no verb `{tail[0]}`")
            continue
        accepted: set[str] = set()
        for param in command.get_params(click.Context(command)):
            accepted.update(getattr(param, "opts", []) or [])
        rejected = [f for f in flags if f not in accepted]
        if rejected:
            broken.append(f"{path.name}:{lineno} `navig {raw}` — rejects {rejected}")
    assert not broken, "plugin advice names commands that do not exist:\n  " + "\n  ".join(
        broken
    )
