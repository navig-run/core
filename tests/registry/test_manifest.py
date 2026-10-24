"""
Tests for navig.registry.manifest — pure helper functions.
"""

import pytest

from navig.registry.manifest import (
    _entry_from_command,
    _extract_arguments,
    _extract_options,
    _first_line,
    _prefer_new_entry,
    build_public_manifest,
    deprecations_report,
    render_markdown,
    topic_index_from_manifest,
    validate_manifest,
)

# ---------------------------------------------------------------------------
# _first_line
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        (None, ""),
        ("", ""),
        ("single line", "single line"),
        ("first\nsecond\nthird", "first"),
        ("  leading spaces  \nsecond", "leading spaces"),
        ("\nblank first\nsecond", "blank first"),
    ],
)
def test_first_line(text, expected):
    assert _first_line(text) == expected


# ---------------------------------------------------------------------------
# _prefer_new_entry
# ---------------------------------------------------------------------------


def test_prefer_new_entry_explicit_meta_wins():
    new = {"_has_explicit_meta": True, "summary": "short"}
    current = {"_has_explicit_meta": False, "summary": "much longer summary here"}
    assert _prefer_new_entry(new, current) is True


def test_prefer_new_entry_both_explicit_longer_summary_wins():
    new = {"_has_explicit_meta": True, "summary": "x" * 50}
    current = {"_has_explicit_meta": True, "summary": "x" * 30}
    assert _prefer_new_entry(new, current) is True


def test_prefer_new_entry_both_explicit_shorter_summary_loses():
    new = {"_has_explicit_meta": True, "summary": "short"}
    current = {"_has_explicit_meta": True, "summary": "much longer summary"}
    assert _prefer_new_entry(new, current) is False


def test_prefer_new_entry_no_explicit_meta_over_explicit_returns_false():
    new = {"_has_explicit_meta": False, "summary": "x" * 100}
    current = {"_has_explicit_meta": True, "summary": "short"}
    assert _prefer_new_entry(new, current) is False


# ---------------------------------------------------------------------------
# `since` is authoritative-or-empty — never the build version
#
# Regression: a meta-less command used to be stamped with the current
# navig.__version__, so `generated/commands.json` churned `since` on ~1300 rows
# every version bump and the CI freshness gate (git diff --exit-code) was
# permanently red — it could only pass on a manifest regenerated at the exact
# committed version. A meta-less command's `since` must be "" and STABLE.
# ---------------------------------------------------------------------------


def _fake_command(handler_name: str, path: str):
    """A Typer-command dict shaped like _iter_typer_commands yields, with no meta."""
    def cb():  # a plain callback carries no @command_meta
        """Does a thing."""

    cb.__name__ = handler_name
    cb.__qualname__ = f"tests.{handler_name}"
    cb.__module__ = "tests.registry.fake"
    return {"path": path, "callback": cb, "help": None}


def test_meta_less_command_since_is_empty_not_build_version():
    entry = _entry_from_command(_fake_command("do_thing", "navig fake do-thing"))
    assert entry["since"] == "", (
        "a command with no @command_meta must have since='' — stamping the build "
        "version is the bug that kept the manifest freshness gate red"
    )


def test_meta_less_since_is_stable_when_navig_version_changes(monkeypatch):
    """The whole point: the value must not move when __version__ moves."""
    import navig

    monkeypatch.setattr(navig, "__version__", "9.9.9", raising=False)
    entry = _entry_from_command(_fake_command("do_thing", "navig fake do-thing"))
    assert entry["since"] == "", "since must be independent of navig.__version__"


def test_real_manifest_carries_no_build_version_in_since():
    """The committed manifest must never stamp since with the running version."""
    import navig

    manifest = build_public_manifest(validate=False)
    polluted = [c["path"] for c in manifest["commands"] if c.get("since") == navig.__version__]
    assert not polluted, (
        f"{len(polluted)} command(s) have since == the current version "
        f"({navig.__version__}) — the __version__ fallback has returned. "
        f"First few: {polluted[:5]}"
    )


def test_explicit_meta_since_is_preserved():
    """A declared since (e.g. @command_meta(since='2.4.18')) must survive untouched."""
    manifest = build_public_manifest(validate=False)
    by_path = {c["path"]: c for c in manifest["commands"]}
    # navig db query declares since='2.4.18' in navig/commands/db.py
    row = by_path.get("navig db query")
    if row is not None:
        assert row["since"] == "2.4.18"


# ---------------------------------------------------------------------------
# validate_manifest — happy path and specific error conditions
# ---------------------------------------------------------------------------


def _valid_command(**kwargs) -> dict:
    base = {
        "path": "navig test cmd",
        "summary": "A test command",
        "module": "navig.commands.test",
        "handler": "test_handler",
        "status": "stable",
        "since": "1.0.0",
        "aliases": [],
        "tags": [],
        "examples": ["navig test cmd"],
        "arguments": [],
        "options": [],
    }
    base.update(kwargs)
    return base


def test_validate_manifest_passes_valid():
    manifest = {
        "commands": [_valid_command()],
        "generated_at": "2024-01-01T00:00:00",
        "total": 1,
        "schema_version": "1.0.0",
    }
    validate_manifest(manifest)  # should not raise


def test_validate_manifest_raises_on_missing_field():
    cmd = _valid_command()
    del cmd["summary"]
    with pytest.raises(ValueError, match="missing required field 'summary'"):
        validate_manifest({"commands": [cmd]})


def test_validate_manifest_raises_on_invalid_status():
    cmd = _valid_command(status="legendary")
    with pytest.raises(ValueError, match="invalid status"):
        validate_manifest({"commands": [cmd]})


def test_validate_manifest_raises_on_empty_summary():
    cmd = _valid_command(summary="")
    with pytest.raises(ValueError, match="summary must not be empty"):
        validate_manifest({"commands": [cmd]})


def test_validate_manifest_raises_on_too_long_summary():
    cmd = _valid_command(summary="x" * 101)
    with pytest.raises(ValueError, match="summary exceeds 100 chars"):
        validate_manifest({"commands": [cmd]})


def test_validate_manifest_raises_on_empty_examples():
    cmd = _valid_command(examples=[])
    with pytest.raises(ValueError, match="examples must be a non-empty list"):
        validate_manifest({"commands": [cmd]})


def test_validate_manifest_deprecated_block_required():
    cmd = _valid_command(status="deprecated")
    with pytest.raises(ValueError, match="deprecated command must include deprecated block"):
        validate_manifest({"commands": [cmd]})


def test_validate_manifest_deprecated_fields_required():
    cmd = _valid_command(
        status="deprecated",
        deprecated={
            "since": "1.0.0",
            "remove_after": "2.0.0",
            "replaced_by": "navig new cmd",
            "note": "Use the new cmd instead.",
        },
    )
    validate_manifest({"commands": [cmd]})  # should not raise


def test_validate_manifest_deprecated_missing_note():
    cmd = _valid_command(
        status="deprecated",
        deprecated={
            "since": "1.0.0",
            "remove_after": "2.0.0",
            "replaced_by": "navig new cmd",
            "note": "",  # empty
        },
    )
    with pytest.raises(ValueError, match="deprecated.note is required"):
        validate_manifest({"commands": [cmd]})


def test_validate_manifest_commands_not_list_raises():
    with pytest.raises(ValueError, match="commands must be a list"):
        validate_manifest({"commands": "not a list"})


# ---------------------------------------------------------------------------
# deprecations_report
# ---------------------------------------------------------------------------


def test_deprecations_report_empty():
    manifest = {"commands": [_valid_command()], "generated_at": "now"}
    report = deprecations_report(manifest, include_superseded=False)
    assert report["total_deprecated"] == 0
    assert report["deprecated_commands"] == []


def test_deprecations_report_includes_deprecated():
    cmd = _valid_command(
        status="deprecated",
        deprecated={
            "since": "1.5.0",
            "remove_after": "2.0.0",
            "replaced_by": "navig new",
            "note": "switch to new",
        },
    )
    manifest = {"commands": [cmd], "generated_at": "now"}
    report = deprecations_report(manifest, include_superseded=False)
    assert report["total_deprecated"] == 1
    row = report["deprecated_commands"][0]
    assert row["path"] == "navig test cmd"
    assert row["since"] == "1.5.0"


# ---------------------------------------------------------------------------
# render_markdown
# ---------------------------------------------------------------------------


def test_render_markdown_headers():
    manifest = {"commands": [_valid_command()], "generated_at": "2024-01-01"}
    md = render_markdown(manifest)
    assert "# NAVIG Command Reference" in md
    assert "## `navig test cmd`" in md
    assert "A test command" in md


def test_render_markdown_deprecated_block():
    cmd = _valid_command(
        status="deprecated",
        deprecated={
            "since": "1.5.0",
            "remove_after": "2.0.0",
            "replaced_by": "navig new cmd",
            "note": "switch",
        },
    )
    md = render_markdown({"commands": [cmd], "generated_at": ""})
    assert "Deprecated" in md
    assert "navig new cmd" in md


def test_render_markdown_examples():
    cmd = _valid_command(examples=["navig test cmd --help"])
    md = render_markdown({"commands": [cmd], "generated_at": ""})
    assert "navig test cmd --help" in md
    assert "```sh" in md


# ---------------------------------------------------------------------------
# topic_index_from_manifest
# ---------------------------------------------------------------------------


def test_topic_index_groups_by_second_word():
    commands = [
        _valid_command(path="navig db list"),
        _valid_command(path="navig db dump"),
        _valid_command(path="navig host list"),
    ]
    index = topic_index_from_manifest({"commands": commands})
    assert "db" in index
    assert len(index["db"]) == 2
    assert "host" in index
    assert len(index["host"]) == 1


def test_topic_index_sorted():
    commands = [
        _valid_command(path="navig web reload"),
        _valid_command(path="navig db list"),
        _valid_command(path="navig app use"),
    ]
    index = topic_index_from_manifest({"commands": commands})
    keys = list(index.keys())
    assert keys == sorted(keys)


def test_topic_index_skips_short_paths():
    commands = [
        _valid_command(path="navig"),  # only 1 part
        _valid_command(path="db list"),  # doesn't start with navig
    ]
    index = topic_index_from_manifest({"commands": commands})
    assert index == {}


# ---------------------------------------------------------------------------
# _extract_arguments — positional argument signatures for usage hints
# ---------------------------------------------------------------------------


def test_extract_arguments_reads_typer_arguments():
    import typer

    def handler(
        name: str = typer.Argument(..., help="required"),
        note: str = typer.Argument("", help="optional"),
        verbose: bool = typer.Option(False, "--verbose"),  # an Option, not positional
    ):
        pass

    args = _extract_arguments(handler)
    assert args == [
        {"name": "name", "required": True, "variadic": False},
        {"name": "note", "required": False, "variadic": False},
    ]  # the Option is excluded


def test_extract_arguments_marks_variadic_list():
    import typer

    def handler(paths: list[str] = typer.Argument(...)):
        pass

    args = _extract_arguments(handler)
    assert args == [{"name": "paths", "required": True, "variadic": True}]


def test_extract_arguments_skips_context_and_underscores_to_dashes():
    import typer

    def handler(
        ctx: typer.Context,  # not an argument or option — skipped
        remote_path: str = typer.Argument(...),
    ):
        pass

    args = _extract_arguments(handler)
    assert args == [{"name": "remote-path", "required": True, "variadic": False}]


def test_extract_arguments_empty_for_no_positional_args():
    import typer

    def handler(flag: bool = typer.Option(False)):
        pass

    assert _extract_arguments(handler) == []


def test_extract_arguments_never_raises_on_unsignaturable():
    # A C builtin has no inspectable signature — must degrade to [] not raise.
    assert _extract_arguments(len) == []


def test_validate_manifest_requires_arguments_field():
    bad = _valid_command()
    del bad["arguments"]
    with pytest.raises(ValueError, match="arguments"):
        validate_manifest({"commands": [bad], "generated_at": "x", "total": 1})


# ---------------------------------------------------------------------------
# _extract_options — option flags for the command schema
# ---------------------------------------------------------------------------


def test_extract_options_reads_flags_and_value_kind():
    import typer

    def handler(
        name: str = typer.Argument(...),  # positional — not an option
        host: str = typer.Option("", "--host", "-h"),  # takes a value
        verbose: bool = typer.Option(False, "--verbose"),  # boolean switch
    ):
        pass

    opts = _extract_options(handler)
    assert opts == [
        {"flags": ["--host", "-h"], "takes_value": True},
        {"flags": ["--verbose"], "takes_value": False},
    ]  # the positional Argument is excluded


def test_extract_options_falls_back_to_param_name_flag():
    import typer

    def handler(dry_run: bool = typer.Option(False)):  # no explicit flag declared
        pass

    # With no declared flag Typer generates the pair, and the CLI really accepts both.
    assert _extract_options(handler) == [{"flags": ["--dry-run/--no-dry-run"], "takes_value": False}]


def test_extract_options_empty_for_no_options():
    import typer

    def handler(name: str = typer.Argument(...)):
        pass

    assert _extract_options(handler) == []


def test_extract_options_never_raises_on_unsignaturable():
    assert _extract_options(len) == []


def test_validate_manifest_requires_options_field():
    bad = _valid_command()
    del bad["options"]
    with pytest.raises(ValueError, match="options"):
        validate_manifest({"commands": [bad], "generated_at": "x", "total": 1})


def test_validate_manifest_rejects_option_without_flags():
    bad = _valid_command(options=[{"takes_value": True}])  # missing 'flags'
    with pytest.raises(ValueError, match="flags"):
        validate_manifest({"commands": [bad], "generated_at": "x", "total": 1})


# ── the shapes the signature reader got wrong (730 wrong / 863 missing options) ─────────────
#
# Defined in a module with `from __future__ import annotations` semantics by building the
# handler from source: every annotation is then a STRING, which is what most navig command
# modules (98 of them) and most plugins look like.

_FUTURE_SRC = '''
from __future__ import annotations

from typing import Annotated, Optional

import typer


def handler(
    target: str,
    files: Annotated[list[str], typer.Argument(help="files")] = None,
    play: bool = typer.Option(True, "--play/--no-play", "-p"),
    json_out: Annotated[bool, typer.Option("--json")] = False,
    maybe: Optional[bool] = typer.Option(None, "--maybe"),
    verbose: Annotated[int, typer.Option("-v", count=True)] = 0,
    tag: Annotated[Optional[str], typer.Option("--tag", "-t")] = None,
    secret: Annotated[str, typer.Option(hidden=True)] = "",
):
    pass
'''


def _future_handler():
    ns: dict = {}
    exec(compile(_FUTURE_SRC, "<future-annotations>", "exec"), ns)  # noqa: S102 - test fixture
    return ns["handler"]


def test_options_under_future_annotations_and_annotated_are_all_read():
    opts = _extract_options(_future_handler())
    assert opts == [
        {"flags": ["--play/--no-play", "-p"], "takes_value": False},  # string "bool" annotation
        {"flags": ["--json"], "takes_value": False},  # Annotated — was missing entirely
        {"flags": ["--maybe"], "takes_value": False},  # Optional[bool] is still a switch
        {"flags": ["-v"], "takes_value": False},  # a counter takes no value
        {"flags": ["--tag", "-t"], "takes_value": True},
    ]  # the hidden option is not advertised


def test_arguments_under_future_annotations_and_annotated_are_all_read():
    assert _extract_arguments(_future_handler()) == [
        {"name": "target", "required": True, "variadic": False},  # a bare parameter is positional
        {"name": "files", "required": False, "variadic": True},  # list[...] under a string annotation
    ]


def test_deprecations_report_includes_the_superseded_table_by_default():
    """Superseded verbs are hidden, so never IN the public manifest — the code table is
    the only way the committed report can name them (navig.cli.superseded)."""
    from navig.cli.superseded import SUPERSEDED

    report = deprecations_report({"commands": [_valid_command()], "generated_at": "now"})
    paths = {row["path"] for row in report["deprecated_commands"]}
    assert {s.path for s in SUPERSEDED} <= paths
    assert report["total_deprecated"] == len(SUPERSEDED)


# ---------------------------------------------------------------------------
# PluginManager mounts — the other half of the command surface
# ---------------------------------------------------------------------------


def test_manifest_lists_the_plugin_manager_mounted_commands():
    """`navig mini …` is registered by the CLI yet never reached the shipped manifest.

    `_register_external_commands` walks only the CLI app's own sub-apps; a plugin app
    mounted by `navig.main.load_plugins_into_app` (the builtin plugin host, which is how
    `navig mini` exists) goes through a different seam, so completion, the markdown
    reference and the deck CLI schema documented none of it. Asserted against a real
    build rather than a fixture, because that is the shape the exporter ships.
    """
    manifest = build_public_manifest(validate=False)
    by_path = {c["path"]: c for c in manifest["commands"]}

    assert "navig mini status" in by_path, (
        "navig mini status is registered by the builtin plugin host but is missing from "
        "the manifest — the exporter no longer enumerates PluginManager-mounted apps"
    )
    assert by_path["navig mini status"]["module"] == "navig.builtins.mini_control.plugin"
    # The group's own description, not the first command's summary.
    assert manifest["groups"].get("mini", "").startswith("Control remote NAVIG Mini")


def _fake_plugin_manager(apps, *, source="builtin"):
    """A PluginManager stand-in: the host contract `_plugin_mount` actually uses."""
    from pathlib import Path

    from navig.plugins import PluginInfo

    class _Manager:
        def discover_plugins(self):
            return {
                name: PluginInfo(name=name, path=Path("fake") / name, source=source)
                for name in apps
            }

        def load_all_plugins(self, silent=False):
            return (list(apps), [])

        def get_loaded_apps(self):
            return dict(apps)

    return _Manager()


def test_plugin_mount_yields_the_paths_the_runtime_mounts(monkeypatch):
    """A fake plugin app must appear under `navig <name> …`, exactly as a real one does."""
    import typer

    from navig.registry.manifest import _iter_typer_commands, _plugin_mount

    def hello() -> None:
        """Say hello."""

    app = typer.Typer(name="faketest", help="A plugin app the host loaded.")
    app.command("hello")(hello)

    monkeypatch.setattr(
        "navig.plugins.get_plugin_manager",
        lambda: _fake_plugin_manager({"faketest": app}),
    )
    mount = _plugin_mount()

    assert mount is not None
    paths = {c["path"] for c in _iter_typer_commands(mount, ["navig"])}
    assert "navig faketest hello" in paths


def test_plugin_mount_never_mutates_the_live_cli_app(monkeypatch):
    """Building a manifest is a read. Mounting on `navig.cli.app` would make it a write.

    The runtime mounts once, in `navig.main`; if the exporter did the same, a second build
    in one process would register the group twice, and anything importing the CLI app after
    an export would see commands the caller never asked for.
    """
    import typer

    import navig.cli as cli_mod
    from navig.registry.manifest import _plugin_mount

    def hello() -> None:
        """Say hello."""

    app = typer.Typer(name="faketest", help="A plugin app the host loaded.")
    app.command("hello")(hello)

    before = len(cli_mod.app.registered_groups)
    monkeypatch.setattr(
        "navig.plugins.get_plugin_manager",
        lambda: _fake_plugin_manager({"faketest": app}),
    )
    assert _plugin_mount() is not None
    _plugin_mount()  # a second build in the same process
    assert len(cli_mod.app.registered_groups) == before


def test_plugin_mount_degrades_to_none_when_the_host_is_broken(monkeypatch):
    """A plugin host that cannot be consulted must not take the whole export down.

    Pinned deliberately: the shipped manifest then simply lacks plugin commands, which the
    freshness gate reports as `removed`/core drift rather than as a crash here.
    """
    from navig.registry.manifest import _plugin_mount

    def _boom():
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("navig.plugins.get_plugin_manager", _boom)
    assert _plugin_mount() is None


def test_plugin_mount_excludes_the_operators_own_plugins(monkeypatch):
    """A SHIPPED artifact documents only commands a navig install contains.

    The host also discovers `~/.navig/plugins` and `<space>/.navig/plugins`. Mounting
    those would bake a private, unshipped command into `generated/commands.json` on the
    next regen — the phantom-command class (a row whose code the repo does not have),
    which the freshness gate can only report as an out-of-tree NOTE, never refuse.
    """
    import typer

    from navig.registry.manifest import _plugin_mount

    def hello() -> None:
        """Say hello."""

    app = typer.Typer(name="faketest", help="A plugin app the host loaded.")
    app.command("hello")(hello)

    monkeypatch.setattr(
        "navig.plugins.get_plugin_manager",
        lambda: _fake_plugin_manager({"faketest": app}, source="user"),
    )
    assert _plugin_mount() is None


# ── runnable top-level verbs ─────────────────────────────────────────────────


def test_runnable_top_level_verbs_are_in_the_manifest() -> None:
    """`navig <verb>` that runs bare must be documentable — doctor wasn't.

    Two halves, because there were two failures: the walk yields a group's SUBCOMMANDS
    but never the group's own callback (so `navig doctor` was runnable for years while
    absent from commands.md — `help --schema` and the website's /commands page had no
    page for the most-used verb), and the shipped commands.md must document what the
    public manifest produces (which forces a regen in the same change rather than a
    silently stale file).
    """
    import re
    from pathlib import Path

    from typer.main import get_command

    import navig.cli as cli_mod
    from navig.registry.manifest import _build_manifest

    cli_mod._register_external_commands(register_all=True)
    runtime = {
        name
        for name, cmd in get_command(cli_mod.app).commands.items()
        if cmd.callback is not None
    }
    fresh = {c["path"] for c in _build_manifest(include_hidden=True)["commands"]}
    missing = sorted(f"navig {n}" for n in runtime if f"navig {n}" not in fresh)
    assert not missing, f"the manifest walk skips runnable top-level verbs: {missing}"

    import importlib.util

    manifest = build_public_manifest(validate=False)["commands"]
    public = {c["path"] for c in manifest}
    tree_root = Path(__file__).resolve().parents[3]

    def _shipped_by_this_tree(module: str | None) -> bool:
        """Is *module* part of the tree under test?

        A machine-level plugin (an editable install from another checkout) appears in
        the runtime CLI, but its commands are not this tree's to document in
        commands.md; requiring them would make the gate depend on whatever happens to
        be installed beside the tree.
        """
        if not module:
            return True
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, ValueError):
            return False
        origin = getattr(spec, "origin", None)
        if not origin:
            return False
        try:
            Path(origin).resolve().relative_to(tree_root)
        except ValueError:
            return False
        return True

    modules = {c["path"]: c.get("module") for c in manifest}
    bare = {
        p for p in public if len(p.split()) == 2 and _shipped_by_this_tree(modules.get(p))
    }
    md_path = Path(__file__).resolve().parents[2] / "generated" / "commands.md"
    documented = set(re.findall(r"^## `(navig [^`]+)`$", md_path.read_text(encoding="utf-8"), re.M))
    stale = sorted(bare - documented)
    assert not stale, f"commands.md lacks {stale} — regenerate core/generated"
