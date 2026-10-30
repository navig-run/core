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
import shlex
import sys
from pathlib import Path

import pytest
from typer.main import get_command

from navig.core.click_compat import click_module, is_group

# The click TYPER runs on, never a bare `import click` — Typer 0.27 vendors click, and a
# separately installed copy is a different class hierarchy, so `_resolve` would stop at the
# root of every app and report every documented flag as rejected.
click = click_module()

# Top-level groups NAVIG does not implement. Each entry is a planned surface, not a typo;
# removing a skill that documents one is a product decision, so the gap is recorded.
KNOWN_MISSING_GROUPS = {
    "sys": "planned Windows-ops surface (defender/nssm/nvidia/procmon/screenshot/vivetool/perf) — 7 skills",
    "disk": "disk-reclaim — the skill is the companion doc to the `navig disk` feature being "
    "built on skill/disk-reclaim; the guard's staleness check below fails the day the "
    "verb lands, so this entry cannot rot",
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

# Verb groups that exist on ONE platform only. `navig-ahk` returns early off Windows
# (`plugins/navig-ahk/navig_ahk/plugin.py:43`), so every `navig ahk …` reference resolves to
# nothing on Linux while the skill remains correct on the machine that can run it. This is a
# platform fact, not a recorded gap: the group DOES ship, so it must not go in KNOWN_MISSING.
PLATFORM_ONLY_VERBS = {"ahk": "win32"}


def _platform_scoped_out(words: list[str]) -> bool:
    """True when the verb group cannot exist on this platform, by design."""
    platform = PLATFORM_ONLY_VERBS.get(words[0])
    return platform is not None and sys.platform != platform


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


def _tokens(raw: str) -> list[str]:
    """Whitespace tokens with a QUOTED argument kept whole, quotes and all.

    `raw.split()` broke `--task "use --foo here"` apart and read `--foo` as one of the
    command's flags. Non-POSIX shlex keeps the quoted span as ONE token that still
    starts with `"`, so `_NOT_A_VERB` treats it exactly as before. An odd quote count
    (a YAML list item's closing `"` captured with its line) drops that trailing quote.
    """
    text = raw.strip()
    if text.count('"') % 2:
        text = text.rstrip('",: ')
    try:
        return shlex.split(text, posix=False)
    except ValueError:
        return text.split()


def _parse(raw: str) -> tuple[list[str], list[str]]:
    words: list[str] = []
    flags: list[str] = []
    seen_flag = False
    for token in _tokens(raw):
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
        if not is_group(cur):
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


def _repo_command_file(node) -> str:
    """The file that defines this command, when that file is inside this repository.

    ``KNOWN_MISSING`` records what THIS repository does not ship yet. A developer who has an
    unrelated plugin installed (``pip install -e plugins/navig-disk``, an entry-point package)
    adds commands to the tree this test can see, and a staleness check that counted those
    would refuse every landing on that workstation — and would be measuring the machine, not
    the repo. Returning "" for such a command is what keeps the check honest in both
    directions: when the group lands in ``core`` the entry goes stale, as it should.
    """
    callback = getattr(node, "callback", None)
    module_name = str(getattr(callback, "__module__", "") or "")
    module = sys.modules.get(module_name)
    source = getattr(module, "__file__", "") or ""
    if not source:
        return ""
    core_dir = Path(__file__).resolve().parents[2]
    resolved = Path(source).resolve()
    return str(resolved) if str(resolved).startswith(str(core_dir)) else ""


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
        rest = argv[1 + len(words):]
        if why is None and rest and not rest[0].startswith("-"):
            # The walk stops at the first `{{…}}` token, so a GROUP called with an argument
            # where its subcommand belongs used to pass: `db-snapshot` ran
            # `navig download <file> <dir>` — `download` is the TikTok plugin's group, not
            # a file transfer.
            node, _ = _resolve(root, words)
            if is_group(node) and not node.invoke_without_command:
                why = f"`{' '.join(words)}` is a command group; `{rest[0]}` is not a subcommand"
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
        if _known_missing(words) or _platform_scoped_out(words):
            continue
        why = _check(root, words, flags)
        if why:
            broken.append(f"{skill}: `navig {raw}` — {why}")
    assert not broken, (
        "skills instruct the agent to run commands that do not exist:\n  " + "\n  ".join(broken)
    )


def test_skills_naming_unimplemented_commands_never_auto_activate() -> None:
    """KNOWN_MISSING exempts a skill from the resolve check — it must not ALSO be injected
    into the agent's prompt, or the agent is told to run a command that cannot resolve."""
    from navig.agent.skills_context import _parse_skill_file

    paths = {p.parent.name: p for p in _builtin().rglob("SKILL.md")}
    activatable = sorted(
        {
            skill
            for skill, _raw, words, _flags in _skill_refs()
            if _known_missing(words) and _parse_skill_file(paths[skill], "builtin").activatable
        }
    )
    assert not activatable, (
        "skills that name an unimplemented command still auto-activate — add "
        "`activatable: false` to their frontmatter: " + ", ".join(activatable)
    )


def test_known_missing_entries_are_still_missing(root) -> None:
    """A stale entry is worse than none — it silently exempts a command that now exists."""
    stale = []
    for group, reason in KNOWN_MISSING_GROUPS.items():
        node, _ = _resolve(root, [group])
        if node is not None and _repo_command_file(node):
            stale.append(f"group `{group}` now EXISTS — drop it ({reason})")
    for (group, verb), reason in KNOWN_MISSING_VERBS.items():
        node, _ = _resolve(root, [group])
        if node is None:
            continue  # the whole group is missing; covered by the group entry
        verb_node, _ = _resolve(root, [group, verb])
        if verb_node is not None and _repo_command_file(verb_node):
            stale.append(f"`{group} {verb}` now EXISTS — drop it ({reason})")
    assert not stale, "KNOWN_MISSING is out of date:\n  " + "\n  ".join(stale)


# ── Registry spaces: instructions an operator (and a space's agents) follow ──────
#
# Scoped to commands a PLUGIN provides (core/navig/data/command_providers.json) — the
# references that break when a plugin is renamed, merged or reshaped, which is exactly
# what folding `navig-paperwork` into `navig-cabinet` did. Measured when this was added
# (2026-09-27): 60 such references across the tracked spaces, 1 dead (`navig contacts
# --filter`, fixed). The spaces' references to CORE commands are a separate, larger
# debt — 56 of 153 backticked refs were dead, mostly `skill run <id> --<flag>` and
# planned `space agent`/`workflow`/`pack` surfaces — deliberately NOT gated here: a
# 56-entry baseline is the "place to hide things" the call-arg guard's design rejects.

# Runs to the end of the line (a backtick or `#` ends it), NOT to the first `"`: stopping
# at a quote truncated `agent run herald --task "…" --attach script.sh` to
# `agent run herald --task ` and the unsupported `--attach` was never checked.
# `_tokens` keeps the quoted argument as one token instead.
_SPACE_LINE = re.compile(r'^\s*(?:[-*]\s+|\$\s+|"|- ")?navig ([^\n`#]+)', re.M)


def _yaml_command_values(rel: str, text: str) -> list[str]:
    """Every single-line YAML string VALUE that is a `navig …` command, minus `navig `.

    `_SPACE_LINE` only sees a command at the START of a line, so a command-valued key —
    `command: "navig skill run adb-connect --mode wifi"` — was never scanned at all.
    Multi-line values are left to `_SPACE_LINE`, which already sees each of their lines.
    """
    if not rel.endswith((".yaml", ".yml")):
        return []
    import yaml

    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return []
    found: list[str] = []
    stack: list[object] = list(docs)
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
        elif isinstance(node, str):
            value = node.strip()
            if value.startswith("navig ") and "\n" not in value:
                found.append(value[len("navig "):])
    return found


def _space_plugin_refs():
    import json
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    assert (repo / "registry" / "spaces").is_dir(), repo  # structural anchor
    providers = json.loads(
        (repo / "core" / "navig" / "data" / "command_providers.json").read_text(encoding="utf-8")
    )["commands"]
    files = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--", "registry/spaces"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split()
    for rel in files:
        if not rel.endswith((".md", ".yaml", ".yml")):
            continue
        text = (repo / rel).read_text(encoding="utf-8", errors="replace")
        raws = [m.group(1) for m in _BACKTICKED.finditer(text)]
        raws += [m.group(1) for m in _SPACE_LINE.finditer(text)]  # fenced code blocks
        raws += _yaml_command_values(rel, text)
        for raw in raws:
            words, flags = _parse(raw)
            if words and words[0] in providers:
                yield rel, raw.strip(), words, flags


def test_space_plugin_refs_are_found():
    """A vacuity floor: a scan that silently finds nothing would pass forever."""
    refs = list(_space_plugin_refs())
    assert len(refs) >= 40, (
        f"only {len(refs)} plugin-command refs in registry/spaces — has the shape moved?"
    )
    assert any(words[0] == "cabinet" for _rel, _raw, words, _f in refs)


def test_registry_spaces_use_real_plugin_commands(root) -> None:
    broken = []
    for rel, raw, words, flags in _space_plugin_refs():
        why = _check(root, words, flags)
        if why:
            broken.append(f"{rel}: `navig {raw}` — {why}")
    assert not broken, (
        "registry spaces tell the operator to run plugin commands that do not exist:\n  "
        + "\n  ".join(broken)
    )


# ── Registry spaces: EVERY command they name ────────────────────────────────────
#
# Measured 2026-09-27: 441 `navig …` references across the tracked spaces, 162 dead.
# 50 had an exact real equivalent and were rewritten (`agent ask X "q"` → `agent run X
# --task "q"`, `space load` → `space switch`, `flow run X --trip Y` → `--var trip=Y`,
# `run --host H -- cmd` → `host use H` + `run "cmd"`, …). The other 112 describe
# surfaces that were planned and never built. They are recorded below by FAMILY, with
# the reason, NOT as 112 baseline rows. Rewriting them to some other command would
# misrepresent what the space describes, and building them is feature work.
#
# The value is the floor, exactly as with KNOWN_MISSING above: any NEW dead reference
# outside these families fails, and each family is checked for staleness in both
# directions (it now exists → drop it; nothing references it any more → drop it).

# Every family that used to be recorded here — pack, workflow, contact, graph, space
# run/agent/clone/pack/import/system, skill test, memory edit/show — was REMOVED from the
# spaces by the 2026-10-16 registry scrub (rewritten to real commands or dropped), so the
# allowlists are empty. Keep them empty: a new dead reference should be fixed, not listed.
SPACE_PLANNED_GROUPS: dict[str, str] = {}
SPACE_PLANNED_VERBS: dict[tuple[str, str], str] = {}
# `navig skill run SPEC [ARGS]` exists, but only for SKILL.md commands and script
# entrypoints. The spaces' `.navig/skills/*.yaml` step-skills declare `parameters:`
# and are documented as `skill run <id> --<param> <value>`; no runner executes them,
# and Click rejects the flags. Executing scripts shipped in community YAML is a
# security decision, not a docs fix.
SPACE_PLANNED_FLAGS = {
    ("skill", "run"): "YAML step-skill parameters (`--mode`, `--target` …) — no step-skill runner",
}


def _space_refs():
    for rel, raw_text in _space_texts():
        raws = [m.group(1) for m in _BACKTICKED.finditer(raw_text)]
        raws += [m.group(1) for m in _SPACE_LINE.finditer(raw_text)]
        raws += _yaml_command_values(rel, raw_text)
        for raw in raws:
            words, flags = _parse(raw)
            if words:
                yield rel, raw.strip(), words, flags


def _space_texts():
    import subprocess

    repo = Path(__file__).resolve().parents[3]
    assert (repo / "registry" / "spaces").is_dir(), repo  # structural anchor
    files = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "--", "registry/spaces"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout.split()
    for rel in files:
        if rel.endswith((".md", ".yaml", ".yml")):
            yield rel, (repo / rel).read_text(encoding="utf-8", errors="replace")


def _space_planned(root, words: list[str], flags: list[str]) -> str | None:
    """Which planned family a dead reference belongs to, or None when it is a new one."""
    if words[0] in SPACE_PLANNED_GROUPS:
        return words[0]
    key = tuple(words[:2])
    if len(words) >= 2 and key in SPACE_PLANNED_VERBS:
        return " ".join(key)
    if key in SPACE_PLANNED_FLAGS and _resolve(root, words[:2])[0] is not None:
        return " ".join(key) + " --flags"
    return None


def test_space_refs_are_found():
    refs = list(_space_refs())
    assert len(refs) >= 300, (
        f"only {len(refs)} command refs in registry/spaces — has the shape moved?"
    )


def test_registry_spaces_name_only_real_or_recorded_commands(root) -> None:
    broken = []
    for rel, raw, words, flags in _space_refs():
        if _known_missing(words):
            continue
        why = _check(root, words, flags)
        if why and not _space_planned(root, words, flags):
            broken.append(f"{rel}: `navig {raw}` — {why}")
    assert not broken, (
        "registry spaces name commands that do not exist (fix the reference, or — only "
        "for a genuinely planned surface — record the family with its reason):\n  "
        + "\n  ".join(broken)
    )


def test_space_planned_families_are_not_stale(root) -> None:
    """Both directions: a family that now EXISTS silently exempts a real command, and one
    nothing references any more exempts nothing and misleads the next reader."""
    stale = []
    for group, reason in SPACE_PLANNED_GROUPS.items():
        if _resolve(root, [group])[0] is not None:
            stale.append(f"group `{group}` now EXISTS — drop it ({reason})")
    for (group, verb), reason in SPACE_PLANNED_VERBS.items():
        if _resolve(root, [group, verb])[0] is not None:
            stale.append(f"`{group} {verb}` now EXISTS — drop it ({reason})")
    used = {_space_planned(root, w, f) for _r, _raw, w, f in _space_refs() if _check(root, w, f)}
    every = (
        {g for g in SPACE_PLANNED_GROUPS}
        | {" ".join(k) for k in SPACE_PLANNED_VERBS}
        | {" ".join(k) + " --flags" for k in SPACE_PLANNED_FLAGS}
    )
    stale += [
        f"`{fam}` is referenced by no space any more — drop it" for fam in sorted(every - used)
    ]
    assert not stale, "SPACE_PLANNED is out of date:\n  " + "\n  ".join(stale)


def test_space_line_scan_reads_past_a_quoted_argument():
    """Teeth: the whole line is checked, so a flag AFTER a quoted value is seen.

    `_SPACE_LINE` used to stop at the first `"`, which captured only
    `agent run herald --task ` and never saw the unsupported `--attach`.
    """
    line = 'navig agent run herald --task "Review it --not-a-flag" --attach script.sh\n'
    raws = [m.group(1) for m in _SPACE_LINE.finditer(line)]
    assert len(raws) == 1
    words, flags = _parse(raws[0])
    assert words == ["agent", "run", "herald"]
    # the quoted value is one token, so its `--not-a-flag` is not read as a flag
    assert flags == ["--task", "--attach"]


def test_yaml_command_values_are_scanned():
    """Teeth: a command-valued YAML key is invisible to the line-start regex."""
    text = (
        "examples:\n"
        "  - description: connect\n"
        '    command: "navig skill run adb-connect --mode wifi"\n'
    )
    assert not [m.group(1) for m in _SPACE_LINE.finditer(text)]
    values = _yaml_command_values("x/skills/adb.yaml", text)
    assert values == ["skill run adb-connect --mode wifi"]
    assert _parse(values[0]) == (["skill", "run", "adb-connect"], ["--mode"])
    assert _yaml_command_values("x/README.md", text) == []
