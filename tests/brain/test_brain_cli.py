"""The ownership CLI: status / claim / release / takeover / verify / migrate / roles.

Drives the real Typer commands through ``CliRunner`` on an isolated config dir. The
contracts pinned here are the ones a script or the OS UI depends on: a stable JSON
envelope, exit code 77 for "this machine may not be the Brain", and no stdout
corruption in ``--json`` mode.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.brain import guard
from navig.brain.identity import installation_fingerprint
from navig.brain.lease import LeaseStore

pytestmark = pytest.mark.integration

DECK_KEY = "navig_cli_test_key_0123456789abcdef"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "cfg"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "config.yaml").write_text(
        f"deck:\n  api_key: {DECK_KEY}\nbrain:\n  role: brain\n", encoding="utf-8"
    )
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "brain")
    monkeypatch.setenv("NAVIG_SKIP_ONBOARDING", "1")
    monkeypatch.delenv("NAVIG_BRAIN_AUTO_PROMOTE", raising=False)
    monkeypatch.delenv("NAVIG_BRAIN_LEASE_PATH", raising=False)
    guard.reset_process_lease()


@pytest.fixture()
def cli():
    import typer

    from navig.commands.brain import brain_app

    app = typer.Typer()
    app.add_typer(brain_app, name="brain")
    return CliRunner(), app


def _json(result) -> dict:
    """Parse the ONE JSON document on stdout — the tripwire this must never violate."""
    assert result.exit_code in (0, 1, 77), result.output
    return json.loads(result.stdout)


# ── status ─────────────────────────────────────────────────────────────────


def test_status_json_is_one_parseable_document(cli) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "status", "--json"])
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["brain"]["state"] == "free"
    assert payload["brain"]["you"]["role"] == "brain"
    assert "telegram" in payload
    # Identity travels as a fingerprint, never as the key.
    assert payload["identity"]["deck_key_fingerprint"] == installation_fingerprint(DECK_KEY)
    # A fresh install must not be warned about a lease it does not have.
    assert payload["brain"]["owner"] is None


def test_status_human_view_is_a_table_with_a_next_step(cli) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "status"])
    assert result.exit_code == 0, result.output
    assert "Lease" in result.output
    assert "Next step" in result.output


# ── claim ──────────────────────────────────────────────────────────────────


def test_claim_then_status_shows_this_machine_as_owner(cli, tmp_path: Path) -> None:
    runner, app = cli
    claimed = runner.invoke(app, ["brain", "claim", "--reason", "test", "--json"])
    payload = _json(claimed)
    assert payload["ok"] is True
    assert payload["lease"]["epoch"] == 1
    assert payload["lease"]["reason"] == "test"

    status = _json(runner.invoke(app, ["brain", "status", "--json"]))
    assert status["brain"]["state"] == "held"
    assert status["brain"]["you"]["is_owner"] is True


def test_claim_refuses_a_client_role_with_exit_1(cli) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "claim", "--role", "client", "--json"])
    assert result.exit_code == 1
    payload = _json(result)
    assert payload["ok"] is False
    assert "client" in payload["error"]


def test_claim_refuses_someone_elses_live_lease_with_exit_77(cli, tmp_path: Path) -> None:
    LeaseStore(tmp_path / "cfg" / "brain" / "lease.json").acquire(
        oid="brn-someoneelse0000", role="brain"
    )
    runner, app = cli
    result = runner.invoke(app, ["brain", "claim", "--json"])
    assert result.exit_code == 77
    payload = _json(result)
    assert payload["ok"] is False
    assert "brn-someoneelse0000" in payload["error"]


# ── release / takeover ─────────────────────────────────────────────────────


def test_release_after_claim_frees_the_lease(cli) -> None:
    runner, app = cli
    runner.invoke(app, ["brain", "claim", "--json"])
    released = runner.invoke(app, ["brain", "release", "--reason", "done", "--json"])
    payload = _json(released)
    assert payload["ok"] is True
    assert payload["lease"]["released_at"] is not None


def test_takeover_requires_a_reason(cli) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "takeover"])
    assert result.exit_code != 0


def test_takeover_of_an_expired_lease_is_audited(cli, tmp_path: Path) -> None:
    store = LeaseStore(tmp_path / "cfg" / "brain" / "lease.json")
    store.acquire(oid="brn-oldowner00000000", role="brain", ttl=30)
    lease = store.read()
    assert lease is not None
    store.path.write_text(
        json.dumps({**lease.to_dict(), "heartbeat_at": time.time() - 600}), encoding="utf-8"
    )

    runner, app = cli
    result = runner.invoke(
        app, ["brain", "takeover", "--from", "brn-oldowner00000000", "--reason", "moved", "--json"]
    )
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["lease"]["epoch"] == 2
    assert payload["lease"]["takeovers"][-1]["reason"] == "moved"


# ── verify ─────────────────────────────────────────────────────────────────


def test_verify_json_carries_capabilities_and_is_honest_about_missing_ones(cli) -> None:
    runner, app = cli
    runner.invoke(app, ["brain", "claim", "--json"])
    result = runner.invoke(app, ["brain", "verify", "--json"])
    payload = _json(result)
    assert set(payload["capabilities"]) == {"telegram", "scheduler", "mail_watcher", "api"}
    # Nothing is running in a test: the API capability must NOT claim success.
    assert payload["capabilities"]["api"]["verified"] is False
    assert payload["ok"] is False
    assert result.exit_code == 1


def test_verify_refuses_when_this_machine_does_not_own_the_brain(
    cli, tmp_path: Path
) -> None:
    LeaseStore(tmp_path / "cfg" / "brain" / "lease.json").acquire(
        oid="brn-otherownermachine", role="brain"
    )
    runner, app = cli
    result = runner.invoke(app, ["brain", "verify", "--json"])
    assert result.exit_code == 77
    payload = _json(result)
    assert payload["ownership"]["allowed"] is False
    assert "brn-otherownermachine" in payload["ownership"]["message"]


# ── migrate ────────────────────────────────────────────────────────────────


def test_migrate_dry_run_lists_eight_stages_and_changes_nothing(cli, tmp_path: Path) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "migrate", "--to", "outpost", "--dry-run", "--json"])
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    names = [s["name"] for s in payload["receipt"]["stages"]]
    assert names == [
        "discover",
        "inspect",
        "backup",
        "transfer",
        "validate",
        "cutover",
        "verify",
        "complete",
    ]
    # Nothing was written: a dry run is a plan.
    assert not (tmp_path / "cfg" / "daemon" / "moved.json").exists()
    assert not (tmp_path / "cfg" / "brain" / "migrations").exists()


def test_migrate_without_a_target_fails_clearly(cli) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "migrate", "--json"])
    assert result.exit_code == 1
    payload = _json(result)
    assert payload["ok"] is False
    assert "--to" in str(payload["error"])


def test_migrate_history_is_empty_and_parseable(cli) -> None:
    runner, app = cli
    payload = _json(runner.invoke(app, ["brain", "migrate", "--history", "--json"]))
    assert payload == {"ok": True, "receipts": []}


# ── telegram ───────────────────────────────────────────────────────────────


def test_telegram_status_is_off_until_a_brain_sets_it(cli) -> None:
    runner, app = cli
    payload = _json(runner.invoke(app, ["brain", "telegram", "status", "--json"]))
    assert payload["telegram"]["mode"] == "off"
    assert payload["telegram"]["owner_id"] == ""


def test_telegram_transition_refuses_a_client_install(cli, monkeypatch) -> None:
    monkeypatch.setenv("NAVIG_BRAIN_ROLE", "client")
    runner, app = cli
    result = runner.invoke(app, ["brain", "telegram", "webhook", "--json"])
    assert result.exit_code == 77
    payload = _json(result)
    assert payload["ok"] is False
    assert "client" in payload["error"]


def test_telegram_transition_refuses_an_unknown_mode(cli) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "telegram", "carrier-pigeon", "--json"])
    assert result.exit_code == 1
    assert _json(result)["ok"] is False


def test_telegram_webhook_drives_the_bot_api_callable(cli, monkeypatch) -> None:
    """A transition must pass the CALLABLE, not the result of calling it.

    Calling it in `_run_transition` produced a TypeError on the first Bot API call
    (or, once wrapped, a silent "did not happen"); the module-attr gate caught the
    call-signature mismatch. This pins the working wiring without a network.
    """
    runner, app = cli
    seen: list[str] = []

    async def fake_api(method: str, params: dict):
        seen.append(method)
        return {"url": "", "pending_update_count": 0}

    monkeypatch.setattr(
        "navig.commands.brain_ownership._cli_telegram_api", fake_api, raising=True
    )
    result = runner.invoke(app, ["brain", "telegram", "webhook", "--json"])
    assert result.exit_code == 0, result.output
    payload = _json(result)
    assert payload["ok"] is True
    assert payload["transition"]["mode"] == "webhook"
    assert seen == ["getWebhookInfo"], seen


# ── roles ──────────────────────────────────────────────────────────────────


def test_roles_json_offers_the_four_choices_with_consequences(cli) -> None:
    runner, app = cli
    payload = _json(runner.invoke(app, ["brain", "roles", "--json"]))
    ids = [choice["id"] for choice in payload["choices"]]
    assert ids == ["all-in-one", "client", "brain", "migrate"]
    for choice in payload["choices"]:
        assert choice["consequence"], choice["id"]
        assert choice["host_dependency"] in ("workstation", "none", "always-on")
    assert payload["context"]["role"] == "brain"
    assert payload["context"]["deck_key_fingerprint"] == installation_fingerprint(DECK_KEY)


def test_roles_human_view_names_the_current_choice(cli) -> None:
    runner, app = cli
    result = runner.invoke(app, ["brain", "roles"])
    assert result.exit_code == 0, result.output
    assert "Run on this computer" in result.output
    assert "Migrate an existing Brain" in result.output
