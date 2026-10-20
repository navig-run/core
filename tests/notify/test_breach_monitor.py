"""The breach MONITOR — masking, dedupe, notification content and cycle honesty.

The monitor's whole contract lives here: a leak record reaches a notification
only masked and counted (never as a full list), already-announced records are
never re-announced, a manual check stores without announcing, and every
failure path comes back as a reported ``error`` instead of a raised crash.
"""

from __future__ import annotations

import json

import pytest

from navig.notify.monitors import breach as m

# ── masking ──────────────────────────────────────────────────────────────────


def test_real_password_is_masked_but_recognisable():
    masked = m.mask_password("hunter2secret")
    assert masked[0] == "h" and masked[-1] == "t"
    assert "*" in masked
    assert "hunter2secret" not in masked


def test_api_labels_pass_through():
    assert m.mask_password("Hashed") == "Hashed"
    assert m.mask_password("Encrypted") == "Encrypted"


def test_short_passwords_are_fully_masked():
    assert m.mask_password("abc") == "***"
    assert m.mask_password("ab") == "**"
    assert m.mask_password("") == ""


# ── fingerprints ─────────────────────────────────────────────────────────────


def test_fingerprint_is_stable_and_password_sensitive():
    rec = {"databaseName": "SiteX", "createdAt": "2024-05-01", "password": "pw1"}
    a = m.record_fingerprint("a@b.com", rec)
    b = m.record_fingerprint("a@b.com", dict(rec))
    assert a == b
    assert a != m.record_fingerprint("a@b.com", {**rec, "password": "pw2"})
    assert a != m.record_fingerprint("A@B.COM".lower(), {**rec, "databaseName": "Other"})


# ── email enumeration ────────────────────────────────────────────────────────


def test_extract_emails_filters_and_sorts(monkeypatch):
    class _Login:
        def __init__(self, username):
            self.username = username

    logins = [
        _Login("alice@Example.com"),
        _Login("root"),
        _Login("alice@example.com"),  # duplicate after lower()
        _Login("bob@x.dev"),
        _Login(None),
    ]
    monkeypatch.setattr("navig.vault.logins.list_logins", lambda: logins)
    assert m.extract_emails() == ["alice@example.com", "bob@x.dev"]


def test_extract_emails_survives_a_locked_vault(monkeypatch):
    def _boom():
        raise RuntimeError("vault locked")

    monkeypatch.setattr("navig.vault.logins.list_logins", _boom)
    assert m.extract_emails() == []


# ── notification content ─────────────────────────────────────────────────────


def _records(n: int, email: str = "a@b.com") -> list[dict]:
    return [
        {
            "email": email,
            "databaseName": f"Dump{i}",
            "createdAt": f"2024-0{i + 1}-01",
            "password": f"secret{i}pass",
        }
        for i in range(n)
    ]


def test_notification_masks_passwords_and_points_at_the_cli():
    title, body = m.build_notification({"a@b.com": _records(2)})

    assert title == "Breach found: a@b.com"
    assert "Dump0" in body and "Dump1" in body
    assert "secret0pass" not in body and "secret1pass" not in body
    assert m.mask_password("secret0pass") in body  # first/last kept, middle starred
    assert "navig vault breach list" in body


def test_notification_never_carries_the_full_email_list():
    new_by_email = {f"u{i:02d}@x.com": _records(1, f"u{i:02d}@x.com") for i in range(13)}
    title, body = m.build_notification(new_by_email)

    assert "13 vault emails" in title
    assert body.count("•") == m._MAX_EMAILS_IN_BODY  # capped lines
    assert f"+{13 - m._MAX_EMAILS_IN_BODY} more emails" in body
    for i in range(m._MAX_EMAILS_IN_BODY, 13):
        assert f"u{i:02d}@x.com" not in body


def test_notification_caps_sources_per_email():
    _, body = m.build_notification({"a@b.com": _records(5)})
    assert "(+2 more)" in body


# ── rotation ─────────────────────────────────────────────────────────────────


def test_cycle_batch_rotates_and_wraps():
    emails = [f"u{i}@x.com" for i in range(5)]
    state = {"cursor": 3}
    assert m._cycle_batch(emails, state, 3) == ["u3@x.com", "u4@x.com", "u0@x.com"]
    assert m._cycle_batch([], state, 3) == []


# ── scan_once ────────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, records: list[dict]):
        self._records = records

    def get(self, key, default=None):
        if key == "results":
            return self._records
        return default


class _FakeConnector:
    def __init__(self, records_by_email=None, connect_exc=None, check_exc=None):
        self.records_by_email = records_by_email or {}
        self.connect_exc = connect_exc
        self.check_exc = check_exc
        self.connected = 0
        self.checked: list[str] = []

    async def connect(self):
        self.connected += 1
        if self.connect_exc:
            raise self.connect_exc

    async def check(self, email: str, filter_type: str = "all"):
        self.checked.append(email)
        if self.check_exc:
            raise self.check_exc
        return _FakeResponse(self.records_by_email.get(email, []))


class _FakeRegistry:
    def __init__(self, connector):
        self._c = connector

    def get(self, connector_id):
        assert connector_id == "cyberaigen"
        return self._c


@pytest.fixture
def wired(monkeypatch):
    """Patch the state vault, the connector registry and dispatch for scan_once."""
    box: dict = {}
    dispatched: list[tuple[str, str, str, dict]] = []

    def _load():
        if not box:
            return m._empty_state()
        return json.loads(json.dumps(box))

    def _save(state):
        box.clear()
        box.update(json.loads(json.dumps(state)))

    monkeypatch.setattr(m, "load_state", _load)
    monkeypatch.setattr(m, "save_state", _save)
    monkeypatch.setattr(
        "navig.connectors.bootstrap.ensure_connectors_loaded", lambda: None
    )

    def _use(connector):
        monkeypatch.setattr(
            "navig.connectors.registry.get_connector_registry",
            lambda: _FakeRegistry(connector),
        )

    async def _dispatch(type_key, title, body="", **kwargs):
        dispatched.append((type_key, title, body, kwargs))
        return {"type": type_key, "channels": ["deck"]}

    monkeypatch.setattr("navig.notify.dispatch", _dispatch)

    return {"box": box, "dispatched": dispatched, "use": _use}


def _rec(fp_seed: str) -> dict:
    return {
        "email": "a@b.com",
        "databaseName": f"Dump-{fp_seed}",
        "createdAt": "2024-05-01",
        "password": f"pw-{fp_seed}",
    }


def test_new_records_notify_once_and_are_marked(wired):
    wired["use"](_FakeConnector(records_by_email={"a@b.com": [_rec("one")]}))

    import asyncio

    summary = asyncio.run(m.scan_once(emails=["a@b.com"], notify=True))

    assert summary["error"] is None
    assert summary["new_records"] == 1
    assert summary["new_emails"] == ["a@b.com"]
    assert len(wired["dispatched"]) == 1
    type_key, title, body, kwargs = wired["dispatched"][0]
    assert type_key == "breach_alert"
    assert "a@b.com" in title
    assert "pw-one" not in body  # the raw password never reaches the notification

    # state now remembers the fingerprint → a second identical cycle is silent
    wired["use"](_FakeConnector(records_by_email={"a@b.com": [_rec("one")]}))
    summary2 = asyncio.run(m.scan_once(emails=["a@b.com"], notify=True))
    assert summary2["new_records"] == 0
    assert len(wired["dispatched"]) == 1


def test_manual_check_stores_without_announcing(wired):
    wired["use"](_FakeConnector(records_by_email={"a@b.com": [_rec("one")]}))

    import asyncio

    summary = asyncio.run(m.scan_once(emails=["a@b.com"], notify=False))

    assert summary["new_records"] == 1  # reported to the caller
    assert wired["dispatched"] == []  # but nothing was pushed
    # the fingerprint was NOT marked announced → the monitor will still raise it
    assert wired["box"]["notified"] == {}


def test_connect_failure_is_reported_not_raised(wired):
    wired["use"](_FakeConnector(connect_exc=RuntimeError("no token")))

    import asyncio

    summary = asyncio.run(m.scan_once(emails=["a@b.com"], notify=True))

    assert summary["error"] and "no token" in summary["error"]
    assert summary["checked"] == 0
    assert wired["dispatched"] == []
    assert wired["box"]["failures"] == 1


def test_errors_never_carry_the_endpoint_into_state_or_history(wired):
    """A transport failure embeds the request URL — it must not survive into
    summary/state.last_error (which feeds logs and the failure notification)."""
    wired["use"](
        _FakeConnector(
            check_exc=RuntimeError(
                "All connection attempts failed for url: http://127.0.0.1:8800/api/v1/check"
            )
        )
    )

    import asyncio

    summary = asyncio.run(m.scan_once(emails=["a@b.com"], notify=True))

    for text in (summary["error"] or "", wired["box"].get("last_error") or ""):
        assert "127.0.0.1" not in text
        assert "http:" not in text
        assert "<endpoint>" in text


def test_three_failed_cycles_alert_once(wired):
    import asyncio

    wired["use"](_FakeConnector(connect_exc=RuntimeError("down")))
    for _ in range(3):
        asyncio.run(m.scan_once(emails=["a@b.com"], notify=True))

    alerts = [d for d in wired["dispatched"] if d[0] == "system_alert"]
    assert len(alerts) == 1
    assert "failing" in alerts[0][1].lower()
    assert wired["box"]["alerted"] is True

    # a fourth failure stays quiet — one alert per outage
    asyncio.run(m.scan_once(emails=["a@b.com"], notify=True))
    assert len([d for d in wired["dispatched"] if d[0] == "system_alert"]) == 1


def test_empty_targets_early_returns_and_resets_failures(wired):
    wired["box"].update(m._empty_state())
    wired["box"]["failures"] = 2
    wired["box"]["alerted"] = True

    import asyncio

    summary = asyncio.run(m.scan_once(emails=[], notify=True))

    assert summary["checked"] == 0
    assert summary["error"] is None
    assert wired["box"]["failures"] == 0
    assert wired["box"]["alerted"] is False


def test_failed_state_write_is_surfaced(wired, monkeypatch):
    wired["use"](_FakeConnector(records_by_email={"a@b.com": [_rec("one")]}))
    monkeypatch.setattr(m, "save_state", lambda s: (_ for _ in ()).throw(RuntimeError("vault locked")))

    import asyncio

    summary = asyncio.run(m.scan_once(emails=["a@b.com"], notify=True))

    assert summary["error"] and "vault locked" in summary["error"]


# ── the vault state round-trip (the real read/write path, not the patch) ─────


class _FakeVault:
    """In-memory vault exposing the two methods the state layer uses.

    ``get_secret`` deliberately reproduces the REAL vault's behaviour on a
    structured payload: it parses the JSON and returns the FIRST VALUE (which
    was ``version: 1`` → ``"1"``) — the trap that made a successful scan read
    back as "never ran". The state layer must use ``get_bytes``.
    """

    def __init__(self):
        self.data: dict[str, bytes] = {}

    def put(self, label, payload, **kwargs):
        self.data[label] = bytes(payload)
        return "id"

    def get_bytes(self, label):
        if label not in self.data:
            raise KeyError(label)
        return self.data[label]

    def get_secret(self, label):
        import json as _json

        return str(list(_json.loads(self.get_bytes(label)).values())[0])


def test_state_round_trips_through_the_vault_bytes(monkeypatch):
    fake = _FakeVault()
    monkeypatch.setattr(m, "_vault", lambda: fake)

    m.save_state(
        {
            **m._empty_state(),
            "cursor": 7,
            "findings": {"a@b.com": [{"databaseName": "Dump"}]},
            "last_run": "2026-10-14T00:00:00+00:00",
        }
    )

    state = m.load_state()
    assert state["cursor"] == 7  # what was written, not the first JSON value ("1")
    assert state["findings"]["a@b.com"][0]["databaseName"] == "Dump"
    assert state["last_run"] == "2026-10-14T00:00:00+00:00"
    assert state["version"] == 1  # defaults still merged in


def test_missing_state_reads_as_default(monkeypatch):
    monkeypatch.setattr(m, "_vault", lambda: _FakeVault())
    assert m.load_state()["cursor"] == 0
    assert m.load_state()["findings"] == {}


# ── wiring: the monitor is registered where the deck and gateway look ────────


def test_monitor_is_wired_into_the_gateway_and_deck():
    from navig.gateway.deck.routes.notify import _MONITOR_KEYS, _MONITORS
    from navig.gateway.server import NavigGateway

    assert "breach" in NavigGateway.MONITOR_KEYS
    assert "breach" in _MONITOR_KEYS
    assert any(x["key"] == "breach" for x in _MONITORS)
    # opt-in: never in the default-on set
    assert "breach" not in NavigGateway.MONITORS_DEFAULT_ON


def test_type_and_sample_are_registered():
    from navig.notify.producers.samples import monitor_sample
    from navig.notify.types import TYPE_KEYS, emoji_for_type

    assert "breach_alert" in TYPE_KEYS
    assert emoji_for_type("breach_alert") != "📢"
    sample = monitor_sample("breach")
    assert sample is not None and sample[0] == "breach_alert"
