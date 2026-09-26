"""`navig webhook` cannot work in this install, and must say so rather than send the user off.

Every command in the group talks to a webhook-management API on port 7421 that nothing in
the repository serves. The old failure said "host daemon is not running" and hinted
"Start with: navig gateway start (or the Go host daemon)" -- the gateway serves a different
port and a different route, and the Go daemon was never shipped. The user would start the
gateway, retry, and get the same error.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("args", [["list"], ["add-outbound", "--name", "n", "--url", "https://example.com/hook"]])
def test_the_failure_names_the_real_reason_and_offers_no_false_start(args):
    from navig.commands.webhook import webhook_app

    r = CliRunner().invoke(webhook_app, args, obj={})
    assert r.exit_code == 1, r.output
    assert "not part of this install" in r.output, r.output
    assert "nothing to start" in r.output
    assert "gateway start" not in r.output, "must not send the user to start something that cannot help"
    assert "Go host daemon" not in r.output, "must not name a daemon that was never shipped"
