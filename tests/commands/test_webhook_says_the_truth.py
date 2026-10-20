"""`navig webhook` / `webhooks` must send the user to what works: `navig trigger`.

Every command in the group talked to a webhook-management API on port 7421 that nothing in
the repository has ever served, so every invocation failed on every install. Triggers own
webhooks (a `webhook:<url>` action, a `webhook` trigger type), so each verb now names its
`navig trigger` equivalent and exits 1 — and never sends the user off to start a daemon.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    ("args", "target"),
    [
        (["list"], "navig trigger list"),
        (["add-outbound", "--name", "n", "--url", "https://example.com/hook"],
         "navig trigger add <name> --action webhook:<url>"),
        (["add-inbound", "--name", "n"], "navig trigger add <name> --type webhook"),
        (["disable", "wh_1"], "navig trigger disable <id>"),
        (["delete", "wh_1", "--force"], "navig trigger remove <id>"),
        (["test", "wh_1"], "navig trigger test <id>"),
        ([], "navig trigger"),
    ],
)
def test_every_verb_points_at_trigger_and_exits_1(args, target):
    from navig.commands.webhook import webhook_app

    r = CliRunner().invoke(webhook_app, args, obj={})
    assert r.exit_code == 1, r.output
    assert target in r.output, r.output
    assert "gateway start" not in r.output, "must not send the user to start something that cannot help"
    assert "Go host daemon" not in r.output, "must not name a daemon that was never shipped"
