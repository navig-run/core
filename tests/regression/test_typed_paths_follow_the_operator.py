"""A path the operator TYPED must resolve where they typed it, not inside the space.

``main.py`` chdir's into the ACTIVE SPACE before any command body runs (it stashes the
real launch dir in ``NAVIG_INVOCATION_CWD`` first). So ``Path.cwd()`` inside a command is
the space — not where the operator is standing. Every command that resolved a typed
relative path, or defaulted to "the current directory", therefore answered a DIFFERENT
directory, silently::

    E:\\projects\\apps\\getbossed> navig space init getbossed --path . --dry-run
    DRY RUN: Would create 102 item(s) in C:\\Users\\subdose\\.navig-os\\workspaces\\my-workspace

Nothing errored. Without ``--dry-run`` that scaffolds 102 items into the active space.
``navig space doctor`` — whose own help promises "(default: current directory)" —
diagnosed the active space from inside an unrelated project, and ``--fix`` would have
written there. ``navig wire`` with no ``--path`` wired the space's tree.

The same trap was fixed once in ``navig repo`` and once in ``navig sync instructions``;
these are the remaining siblings in the space/wire family.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from navig.commands import space as space_cmd
from navig.commands import wire as wire_cmd
from navig.platform import paths

_wire_app = typer.Typer()
_wire_app.command()(wire_cmd.wire_command)
_runner = CliRunner()


@pytest.fixture
def standing_elsewhere(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    """Put the process cwd in one directory and the operator in another.

    Returns ``(where_the_operator_stands, the_active_space)``. Mirrors the real runtime:
    ``main.py`` has already chdir'd into the space by the time a command runs.
    """
    standing = tmp_path / "project"
    space = tmp_path / "active-space"
    standing.mkdir()
    space.mkdir()
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(standing))
    monkeypatch.chdir(space)
    return standing.resolve(), space.resolve()


# ── the helper itself ────────────────────────────────────────────────────────


def test_invocation_cwd_is_where_the_operator_stands(standing_elsewhere) -> None:
    standing, space = standing_elsewhere
    assert paths.invocation_cwd().resolve() == standing
    assert Path.cwd().resolve() == space  # teeth: the two really differ


def test_invocation_cwd_falls_back_to_the_process_cwd(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("NAVIG_INVOCATION_CWD", raising=False)
    monkeypatch.chdir(tmp_path)
    assert paths.invocation_cwd().resolve() == tmp_path.resolve()


def test_a_stale_invocation_dir_is_ignored(tmp_path: Path, monkeypatch) -> None:
    """A recorded dir that no longer exists must not be handed back as a target."""
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path / "deleted"))
    monkeypatch.chdir(tmp_path)
    assert paths.invocation_cwd().resolve() == tmp_path.resolve()


def test_resolve_user_path_anchors_relative_to_the_operator(standing_elsewhere) -> None:
    standing, _space = standing_elsewhere
    assert paths.resolve_user_path(".") == standing
    assert paths.resolve_user_path("sub") == standing / "sub"
    assert paths.resolve_user_path(Path("a/b")) == standing / "a" / "b"


def test_resolve_user_path_leaves_an_absolute_path_alone(
    standing_elsewhere, tmp_path: Path
) -> None:
    other = tmp_path / "somewhere-else"
    assert paths.resolve_user_path(other) == other.resolve()


def test_resolve_user_path_still_expands_a_tilde(standing_elsewhere) -> None:
    assert paths.resolve_user_path("~") == Path.home().resolve()


# ── the commands that were wrong ─────────────────────────────────────────────


@pytest.fixture
def scaffold_target(monkeypatch) -> list[Path]:
    """Record the directory a command decided to scaffold, without touching disk.

    Asserting on the printed path would be asserting on Rich's line wrapping; the
    argument handed to the scaffolder IS the decision under test.
    """
    seen: list[Path] = []

    def spy(space_path, name, owner="", *, dry_run=False):
        seen.append(Path(space_path))
        return {"created": [], "skipped": [], "conflicts": [], "migrated": []}

    monkeypatch.setattr(space_cmd, "_scaffold_space_skeleton", spy)
    return seen


def test_space_init_scaffolds_where_the_operator_stands(
    standing_elsewhere, scaffold_target
) -> None:
    standing, space = standing_elsewhere
    result = _runner.invoke(space_cmd.space_app, ["init", "demo", "--path", ".", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert scaffold_target == [standing]
    assert space not in scaffold_target  # teeth: this is where it went before the fix


def test_space_init_with_no_path_still_uses_the_spaces_root(
    standing_elsewhere, scaffold_target
) -> None:
    """``--path`` is what follows the operator; the default is still ~/.navig/spaces."""
    result = _runner.invoke(space_cmd.space_app, ["init", "demo", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert scaffold_target == [space_cmd._spaces_dir(create=False) / "demo"]


def test_space_doctor_defaults_to_where_the_operator_stands(standing_elsewhere) -> None:
    standing, _space = standing_elsewhere
    assert space_cmd._resolve_space_target(None).resolve() == standing


def test_a_relative_doctor_target_is_read_from_the_operators_directory(
    standing_elsewhere,
) -> None:
    standing, space = standing_elsewhere
    (standing / "mine").mkdir()
    (space / "mine").mkdir()  # a same-named decoy inside the space
    assert space_cmd._resolve_space_target("mine") == standing / "mine"


def test_an_absolute_doctor_target_is_unchanged(standing_elsewhere, tmp_path: Path) -> None:
    other = tmp_path / "explicit"
    other.mkdir()
    assert space_cmd._resolve_space_target(str(other)) == other.resolve()


def test_a_space_NAME_still_resolves_by_name_not_as_a_path(
    standing_elsewhere, monkeypatch, tmp_path: Path
) -> None:
    """The path probe must not swallow the name lookup it falls through to."""
    registered = tmp_path / "spaces" / "homelab"
    registered.mkdir(parents=True)
    monkeypatch.setattr(space_cmd, "_spaces_dir", lambda create=True: tmp_path / "spaces")
    monkeypatch.setattr(
        "navig.spaces.resolver.discover_space_paths",
        lambda include_disabled=False: {},
    )
    assert space_cmd._resolve_space_target("homelab") == registered


def test_wire_targets_the_operators_folder_not_the_space(
    standing_elsewhere, scaffold_target
) -> None:
    standing, space = standing_elsewhere
    result = _runner.invoke(_wire_app, ["--dry-run"])
    assert result.exit_code == 0, result.output
    assert scaffold_target == [standing]
    assert space not in scaffold_target


def test_wire_with_a_relative_path_follows_the_operator(
    standing_elsewhere, scaffold_target
) -> None:
    standing, _space = standing_elsewhere
    (standing / "sub").mkdir()
    result = _runner.invoke(_wire_app, ["sub", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert scaffold_target == [standing / "sub"]


# ── the contract the fix depends on ──────────────────────────────────────────


def test_main_still_records_the_invocation_dir_before_it_chdirs() -> None:
    """Every fix above is worthless if main.py stops stashing the launch dir.

    Assert the order in source: the env var must be written BEFORE the chdir call.
    """
    src = (Path(__file__).resolve().parents[2] / "navig" / "main.py").read_text(
        encoding="utf-8"
    )
    record = src.index('"NAVIG_INVOCATION_CWD"')
    chdir = src.index("_os.chdir(")
    assert record < chdir, "main.py must record the launch dir before chdir'ing"


def test_repo_helper_still_delegates_to_the_canonical_one(standing_elsewhere) -> None:
    """``commands/sync.py`` imports it from ``commands/repo``; keep that name working."""
    from navig.commands.repo import invocation_cwd as repo_invocation_cwd

    standing, _space = standing_elsewhere
    assert repo_invocation_cwd().resolve() == standing


# ── the second sweep: every remaining user-typed path parameter ──────────────
# (the shape itself is build-enforced by tests/quality/test_typed_paths_resolve_against_operator.py)
#
# After the space/wire family, 62 `Path.cwd()` sites remained in commands/. Most are
# legitimately the process cwd. The broken SHAPE is narrower — a user-typed path
# parameter, or a default whose HELP promises "current directory" — and these are the
# ones that had it. The classification is per site, and the help text is the contract:
#
#   index  <root>/--root   "default: current directory"   -> typed AND default follow you
#   mcp    --path          "default: current dir"         -> typed AND default follow you
#   formation --workspace  "defaults to cwd"              -> typed AND default follow you
#   block  --workdir       "default: current SPACE root"  -> typed follows you; default = space
#   plans  --path          "Workspace path" (space views) -> typed follows you; default = space


class _IndexerSpy:
    """Stands in for ProjectIndexer: records the root it was built with."""

    roots: list[Path] = []

    def __init__(self, root: Path) -> None:
        _IndexerSpy.roots.append(Path(root))
        self._file_hashes = {"x": "y"}  # `search` bails out on an empty index

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False

    def scan(self, *a, **k):
        return {"files": 0, "chunks": 0}

    def search(self, *a, **k):
        return []

    def stats(self):
        return {}

    def drop(self):
        return None


@pytest.fixture
def indexer_spy(monkeypatch) -> list[Path]:
    from navig.commands import index as index_cmd

    _IndexerSpy.roots = []
    monkeypatch.setattr(index_cmd, "ProjectIndexer", _IndexerSpy)
    return _IndexerSpy.roots


@pytest.mark.parametrize(
    "argv",
    [["scan"], ["search", "q"], ["stats"], ["drop", "--yes"]],
    ids=["scan", "search", "stats", "drop"],
)
def test_index_defaults_to_where_the_operator_stands(standing_elsewhere, indexer_spy, argv) -> None:
    """All four `index` verbs promise "current directory"."""
    from navig.commands import index as index_cmd

    standing, space = standing_elsewhere
    _runner.invoke(index_cmd.index_app, argv)
    assert indexer_spy, "the indexer was never constructed"
    assert indexer_spy[0] == standing
    assert indexer_spy[0] != space


def test_index_scan_with_a_relative_root_follows_the_operator(
    standing_elsewhere, indexer_spy
) -> None:
    from navig.commands import index as index_cmd

    standing, _space = standing_elsewhere
    (standing / "sub").mkdir()
    _runner.invoke(index_cmd.index_app, ["scan", "sub"])
    assert indexer_spy == [standing / "sub"]


def test_mcp_install_config_writes_into_the_operators_project(
    standing_elsewhere, monkeypatch
) -> None:
    """The file that lands in the wrong tree here is the editor's MCP config."""
    from navig.commands import mcp_cmd

    standing, space = standing_elsewhere
    monkeypatch.setattr(
        "navig.mcp_server.generate_vscode_mcp_config",
        lambda: {"mcpServers": {"navig": {"command": "navig", "args": ["mcp", "serve"]}}},
    )
    result = _runner.invoke(mcp_cmd.mcp_app, ["install-config"])
    assert result.exit_code == 0, result.output
    assert (standing / ".vscode" / "mcp.json").is_file()
    assert not (space / ".vscode").exists()  # teeth: where it went before


def test_mcp_install_config_with_a_relative_path_follows_the_operator(
    standing_elsewhere, monkeypatch
) -> None:
    from navig.commands import mcp_cmd

    standing, _space = standing_elsewhere
    (standing / "proj").mkdir()
    monkeypatch.setattr(
        "navig.mcp_server.generate_vscode_mcp_config",
        lambda: {"mcpServers": {"navig": {"command": "navig"}}},
    )
    result = _runner.invoke(mcp_cmd.mcp_app, ["install-config", "--path", "proj"])
    assert result.exit_code == 0, result.output
    assert (standing / "proj" / ".vscode" / "mcp.json").is_file()


def test_formation_init_writes_the_profile_into_the_operators_project(
    standing_elsewhere, monkeypatch
) -> None:
    from navig.commands import formation as formation_cmd

    standing, space = standing_elsewhere
    monkeypatch.setattr("navig.formations.loader.discover_formations", lambda: {"demo": object()})
    result = _runner.invoke(formation_cmd.formation_app, ["init", "demo"])
    assert result.exit_code == 0, result.output
    assert (standing / ".navig" / "profile.json").is_file()
    assert not (space / ".navig").exists()


def test_formation_init_with_a_relative_workspace_follows_the_operator(
    standing_elsewhere, monkeypatch
) -> None:
    from navig.commands import formation as formation_cmd

    standing, _space = standing_elsewhere
    (standing / "proj").mkdir()
    monkeypatch.setattr("navig.formations.loader.discover_formations", lambda: {"demo": object()})
    result = _runner.invoke(formation_cmd.formation_app, ["init", "demo", "-w", "proj"])
    assert result.exit_code == 0, result.output
    assert (standing / "proj" / ".navig" / "profile.json").is_file()


@pytest.fixture
def block_apply_spy(monkeypatch) -> list[Path]:
    """Stub everything around `apply_block` and record the workdir it received."""
    from navig.commands import block as block_cmd

    seen: list[Path] = []
    fake_block = type("B", (), {"id": "demo"})()
    monkeypatch.setattr("navig.blocks.find_block", lambda _id: fake_block)
    monkeypatch.setattr("navig.blocks.validate_block", lambda _b: [])
    monkeypatch.setattr(block_cmd, "_collect_inputs", lambda *a, **k: {})

    def _apply(block, inputs, *, workdir, **k):
        seen.append(Path(workdir))
        return type("R", (), {"outcome": "planned"})()

    monkeypatch.setattr("navig.blocks.runner.apply_block", _apply)
    return seen


def _block_app():
    from navig.commands import block as block_cmd

    app = typer.Typer()
    app.command("apply")(block_cmd.apply_command)
    return app


def test_block_apply_with_a_relative_workdir_follows_the_operator(
    standing_elsewhere, block_apply_spy
) -> None:
    standing, _space = standing_elsewhere
    (standing / "work").mkdir()
    # A single-command Typer runs that command as the root: no "apply" token.
    result = _runner.invoke(_block_app(), ["demo", "--workdir", "work", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert block_apply_spy == [standing / "work"]


def test_block_apply_default_is_still_the_space_root(
    standing_elsewhere, block_apply_spy, monkeypatch
) -> None:
    """The help promises "current SPACE root" - the default must NOT follow the operator."""
    _standing, space = standing_elsewhere
    monkeypatch.setattr("navig.platform.paths.find_app_root", lambda *a, **k: None)
    result = _runner.invoke(_block_app(), ["demo", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert block_apply_spy == [space]


def test_plans_typed_path_follows_the_operator_but_the_default_is_the_space(
    standing_elsewhere,
) -> None:
    """`_plans_dir` funnels every `plans` write; it creates `.navig/plans` under the root."""
    from navig.commands import plans as plans_cmd

    standing, space = standing_elsewhere
    # `_find_project_root` walks UP for a `.navig/`; tmp_path lives under core/, which
    # has one, so each root gets its own marker or the walk escapes the sandbox.
    (standing / "proj" / ".navig").mkdir(parents=True)
    (space / ".navig").mkdir()
    assert plans_cmd._plans_dir("proj") == standing / "proj" / ".navig" / "plans"
    assert plans_cmd._plans_dir(None) == space / ".navig" / "plans"


def test_plans_status_with_a_relative_path_follows_the_operator(
    standing_elsewhere, monkeypatch
) -> None:
    from navig.commands import plans as plans_cmd

    standing, _space = standing_elsewhere
    (standing / "proj").mkdir()
    seen: list[Path] = []
    monkeypatch.setattr(
        plans_cmd, "collect_spaces_progress", lambda cwd: seen.append(Path(cwd)) or []
    )
    _runner.invoke(plans_cmd.plans_app, ["status", "--path", "proj"])
    assert seen == [standing / "proj"]


def test_a_missing_path_target_is_reported_as_missing_not_as_a_bad_name(standing_elsewhere) -> None:
    """`navig space doctor ../nope` said "Invalid space name `../nope`. Use lowercase
    letters, digits, hyphens" — the wrong diagnosis for a directory that does not exist."""
    import typer

    standing, _space = standing_elsewhere
    with pytest.raises(typer.BadParameter, match="No such directory") as exc:
        space_cmd._resolve_space_target("../nope")
    assert str((standing / "../nope").resolve()) in str(exc.value)
    with pytest.raises(typer.BadParameter, match="No such directory"):
        space_cmd._resolve_space_target("./missing")


def test_a_bare_unknown_name_still_reads_as_a_name(standing_elsewhere, monkeypatch, tmp_path) -> None:
    """No separator, no dot: `homelab` is a NAME lookup and an unknown one is None, as before."""
    monkeypatch.setattr(space_cmd, "_spaces_dir", lambda create=True: tmp_path / "spaces")
    monkeypatch.setattr("navig.spaces.resolver.discover_space_paths", lambda include_disabled=False: {})
    assert space_cmd._resolve_space_target("homelab") is None
