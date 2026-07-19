"""Regression: the Telegram Business bot went 100% deaf, with every light green.

Root cause (2026-07-12): ``telegram.webhook_url`` embeds ``sha256(deck.api_key)`` —
the Durable Object the lighthouse edge routes updates to. ``deck.api_key`` was rotated,
but the stored URL was never recomputed, so Telegram kept POSTing to the OLD tenant.
That DO has no uplink socket, so it queued every update and acked ``202 {"queued":true}``,
while the brain — attached to the DO of the NEW key — reported "uplink online, served=0".
Worse, the channel re-registered the stale URL on every boot, making it permanent.

Two invariants are pinned here:
  1. a stale tenant is detected and corrected (and a healthy/custom one is left alone);
  2. every caller that tells Telegram what to send declares the SAME update list —
     a deploy that omits ``business_*`` turns the Business bot off silently.
"""

from __future__ import annotations

import hashlib

import pytest

from navig.telegram.updates import (
    ALLOWED_UPDATES,
    corrected_webhook_url,
    webhook_tenant,
    webhook_url_for,
)

EDGE = "https://navig-lighthouse.example.workers.dev"
OLD_KEY = "navig_old_rotated_key"
NEW_KEY = "navig_new_live_key"


class FakeConfig:
    """Minimal stand-in for navig.core.Config (dotted-key get)."""

    def __init__(self, **values):
        self._v = values

    def get(self, key, default=None):
        return self._v.get(key, default)


def _cfg(mode="lighthouse", edge=EDGE, key=NEW_KEY):
    return FakeConfig(**{"cloud.mode": mode, "cloud.lighthouse_url": edge, "deck.api_key": key})


def test_stale_tenant_is_detected_and_corrected():
    """The actual bug: URL built from the OLD key while the brain uses the NEW one."""
    stale = webhook_url_for(EDGE, OLD_KEY)          # what Telegram was still POSTing to
    fixed = corrected_webhook_url(stale, _cfg(key=NEW_KEY))

    assert fixed is not None, "a stale tenant must be detected, not silently kept"
    assert fixed == webhook_url_for(EDGE, NEW_KEY)
    assert webhook_tenant(fixed) == hashlib.sha256(NEW_KEY.encode()).hexdigest()
    assert webhook_tenant(fixed) != webhook_tenant(stale)


def test_healthy_url_is_left_alone():
    assert corrected_webhook_url(webhook_url_for(EDGE, NEW_KEY), _cfg(key=NEW_KEY)) is None


@pytest.mark.parametrize(
    "url, cfg, why",
    [
        ("https://my-own-domain.example.com/tg/hook", _cfg(), "a custom webhook host is not ours to rewrite"),
        (webhook_url_for(EDGE, OLD_KEY), _cfg(mode="direct"), "not lighthouse mode"),
        (webhook_url_for(EDGE, OLD_KEY), _cfg(key=""), "no deck.api_key to derive a tenant from"),
        (None, _cfg(), "no webhook configured at all"),
    ],
)
def test_never_rewrites_what_it_must_not_touch(url, cfg, why):
    assert corrected_webhook_url(url, cfg) is None, why


def test_business_updates_are_in_the_shared_list():
    """Business updates are NOT in Telegram's default set — omit them and the
    Business bot receives nothing, forever, with no error anywhere."""
    for u in (
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    ):
        assert u in ALLOWED_UPDATES


def test_every_caller_declares_the_same_update_list():
    """The deploy path used to declare its own narrower list, which silently
    switched Business updates off the next time anyone ran `lighthouse deploy`.
    Both webhook registrars must now use the one shared constant."""
    import inspect

    from navig.commands import lighthouse
    from navig.gateway.channels import telegram as tg_channel

    for mod, fn in ((lighthouse, "configure_telegram_webhook"), (tg_channel, None)):
        src = inspect.getsource(getattr(mod, fn) if fn else mod.TelegramChannel._setup_webhook)
        assert "ALLOWED_UPDATES" in src, f"{mod.__name__} must use the shared list"
        assert '"business_message"' not in src, f"{mod.__name__} re-declares its own list"


# ── `navig doctor` must SEE the outage (it reported everything green for days) ──


def _patch_config(monkeypatch, cfg):
    import navig.core

    monkeypatch.setattr(navig.core, "Config", lambda *a, **k: cfg)


def test_doctor_flags_a_stale_tenant(monkeypatch):
    from navig.commands.doctor import check_reachability

    cfg = FakeConfig(**{
        "cloud.mode": "lighthouse",
        "cloud.lighthouse_url": EDGE,
        "deck.api_key": NEW_KEY,
        "telegram.webhook_url": webhook_url_for(EDGE, OLD_KEY),   # rotated away
    })
    _patch_config(monkeypatch, cfg)

    results = check_reachability()
    assert results, "lighthouse mode must produce a Reachability row"
    assert not any(ok for _icon, ok, _txt in results), "a stale tenant must FAIL, not pass"
    assert "STALE" in results[0][2]


def test_doctor_passes_on_a_healthy_tenant(monkeypatch):
    from navig.commands.doctor import check_reachability

    cfg = FakeConfig(**{
        "cloud.mode": "lighthouse",
        "cloud.lighthouse_url": EDGE,
        "deck.api_key": NEW_KEY,
        "telegram.webhook_url": webhook_url_for(EDGE, NEW_KEY),
    })
    _patch_config(monkeypatch, cfg)

    results = check_reachability()
    assert all(ok for _icon, ok, _txt in results)


def test_doctor_does_not_vouch_for_a_custom_webhook_host(monkeypatch):
    """A webhook on the user's own domain has no tenant we can derive — report that
    honestly instead of claiming "tenant matches the live brain"."""
    from navig.commands.doctor import check_reachability

    cfg = FakeConfig(**{
        "cloud.mode": "lighthouse",
        "cloud.lighthouse_url": EDGE,
        "deck.api_key": NEW_KEY,
        "telegram.webhook_url": "https://my-own-domain.example.com/tg/hook",
    })
    _patch_config(monkeypatch, cfg)

    results = check_reachability()
    assert "custom host" in results[0][2]
    assert "matches the live brain" not in results[0][2]


def test_doctor_skips_the_section_outside_lighthouse(monkeypatch):
    from navig.commands.doctor import check_reachability

    _patch_config(monkeypatch, FakeConfig(**{"cloud.mode": "direct"}))
    assert check_reachability() == [], "the section must not appear when not in lighthouse mode"


# ── rotating the key must not silently deafen the bot ────────────────────────


def test_key_rotation_repoints_the_webhook(monkeypatch):
    """`navig cloud key --rotate` mints a new deck.api_key — which moves the edge
    tenant. Without re-registering, Telegram keeps POSTing to the dead one."""
    from navig.cloud import rotation
    from navig.commands import cloud, lighthouse

    called: list[str] = []
    monkeypatch.setattr(
        lighthouse, "configure_telegram_webhook", lambda edge: called.append(edge) or "hook"
    )
    # NEVER let a test reach Telegram: unpatched, this would re-register the menu
    # button on the developer's REAL bot.
    monkeypatch.setattr(rotation, "repoint_menu_button", lambda cfg=None: False)
    monkeypatch.setattr(rotation, "manual_repoint_urls", lambda cfg=None: [])

    cloud._repoint_telegram_webhook(
        FakeConfig(**{
            "cloud.mode": "lighthouse",
            "cloud.lighthouse_url": EDGE,
            "deck.api_key": NEW_KEY,   # the freshly-rotated key
        })
    )
    assert called == [EDGE], "a rotation must re-register the webhook on the new tenant"


def test_key_rotation_is_a_noop_outside_lighthouse(monkeypatch):
    from navig.cloud import rotation
    from navig.commands import cloud, lighthouse

    called: list[str] = []
    monkeypatch.setattr(
        lighthouse, "configure_telegram_webhook", lambda edge: called.append(edge)
    )
    monkeypatch.setattr(rotation, "repoint_menu_button", lambda cfg=None: False)

    cloud._repoint_telegram_webhook(FakeConfig(**{"cloud.mode": "direct"}))
    assert called == [], "no lighthouse edge → nothing to re-point"


# ── the gateway's OWN rotation must heal both derivatives it orphans ─────────


def test_heal_reports_whether_a_rotation_happened(monkeypatch):
    """_heal_stale_tenant returns True ONLY when it actually healed a rotation — the
    caller uses that to decide whether the Mini App button needs re-pointing too."""
    from navig.gateway.channels.telegram import TelegramChannel

    saved: dict = {}

    class Cfg:
        def get(self, k, d=None):
            return {"cloud.mode": "lighthouse", "cloud.lighthouse_url": EDGE,
                    "deck.api_key": NEW_KEY}.get(k, d)

        def set(self, k, v, scope=None):
            saved[k] = v

        def save(self, scope=None):
            pass

    import navig.core

    monkeypatch.setattr(navig.core, "Config", lambda *a, **k: Cfg())

    stale = type("S", (), {"webhook_url": webhook_url_for(EDGE, OLD_KEY)})()
    assert TelegramChannel._heal_stale_tenant(stale) is True
    assert stale.webhook_url == webhook_url_for(EDGE, NEW_KEY)
    assert saved["telegram.webhook_url"] == webhook_url_for(EDGE, NEW_KEY)

    healthy = type("S", (), {"webhook_url": webhook_url_for(EDGE, NEW_KEY)})()
    assert TelegramChannel._heal_stale_tenant(healthy) is False, "no rotation → no heal"


async def test_menu_button_heal_calls_telegram(monkeypatch):
    """The rotation that orphaned the webhook also orphaned the Mini App button (it
    embeds the RAW key), so the heal path must re-register it."""
    from navig.cloud import rotation
    from navig.gateway.channels.telegram import TelegramChannel

    url = f"https://deck.example{'/'}connect?key=x"
    monkeypatch.setattr(rotation, "menu_button_url", lambda cfg=None: url)

    calls: list[tuple] = []

    class Chan:
        async def _api_call(self, method, data):
            calls.append((method, data))
            return {"ok": True}

    await TelegramChannel._heal_menu_button(Chan())

    assert calls and calls[0][0] == "setChatMenuButton"
    assert calls[0][1]["menu_button"]["web_app"]["url"] == url


async def test_menu_button_heal_skips_when_no_deck(monkeypatch):
    from navig.cloud import rotation
    from navig.gateway.channels.telegram import TelegramChannel

    monkeypatch.setattr(rotation, "menu_button_url", lambda cfg=None: "")

    calls: list = []

    class Chan:
        async def _api_call(self, method, data):
            calls.append(method)

    await TelegramChannel._heal_menu_button(Chan())
    assert calls == [], "no deck deployed → no button to re-point"
