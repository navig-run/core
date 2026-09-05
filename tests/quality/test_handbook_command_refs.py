"""The operator handbook must not acquire NEW commands that do not exist.

`core/docs/user/HANDBOOK.md` calls itself the "Primary Knowledge Base for AI Assistants",
and the global operating doc points agents at it before they touch live infrastructure.
A command named there that does not exist becomes a failed action against a real server.

Measured on 2026-08-07: **232 distinct command paths in the handbook do not resolve** —
whole families (`navig pack`, `template`, `sql`, `workflow`, `ls`, `cat`, `chmod`,
`chown`, `mkdir`, `write-file`, `monitor`, `security`) plus missing verbs under groups
that do exist (`history`, `finance`, `host`, `space`, `portable`, `origin`, `deck`,
`bridge`, `node`, `system`). The handbook documents an older generation of the CLI: not
only different verbs but different FLAGS (`chmod --recursive`, `cat --head 20`), so a
faithful rewrite is a documentation project with a per-command intent decision, not a
mechanical rename. Guessing 232 times would be worse than the debt.

What WAS fixed, because it was verifiable and actively harmful: the handbook stated
"Legacy compatibility: `navig upload …` **still works**" — it does not, and no dispatch
alias layer exists (`_RESOURCE_ALIASES` in cli/middleware.py is the operation-recorder's
classifier, not a dispatcher). Its decision tree also recommended `navig upload` as the
RECOMMENDED path for config files. Both corrected to `navig file add`, which the handbook
itself calls canonical.

This guard is a RATCHET, not a pass: the recorded set may only shrink. A new dead command
fails the build, and an entry that starts resolving must be deleted — so fixing the
handbook is rewarded and the debt cannot quietly be re-added under a different name.

⚠ FLAG CHECKING USES ``cmd.get_params(ctx)``, NEVER ``cmd.params``. Click appends the help
option at PARSE time, so ``.params`` omits it and every documented ``<cmd> --help`` gets
recorded as rejecting a flag it plainly accepts. Seven entries here were false that way
(``app --help``, ``db --help``, ``db list --help``, ``file --help``, ``flow --help``,
``host --help``, ``host monitor show --help``) — all verified runnable — and the same
mistake was in four sibling guards. A debt list that records working commands teaches
people to distrust the whole list.

⚠ WRITING A NEGATIVE NOTE: do not spell a dead command as ``navig <verb>`` in backticks,
even to say it does not exist. This scan cannot tell "run this" from "this is not a thing",
so ``There is no `navig cat`.`` keeps `cat` in the debt forever and reads, to anyone
skimming for a command, exactly like advice. Quote the VERB alone — ``There is no `cat`
verb.`` — which is also what the reader needs. Nine entries were held open this way by the
first draft of the very notes that documented their removal.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest
from typer.main import get_command

_NOT_A_VERB = re.compile(r'^(?:<|"|\[|\{|\$)')
_BACKTICKED = re.compile(r"`navig ([^`\n]+)`")
_FENCED = re.compile(r"^\s*(?:\$\s*)?navig ([^\n]+)$", re.M)

_DEBT_FILE = Path(__file__).with_name("handbook_known_missing.txt")


@pytest.fixture(scope="module")
def root():
    from navig.cli import app
    from navig.cli.registration import _register_external_commands

    _register_external_commands(register_all=True, target_app=app)
    return get_command(app)


def _handbook() -> Path:
    # core/tests/quality/<this file> → core/
    return Path(__file__).resolve().parents[2] / "docs" / "user" / "HANDBOOK.md"


def _parse(raw: str) -> tuple[list[str], list[str]]:
    words: list[str] = []
    flags: list[str] = []
    seen_flag = False
    for token in raw.split():
        if token.startswith("-"):
            seen_flag = True
            flags.append(token.split("=", 1)[0])
        elif seen_flag:
            continue  # a flag's VALUE, not a subcommand
        elif _NOT_A_VERB.match(token) or not re.fullmatch(r"[a-z][a-z0-9-]*", token):
            break
        else:
            words.append(token)
    return words, flags


def _resolve(root, words: list[str]):
    cur = root
    for i, word in enumerate(words):
        if not isinstance(cur, click.Group):
            return cur, []
        nxt = cur.get_command(click.Context(cur), word)
        if nxt is None:
            return None, words[i:]
        cur = nxt
    return cur, []


def _unresolvable(root) -> set[str]:
    """Command paths in the handbook that the real CLI cannot run."""
    text = _handbook().read_text(encoding="utf-8", errors="replace")
    raws = [m.group(1) for m in _BACKTICKED.finditer(text)]
    raws += [m.group(1) for m in _FENCED.finditer(text)]
    out: set[str] = set()
    for raw in raws:
        words, flags = _parse(raw)
        if not words:
            continue
        command, _tail = _resolve(root, words)
        if command is None:
            out.add(" ".join(words))
            continue
        accepted: set[str] = set()
        for param in command.get_params(click.Context(command)):
            accepted.update(getattr(param, "opts", []) or [])
        rejected = sorted(set(flags) - accepted)
        if rejected:
            out.add(" ".join(words) + " " + " ".join(rejected))
    return out


def _recorded() -> set[str]:
    return {
        line.strip()
        for line in _DEBT_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def test_the_handbook_and_debt_file_are_both_present():
    """A guard over a missing file, or an empty debt list, would pass vacuously."""
    assert _handbook().exists(), "HANDBOOK.md moved — update this guard"
    assert len(_recorded()) > 100, "the recorded debt shrank implausibly — regenerate it"


def test_no_new_unresolvable_command_in_the_handbook(root) -> None:
    new = sorted(_unresolvable(root) - _recorded())
    assert not new, (
        "the handbook gained command(s) the CLI cannot run:\n  "
        + "\n  ".join(new)
        + f"\n\nEither use a real command, or (if it is genuinely planned) add it to "
        f"{_DEBT_FILE.name} with a reason in the commit message."
    )


def test_recorded_entries_that_now_resolve_must_be_removed(root) -> None:
    """The ratchet. Without this the list would drift into an amnesty that never shrinks,
    and a command deleted from the handbook would keep its exemption forever — free cover
    for re-adding it later."""
    still_bad = _unresolvable(root)
    stale = sorted(entry for entry in _recorded() if entry not in still_bad)
    assert not stale, (
        f"{len(stale)} recorded entr(ies) no longer appear as broken — delete them from "
        f"{_DEBT_FILE.name} so the ratchet keeps its teeth:\n  " + "\n  ".join(stale[:20])
    )
