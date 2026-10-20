"""`navig skill` command parsing: the shorthand form must not look harmless.

Two bugs lived in the shorthand `navig-commands: ["navig docker logs {container}"]` form:

  * the NAME was the last token, so `logs {container}` and `restart {container}` were both
    called `{container}` — indistinguishable in `skill show`, unaddressable by `skill run`;
  * the RISK was hard-coded to `safe`, so `skill run docker-manage:restart` skipped the
    confirmation a restart should ask for. Risk is now inferred from the verbs, the same
    principle Blocks enforce (what the command does outranks what the manifest claims).
"""

from __future__ import annotations

import pytest

from navig.commands.skills import infer_command_risk


@pytest.mark.parametrize(
    ("syntax", "expected"),
    [
        ("navig docker ps", "safe"),
        ("navig docker logs {container}", "safe"),
        ("navig docker stats", "safe"),
        ("navig docker restart {container}", "destructive"),
        ("navig docker stop {container}", "destructive"),
        ("navig run 'rm -rf /tmp/x'", "destructive"),
        ("navig systemctl restart nginx", "destructive"),
        ("navig deploy api", "moderate"),
        ("navig sync", "moderate"),
        ("navig skill list", "safe"),
    ],
)
def test_risk_is_inferred_from_the_verb(syntax: str, expected: str) -> None:
    assert infer_command_risk(syntax) == expected


def test_empty_syntax_is_safe() -> None:
    assert infer_command_risk("") == "safe"
