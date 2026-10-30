"""GET/POST /api/deck/telegram/features — the allowlisted feature switches.

The catalog is the security boundary: an id outside it must be refused, a value
must be a real bool, and the value the Mini App shows must come from the same
resolver the bot reads.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from navig.gateway.deck.routes import telegram_features as tf


class _CM:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.writes: list[tuple[str, object]] = []

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set_global(self, key, value):
        self.writes.append((key, value))
        self.data[key] = value


@pytest.fixture
def cm(monkeypatch):
    import navig.config

    fake = _CM()
    monkeypatch.setattr(navig.config, "get_config_manager", lambda *a, **k: fake)
    return fake


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _post(body):
    resp = asyncio.run(tf.handle_deck_telegram_feature_set(_Req(body)))
    return resp.status, json.loads(resp.text)


def test_catalog_ids_are_unique_and_groups_known():
    ids = [f.id for f in tf.FEATURES]
    assert len(ids) == len(set(ids))
    assert {f.group for f in tf.FEATURES} <= set(tf.GROUP_ORDER)
    for f in tf.FEATURES:
        assert f.applies in {"live", "minute", "restart"}
        if f.depends_on:
            assert f.depends_on in ids


def test_catalog_does_not_duplicate_an_extension_or_a_behaviour_key():
    """Extensions and /social/telegram keys already have a writer — one each."""
    from navig.gateway.channels.telegram_extensions import EXTENSIONS
    from navig.gateway.deck.routes.social import _TELEGRAM_BOOL_KEYS

    keys = {f.key for f in tf.FEATURES}
    assert not keys & {f"telegram.{k}" for k in _TELEGRAM_BOOL_KEYS}
    assert not keys & {f"modules.overrides.{e.module_id}" for e in EXTENSIONS}
    for f in tf.FEATURES:
        if f.extension:
            assert any(e.id == f.extension for e in EXTENSIONS)


def test_unknown_id_is_refused_and_nothing_written(cm):
    status, body = _post({"id": "vault.enabled", "enabled": False})
    assert status == 404 and body["ok"] is False
    assert cm.writes == []


@pytest.mark.parametrize(
    "body",
    [{"id": "rich_messages", "enabled": "false"}, {"id": 3, "enabled": True}, [], ValueError("x")],
)
def test_malformed_bodies_are_refused(cm, body):
    status, _ = _post(body)
    assert status == 400
    assert cm.writes == []


def test_switch_round_trips_through_the_bots_own_resolver(cm):
    status, body = _post({"id": "rich_messages", "enabled": False})
    assert status == 200 and body["ok"] is True
    assert cm.writes == [("telegram.rich_messages", False)]
    assert body["feature"]["enabled"] is False
    # A CLI-written string "false" reads as off too.
    cm.data["telegram.rich_messages"] = "false"
    row = next(r for r in tf.list_features()["features"] if r["id"] == "rich_messages")
    assert row["enabled"] is False


def test_a_write_the_bot_does_not_honour_is_reported(cm, monkeypatch):
    feature = tf._BY_ID["rich_messages"]
    stuck = tf.FeatureSwitch(**{**feature.__dict__, "read": lambda: True})
    monkeypatch.setitem(tf._BY_ID, "rich_messages", stuck)
    status, body = _post({"id": "rich_messages", "enabled": False})
    assert status == 409 and body["error"] == "saved but not applied"


def test_child_is_reported_blocked_while_its_parent_is_off(cm, monkeypatch):
    monkeypatch.setattr(
        "navig.gateway.channels.telegram_catalog_ingest.catalog_enabled", lambda: False
    )
    rows = {r["id"]: r for r in tf.list_features()["features"]}
    assert rows["catalog_auto_analyze"]["blocked_by"] == "feature:catalog"


def test_feature_is_reported_blocked_while_its_extension_is_off(cm, monkeypatch):
    from navig.gateway.channels import telegram_extensions as tx

    real = tx.is_enabled
    monkeypatch.setattr(tx, "is_enabled", lambda e: False if e == "media" else real(e))
    rows = {r["id"]: r for r in tf.list_features()["features"]}
    assert rows["music_links"]["blocked_by"] == "extension:media"
    assert rows["rich_messages"]["blocked_by"] is None


def test_get_lists_every_feature(cm):
    resp = asyncio.run(tf.handle_deck_telegram_features(_Req({})))
    body = json.loads(resp.text)
    assert resp.status == 200 and body["ok"] is True
    assert [r["id"] for r in body["features"]] == [f.id for f in tf.FEATURES]
    assert [g["id"] for g in body["groups"]] == list(tf.GROUP_ORDER)
