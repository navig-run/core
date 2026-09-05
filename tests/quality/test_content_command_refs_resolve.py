"""Shipped content must not name a CLI command that does not exist.

Blocks and Skills are the two content types that put commands in front of an executor:

* a **Block** `kind: command` step is RUN — `argv` goes to the CLI. `safe-deployment`
  shipped `argv: [navig, upload, …]`; `navig upload` has never existed. That step sits
  AFTER a `safety: destructive` backup step, so applying the Block copied a backup onto
  the remote and then died — a half-done deploy from a product whose selling point is a
  verified receipt.
* a **Skill** is instruction an agent follows, so a dead command there becomes a failed
  agent action. Eleven were corrected (`navig db databases` → `db list`,
  `docker stats --no-stream` → no such flag, `run --sudo …` → `run "sudo …"`,
  `tunnel list` → `tunnel show`, `docker system prune` → `run "docker system prune -f"`,
  `navig shell` → `navig run`).

⚠ KNOWN_MISSING is a RECORD, not an excuse. Sweeping the skills found something bigger
than typos: a family of tool-wrapper skills written against a planned
`navig <domain> <tool> <verb>` surface that was never built — `navig sys` alone is
referenced by SEVEN skills (defender, nssm, nvidia, procmon, screenshot, vivetool, perf),
plus `ios`, `net`, `sync`, `telemetry` and three verbs. Rewriting those to some other
command would misrepresent what the skill does, and implementing the surface is feature
work, not a text fix. So they are recorded here, exactly as `KNOWN_MISSING` in
`test_interactive_menu_targets.py` records menu options wired before their implementation
— dropping shipped, user-visible content is an owner call.

The value is the FLOOR: any NEW dead reference under a group that DOES exist fails the
build. Every one of the eleven fixed above was that shape.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest
from typer.main import get_command

# Top-level groups NAVIG does not implement. Each entry is a planned surface, not a typo;
# removing a skill that documents one is a product decision, so the gap is recorded.
KNOWN_MISSING_GROUPS = {
    "sys": "planned Windows-ops surface (defender/nssm/nvidia/procmon/screenshot/vivetool/perf) — 7 skills",
}
# Verbs under a group that DOES exist, but which were never implemented either.
# ⚠ Four of these were first written as missing GROUPS and the staleness check below
# rejected them immediately — `ios`/`net`/`sync`/`telemetry` are all real groups whose
# planned SUBCOMMAND is what is absent. Classify at the level that is actually missing,
# or the entry exempts far more than the gap.
KNOWN_MISSING_VERBS = {
    ("system", "disk"): "disk-space-basic — `navig system` has only info/clean",
    ("system", "storage"): "disk-space-basic — same planned surface",
    ("media", "yt"): "yt-dlp-media-download — planned `navig media yt download`",
    ("tunnel", "add"): "ssh-tunnel — `tunnel run` takes no name/port options",
    ("tunnel", "start"): "ssh-tunnel — same planned surface",
    ("tunnel", "stop"): "ssh-tunnel — same planned surface",
    ("ios", "futurerestore"): "futurerestore-ios — planned wrapper under a real `ios` group",
    ("net", "iperf3"): "iperf3-network-test — planned wrapper",
    ("sync", "pull"): "file-transfer — planned `navig sync pull/push`",
    ("sync", "push"): "file-transfer — same planned surface",
    ("telemetry", "audit"): "telemetry-audit — planned `navig telemetry audit scan/whois`",
    ("cloud", "rclone"): "rclone-cloud-sync — planned wrapper",
    ("dev", "gh"): "gh-cli-github — planned wrapper (the shipped GitHub surface is `navig github`)",
}

_NOT_A_VERB = re.compile(r'^(?:<|"|\[|\{|\$)')
_BACKTICKED = re.compile(r"`navig ([^`\n]+)`")
_FRONTMATTER_ITEM = re.compile(r"^\s*-\s+navig ([^\n]+)$", re.M)


@pytest.fixture(scope="module")
def root():
    from navig.cli import app
    from navig.cli.registration import _register_external_commands

    _register_external_commands(register_all=True, target_app=app)
    return get_command(app)


def _builtin() -> Path:
    from navig.platform.paths import builtin_store_dir

    return builtin_store_dir()


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
            break  # a placeholder/argument — nothing after it is a subcommand
        else:
            words.append(token)
    return words, flags


def _resolve(root, words: list[str]):
    cur = root
    for i, word in enumerate(words):
        if not isinstance(cur, click.Group):
            return cur, []  # leaf command — the rest are arguments
        nxt = cur.get_command(click.Context(cur), word)
        if nxt is None:
            return None, words[i:]
        cur = nxt
    return cur, []


def _known_missing(words: list[str]) -> bool:
    if words[0] in KNOWN_MISSING_GROUPS:
        return True
    return len(words) >= 2 and (words[0], words[1]) in KNOWN_MISSING_VERBS


def _check(root, words: list[str], flags: list[str]) -> str | None:
    command, tail = _resolve(root, words)
    if command is None:
        return f"no such command `{tail[0]}`"
    accepted: set[str] = set()
    for param in command.get_params(click.Context(command)):
        accepted.update(getattr(param, "opts", []) or [])
    rejected = [f for f in flags if f not in accepted]
    return f"rejects {rejected}" if rejected else None


# ── Blocks: these steps are EXECUTED ─────────────────────────────────────────


def _block_command_steps():
    from navig.blocks.loader import discover_blocks

    for block in discover_blocks():
        for step in block.steps:
            if step.kind == "command" and step.argv and step.argv[0] == "navig":
                yield block.id, step.id, step.argv
        verify = getattr(block, "verify", None)
        argv = getattr(verify, "argv", None) if verify else None
        if argv and argv[0] == "navig":
            yield block.id, "verify", argv


def test_blocks_are_discovered():
    assert list(_block_command_steps()), "no Block command steps found — has the shape moved?"


def test_every_block_command_step_resolves(root) -> None:
    """No allowlist here, deliberately: a Block step is executed, so an unrunnable one is
    a broken product, not documentation debt."""
    broken = []
    for block_id, step_id, argv in _block_command_steps():
        words: list[str] = []
        for token in argv[1:]:
            if token.startswith("-") or "{{" in token:
                break
            words.append(token)
        if not words:
            continue
        why = _check(root, words, [])
        if why:
            broken.append(f"{block_id}/{step_id}: `{' '.join(argv[:4])}…` — {why}")
    assert not broken, "Block steps that would fail at apply time:\n  " + "\n  ".join(broken)


# ── Skills: instruction an agent follows ─────────────────────────────────────


def _skill_refs():
    for path in sorted(_builtin().rglob("SKILL.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        raws = [m.group(1) for m in _BACKTICKED.finditer(text)]
        # the `navig-commands:` frontmatter list is not backticked, and is exactly where
        # `tunnel start`/`tunnel stop` hid from the first sweep
        raws += [m.group(1) for m in _FRONTMATTER_ITEM.finditer(text)]
        for raw in raws:
            words, flags = _parse(raw)
            if words:
                yield path.parent.name, raw.strip(), words, flags


def test_skills_are_present():
    assert len(list(_skill_refs())) >= 50, "skill sweep found almost nothing — check the store"


def test_skills_use_real_commands_under_groups_that_exist(root) -> None:
    broken = []
    for skill, raw, words, flags in _skill_refs():
        if _known_missing(words):
            continue
        why = _check(root, words, flags)
        if why:
            broken.append(f"{skill}: `navig {raw}` — {why}")
    assert not broken, (
        "skills instruct the agent to run commands that do not exist:\n  " + "\n  ".join(broken)
    )


def test_known_missing_entries_are_still_missing(root) -> None:
    """A stale entry is worse than none — it silently exempts a command that now exists."""
    stale = []
    for group, reason in KNOWN_MISSING_GROUPS.items():
        if _resolve(root, [group])[0] is not None:
            stale.append(f"group `{group}` now EXISTS — drop it ({reason})")
    for (group, verb), reason in KNOWN_MISSING_VERBS.items():
        if _resolve(root, [group])[0] is None:
            continue  # the whole group is missing; covered by the group entry
        if _resolve(root, [group, verb])[0] is not None:
            stale.append(f"`{group} {verb}` now EXISTS — drop it ({reason})")
    assert not stale, "KNOWN_MISSING is out of date:\n  " + "\n  ".join(stale)
