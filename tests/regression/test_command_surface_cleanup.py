"""The 2026-10 command-surface cleanup: one visible name per capability, nothing broken.

The CLI contract is "no breaking changes", so the cleanup removed no working verb. It
hid duplicate names (aliases), made superseded verbs point at their replacement (hint),
and made verbs that never worked say where the real command is and exit 1 (moved). The
single source of truth is ``navig.cli.superseded.SUPERSEDED``.

What these tests pin:

* every replacement a superseded verb names RESOLVES — command path AND flags — through
  the real Click tree (a pointer at a command that does not exist is worse than no
  pointer: the operator, or an agent, runs it and fails);
* every superseded verb is still registered (no CLI break);
* canonical names are listed in ``navig --help``; aliases and superseded groups are not,
  and the published manifest does not carry them;
* aliases and hint verbs still run; moved verbs exit 1 naming their replacement;
* ``navig plan run`` hands off to a command that exists (it called
  ``navig agent run --context-file``, a flag ``agent run`` never had);
* ``generated/deprecations.json`` records every superseded verb.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import click
import pytest
from typer.main import get_command
from typer.testing import CliRunner

from navig.cli.registration import _ALIAS_COMMANDS, _NOT_PUBLISHED, _RETIRED_COMMANDS
from navig.cli.superseded import SUPERSEDED

pytestmark = pytest.mark.integration

CORE = Path(__file__).resolve().parents[2]
runner = CliRunner()

# Groups that must stay visible: the canonical side of every twin and every replacement.
CANONICAL = (
    "skill", "space", "plugin", "signals", "checkin", "blackbox", "kg", "generate",
    "store", "cdp", "goal", "formation", "trigger", "history", "deck", "service",
    "backup", "block", "agent", "plan", "plans", "flow", "task", "install",
)
# Plugin-provided canonical names — checked only when the plugin is installed here.
PLUGIN_CANONICAL = ("download", "facebook")


def _flat(text: str) -> str:
    """Output with whitespace collapsed — Rich wraps long lines at the terminal width."""
    return " ".join(text.split())


@pytest.fixture(scope="module")
def app():
    from navig.cli import _register_external_commands
    from navig.cli import app as typer_app

    _register_external_commands(register_all=True, target_app=typer_app)
    return typer_app


@pytest.fixture(scope="module")
def root(app) -> click.Group:
    return get_command(app)


def _resolve(root: click.Group, cmdline: str) -> str | None:
    """None when *cmdline* resolves (path and flags) to a real command, else why not.

    ``<placeholder>`` tokens stand for operator-supplied values; a token after a flag
    that takes a value is that flag's value; ``--`` ends option checking.
    """
    tokens = cmdline.split()
    assert tokens[0] == "navig", cmdline
    cmd: click.Command = root
    ctx = click.Context(root, info_name="navig")
    i = 1
    while i < len(tokens) and isinstance(cmd, click.Group):
        tok = tokens[i]
        if tok.startswith("-") or tok.startswith("<"):
            break
        sub = cmd.get_command(ctx, tok)
        if sub is None:
            return f"`{' '.join(tokens[: i + 1])}` is not a command"
        cmd = sub
        ctx = click.Context(cmd, info_name=tok, parent=ctx)
        i += 1
    params = cmd.get_params(ctx)  # NOT .params: --help is appended at parse time
    takes_value = {}
    for p in params:
        if isinstance(p, click.Option):
            for opt in [*p.opts, *p.secondary_opts]:
                takes_value[opt] = not (p.is_flag or p.count)
    rest = tokens[i:]
    j = 0
    while j < len(rest):
        tok = rest[j]
        if tok == "--":
            break
        if tok.startswith("-"):
            name = tok.split("=", 1)[0]
            if name not in takes_value:
                return f"`{cmdline}`: {cmd.name} has no option {name}"
            if takes_value[name] and "=" not in tok:
                j += 1  # skip the value
        j += 1
    return None


# ── every pointer resolves; every old verb still exists ───────────────────────


@pytest.mark.parametrize("row", SUPERSEDED, ids=lambda r: r.path)
def test_every_replacement_resolves_verb_and_flags(root, row):
    why = _resolve(root, row.replaced_by)
    assert why is None, f"{row.path} points at a command that does not exist: {why}"


@pytest.mark.parametrize("row", SUPERSEDED, ids=lambda r: r.path)
def test_every_superseded_verb_is_still_registered(root, row):
    why = _resolve(root, row.path)
    assert why is None, f"{row.path} stopped resolving — that is a CLI break: {why}"


def test_superseded_paths_are_unique():
    paths = [s.path for s in SUPERSEDED]
    assert len(paths) == len(set(paths))


def test_the_resolver_has_teeth(root):
    """A resolver that accepts everything would pass every test above."""
    assert _resolve(root, "navig cdp nav <url>") is None
    assert _resolve(root, "navig cdp nav <url> --port 9222") is None
    assert _resolve(root, "navig cdp navigate <url>") is not None
    assert _resolve(root, "navig cdp nav <url> --no-such-flag") is not None
    assert _resolve(root, "navig agent run <id> --context-file x") is not None


# ── visibility: --help and the published manifest ─────────────────────────────


def _help_listed(root: click.Group) -> set[str]:
    ctx = click.Context(root)
    return {
        name for name in root.list_commands(ctx)
        if not getattr(root.get_command(ctx, name), "hidden", False)
    }


def test_canonical_names_are_listed_in_help(root):
    listed = _help_listed(root)
    missing = [n for n in CANONICAL if n not in listed]
    assert not missing, f"canonical commands hidden from `navig --help`: {missing}"
    ctx = click.Context(root)
    for name in PLUGIN_CANONICAL:
        if root.get_command(ctx, name) is not None:
            assert name in listed, f"`navig {name}` is canonical but hidden"


def test_aliases_and_superseded_groups_are_not_listed_in_help(root):
    leaked = sorted(_help_listed(root) & _NOT_PUBLISHED)
    assert not leaked, f"aliases / superseded groups listed in `navig --help`: {leaked}"


def test_the_committed_manifest_does_not_publish_them():
    manifest = json.loads((CORE / "generated" / "commands.json").read_text(encoding="utf-8"))
    groups = {c["path"].split()[1] for c in manifest["commands"] if len(c["path"].split()) > 1}
    leaked = sorted(groups & _NOT_PUBLISHED)
    assert not leaked, f"published in generated/commands.json: {leaked}"
    for name in ("skill", "space", "plugin", "formation", "history", "store", "cdp"):
        assert name in groups, f"canonical `navig {name}` missing from the manifest"
    paths = {c["path"] for c in manifest["commands"]}
    assert "navig formation council" in paths
    assert {"navig history files versions", "navig history files diff",
            "navig history files restore"} <= paths


def test_sub_verbs_superseded_inside_a_public_group_are_hidden(root):
    ctx = click.Context(root)
    for path in ("agent goal", "plugin add", "plugin enable", "plugin disable",
                 "plugin remove", "plans run"):
        group, verb = path.split()
        cmd = root.get_command(ctx, group).get_command(ctx, verb)
        assert cmd is not None and cmd.hidden, f"`navig {path}` should be registered and hidden"


def test_alias_and_retired_sets_are_classified_in_the_table():
    """Every hidden twin / superseded group has a row saying what replaced it."""
    rows = {s.path for s in SUPERSEDED}
    cleanup_aliases = {"skills", "spaces", "plugins", "signal", "checkins", "bb",
                       "knowledge", "media", "dl", "tiktok", "tt", "fb"}
    assert cleanup_aliases <= _ALIAS_COMMANDS
    for name in cleanup_aliases | _RETIRED_COMMANDS:
        assert f"navig {name}" in rows, f"`navig {name}` is hidden but not in SUPERSEDED"


# ── behaviour: aliases run, hints point, moved verbs exit 1 ───────────────────


@pytest.mark.parametrize(
    "argv",
    [["skills", "--help"], ["spaces", "--help"], ["plugins", "--help"], ["signal", "--help"],
     ["checkins", "--help"], ["bb", "--help"], ["knowledge", "--help"], ["media", "--help"],
     ["module", "--help"], ["modules", "--help"], ["council", "--help"], ["snapshot", "--help"],
     ["browser", "--help"], ["formation", "council", "--help"],
     ["history", "files", "--help"]],
)
def test_aliases_and_new_homes_still_invoke(app, argv):
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, f"{argv}: {res.output}"


@pytest.mark.parametrize("name", ["dl", "tiktok", "tt", "fb"])
def test_plugin_aliases_still_invoke(app, root, name):
    if root.get_command(click.Context(root), name) is None:
        pytest.skip(f"the plugin providing `navig {name}` is not installed here")
    res = runner.invoke(app, [name, "--help"])
    assert res.exit_code == 0, res.output


MOVED = [s for s in SUPERSEDED if s.kind == "moved"]


@pytest.mark.parametrize("row", MOVED, ids=lambda r: r.path)
def test_moved_verbs_exit_1_naming_the_replacement(app, row):
    res = runner.invoke(app, row.path.split()[1:])
    assert res.exit_code == 1, f"{row.path}: {res.output}"
    assert row.replaced_by in _flat(res.output), res.output


@pytest.mark.parametrize(
    ("argv", "points_at"),
    [
        (["council", "run", "--help"], "navig formation council"),
        (["snapshot", "versions", "--help"], "navig history files versions"),
        (["module", "list", "--help"], "navig store list --kind module"),
        (["modules", "info", "--help"], "navig store info module:"),
    ],
)
def test_group_level_hints_name_the_replacement(app, argv, points_at):
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    assert points_at in _flat(res.output)


def test_browser_verbs_still_run_and_name_cdp(app):
    reply = MagicMock(status_code=200)
    reply.json.return_value = {"ok": True, "data": {"started": False}, "error": None}
    with patch("requests.sessions.Session.request", return_value=reply):
        res = runner.invoke(app, ["browser", "open", "https://example.com"])
    assert res.exit_code == 0, res.output
    assert "navig cdp nav https://example.com" in _flat(res.output)


def test_plugin_lifecycle_verbs_still_run_and_name_store(app):
    host = MagicMock()
    host.enable.return_value = MagicMock(id="navig-x", format="pip")
    with patch("navig.commands.plugin._host", return_value=host):
        res = runner.invoke(app, ["plugin", "enable", "navig-x"])
    assert res.exit_code == 0, res.output
    host.enable.assert_called_once_with("navig-x")
    assert "navig store enable plugin:navig-x" in _flat(res.output)


def test_agent_goal_still_runs_and_names_goal(app):
    planner = MagicMock()
    planner.list_goals.return_value = []
    with patch("navig.agent.goals.GoalPlanner", return_value=planner):
        res = runner.invoke(app, ["agent", "goal", "list"])
    assert res.exit_code == 0, res.output
    assert "use `navig goal`" in _flat(res.output)


# ── plan run: hands off to a command that exists ──────────────────────────────


def _plan(tmp_path: Path, monkeypatch, body: str) -> None:
    plans = tmp_path / ".navig" / "plans"
    plans.mkdir(parents=True)
    (plans / "demo.md").write_text(
        f"---\nslug: demo\nstatus: ready\n---\n{body}", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)


def test_plan_run_invokes_agent_plan_with_the_plan_body(root, tmp_path, monkeypatch):
    from navig.commands.plan_mode import app as plan_app

    _plan(tmp_path, monkeypatch, "- restart nginx\n- verify it is healthy\n")
    calls: list[list[str]] = []

    def _fake_run(cmd, check=False, **_kw):
        calls.append(list(cmd))
        return MagicMock(returncode=0)

    monkeypatch.setattr("subprocess.run", _fake_run)
    res = runner.invoke(plan_app, ["run", "demo", "--yes"])
    assert res.exit_code == 0, res.output
    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[1:3] == ["-m", "navig"]
    argv = cmd[3:]
    # The task is the plan BODY (front-matter stripped), after `--` so a leading "-"
    # bullet is not read as a flag.
    assert argv[-1].startswith("- restart nginx")
    assert "status: ready" not in argv[-1]
    assert argv[-2] == "--"
    why = _resolve(root, "navig " + " ".join(argv[:-1]))
    assert why is None, f"plan run hands off to a command that does not exist: {why}"
    assert argv[:2] == ["agent", "plan"]
    assert "--context-file" not in argv


def test_plan_run_dry_run_names_agent_plan(tmp_path, monkeypatch):
    from navig.commands.plan_mode import app as plan_app

    _plan(tmp_path, monkeypatch, "Do the thing.\n")
    monkeypatch.setattr("subprocess.run", MagicMock(side_effect=AssertionError("ran")))
    res = runner.invoke(plan_app, ["run", "demo", "--dry-run"])
    assert res.exit_code == 0, res.output
    assert "agent plan" in _flat(res.output)


# ── deprecations.json is the table, regenerated ───────────────────────────────


def test_deprecations_json_records_every_superseded_verb():
    report = json.loads((CORE / "generated" / "deprecations.json").read_text(encoding="utf-8"))
    rows = {r["path"]: r for r in report["deprecated_commands"]}
    missing = [s.path for s in SUPERSEDED if s.path not in rows]
    assert not missing, (
        f"not in generated/deprecations.json: {missing} — regenerate with "
        "`py -3.13 tools/export_registry.py --format both --deprecations-report --output-dir generated`"
    )
    for s in SUPERSEDED:
        assert rows[s.path]["replaced_by"] == s.replaced_by, s.path
