"""Unlisted from `--help` is NOT the same as "not public API".

`build_public_manifest()` feeds `navig help`, the deck's command schema (`/api/deck/cli`),
navig.run/commands, and every agent that asks navig what commands exist. It derived a
command's status from the Typer `hidden` flag — but that flag was carrying TWO unrelated
meanings at once:

  * "this is a duplicate ALIAS"     → correctly private (the canonical group documents it)
  * "this clutters `navig --help`"  → still very much PUBLIC

Collapsing them silently deleted real, unique commands from every discovery surface:
`navig ai` (18), `navig brain` (which navig-bridge itself shells out to), `navig cost`,
`navig continuation`, `navig hosts`, `navig software`, `navig output-style`, `navig habit`,
`navig life`. An agent asking navig for its own command list could not see them.

These tests pin both directions: real commands are published, aliases are not, and
`navig --help` keeps hiding the union so nothing changes for the human at the terminal.
"""

from __future__ import annotations

import pytest

from navig.cli.registration import (
    _ALIAS_COMMANDS,
    _HIDDEN_COMMANDS,
    _UNLISTED_COMMANDS,
)
from navig.registry.manifest import build_public_manifest


@pytest.fixture(scope="module")
def public_groups() -> dict[str, int]:
    manifest = build_public_manifest(validate=True)
    groups: dict[str, int] = {}
    for cmd in manifest["commands"]:
        parts = str(cmd["path"]).split()
        if len(parts) > 1:
            groups[parts[1]] = groups.get(parts[1], 0) + 1
    return groups


# Real capabilities that no other name serves. Each was invisible before.
@pytest.mark.parametrize(
    "group",
    ["ai", "brain", "cost", "continuation", "hosts", "software", "output-style", "habit", "life"],
)
def test_unlisted_but_real_commands_are_public(group: str, public_groups: dict[str, int]) -> None:
    assert group in public_groups, (
        f"`navig {group}` is a real, unique command but is missing from the public manifest — "
        "so it is invisible to `navig help`, the deck schema, the website, and every agent."
    )
    assert public_groups[group] > 0


@pytest.mark.parametrize("alias", ["tg", "mx", "a", "f", "database", "env", "job", "health", "day", "habits"])
def test_aliases_stay_out_of_the_manifest(alias: str, public_groups: dict[str, int]) -> None:
    """A duplicate name must not be published — the canonical group already documents it.

    `database` is the regression that motivated the alias check being authoritative over
    `CommandMeta`: an alias inherits the canonical command's meta, and `meta.status` used
    to win over the hidden flag, so `navig database list` / `query` shipped as duplicates
    of `navig db list` / `query`.
    """
    assert alias not in public_groups, f"alias `navig {alias}` leaked into the public manifest"


def test_canonical_command_behind_an_alias_is_still_published(public_groups: dict[str, int]) -> None:
    """Excluding the alias must not take the real command down with it."""
    for canonical in ("db", "telegram", "matrix", "config", "vault", "host", "stack"):
        assert canonical in public_groups, f"canonical `navig {canonical}` disappeared"


def test_hidden_set_is_the_union_so_help_output_is_unchanged() -> None:
    """`--help` hides exactly what it hid before: aliases AND unlisted commands."""
    assert _HIDDEN_COMMANDS == _ALIAS_COMMANDS | _UNLISTED_COMMANDS


def test_the_two_sets_are_disjoint() -> None:
    """A name is either a duplicate or a real command — never both."""
    overlap = _ALIAS_COMMANDS & _UNLISTED_COMMANDS
    assert not overlap, f"classified as both alias and real: {sorted(overlap)}"


def test_every_unlisted_name_is_actually_registered() -> None:
    """Guard against a stale entry: a name here that no longer exists silently does nothing."""
    import navig.cli as cli

    cli._register_external_commands(register_all=True)
    registered = {g.name for g in cli.app.registered_groups}
    missing = sorted(n for n in _HIDDEN_COMMANDS if n not in registered)
    assert not missing, f"_HIDDEN_COMMANDS names that are not registered commands: {missing}"


def test_a_hidden_subcommand_inside_a_public_group_stays_hidden() -> None:
    """Unlisting a GROUP must not publish genuinely-internal subcommands inside it.

    `top_hidden` (the group is unlisted from --help) and `nested_hidden` (this command or
    a sub-group is itself internal) are tracked separately for exactly this reason.
    """
    from navig.registry.manifest import _public_status

    internal = {
        "path_parts": ["navig", "ai", "secret-thing"],
        "top_hidden": True,
        "nested_hidden": True,  # the command itself is hidden
    }
    assert _public_status(internal) == "hidden"

    normal = {
        "path_parts": ["navig", "ai", "ask"],
        "top_hidden": True,  # `ai` is unlisted from --help...
        "nested_hidden": False,  # ...but this command is not internal
    }
    assert _public_status(normal) == "stable"
