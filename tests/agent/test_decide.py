"""navig.agent.decide — the decision-model (Cloudflare Clef) primitive.

The property every consumer leans on: ``decide()`` returns ``None`` — never raises —
whenever it cannot give a confident answer, so the caller's previous logic runs. These
tests pin each of those ``None`` paths, the wire format against Cloudflare's published
schema, and that a refused credential / a failing edge stops the calls instead of
adding a timeout to every message.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import httpx
import pytest

from navig.agent import decide

# ── fixtures ─────────────────────────────────────────────────


def _settings(**over) -> decide.Settings:
    base = decide.Settings(
        enabled=True,
        model="clef-flash",
        uses=dict.fromkeys(decide.USES, "on"),
        min_confidence=0.5,
        timeout_s=1.5,
        daily_cap=2000,
        shadow=False,
        redact=True,
        account_id="acct-1",
    )
    return replace(base, **over)


class _Resp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload
        self.text = str(payload)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _ok_payload(answers: dict) -> dict:
    """The REST envelope Workers AI wraps the model output in."""
    return {
        "result": {"model": "clef-flash", "answers": answers, "usage": {"input_tokens": 42, "output_tokens": 0}},
        "success": True,
        "errors": [],
        "messages": [],
    }


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Switches on, a token, an isolated usage ledger, captured HTTP + incidents."""
    from navig.store import decide_usage

    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    decide_usage.reset_decide_usage_store()
    decide.reset_state_for_tests()

    state = {"settings": _settings(), "token": "tok-A", "posts": [], "incidents": [], "reply": None}
    monkeypatch.setattr(decide, "settings", lambda: state["settings"])
    monkeypatch.setattr(decide, "_token", lambda: state["token"])

    def fake_post(url, headers=None, json=None, timeout=None):
        state["posts"].append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        reply = state["reply"]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(httpx, "post", fake_post)

    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda event, **data: state["incidents"].append((event, data)))
    yield state
    decide.reset_state_for_tests()
    decide_usage.reset_decide_usage_store()


TIER_Q = [decide.choice("tier", "How complex?", {"SIMPLE": "easy", "COMPLEX": "hard"})]


# ── question builders ────────────────────────────────────────


def test_question_wire_format_matches_the_published_schema():
    body = decide.build_body(
        "state text",
        [
            decide.yesno("urgent", "Urgent?", yes="needs action now"),
            decide.choice("team", "Which team?", ["billing", "technical"]),
            decide.score("severity", "How bad?", ["none", "minor", "major"]),
        ],
        model="clef",
    )
    assert body["model"] == "clef"
    assert body["state"] == "state text"
    qs = body["questions"]
    assert qs["urgent"] == {"type": "noul", "instructions": "Urgent?", "criteria": {"true": "needs action now"}}
    assert qs["team"] == {"type": "choice", "instructions": "Which team?", "criteria": {"billing": None, "technical": None}}
    assert qs["severity"]["type"] == "score"
    assert qs["severity"]["criteria"] == ["none", "minor", "major"]


@pytest.mark.parametrize(
    "build",
    [
        lambda: decide.choice("x", "?", ["only-one"]),
        lambda: decide.score("x", "?", ["a"]),
        lambda: decide.score("x", "?", [str(i) for i in range(11)]),
        lambda: decide.yesno("bad id!", "?"),
    ],
)
def test_builders_reject_what_cloudflare_would_reject(build):
    with pytest.raises(ValueError):
        build()


def test_body_rejects_too_many_and_duplicate_questions():
    many = [decide.yesno(f"q{i}", "?") for i in range(decide.MAX_QUESTIONS + 1)]
    with pytest.raises(decide.DecideError) as exc:
        decide.build_body("s", many, model="clef")
    assert exc.value.kind == "invalid"
    with pytest.raises(decide.DecideError):
        decide.build_body("s", [decide.yesno("a", "?"), decide.yesno("a", "?")], model="clef")


def test_redaction_strips_emails_phones_and_secrets_before_sending():
    # The Stripe-shaped value is adjacent literals on purpose: the redactor must see the whole
    # `sk_live_…` shape at runtime, but a literal one in source makes GitHub's secret scanning
    # refuse to publish this file. Concatenation keeps the runtime value identical.
    body = decide.build_body(
        "mail jane.doe@example.com or call +1 (555) 123-4567, key sk_" "live_" "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345",
        [decide.yesno("a", "?")],
        model="clef",
        redact_state=True,
    )
    sent = body["state"]
    assert "jane.doe@example.com" not in sent and "[email]" in sent
    assert "555" not in sent and "[phone]" in sent
    assert "sk_live_" not in sent and "[secret]" in sent


# ── response parsing ─────────────────────────────────────────


def test_parse_all_three_answer_types_from_the_rest_envelope():
    qs = [
        decide.yesno("urgent", "?"),
        decide.choice("team", "?", ["billing", "technical", "sales"]),
        decide.score("severity", "?", ["none", "minor", "major", "critical"]),
    ]
    payload = _ok_payload(
        {
            "urgent": {"type": "noul", "noul": 0.93},
            "team": {
                "type": "choice",
                "choice": "technical",
                "probabilities": {"billing": 0.04, "technical": 0.94, "sales": 0.02},
                "confidence": 0.9,
            },
            "severity": {
                "type": "score",
                "score": 2.7,
                "legend": {"0": "none", "1": "minor", "2": "major", "3": "critical"},
                "probabilities": {"0": 0.01, "1": 0.04, "2": 0.2, "3": 0.75},
                "confidence": 0.7,
            },
        }
    )
    answers, model, tokens = decide.parse_answers(payload, qs)
    assert model == "clef-flash" and tokens == 42
    assert answers["urgent"].yes and answers["urgent"].answer == "yes"
    assert answers["urgent"].confidence == pytest.approx(0.93)
    assert answers["team"].answer == "technical"
    assert answers["team"].confidence == 0.94  # the chosen option's probability
    assert answers["team"].clef_confidence == 0.9  # Clef's own, stricter certainty
    assert answers["team"].ranked()[0] == ("technical", 0.94)
    assert answers["severity"].answer == "critical"
    assert answers["severity"].value == pytest.approx(2.7)
    assert answers["severity"].probs["critical"] == 0.75


def test_parse_accepts_the_bare_model_output_too():
    payload = {"model": "clef", "answers": {"a": {"type": "noul", "noul": 0.1}}, "usage": {"input_tokens": 3}}
    answers, _, _ = decide.parse_answers(payload, [decide.yesno("a", "?")])
    assert answers["a"].answer == "no" and answers["a"].confidence == pytest.approx(0.9)


@pytest.mark.parametrize(
    "payload",
    [
        {"result": None, "success": False, "errors": [{"code": 1}]},
        {"result": {"answers": {}}, "success": True},
        {"result": {"answers": {"team": {"type": "choice", "choice": "marketing", "probabilities": {"marketing": 1}}}}, "success": True},
        {"nothing": "here"},
    ],
)
def test_malformed_responses_are_bad_response_not_a_crash(payload):
    with pytest.raises(decide.DecideError) as exc:
        decide.parse_answers(payload, [decide.choice("team", "?", ["billing", "technical"])])
    assert exc.value.kind == "bad_response"


# ── decide(): every None path ────────────────────────────────


def test_disabled_returns_none_without_any_network(env):
    env["settings"] = _settings(enabled=False)
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert env["posts"] == []


def test_a_switched_off_use_returns_none_without_any_network(env):
    env["settings"] = _settings(uses={"telegram_triage": "off", "inbox_sort": "on", "inbox_space": "on"})
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert env["posts"] == []


def test_no_credential_returns_none(env):
    env["token"] = ""
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert env["posts"] == []


def test_success_returns_decisions_and_posts_to_the_users_account(env):
    env["reply"] = _Resp(200, _ok_payload({"tier": {"type": "choice", "choice": "COMPLEX",
                                                     "probabilities": {"SIMPLE": 0.1, "COMPLEX": 0.9},
                                                     "confidence": 0.85}}))
    out = decide.decide("write me a migration plan", TIER_Q, use="telegram_triage")
    assert out is not None and out["tier"].answer == "COMPLEX"
    post = env["posts"][0]
    assert post["url"] == "https://api.cloudflare.com/client/v4/accounts/acct-1/ai/run/@cf/cloudflare/clef-flash"
    assert post["headers"]["Authorization"] == "Bearer tok-A"
    assert post["json"]["model"] == "clef-flash"
    assert post["timeout"] == 1.5


def test_success_is_counted_in_the_usage_ledger(env):
    from navig.store.decide_usage import get_decide_usage_store

    env["reply"] = _Resp(200, _ok_payload({"tier": {"type": "choice", "choice": "SIMPLE",
                                                     "probabilities": {"SIMPLE": 0.9, "COMPLEX": 0.1},
                                                     "confidence": 0.8}}))
    decide.decide("hi", TIER_Q, use="telegram_triage")
    store = get_decide_usage_store()
    assert store.calls_today() == 1
    assert store.usage_today()["telegram_triage"]["input_tokens"] == 42
    last = store.last_call()
    assert last["ok"] is True and last["use"] == "telegram_triage"


def test_low_confidence_answers_are_dropped(env):
    env["settings"] = _settings(min_confidence=0.8)
    env["reply"] = _Resp(200, _ok_payload({"tier": {"type": "choice", "choice": "SIMPLE",
                                                     "probabilities": {"SIMPLE": 0.55, "COMPLEX": 0.45},
                                                     "confidence": 0.3}}))
    assert decide.decide("hmm", TIER_Q, use="telegram_triage") is None


def test_shadow_mode_calls_but_does_not_apply(env):
    env["settings"] = _settings(shadow=True)
    env["reply"] = _Resp(200, _ok_payload({"tier": {"type": "choice", "choice": "SIMPLE",
                                                     "probabilities": {"SIMPLE": 0.95, "COMPLEX": 0.05},
                                                     "confidence": 0.9}}))
    assert decide.decide("hi", TIER_Q, use="telegram_triage") is None
    assert len(env["posts"]) == 1  # it DID ask — shadow is for comparing, not skipping


def test_a_refused_credential_records_an_incident_and_stops_calling(env):
    env["reply"] = _Resp(401, {"success": False, "errors": [{"code": 10000, "message": "Authentication error"}]})
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert [e for e, _ in env["incidents"]] == ["decider_auth_failed"]

    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert len(env["posts"]) == 1, "the same refused credential must not be retried"

    env["token"] = "tok-B"  # the user re-ran `navig lighthouse login`
    env["reply"] = _Resp(200, _ok_payload({"tier": {"type": "choice", "choice": "SIMPLE",
                                                     "probabilities": {"SIMPLE": 0.9, "COMPLEX": 0.1},
                                                     "confidence": 0.9}}))
    assert decide.decide("x", TIER_Q, use="telegram_triage")["tier"].answer == "SIMPLE"


@pytest.mark.parametrize(
    "reply",
    [
        _Resp(500, {"success": False}),
        httpx.ReadTimeout("slow"),
        httpx.ConnectError("down"),
    ],
)
def test_a_failing_edge_pauses_calls_instead_of_adding_a_timeout_to_every_message(env, reply):
    env["reply"] = reply
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert len(env["posts"]) == 1


def test_daily_cap_stops_calls_and_is_reported_once(env, monkeypatch):
    env["settings"] = _settings(daily_cap=3)
    monkeypatch.setattr(decide, "_calls_today", lambda: 3)
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert env["posts"] == []
    assert [e for e, _ in env["incidents"]] == ["decider_budget"]


def test_decide_never_raises_even_when_settings_explode(env, monkeypatch):
    def boom():
        raise RuntimeError("config on fire")

    monkeypatch.setattr(decide, "settings", boom)
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None
    assert decide.is_active("telegram_triage") is False


def test_adecide_fast_path_when_off(env):
    env["settings"] = _settings(enabled=False)
    assert asyncio.run(decide.adecide("x", TIER_Q, use="telegram_triage")) is None
    assert env["posts"] == []


def test_adecide_runs_the_call_off_the_event_loop(env):
    env["reply"] = _Resp(200, _ok_payload({"tier": {"type": "choice", "choice": "COMPLEX",
                                                     "probabilities": {"SIMPLE": 0.1, "COMPLEX": 0.9},
                                                     "confidence": 0.9}}))
    out = asyncio.run(decide.adecide("x", TIER_Q, use="telegram_triage"))
    assert out["tier"].answer == "COMPLEX"


# ── call(): the raising form `navig decider test` uses ───────


def test_call_names_the_fix_for_a_refused_oauth_login(env, monkeypatch):
    from navig.cloud import cf_token

    monkeypatch.setattr(cf_token, "credential_kind", lambda: "oauth")
    env["reply"] = _Resp(403, {"success": False})
    with pytest.raises(decide.DecideError) as exc:
        decide.call("x", TIER_Q)
    assert exc.value.kind == "auth"
    assert "navig lighthouse login" in str(exc.value)


def test_call_without_credential_says_how_to_connect(env):
    env["token"] = ""
    with pytest.raises(decide.DecideError) as exc:
        decide.call("x", TIER_Q)
    assert exc.value.kind == "no_credential"
    assert "navig lighthouse login" in str(exc.value)


def test_failure_detail_in_the_ledger_never_carries_a_secret(env):
    from navig.store.decide_usage import get_decide_usage_store

    env["reply"] = _Resp(500, {"echo": "Bearer abcdefghijklmnopqrstuvwxyz0123456789ABCD"})
    with pytest.raises(decide.DecideError):
        decide.call("x", TIER_Q)
    detail = get_decide_usage_store().last_call()["detail"]
    assert "abcdefghijklmnopqrstuvwxyz0123456789ABCD" not in detail


# ── settings: config values arrive as strings ────────────────


def test_settings_coerce_string_booleans_and_clamp_numbers(monkeypatch):
    decide.reset_state_for_tests()
    monkeypatch.setattr(
        decide,
        "_section",
        lambda: {
            "enabled": "false",  # `navig config set decide.enabled false` stores a string
            "model": "CLEF",
            "use": {"inbox_sort": "off"},
            "min_confidence": "7",
            "timeout_ms": "abc",
            "daily_cap": "-5",
            "shadow": "yes",
        },
    )
    s = decide.settings()
    assert s.enabled is False
    assert s.model == "clef"
    assert s.uses["inbox_sort"] == "off" and s.uses["telegram_triage"] == "on"
    assert s.min_confidence == 1.0
    assert s.timeout_s == pytest.approx(1.5)
    assert s.daily_cap == 2000  # a negative cap is malformed → the default, never "no cap"
    assert s.shadow is True


def test_an_explicit_zero_cap_means_no_cap(monkeypatch):
    monkeypatch.setattr(decide, "_section", lambda: {"daily_cap": "0"})
    assert decide.settings().daily_cap == 0


def test_unknown_model_falls_back_to_the_default(monkeypatch):
    monkeypatch.setattr(decide, "_section", lambda: {"model": "gpt-9"})
    assert decide.settings().model == decide.DEFAULT_MODEL


def test_every_use_has_a_label_and_description():
    for uid, use in decide.USES.items():
        assert use.id == uid and use.label and len(use.description) > 20


# ── modes: on / shadow / off ─────────────────────────────────


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [
        (None, "shadow", "shadow"),
        ("", "on", "on"),
        ("shadow", "on", "shadow"),
        ("SHADOW ", "on", "shadow"),
        (True, "shadow", "on"),
        ("false", "shadow", "off"),
        ("yes", "off", "on"),
        ("banana", "shadow", "shadow"),
    ],
)
def test_parse_mode(value, default, expected):
    assert decide.parse_mode(value, default) == expected


_TIER_REPLY = {"tier": {"type": "choice", "choice": "COMPLEX", "probabilities": {"SIMPLE": 0.1, "COMPLEX": 0.9},
                        "confidence": 0.9}}


def test_a_shadow_use_answers_but_is_never_applied(env):
    env["settings"] = _settings(uses={"telegram_triage": "shadow", "inbox_sort": "on", "inbox_space": "on"})
    env["reply"] = _Resp(200, _ok_payload(_TIER_REPLY))
    out = decide.outcome("x", TIER_Q, use="telegram_triage")
    assert out is not None and out.live is False
    assert out.get("tier").answer == "COMPLEX"
    assert out.applied("tier") is None, "shadow answers must never be acted on"
    assert decide.decide("x", TIER_Q, use="telegram_triage") is None


def test_the_global_shadow_switch_overrides_a_live_use(env):
    env["settings"] = _settings(shadow=True)
    env["reply"] = _Resp(200, _ok_payload(_TIER_REPLY))
    assert decide.outcome("x", TIER_Q, use="telegram_triage").live is False


def test_a_live_outcome_applies_only_confident_answers(env):
    env["settings"] = _settings(min_confidence=0.95)
    env["reply"] = _Resp(200, _ok_payload(_TIER_REPLY))
    out = decide.outcome("x", TIER_Q, use="telegram_triage")
    assert out.live is True
    assert out.applied("tier") is None  # 0.9 < 0.95
    assert out.answers["tier"].answer == "COMPLEX"  # still there to compare


def test_compare_records_agreement_disagreement_and_abstain(env):
    from navig.store.decide_usage import get_decide_usage_store

    out = decide.Outcome(
        use="telegram_triage",
        answers={
            "a": decide.Decision("COMPLEX", 0.9, {}),
            "b": decide.Decision("yes", 0.8, {}),
            "c": decide.Decision("SIMPLE", 0.2, {}),  # below min_confidence → abstain
        },
        live=False,
        min_confidence=0.5,
    )
    decide.compare(out, "a", "COMPLEX")
    decide.compare(out, "b", "no", sample="call me at +1 555 123 4567 tomorrow")
    decide.compare(out, "c", "MEDIUM")
    decide.compare(None, "a", "whatever")  # no outcome → nothing recorded, no error

    store = get_decide_usage_store()
    assert store.agreement()["telegram_triage"] == {"total": 3, "agree": 1, "abstain": 1}
    (row,) = store.recent_disagreements()
    assert (row["question"], row["clef"], row["legacy"]) == ("b", "yes", "no")
    assert "555" not in row["sample"], "disagreement samples are redacted"


def test_an_existing_v1_ledger_is_migrated(tmp_path):
    import sqlite3

    from navig.store.decide_usage import DecideUsageStore

    db = tmp_path / "decide_usage.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE schema_version (version INTEGER PRIMARY KEY); INSERT INTO schema_version VALUES (1);"
        "CREATE TABLE usage (day TEXT, use TEXT, calls INTEGER, ok INTEGER, failed INTEGER, input_tokens INTEGER,"
        " PRIMARY KEY (day, use));"
        "CREATE TABLE last_call (id INTEGER PRIMARY KEY, at TEXT, use TEXT, ok INTEGER, kind TEXT, latency_ms INTEGER,"
        " model TEXT, detail TEXT);"
    )
    con.close()
    store = DecideUsageStore(db)
    store.record_comparison(use="u", question="q", clef="a", legacy="a")
    assert store.agreement()["u"]["agree"] == 1
    assert store.get_schema_version() == 2
    store.close()


@pytest.mark.parametrize("model", ["clef", "clef-flash"])
def test_each_model_is_posted_to_its_own_endpoint(env, model):
    """Live, 2026-10-04: `model: clef-flash` sent to @cf/cloudflare/clef answers HTTP 422
    "Unsupported model 'clef-flash'. Use 'clef'." — the path must follow the model."""
    env["reply"] = _Resp(200, _ok_payload({"tier": {"type": "choice", "choice": "SIMPLE",
                                                     "probabilities": {"SIMPLE": 0.9, "COMPLEX": 0.1},
                                                     "confidence": 0.8}}))
    decide.call("x", TIER_Q, model=model)
    post = env["posts"][-1]
    assert post["url"].endswith(f"/ai/run/@cf/cloudflare/{model}")
    assert post["json"]["model"] == model


def test_confidence_is_the_chosen_answers_probability_not_clefs_spread_score():
    """Live answer: technical 0.69 / billing 0.29 / sales 0.02 came with Clef confidence
    0.34. Gating 0.5 on that would discard a plainly right answer."""
    q = [decide.choice("team", "?", ["billing", "technical", "sales"])]
    payload = _ok_payload({"team": {"type": "choice", "choice": "technical",
                                    "probabilities": {"technical": 0.69, "billing": 0.29, "sales": 0.02},
                                    "confidence": 0.34}})
    d = decide.parse_answers(payload, q)[0]["team"]
    assert d.confidence == 0.69 and d.clef_confidence == 0.34
    out = decide.Outcome(use="u", answers={"team": d}, live=True, min_confidence=0.5)
    assert out.applied("team") is d


def test_credential_reads_are_single_flight_and_an_empty_read_is_not_cached(monkeypatch):
    """Concurrent first reads of the vault fail in a fresh process (all but one). The
    decider must read once at a time, and never remember an empty answer."""
    import threading

    from navig.cloud import cf_token

    decide.reset_state_for_tests()
    monkeypatch.setattr(decide, "_token", decide.__dict__["_token"])  # the real one
    inside = {"now": 0, "max": 0, "calls": 0}
    results = iter(["", "tok-1"])
    lock = threading.Lock()

    def fake_resolve(caller=""):
        with lock:
            inside["now"] += 1
            inside["max"] = max(inside["max"], inside["now"])
            inside["calls"] += 1
        import time as _t

        _t.sleep(0.05)
        with lock:
            inside["now"] -= 1
            return next(results, "tok-1")

    monkeypatch.setattr(cf_token, "resolve_cf_token", fake_resolve)
    assert decide._token() == ""  # a failed read...
    got = []
    ts = [threading.Thread(target=lambda: got.append(decide._token())) for _ in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert got == ["tok-1"] * 5  # ...is retried, not cached
    assert inside["max"] == 1, "two vault reads ran at once"
    assert inside["calls"] == 2, "the good token must be cached after the first success"
    decide.reset_state_for_tests()

