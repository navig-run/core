"""``navig space init`` names the space after the folder, and lands in the folder.

Both arguments are optional and each defaults to the other's answer:

===================================== ============================= ========================
Command                               Space name                    Created in
===================================== ============================= ========================
``navig space init``                  the folder you are in         the folder you are in
``navig space init foo``              ``foo``                       ``~/.navig/spaces/foo``
``navig space init --path D:\\work``   ``work``                      ``D:\\work``
``navig space init foo --path .``     ``foo``                       the folder you are in
===================================== ============================= ========================

The name is derived from the RESOLVED TARGET, never from the process cwd — ``main.py``
chdir's into the active space before the command runs, so the two differ (see
``tests/regression/test_typed_paths_follow_the_operator.py``).

Inferring a target is not the same as being handed one, so the inferred case refuses a
filesystem root or the operator's home directory: a bare ``navig space init`` typed in the
wrong terminal must not seed 100+ items into ``~``. An explicit ``--path`` is never
second-guessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import space as space_cmd

_runner = CliRunner()


@pytest.fixture
def in_folder(tmp_path: Path, monkeypatch):
    """Stand the operator in a named folder while the process cwd is the active space."""

    def _stand(folder_name: str) -> Path:
        standing = tmp_path / folder_name
        standing.mkdir(parents=True)
        space = tmp_path / "active-space"
        space.mkdir(exist_ok=True)
        monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(standing))
        monkeypatch.chdir(space)
        return standing.resolve()

    return _stand


@pytest.fixture
def scaffold_calls(monkeypatch) -> list[tuple[Path, str]]:
    """Record ``(target, name)`` a run decided on, without touching disk."""
    seen: list[tuple[Path, str]] = []

    def spy(space_path, name, owner="", *, dry_run=False):
        seen.append((Path(space_path), name))
        return {"created": [], "skipped": [], "conflicts": [], "migrated": []}

    monkeypatch.setattr(space_cmd, "_scaffold_space_skeleton", spy)
    return seen


def _init(*args: str):
    return _runner.invoke(space_cmd.space_app, ["init", *args, "--dry-run"])


def _flat(output: str) -> str:
    """Output with Rich's line-wrapping undone.

    The refusal line carries the full target path, and the captured console wraps
    at 80 columns — with xdist's `popen-gwN` segment and a `.dev/worktrees/<slug>/`
    checkout the path is long enough that the wrap landed between "home" and
    "directory", so `"home directory" in result.output` failed under `-n auto` and
    passed serially. Not a flake: a whitespace-sensitive assertion over wrapped text.
    """
    return " ".join(output.split())


# ── the four cases in the table ──────────────────────────────────────────────


def test_no_arguments_uses_the_folder_for_both(in_folder, scaffold_calls) -> None:
    standing = in_folder("getbossed")
    result = _init()
    assert result.exit_code == 0, result.output
    assert scaffold_calls == [(standing, "getbossed")]


def test_a_name_alone_still_lands_in_the_spaces_root(in_folder, scaffold_calls) -> None:
    """The historical destination is unchanged — this is the back-compat case."""
    in_folder("getbossed")
    result = _init("foo")
    assert result.exit_code == 0, result.output
    assert scaffold_calls == [(space_cmd._spaces_dir(create=False) / "foo", "foo")]


def test_a_path_alone_takes_its_name_from_that_folder(
    in_folder, scaffold_calls, tmp_path: Path
) -> None:
    in_folder("getbossed")
    target = tmp_path / "work"
    target.mkdir()
    result = _init("--path", str(target))
    assert result.exit_code == 0, result.output
    assert scaffold_calls == [(target.resolve(), "work")]


def test_both_given_are_both_honoured(in_folder, scaffold_calls) -> None:
    standing = in_folder("getbossed")
    result = _init("foo", "--path", ".")
    assert result.exit_code == 0, result.output
    assert scaffold_calls == [(standing, "foo")]


# ── deriving the name ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("getbossed", "getbossed"),
        ("My Project", "my-project"),
        ("My Project (2026)", "my-project-2026"),
        ("navig_core", "navig-core"),
        ("--weird--", "weird"),
        ("UPPER", "upper"),
        ("a.b.c", "a-b-c"),
    ],
)
def test_a_folder_name_becomes_a_slug(in_folder, scaffold_calls, folder, expected) -> None:
    standing = in_folder(folder)
    result = _init()
    assert result.exit_code == 0, result.output
    assert scaffold_calls == [(standing, expected)]


def test_an_over_long_folder_name_is_trimmed_on_a_hyphen(in_folder, scaffold_calls) -> None:
    """_SLUG_RE caps at 30 chars; the trim must not leave a dangling hyphen."""
    standing = in_folder("a-very-long-project-name-that-keeps-going-forever")
    result = _init()
    assert result.exit_code == 0, result.output
    (_target, name) = scaffold_calls[0]
    assert len(name) <= 30
    assert not name.endswith("-")
    assert space_cmd._SLUG_RE.match(name), name
    assert scaffold_calls == [(standing, name)]


def test_a_folder_with_no_usable_characters_asks_for_a_name(
    in_folder, scaffold_calls
) -> None:
    """Never invent a name — say so and exit non-zero."""
    in_folder("___")
    result = _init()
    assert result.exit_code == 1
    assert "Cannot derive a space name" in result.output
    assert scaffold_calls == []  # nothing was scaffolded


def test_an_explicit_name_is_still_validated_not_slugified(in_folder, scaffold_calls) -> None:
    """A typed name is a contract; silently rewriting it would hide a typo."""
    in_folder("getbossed")
    result = _init("My Project")
    assert result.exit_code != 0
    assert scaffold_calls == []


# ── refusing an INFERRED target ──────────────────────────────────────────────


def test_an_inferred_home_directory_is_refused(monkeypatch, scaffold_calls, tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(home))
    monkeypatch.chdir(tmp_path)
    result = _init()
    assert result.exit_code == 1
    assert "home directory" in _flat(result.output)
    assert scaffold_calls == []


def test_an_inferred_filesystem_root_is_refused(monkeypatch, scaffold_calls, tmp_path) -> None:
    root = Path(tmp_path.anchor)
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(root))
    monkeypatch.chdir(tmp_path)
    result = _init()
    assert result.exit_code == 1
    assert "filesystem root" in _flat(result.output)
    assert scaffold_calls == []


def test_an_EXPLICIT_home_path_is_obeyed(monkeypatch, scaffold_calls, tmp_path) -> None:
    """The guard covers what navig CHOSE, never what the operator typed."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    result = _init("--path", str(home))
    assert result.exit_code == 0, result.output
    assert scaffold_calls == [(home.resolve(), "home")]


# ── the registry classification that keys off the result ─────────────────────


def test_an_inferred_space_registers_as_external_not_root(
    in_folder, monkeypatch, tmp_path: Path
) -> None:
    """`source` must follow where the space LANDED, not whether --path was typed.

    A bare `navig space init` passes no --path yet lands in the operator's own folder,
    which is external. Keying off the flag filed it as a `~/.navig/spaces` resident.
    """
    standing = in_folder("getbossed")
    monkeypatch.setattr(
        space_cmd,
        "_scaffold_space_skeleton",
        lambda *a, **k: {"created": [], "skipped": [], "conflicts": [], "migrated": []},
    )
    monkeypatch.setattr(space_cmd, "_link_space_roots", lambda p: [])
    monkeypatch.setattr(space_cmd, "_link_space_capabilities", lambda p: [])
    monkeypatch.setattr(space_cmd, "_spaces_dir", lambda create=True: tmp_path / "spaces")

    recorded: list[dict] = []
    monkeypatch.setattr(
        "navig.spaces.registry.register",
        lambda path, **kw: recorded.append({"path": Path(path), **kw}),
    )

    result = _runner.invoke(space_cmd.space_app, ["init"])
    assert result.exit_code == 0, result.output
    assert recorded and recorded[0]["path"] == standing
    assert recorded[0]["source"] == "external"


def test_a_named_space_in_the_spaces_root_still_registers_as_root(
    in_folder, monkeypatch, tmp_path: Path
) -> None:
    in_folder("getbossed")
    spaces = tmp_path / "spaces"
    spaces.mkdir()
    monkeypatch.setattr(
        space_cmd,
        "_scaffold_space_skeleton",
        lambda *a, **k: {"created": [], "skipped": [], "conflicts": [], "migrated": []},
    )
    monkeypatch.setattr(space_cmd, "_link_space_roots", lambda p: [])
    monkeypatch.setattr(space_cmd, "_link_space_capabilities", lambda p: [])
    monkeypatch.setattr(space_cmd, "_spaces_dir", lambda create=True: spaces)
    # `source` is decided by navig.spaces.registry.source_for, which reads the platform
    # spaces_dir — the same directory in production, a separate seam in a test.
    monkeypatch.setattr("navig.spaces.registry.paths.spaces_dir", lambda: spaces)

    recorded: list[dict] = []
    monkeypatch.setattr(
        "navig.spaces.registry.register",
        lambda path, **kw: recorded.append({"path": Path(path), **kw}),
    )

    result = _runner.invoke(space_cmd.space_app, ["init", "foo"])
    assert result.exit_code == 0, result.output
    assert recorded and recorded[0]["source"] == "root"


# ── the aliases carry the same behaviour ─────────────────────────────────────


@pytest.mark.parametrize("verb", ["init", "new", "create"])
def test_every_alias_defaults_the_same_way(in_folder, scaffold_calls, verb) -> None:
    standing = in_folder("getbossed")
    result = _runner.invoke(space_cmd.space_app, [verb, "--dry-run"])
    assert result.exit_code == 0, result.output
    assert scaffold_calls == [(standing, "getbossed")]


# ── the headline says what the folder WAS ───────────────────────────────────


@pytest.fixture
def real_scaffold(monkeypatch, tmp_path: Path):
    """Let the real scaffold run (into tmp) but keep links/registry out of it."""
    monkeypatch.setattr(space_cmd, "_link_space_roots", lambda p: [])
    monkeypatch.setattr(space_cmd, "_link_space_capabilities", lambda p: [])
    monkeypatch.setattr(space_cmd, "_spaces_dir", lambda create=True: tmp_path / "spaces")
    monkeypatch.setattr("navig.spaces.registry.register", lambda *a, **k: None)


def test_an_empty_folder_reads_as_created(in_folder, real_scaffold) -> None:
    in_folder("fresh")
    r = _runner.invoke(space_cmd.space_app, ["init"])
    assert r.exit_code == 0, r.output
    assert "Created space 'fresh'" in r.output


def test_a_non_empty_project_reads_as_turned_into_a_space_not_existing_space(
    in_folder, real_scaffold
) -> None:
    """A README and a .git made a project print "existing space" — it never was one."""
    standing = in_folder("proj")
    (standing / "README.md").write_text("# p\n", encoding="utf-8")
    r = _runner.invoke(space_cmd.space_app, ["init"])
    assert r.exit_code == 0, r.output
    assert "Turned existing folder into space 'proj'" in r.output
    assert "existing space" not in r.output


def test_a_folder_that_already_is_a_space_reads_as_existing_space(in_folder, real_scaffold) -> None:
    standing = in_folder("again")
    (standing / ".navig").mkdir()
    r = _runner.invoke(space_cmd.space_app, ["init"])
    assert r.exit_code == 0, r.output
    assert "Initialized structure in existing space 'again'" in r.output
