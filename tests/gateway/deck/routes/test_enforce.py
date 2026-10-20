"""The deck authorization adapter: server-derived identity, one slug mapping, composed gates.

Regression target: deck remote routes executed with no policy check, no host lock and no
audit record, while the identical CLI verbs were gated — and the actor was client-settable.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


# ── fakes ────────────────────────────────────────────────────────────────────


class FakeRequest(dict):
    """Just enough of ``web.Request``: mapping keys for identity, ``app`` for the gateway."""

    def __init__(self, app: dict | None = None, **identity):
        super().__init__(**identity)
        self.app = app or {}


def _policy_gw(level: str | None):
    """A gateway stub whose approval manager classifies every command as *level*."""
    from navig.approval.policies import ApprovalLevel

    policy = None
    if level is not None:
        policy = type(
            "StubPolicy",
            (),
            {"classify_command": lambda self, cmd: ApprovalLevel(level)},
        )()
    am = type("StubAM", (), {"policy": policy})()
    return type("StubGW", (), {"approval_manager": am})()


# ── deck_actor ───────────────────────────────────────────────────────────────


def test_actor_prefers_the_brain_device():
    from navig.gateway.deck.routes._enforce import deck_actor

    req = FakeRequest(deck_brain_device="dev-1", deck_auth_local=True, deck_user_id=42)
    assert deck_actor(req) == "brain-device:dev-1"


def test_actor_falls_back_local_then_user_then_unknown():
    from navig.gateway.deck.routes._enforce import deck_actor

    assert deck_actor(FakeRequest(deck_auth_local=True, deck_user_id=42)) == "deck:local"
    assert deck_actor(FakeRequest(deck_user_id=42)) == "deck:42"
    assert deck_actor(FakeRequest()) == "deck:unknown"


def test_actor_ignores_x_actor_headers_entirely():
    """Identity is server-derived: a hostile header must never appear in the audit."""
    from navig.gateway.deck.routes._enforce import deck_actor

    req = FakeRequest(deck_user_id=7)
    req.headers = {"X-Actor": "mallory"}  # noqa: B010 — deliberately ignored by design
    assert deck_actor(req) == "deck:7"


# ── action_for_shell ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("level", "expected_slug", "expected_level"),
    [
        ("never", "remote.exec.never", "never"),
        ("dangerous", "remote.exec.dangerous", "dangerous"),
        ("safe", "remote.exec", "safe"),
        ("confirm", "remote.exec", "confirm"),
    ],
)
def test_slug_mapping_covers_every_approval_level(level, expected_slug, expected_level):
    from navig.gateway.deck.routes._enforce import action_for_shell

    gw = _policy_gw(level)
    slug, lv = action_for_shell("navig run 'ls'", "remote.exec", gw=gw)
    assert (slug, lv) == (expected_slug, expected_level)


def test_slug_mapping_without_a_gateway_falls_back_to_a_default_policy():
    """Unlisted commands classify CONFIRM under the default policy → base slug."""
    from navig.gateway.deck.routes._enforce import action_for_shell

    slug, lv = action_for_shell("something never seen before", "cli.exec", gw=None)
    assert slug == "cli.exec"
    assert lv == "confirm"


def test_slug_mapping_never_crashes_a_route_on_a_broken_policy():
    from navig.gateway.deck.routes._enforce import action_for_shell

    gw = type(
        "BrokenGW",
        (),
        {
            "approval_manager": type(
                "AM", (), {"policy": type("P", (), {"classify_command": staticmethod(lambda c: 1 / 0)})()}
            )()
        },
    )()
    slug, lv = action_for_shell("rm -rf /", "remote.exec", gw=gw)
    assert (slug, lv) == ("remote.exec", "confirm")  # fail toward the base slug


# ── authorize_deck ───────────────────────────────────────────────────────────


async def test_no_gateway_wiring_fails_closed_with_503():
    from navig.gateway.deck.routes._enforce import authorize_deck

    resp = await authorize_deck(FakeRequest(), "remote.exec")
    assert resp is not None and resp.status == 503
    body = json.loads(resp.body)
    assert body["error_code"] == "auth_unavailable"


async def test_policy_deny_passes_the_403_through_untouched():
    from aiohttp import web

    from navig.gateway.deck.routes._enforce import authorize_deck

    deny = web.json_response({"ok": False, "error_code": "policy_denied"}, status=403)

    async def policy_check(action, actor, raw_input=""):
        assert action == "remote.exec.dangerous"
        assert actor == "deck:7"
        return deny

    gw = type("GW", (), {"policy_check": staticmethod(policy_check)})()
    req = FakeRequest({"gateway": gw}, deck_user_id=7)
    resp = await authorize_deck(req, "remote.exec.dangerous", raw_input="rm -rf /")
    assert resp is deny  # the very response policy_check produced — never re-wrapped


async def test_allowed_action_without_host_returns_none():
    from navig.gateway.deck.routes._enforce import authorize_deck

    async def policy_check(action, actor, raw_input=""):
        return None

    gw = type("GW", (), {"policy_check": staticmethod(policy_check)})()
    assert await authorize_deck(FakeRequest({"gateway": gw}), "remote.exec") is None


async def test_host_conflict_maps_to_409_and_describes_the_holder(
    monkeypatch,
):
    """Policy passes, but another session holds the host → 409, execution must not start."""
    import navig.config as config_mod
    from navig.core import host_lock
    from navig.gateway.deck.routes._enforce import authorize_deck

    async def policy_check(action, actor, raw_input=""):
        return None

    gw = type("GW", (), {"policy_check": staticmethod(policy_check)})()

    class Cfg:
        def is_local_host(self, host):
            return False

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: Cfg())

    # Another live session already holds it.
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-A")
    host_lock.claim("vps-1", "other session work")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")

    resp = await authorize_deck(
        FakeRequest({"gateway": gw}), "remote.exec", host="vps-1", command="rm -rf /"
    )
    assert resp is not None and resp.status == 409
    body = json.loads(resp.body)
    assert body["error_code"] == "host_locked"
    assert "session-A" in body["detail"]
    # The loser claimed nothing.
    assert host_lock.lock_state(host_lock.read_lock("vps-1")).session == "session-A"


async def test_allowed_action_with_free_host_claims_and_proceeds(monkeypatch):
    import navig.config as config_mod
    from navig.core import host_lock
    from navig.gateway.deck.routes._enforce import authorize_deck

    async def policy_check(action, actor, raw_input=""):
        return None

    gw = type("GW", (), {"policy_check": staticmethod(policy_check)})()

    class Cfg:
        def is_local_host(self, host):
            return False

    monkeypatch.setattr(config_mod, "get_config_manager", lambda: Cfg())
    assert await authorize_deck(
        FakeRequest({"gateway": gw}), "remote.exec", host="vps-2", command="ls"
    ) is None
    assert host_lock.lock_state(host_lock.read_lock("vps-2")).state == "mine"
