"""`navig decider` — the switches, the live test, the deck route and the doctor row.

Config writes are captured in a dict (never the session's config file), so no test can
leave the decider switched on for the rest of the run.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.agent import decide
from navig.commands import decider as decider_cmd

runner = CliRunner()


def _status(**over) -> dict:
    base = {
        "enabled": False,
        "model": "clef-flash",
        "shadow": False,
        "redact": True,
        "min_confidence": 0.5,
        "timeout_ms": 1500,
        "daily_cap": 2000,
        "calls_today": 0,
        "usage_today": {},
        "uses": [
            {"id": u.id, "label": u.label, "description": u.description, "on": True, "mode": "on",
             "agreement_7d": {"total": 0, "agree": 0, "abstain": 0}}
            for u in decide.USES.values()
        ],
        "credential": "oauth",
        "account_known": True,
        "last_call": None,
    }
    base.update(over)
    return base


@pytest.fixture
def writes(monkeypatch):
    out: dict[str, object] = {}
    monkeypatch.setattr(decider_cmd, "_set", lambda key, value: out.__setitem__(key, value))
    monkeypatch.setattr(decide, "status", lambda: _status())
    return out


# ── switches ─────────────────────────────────────────────────


def test_on_switches_the_master_on(writes):
    r = runner.invoke(decider_cmd.decider_app, ["on"])
    assert r.exit_code == 0, r.output
    assert writes == {"decide.enabled": True}


def test_on_with_a_prefix_switches_every_matching_use(writes):
    r = runner.invoke(decider_cmd.decider_app, ["on", "--use", "inbox"])
    assert r.exit_code == 0, r.output
    assert writes == {"decide.use.inbox_sort": True, "decide.use.inbox_space": True, "decide.enabled": True}


def test_off_for_one_use_leaves_the_master_alone(writes):
    r = runner.invoke(decider_cmd.decider_app, ["off", "--only", "telegram_triage"])
    assert r.exit_code == 0, r.output
    assert writes == {"decide.use.telegram_triage": False}


def test_a_prefix_matches_every_use_it_starts(writes):
    r = runner.invoke(decider_cmd.decider_app, ["off", "--use", "telegram"])
    assert r.exit_code == 0, r.output
    assert writes == {"decide.use.telegram_triage": False, "decide.use.telegram_brief": False}


def test_off_without_a_use_switches_everything_off(writes):
    assert runner.invoke(decider_cmd.decider_app, ["off"]).exit_code == 0
    assert writes == {"decide.enabled": False}


def test_unknown_use_fails_and_writes_nothing(writes):
    r = runner.invoke(decider_cmd.decider_app, ["on", "--use", "calendar"])
    assert r.exit_code == 1
    assert writes == {}


def test_model_switch_validates(writes):
    assert runner.invoke(decider_cmd.decider_app, ["use", "clef"]).exit_code == 0
    assert writes == {"decide.model": "clef"}
    assert runner.invoke(decider_cmd.decider_app, ["use", "gpt-5"]).exit_code == 1
    assert writes == {"decide.model": "clef"}


def test_shadow_switch(writes):
    assert runner.invoke(decider_cmd.decider_app, ["shadow", "on"]).exit_code == 0
    assert writes == {"decide.shadow": True}
    assert runner.invoke(decider_cmd.decider_app, ["shadow", "maybe"]).exit_code == 1


def test_status_json_is_the_raw_status(writes):
    r = runner.invoke(decider_cmd.decider_app, ["status", "--json"])
    assert r.exit_code == 0
    assert json.loads(r.output)["model"] == "clef-flash"


def test_bare_command_renders_status(writes):
    r = runner.invoke(decider_cmd.decider_app, [])
    assert r.exit_code == 0, r.output
    assert "Decision model (Clef)" in r.output
    assert "telegram_triage" in r.output


# ── test (live call) ─────────────────────────────────────────


def test_test_command_prints_answers(monkeypatch):
    seen = {}

    def fake_call(text, questions, *, model=None, images=None, use=decide.TEST_USE, timeout_s=None):
        seen["ids"] = [q.id for q in questions]
        seen["types"] = [q.type for q in questions]
        return decide.CallResult(
            answers={
                "urgent": decide.Decision("yes", 0.97, {"yes": 0.97, "no": 0.03}),
                "team": decide.Decision("technical", 0.9, {"technical": 0.9, "billing": 0.1}),
            },
            model="clef-flash",
            latency_ms=41,
            input_tokens=60,
        )

    monkeypatch.setattr(decide, "call", fake_call)
    r = runner.invoke(
        decider_cmd.decider_app,
        ["test", "Checkout is down", "--yesno", "urgent=Is it urgent?", "--choice", "team=billing,technical"],
    )
    assert r.exit_code == 0, r.output
    assert seen == {"ids": ["urgent", "team"], "types": ["noul", "choice"]}
    assert "technical" in r.output and "41 ms" in r.output


def test_test_command_fails_honestly(monkeypatch):
    def refused(*a, **k):
        raise decide.DecideError("auth", "Cloudflare refused the call (HTTP 401)", status=401)

    monkeypatch.setattr(decide, "call", refused)
    r = runner.invoke(decider_cmd.decider_app, ["test", "x", "--json"])
    assert r.exit_code == 1
    assert json.loads(r.output) == {"ok": False, "kind": "auth", "error": "Cloudflare refused the call (HTTP 401)"}


def test_test_command_rejects_a_malformed_spec(monkeypatch):
    monkeypatch.setattr(decide, "call", lambda *a, **k: pytest.fail("must not call"))
    r = runner.invoke(decider_cmd.decider_app, ["test", "x", "--choice", "team=onlyone"])
    assert r.exit_code == 1


def test_the_command_group_is_registered():
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    assert _EXTERNAL_CMD_MAP["decider"] == ("navig.commands.decider", "decider_app")


# ── deck route ───────────────────────────────────────────────


class _FakeCM:
    def __init__(self):
        self.writes: dict[str, object] = {}

    def set_global(self, key, value):
        self.writes[key] = value


@pytest.fixture
def cm(monkeypatch):
    fake = _FakeCM()
    import navig.config as config_mod

    monkeypatch.setattr(config_mod, "get_config_manager", lambda *a, **k: fake)
    monkeypatch.setattr(decide, "status", lambda: _status())
    return fake


def test_deck_update_writes_every_valid_field(cm):
    from navig.gateway.deck.routes import decider as route

    out = route._apply({"enabled": "true", "model": "clef", "shadow": False, "uses": {"inbox_sort": False}})
    assert cm.writes == {
        "decide.enabled": True,
        "decide.shadow": False,
        "decide.model": "clef",
        "decide.use.inbox_sort": "off",
    }
    assert out["model"] == "clef-flash"  # the route returns the fresh status


@pytest.mark.parametrize(
    "body",
    [
        {"enabled": True, "model": "gpt-5"},
        {"enabled": True, "uses": {"calendar": True}},
        {"uses": ["inbox_sort"]},
        {},
    ],
)
def test_deck_update_is_all_or_nothing(cm, body):
    from navig.gateway.deck.routes import decider as route

    with pytest.raises(ValueError):
        route._apply(body)
    assert cm.writes == {}, "a rejected request must not half-apply"


# ── doctor row ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("status", "ok", "warn_expected", "needle"),
    [
        (_status(enabled=False), True, False, "off (optional)"),
        (_status(enabled=True, credential=""), False, True, "no Cloudflare credential"),
        (_status(enabled=True), False, True, "no call has succeeded yet"),
        (
            _status(enabled=True, last_call={"at": "2026-10-04T10:00:00Z", "use": "test", "ok": False,
                                              "kind": "auth", "latency_ms": 500, "model": "clef-flash",
                                              "detail": "HTTP 401"}),
            False,
            True,
            "FAILED (auth)",
        ),
        (
            _status(enabled=True, calls_today=7, last_call={"at": "2026-10-04T10:00:00Z", "use": "inbox_sort",
                                                             "ok": True, "kind": "", "latency_ms": 44,
                                                             "model": "clef-flash", "detail": ""}),
            True,
            False,
            "7 calls today",
        ),
    ],
)
def test_doctor_row_is_green_only_after_a_real_success(monkeypatch, status, ok, warn_expected, needle):
    from navig.commands import doctor

    monkeypatch.setattr(decide, "status", lambda: status)
    (row,) = doctor.check_decider()
    icon, row_ok, _line = row
    assert row_ok is ok
    assert needle in row.detail
    assert icon == (doctor._WARN if warn_expected else doctor._OK)


def test_shadow_for_one_use(writes):
    r = runner.invoke(decider_cmd.decider_app, ["shadow", "on", "--use", "telegram_triage"])
    assert r.exit_code == 0, r.output
    assert writes == {"decide.use.telegram_triage": "shadow"}
    r = runner.invoke(decider_cmd.decider_app, ["shadow", "off", "--use", "telegram_triage"])
    assert writes == {"decide.use.telegram_triage": "on"}


def test_review_lists_disagreements(monkeypatch):
    from navig.store import decide_usage

    class _Store:
        def recent_disagreements(self, *, limit, use):
            return [{"at": "2026-10-04T10:00:00Z", "use": "telegram_triage", "question": "tier",
                     "clef": "COMPLEX", "legacy": "MEDIUM", "confidence": 0.91, "sample": "plan a migration"}]

    monkeypatch.setattr(decide_usage, "get_decide_usage_store", lambda: _Store())
    r = runner.invoke(decider_cmd.decider_app, ["review"])
    assert r.exit_code == 0, r.output
    assert "COMPLEX" in r.output and "MEDIUM" in r.output and "91%" in r.output


def test_deck_update_accepts_a_shadow_mode(cm):
    from navig.gateway.deck.routes import decider as route

    route._apply({"uses": {"telegram_triage": "shadow", "inbox_sort": True}})
    assert cm.writes == {"decide.use.telegram_triage": "shadow", "decide.use.inbox_sort": "on"}
