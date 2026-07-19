"""
Tests for navig.registry.manifest — pure helper functions.
"""

import pytest

from navig.registry.manifest import (
    _extract_arguments,
    _extract_options,
    _first_line,
    _prefer_new_entry,
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
    report = deprecations_report(manifest)
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
    report = deprecations_report(manifest)
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

    assert _extract_options(handler) == [{"flags": ["--dry-run"], "takes_value": False}]


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
