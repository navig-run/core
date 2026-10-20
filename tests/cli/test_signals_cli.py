"""`navig signals` — the Stripe provider flow and the edit/list surfaces."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

WHSEC = "whsec_cli_test_0123456789abcdefghijk"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    # No Lighthouse in the test config → the CLI falls back to the local URL.
    from navig.commands import signals as cmd

    monkeypatch.setattr(cmd, "_public_ingest_url", lambda _s: None)
    monkeypatch.setattr(cmd, "_local_ingest_url", lambda s: f"http://127.0.0.1:1/api/ingest/{s}")
    return cmd


def _run(cmd, *args):
    return CliRunner().invoke(cmd.signals_app, list(args))


def test_add_stripe_prints_the_events_to_tick_and_never_the_secret(env):
    res = _run(env, "add", "grabnite", "--provider", "stripe", "--secret", WHSEC)
    assert res.exit_code == 0, res.output
    assert "checkout.session.completed" in res.output
    assert WHSEC not in res.output
    assert "Stripe cannot reach a local URL" in res.output


def test_add_stripe_rejects_a_non_whsec_secret(env):
    res = _run(env, "add", "grabnite", "--provider", "stripe", "--secret", "sk_live_nope")
    assert res.exit_code == 1
    assert "whsec_" in res.output


def test_edit_changes_priority_and_requires_a_field(env):
    assert _run(env, "add", "shop").exit_code == 0
    res = _run(env, "edit", "shop", "--priority", "high")
    assert res.exit_code == 0, res.output
    from navig.notify import signals

    assert signals.get_source("shop")["priority"] == "high"
    assert _run(env, "edit", "shop").exit_code == 1
    assert _run(env, "edit", "ghost", "--priority", "low").exit_code == 1


def test_list_json_is_one_parseable_document_with_masked_secrets(env):
    _run(env, "add", "grabnite", "--provider", "stripe", "--secret", WHSEC)
    res = _run(env, "list", "--json")
    assert res.exit_code == 0
    rows = json.loads(res.output)
    assert rows[0]["provider"] == "stripe"
    assert rows[0]["secret"] != WHSEC


def test_rotate_stripe_stores_the_new_secret_without_echoing_it(env):
    _run(env, "add", "grabnite", "--provider", "stripe", "--secret", WHSEC)
    new = "whsec_rolled_cli_0123456789abcdefgh"
    res = _run(env, "rotate", "grabnite", "--secret", new)
    assert res.exit_code == 0, res.output
    assert new not in res.output
    from navig.notify import signals

    assert signals.get_source("grabnite")["secret"] == new


def test_add_stripe_without_secret_prints_the_address_before_asking(env):
    """Stripe shows the whsec_ only after the endpoint exists, and the endpoint
    needs our address — so the address must come BEFORE the secret prompt."""
    res = CliRunner().invoke(
        env.signals_app, ["add", "grabnite", "--provider", "stripe"], input=WHSEC + "\n"
    )
    assert res.exit_code == 0, res.output
    out = res.output
    assert out.index("/api/ingest/grabnite") < out.index("signing secret")
    assert out.count("checkout.session.completed") == 1  # setup printed once, not twice
    from navig.notify import signals

    assert signals.get_source("grabnite")["secret"] == WHSEC


# ── navig signals connect ─────────────────────────────────────────────────────

CREATED = {"status": "created", "endpoint_id": "we_cli_1", "account_id": "acct_cli",
           "account_name": "Gaze", "secret": WHSEC, "livemode": True, "events": 7}


@pytest.fixture
def connect_env(env, monkeypatch):
    """`env` + a public Lighthouse address and a recorded local connect step."""
    monkeypatch.setattr(env, "_public_ingest_url", lambda s: f"https://edge.example/ingest/h/{s}")
    from navig.notify import stripe_connect

    calls: list[tuple] = []
    result = {"value": dict(CREATED)}

    def fake_connect(path, var, url, events, **_kw):
        calls.append((path, var, url, list(events)))
        return dict(result["value"])

    monkeypatch.setattr(stripe_connect, "connect", fake_connect)
    return env, calls, result


def test_connect_creates_the_endpoint_and_stores_the_secret_unseen(connect_env):
    cmd, calls, _ = connect_env
    res = _run(cmd, "connect", "Gaze", "--key", "apps/gaze/.dev.vars::STRIPE_LIVE_SECRET_KEY")
    assert res.exit_code == 0, res.output
    assert WHSEC not in res.output
    assert "we_cli_1" in res.output
    from navig.notify import signals
    from navig.notify.stripe_events import RECOMMENDED_EVENTS

    src = signals.get_source("gaze")
    assert src["provider"] == "stripe" and src["secret"] == WHSEC
    path, var, url, events = calls[0]
    assert path.endswith(".dev.vars") and var == "STRIPE_LIVE_SECRET_KEY"
    assert url == "https://edge.example/ingest/h/gaze"
    assert events == list(RECOMMENDED_EVENTS)


def test_connect_elements_profile_asks_for_payment_intents(connect_env):
    cmd, calls, _ = connect_env
    res = _run(cmd, "connect", "tidymark", "--key", ".dev.vars::K", "--events", "elements")
    assert res.exit_code == 0, res.output
    assert "payment_intent.succeeded" in calls[0][3]


def test_connect_rerun_is_a_no_op_when_already_connected(connect_env):
    cmd, _calls, result = connect_env
    assert _run(cmd, "connect", "gaze", "--key", ".dev.vars::K").exit_code == 0
    result["value"] = {"status": "exists", "endpoint_id": "we_cli_1", "account_id": "acct_cli"}
    res = _run(cmd, "connect", "gaze", "--key", ".dev.vars::K")
    assert res.exit_code == 0, res.output
    assert "already connected" in res.output


def test_connect_will_not_adopt_an_endpoint_it_has_no_secret_for(connect_env):
    cmd, _calls, result = connect_env
    result["value"] = {"status": "exists", "endpoint_id": "we_orphan", "account_id": "acct_cli"}
    res = _run(cmd, "connect", "gaze", "--key", ".dev.vars::K")
    assert res.exit_code == 1
    assert "we_orphan" in res.output and "signals add gaze --provider stripe" in res.output
    from navig.notify import signals

    assert signals.get_source("gaze") is None


def test_connect_reports_a_stripe_error_with_exit_1(connect_env):
    cmd, _calls, result = connect_env
    result["value"] = {"status": "error", "error": "Stripe HTTP 401 Invalid API Key"}
    res = _run(cmd, "connect", "gaze", "--key", ".dev.vars::K")
    assert res.exit_code == 1 and "Invalid API Key" in res.output


@pytest.mark.parametrize("args", [
    ["connect", "gaze", "--key", "no-separator"],
    ["connect", "gaze", "--key", ".dev.vars::K", "--events", "bogus"],
    ["connect", "bad name!", "--key", ".dev.vars::K"],
])
def test_connect_rejects_bad_input_before_touching_stripe(connect_env, args):
    cmd, calls, _ = connect_env
    assert _run(cmd, *args).exit_code == 1
    assert calls == []


def test_connect_without_a_public_address_creates_nothing(connect_env, monkeypatch):
    cmd, calls, _ = connect_env
    monkeypatch.setattr(cmd, "_public_ingest_url", lambda _s: None)
    res = _run(cmd, "connect", "gaze", "--key", ".dev.vars::K")
    assert res.exit_code == 1 and "lighthouse" in res.output.lower()
    assert calls == []


def test_connect_with_host_runs_on_the_host_not_locally(connect_env, monkeypatch):
    cmd, calls, _ = connect_env
    seen: list[tuple] = []

    def on_host(host, env_path, var, url, events):
        seen.append((host, env_path, var))
        return dict(CREATED)

    monkeypatch.setattr(cmd, "_run_connect_on_host", on_host)
    res = _run(cmd, "connect", "schema", "--host", "prod", "--key", "/srv/api/.env::STRIPE_SECRET_KEY")
    assert res.exit_code == 0, res.output
    assert seen == [("prod", "/srv/api/.env", "STRIPE_SECRET_KEY")]
    assert calls == []


def test_connect_json_is_one_document_without_the_secret(connect_env):
    cmd, _calls, _ = connect_env
    res = _run(cmd, "connect", "gaze", "--key", ".dev.vars::K", "--json")
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output)
    assert doc["status"] == "created" and doc["endpoint"] == "we_cli_1"
    assert WHSEC not in res.output
