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

⚠ THE INSTALLED APP CAN ALSO BE STALE — the second half of the same environment fact, and
the one that lands with EVERY branch that adds a verb: the editable installs point at the
MAIN checkout, so `navig pipeline cast` on a feature branch resolves the group (installed,
older) and then misses the new verb, and a new flag is "rejected" by the older click
command. So the committed `core/generated/commands.json` — the tree's own manifest, kept
fresh by the manifest-freshness gate — decides whenever the installed app disagrees with
it. Real advice a missing from BOTH still fails: `blackbox unseal` and `generate
--modality` are not in the manifest either.
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
                # `--` is the end-of-options separator (`navig pipeline cast -o x -- <cmd>`),
                # not a flag a command could reject.
                if token != "--":
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


_MANIFEST = None


def _manifest() -> dict:
    """path -> entry for the committed manifest (read once, tree-fresh by its own gate)."""
    global _MANIFEST
    if _MANIFEST is None:
        import json

        data = json.loads((_repo_root() / "core" / "generated" / "commands.json").read_text(encoding="utf-8"))
        _MANIFEST = {c["path"]: c for c in data["commands"]}
    return _MANIFEST


def _manifest_entry(words: list[str]):
    """Deepest manifest entry under `navig <words>` — the tree's verdict on this advice."""
    for n in range(len(words), 0, -1):
        entry = _manifest().get("navig " + " ".join(words[:n]))
        if entry is not None:
            return entry
    return None


def _manifest_flags(entry) -> set:
    return {f for opt in entry.get("options") or [] for f in opt.get("flags") or []}


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
        entry = _manifest_entry(words)
        if command is None:
            if entry is not None:
                # installed app is older than this tree — the manifest is tree-fresh
                rejected = [f for f in flags if f not in _manifest_flags(entry)]
                if rejected:
                    broken.append(f"{path.name}:{lineno} `navig {raw}` — rejects {rejected}")
                continue
            broken.append(f"{path.name}:{lineno} `navig {raw}` — no verb `{tail[0]}`")
            continue
        accepted: set[str] = set()
        for param in command.get_params(click.Context(command)):
            accepted.update(getattr(param, "opts", []) or [])
        if entry is not None:
            accepted |= _manifest_flags(entry)  # a flag this branch just added
        rejected = [f for f in flags if f not in accepted]
        if rejected:
            broken.append(f"{path.name}:{lineno} `navig {raw}` — rejects {rejected}")
    assert not broken, "plugin advice names commands that do not exist:\n  " + "\n  ".join(
        broken
    )
