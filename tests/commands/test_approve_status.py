"""`navig approve status | level | policy | list` — the face of `approval.security_level`.

Owner ask 2026-10-15: "what level of security are we at, and how do I reduce it?" The
3-level knob shipped with no way to SEE it. Pinned here:

* `status` answers configured-vs-running — the policy is built ONCE at gateway start,
  so the config file and the daemon can disagree (set a level, forget to restart);
  that drift is exactly what made today's fix invisible, and `status` is the only
  thing that says so;
* `level` refuses a typo instead of storing some other level, and verifies by reading
  back (a save that did not land must not print a tick);
* `policy` shows the CONFIGURED policy, not ``ApprovalPolicy.default()``;
* `list` survives the REAL route shape — the route sends ``command``, the old CLI
  read ``req['action']`` and raised KeyError for any actual pending request, while its
  test fed the wrong key and stayed green.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.approval.policies import (
    LEVEL_SUMMARY,
    SECURITY_LEVELS,
    ApprovalPolicy,
    parse_security_level,
)
from navig.commands.gateway import approve_app

pytestmark = pytest.mark.integration


class _Resp:
    def __init__(self, status: int, body: object = None):
        self.status_code = status
        self._body = body
        self.text = "" if body is None else json.dumps(body)

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


def _envelope(data: object) -> dict:
    return {"ok": True, "data": data, "error": None}


@pytest.fixture
def gateway(monkeypatch):
    """Script the gateway's answer; ``box['resp']`` may also be an exception."""
    box: dict[str, object] = {"resp": ConnectionError("down")}
    calls: list[tuple[str, str]] = []

    def fake(method: str, path: str, **kwargs):
        calls.append((method, path))
        resp = box["resp"]
        if isinstance(resp, Exception):
            raise resp
        return resp

    monkeypatch.setattr("navig.commands.gateway._gw_request", fake)
    box["calls"] = calls
    return box


@pytest.fixture
def isolated_config():
    """A config dir that is provably NOT the operator's real one, restored after.

    `ConfigManager.set_global` writes for real. The session conftest repoints
    NAVIG_CONFIG_DIR, but "config is isolated" has failed this suite before
    (a probe wrote the real key store) — so resolve the path and refuse to run if
    it is the real one, rather than trusting the fixture by name.
    """
    import os

    from navig.config import ConfigManager

    cm = ConfigManager()
    # Prove isolation by construction, never by naming the real home (the
    # no-hardcoded-home guard bans that, rightly): the resolved dir must be EXACTLY the
    # one NAVIG_CONFIG_DIR points at. If the env var is unset, or the manager resolved
    # somewhere else, `set_global` below would write the operator's real config.
    pinned = os.environ.get("NAVIG_CONFIG_DIR")
    resolved = Path(cm.global_config_dir).resolve()
    assert pinned and resolved == Path(pinned).resolve(), (
        f"refusing to run: config dir resolves to {resolved}, not the isolated "
        f"NAVIG_CONFIG_DIR ({pinned!r}) — set_global would write a real config"
    )
    original = (cm.refresh_global_config().get("approval") or {}).copy()
    yield cm
    cm.set_global("approval", original)


def _invoke(*args: str):
    return CliRunner().invoke(approve_app, list(args), obj={})


# ── the strict parser ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("high", "high"),
        ("  LOW ", "low"),
        ("strict", "high"),
        ("balanced", "medium"),
        ("permissive", "low"),
        ("hgih", None),  # a typo is NOT some other level
        ("", None),
        (None, None),
        ("off", None),  # "off" is approval.enabled, outside the three levels
    ],
)
def test_parse_is_strict(raw, expected):
    assert parse_security_level(raw) == expected


def test_every_level_has_a_summary():
    assert set(LEVEL_SUMMARY) == set(SECURITY_LEVELS)


# ── posture ───────────────────────────────────────────────────────────────────


def test_posture_is_json_safe_and_reflects_the_level():
    low = ApprovalPolicy.from_config({"approval": {"security_level": "low"}})
    posture = low.posture(read_bypass=True)
    json.dumps(posture)  # raises on a non-serialisable value
    assert posture["security_level"] == "low"
    assert posture["shell_exempt"] is True
    assert posture["summary"] == LEVEL_SUMMARY["low"]

    high = ApprovalPolicy.from_config({"approval": {"security_level": "high"}})
    assert high.posture(read_bypass=False)["fail_closed_timeout"] is True
    assert high.posture(read_bypass=False)["shell_exempt"] is False


# ── status ────────────────────────────────────────────────────────────────────


def test_status_with_the_gateway_down_still_answers(gateway, isolated_config):
    result = _invoke("status")

    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "Security level" in flat
    assert "not running" in flat, "a dead gateway must be SAID, not silently omitted"


def test_status_flags_a_gateway_still_on_the_old_level(gateway, isolated_config):
    """THE drift case: config says low, the daemon booted on medium."""
    isolated_config.set_global("approval.security_level", "low")
    posture = ApprovalPolicy.from_config({"approval": {"security_level": "medium"}}).posture(
        read_bypass=True
    )
    gateway["resp"] = _Resp(200, _envelope(posture))

    result = _invoke("status", "--json")

    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["configured"]["security_level"] == "low"
    assert data["gateway"]["security_level"] == "medium"
    assert data["restart_pending"] is True


def test_status_reports_no_drift_when_they_agree(gateway, isolated_config):
    isolated_config.set_global("approval.security_level", "medium")
    posture = ApprovalPolicy.from_config({"approval": {"security_level": "medium"}}).posture(
        read_bypass=True
    )
    gateway["resp"] = _Resp(200, _envelope(posture))

    data = json.loads(_invoke("status", "--json").output)

    assert data["restart_pending"] is False
    assert data["gateway_state"] == "live"


def test_a_gateway_without_the_route_is_reported_as_older_code(gateway, isolated_config):
    """404 on /approval/policy = the daemon is up but running pre-feature code —
    precisely today's situation, and it must read as restart pending."""
    gateway["resp"] = _Resp(404)

    data = json.loads(_invoke("status", "--json").output)

    assert data["gateway_state"] == "outdated"
    assert data["restart_pending"] is True


def test_status_describes_low_in_plain_words(gateway, isolated_config):
    isolated_config.set_global("approval.security_level", "low")

    flat = " ".join(_invoke("status").output.split())

    assert "never asks" in flat and "reads AND writes" in flat


# ── level ─────────────────────────────────────────────────────────────────────


def test_level_rejects_a_typo_and_stores_nothing(isolated_config):
    isolated_config.set_global("approval.security_level", "medium")

    result = _invoke("level", "hgih")

    assert result.exit_code == 2, result.output
    assert "not a security level" in result.output
    assert isolated_config.refresh_global_config()["approval"]["security_level"] == "medium"


def test_level_sets_and_reads_back(isolated_config):
    result = _invoke("level", "low")

    assert result.exit_code == 0, result.output
    assert isolated_config.refresh_global_config()["approval"]["security_level"] == "low"
    flat = " ".join(result.output.split())
    assert "no longer ask" in flat, "lowering to low must say what it gives up"
    assert "navig service restart" in flat, "the restart requirement must be stated"


def test_level_accepts_an_alias_and_stores_the_canonical_value(isolated_config):
    _invoke("level", "strict")

    assert isolated_config.refresh_global_config()["approval"]["security_level"] == "high"


def test_level_noop_says_so(isolated_config):
    isolated_config.set_global("approval.security_level", "high")

    result = _invoke("level", "high")

    assert result.exit_code == 0
    assert "already high" in " ".join(result.output.split())


# ── policy ────────────────────────────────────────────────────────────────────


def test_policy_shows_the_configured_patterns_not_the_defaults(isolated_config):
    isolated_config.set_global("approval.security_level", "low")
    isolated_config.set_global("approval.levels", {"safe": ["my-custom-safe *"]})

    flat = " ".join(_invoke("policy").output.split())

    assert "my-custom-safe *" in flat, (
        "`approve policy` rendered ApprovalPolicy.default() — the shipped patterns — "
        "over a customised config"
    )
    assert "tool bash_exec*" in flat, "low's derived shell patterns must show"
    assert "Security level: low" in flat


# ── list: the real route shape ────────────────────────────────────────────────


def test_list_survives_what_the_route_actually_sends(gateway):
    """`/approval/pending` returns `command`, never `action`. The CLI read
    `req['action']` and raised KeyError for any real pending request."""
    gateway["resp"] = _Resp(
        200,
        _envelope(
            {
                "pending": [
                    {
                        "id": "ab12cd34",
                        "command": "tool bash_exec",
                        "level": "confirm",
                        "description": "Agent tool call: bash_exec",
                        "user_id": "12345",
                        "created_at": "2026-10-15T02:38:00",
                    }
                ]
            }
        ),
    )

    result = _invoke("list")

    assert result.exit_code == 0, result.output
    flat = " ".join(result.output.split())
    assert "ab12cd34" in flat and "tool bash_exec" in flat


# ── the route, end to end ─────────────────────────────────────────────────────


async def test_the_gateway_route_returns_the_running_posture():
    """GET /approval/policy answers from the manager's LIVE policy object."""
    from navig.approval.manager import ApprovalManager
    from navig.gateway.routes import approval as route

    manager = ApprovalManager(
        policy=ApprovalPolicy.from_config({"approval": {"security_level": "high"}})
    )

    class _Gateway:
        approval_manager = manager

    handler = route._policy(_Gateway())

    class _Req:
        headers: dict = {}
        remote = "127.0.0.1"

    # The bearer check is the route layer's own contract (exercised by the gateway
    # route tests); here it is neutralised so THIS test pins only the payload.
    from unittest.mock import patch

    with patch.object(route, "require_bearer_auth", return_value=None):
        response = await handler(_Req())

    body = json.loads(response.body)
    data = body.get("data", body)
    assert data["security_level"] == "high"
    assert data["fail_closed_timeout"] is True
