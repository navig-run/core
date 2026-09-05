"""Guard: a command group that nothing mounts is a feature no user can reach.

A ``commands/`` module can define a Typer app, decorate commands onto it, ship tests for
it, and still be **invisible** — because nothing ever mounts the app onto the CLI. The code
looks wired from the inside: the decorators are there, the functions are referenced by
Typer itself, and a reader scanning the file sees a finished command group.

Measured 2026-09-05 across every ``commands/`` module in core and every plugin:

    navig contribute      `contribute_app`, 2 commands — "Self-Heal & Hive Mind Protocol:
                          scan, review, and contribute fixes to navig-run/core"
    navig tools           `tools_app`, 3 commands
    navig tailscale       `tailscale_app`, 3 commands — "Tailscale network integration"
    navig space context   `spaces_context_app`, 2 commands — "Switch between personal,
                          workspace, and studio contexts"

**Ten commands no user can invoke.** All four were confirmed at runtime: every one of
`navig contribute|tools|tailscale|space context --help` answers "No such command".
`contribute.py` is not abandoned either — 4 commits, last touched five weeks before this
was written, and TWO test files exercise it. Written, tested, and never wired: the shape a
passing suite cannot see, because the tests import the module directly and never ask
whether the CLI mounts it.

⚠ The detector took FIVE measured corrections, and each one matters if you edit it:

1. **A decorated function needs no reference.** Typer registers by decorator, so
   ``@photos_app.command("events")`` wires ``photos_events_cmd`` with zero mentions of that
   name anywhere. A "is this function referenced?" rule reported 9 dead commands, 6 of them
   perfectly live.
2. **A sub-app is mounted inside its OWN module.** `flow_template_app` is mounted by
   ``flow_app.add_typer(flow_template_app, ...)`` in the same file, so requiring an
   EXTERNAL reference reported 30 unreachable apps — including `navig flow template list`,
   which demonstrably works.
3. **A plugin app is mounted by an ENTRY POINT**, not by `add_typer`:
   ``[project.entry-points."navig.commands"] social = "…:social_app"``. Sixteen plugin
   pyprojects declare them; ignoring those reported every plugin's command group as dead.
4. **A reference from a TEST is not a mount** — and this guard's own docstring names the
   apps it reports, so counting tests made it suppress its own findings. Excluding tests
   surfaced two MORE real ones (`tailscale`, `spaces context`) that had been hidden by
   their own test files. The write-up must never change the verdict.
5. Only after all four does the answer come down to 4 — each confirmed by actually running
   the CLI, which is the ground truth a static rule only approximates.

So the rule errs toward SILENCE: any of `add_typer`, an external reference, or an entry
point counts as mounted. A group this guard flags is one that three independent mounting
mechanisms all failed to claim.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

SKIP_DIRS = {"node_modules", "venv", "site-packages", "__pycache__", "dist", "build", ".archive"}
SEARCH_ROOTS = ("core", "plugins", "private")

# Command groups that are deliberately not mounted, each with the reason. An entry here is
# a PRODUCT decision left open (wire it, or delete it) — not a suppression, and not a place
# to park a group someone forgot about. `test_allowlist_entries_are_still_unmounted` deletes
# any entry that becomes reachable, so wiring one of these fails until the entry goes too.
NOT_MOUNTED_ON_PURPOSE: dict[str, str] = {
    "core/navig/commands/contribute.py:contribute_app": (
        "Self-Heal & Hive Mind Protocol (scan/status). Maintained across 4 commits and "
        "covered by 2 test files, but `navig contribute` has never existed. Mounting it "
        "adds a user-visible verb, which is the owner's call — as is deleting work that "
        "is still being maintained."
    ),
    "core/navig/commands/tools.py:tools_app": (
        "3 commands, `navig tools` has never existed. Same decision as contribute: wire "
        "it or delete it; a guard cannot choose."
    ),
    "core/navig/commands/tailscale_cmd.py:tailscale_app": (
        "\"Tailscale network integration\", 3 commands, `navig tailscale` has never "
        "existed. Declares name= and no_args_is_help= like a mounted group, which is "
        "exactly why nobody noticed."
    ),
    "core/navig/commands/spaces.py:spaces_context_app": (
        "\"Switch between personal, workspace, and studio contexts\", 2 commands. Neither "
        "`navig space context` nor `navig spaces context` exists."
    ),
}

# Vacuity floors. Measured today: 197 command apps, 71 distinct add_typer targets,
# 16 plugin pyprojects declaring navig.commands entry points.
MIN_APPS = 150
MIN_ADD_TYPER_TARGETS = 40
MIN_ENTRY_POINT_APPS = 10


def _source_files() -> dict[Path, str]:
    found: dict[Path, str] = {}
    for root in SEARCH_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in SKIP_DIRS]
            for name in filenames:
                if not name.endswith((".py", ".toml")):
                    continue
                path = Path(dirpath, name)
                try:
                    found[path] = path.read_text(encoding="utf-8", errors="replace")
                except OSError:  # pragma: no cover - unreadable file is another guard's job
                    continue
    return found


def _command_apps(sources: dict[Path, str]) -> list[tuple[Path, str, int]]:
    """Every ``X = typer.Typer(...)`` in a commands/ module that has commands on it."""
    apps: list[tuple[Path, str, int]] = []
    for path, text in sources.items():
        if path.suffix != ".py" or path.parent.name != "commands":
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:  # pragma: no cover
            continue
        counts: dict[str, int] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name):
                    if ast.unparse(node.value).startswith(("typer.Typer(", "Typer(")):
                        counts[target.id] = 0
        if not counts:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                src = ast.unparse(dec)
                for app in counts:
                    if src.startswith(f"{app}.command"):
                        counts[app] += 1
        apps += [(path, app, n) for app, n in counts.items() if n]
    return apps


def _add_typer_targets(sources: dict[Path, str]) -> set[str]:
    names: set[str] = set()
    for text in sources.values():
        for match in re.finditer(r"add_typer\(\s*([A-Za-z_][A-Za-z0-9_.]*)", text):
            names.add(match.group(1).split(".")[-1])
    return names


def _entry_point_apps(sources: dict[Path, str]) -> set[str]:
    """Apps a pyproject exposes as ``navig.commands`` — the plugin mounting mechanism."""
    names: set[str] = set()
    for path, text in sources.items():
        if path.name != "pyproject.toml" or "navig.commands" not in text:
            continue
        for match in re.finditer(r'=\s*"[^"]*:([A-Za-z_][A-Za-z0-9_]*)"', text):
            names.add(match.group(1))
    return names


def _unmounted() -> list[str]:
    sources = _source_files()
    apps = _command_apps(sources)
    mounted = _add_typer_targets(sources) | _entry_point_apps(sources)

    findings: list[str] = []
    for path, app, count in apps:
        if app in mounted:
            continue
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(app) + r"(?![A-Za-z0-9_])")
        # A reference from TEST code is not a mount — and this guard's own docstring names
        # the apps it reports, so counting tests made it suppress its own findings. Same
        # shape as a guard tripping on prose: the write-up must not change the verdict.
        if any(
            other != path and "tests" not in other.parts and pattern.search(text)
            for other, text in sources.items()
        ):
            continue  # referenced from product code — something imports it to register it
        rel = path.relative_to(REPO).as_posix()
        findings.append(f"{rel}:{app} ({count} command(s))")
    return findings


def _key(finding: str) -> str:
    return finding.split(" (")[0]


def test_every_command_group_is_mounted_somewhere() -> None:
    findings = _unmounted()
    unexplained = sorted(f for f in findings if _key(f) not in NOT_MOUNTED_ON_PURPOSE)
    assert not unexplained, (
        "these command groups define commands that NOTHING mounts — the code reads as "
        "finished and the CLI has no such verb:\n  "
        + "\n  ".join(unexplained)
        + "\n\nMount it (`add_typer`, or a `navig.commands` entry point for a plugin), "
        "delete it, or record it in NOT_MOUNTED_ON_PURPOSE with the reason."
    )


def test_the_detector_still_sees_the_mounting_mechanisms() -> None:
    """Anti-vacuity, one assertion per mechanism.

    Each of the three has already produced a wave of false positives on its own (see the
    module docstring). If one silently stops matching, this guard does not go quiet — it
    goes LOUD, reporting every group that mechanism was mounting. That is the failure mode
    that gets a guard deleted rather than fixed, so catch it here instead.
    """
    sources = _source_files()
    apps = _command_apps(sources)
    assert len(apps) >= MIN_APPS, (
        f"only {len(apps)} command apps found (expected >= {MIN_APPS}) — the Typer idiom "
        "changed, or the walk narrowed."
    )
    assert len(_add_typer_targets(sources)) >= MIN_ADD_TYPER_TARGETS, (
        "almost no add_typer targets parsed — every sub-app would be reported unmounted."
    )
    assert len(_entry_point_apps(sources)) >= MIN_ENTRY_POINT_APPS, (
        "no navig.commands entry points parsed — every PLUGIN command group would be "
        "reported unmounted."
    )


def test_allowlist_entries_are_still_unmounted() -> None:
    """An entry that became reachable must be deleted, or it grants itself forever."""
    live = {_key(f) for f in _unmounted()}
    stale = sorted(k for k in NOT_MOUNTED_ON_PURPOSE if k not in live)
    assert not stale, (
        f"these are mounted now (or gone) — remove them from NOT_MOUNTED_ON_PURPOSE: {stale}"
    )
