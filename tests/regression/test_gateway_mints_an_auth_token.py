"""A default install must not let any local process answer the agent's approvals.

`require_bearer_auth` opens with `if not token: return None` — **no token means open
access** — and `gateway.auth.token` had no default. Seventeen route modules sit behind
it, and one of them is `POST /approval/{id}/respond`.

So on a default install a local process could enumerate the agent's pending approvals
(`GET /approval/pending`, which includes the rendered description) and answer them. An
approval endpoint anyone can call is not an approval endpoint: it silently defeats the
entire gate, no matter how carefully the gate itself is built.

The gateway now mints and persists a token at startup, the same way it has always
auto-generated `deck.api_key`. Minting rather than refusing, because refusing breaks the
only consumer: `navig gateway approve` reads this same config key, so a minted token is
picked up transparently, while the deck and desktop authenticate with `deck.api_key` on
separate routes and are untouched.

⚠ The subtle requirement is **enforce only what was persisted**. A token held only in
memory is a token no client can read — the CLI would send no header and be locked out of
its own gateway. A failed write therefore leaves the previous behaviour in place, loudly.
"""

from __future__ import annotations

import pytest

from navig.gateway.server import _ensure_auth_token


class _Recorder:
    """Stands in for ConfigManager, capturing the dotted write."""

    def __init__(self, *, fail: bool = False) -> None:
        self.writes: list[tuple[str, str]] = []
        self._fail = fail

    def set_global(self, key: str, value: str) -> None:
        if self._fail:
            raise OSError("config is read-only")
        self.writes.append((key, value))


@pytest.fixture
def recorder(monkeypatch) -> _Recorder:
    rec = _Recorder()
    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: rec)
    return rec


def test_a_token_is_minted_and_persisted(recorder) -> None:
    cfg: dict = {}
    token = _ensure_auth_token(cfg)

    assert token
    assert recorder.writes == [("gateway.auth.token", token)], (
        "the token must be persisted, or the next restart mints a different one and "
        "every client that cached the old value breaks"
    )


def test_the_minted_token_is_not_guessable(recorder) -> None:
    """A predictable token is the same hole with extra steps."""
    tokens = {_ensure_auth_token({}) for _ in range(20)}

    assert len(tokens) == 20, "minted tokens repeat"
    assert all(len(t) >= 32 for t in tokens), "token is too short to resist guessing"


def test_the_live_config_section_is_updated(recorder) -> None:
    """Anything else reading this dict during boot must see the same value."""
    cfg: dict = {}
    token = _ensure_auth_token(cfg)

    assert cfg["auth"]["token"] == token


def test_a_failed_persist_does_not_enforce(monkeypatch) -> None:
    """A token that exists nowhere a client can read locks the operator out of their own
    gateway. Fail-open is wrong in general and the lesser wrong here."""
    monkeypatch.setattr(
        "navig.config.get_config_manager", lambda *a, **k: _Recorder(fail=True)
    )

    assert _ensure_auth_token({}) is None


def test_a_failed_persist_is_loud(monkeypatch) -> None:
    """Silently staying open is how this hole survived in the first place."""
    import navig.gateway.server as srv

    monkeypatch.setattr(
        "navig.config.get_config_manager", lambda *a, **k: _Recorder(fail=True)
    )
    errors: list[str] = []
    monkeypatch.setattr(srv.logger, "error", lambda msg, *a, **k: errors.append(msg))

    _ensure_auth_token({})

    assert errors, "a gateway left unauthenticated said nothing"
    assert "UNAUTHENTICATED" in errors[0]
    assert "navig config set gateway.auth.token" in errors[0], "no remedy given"


def test_parsing_a_config_never_writes_to_disk(monkeypatch) -> None:
    """Constructing a GatewayConfig must stay a PURE PARSE.

    The first version minted here, and that gave a cheap constructor a disk write:
    every test that built a config — and there are many — wrote a token into the
    operator's real ~/.navig/config.yaml. Four existing gateway tests caught it by
    turning 401, which is how the design flaw surfaced.
    """
    from navig.gateway.server import GatewayConfig

    minted: list = []
    monkeypatch.setattr(
        "navig.gateway.server._ensure_auth_token",
        lambda cfg: minted.append(cfg) or "NEWLY-MINTED",
    )

    assert GatewayConfig({}).auth_token is None
    assert GatewayConfig({"gateway": {}}).auth_token is None
    assert minted == [], "parsing a config minted a credential and wrote it to disk"


def test_an_existing_token_is_parsed(monkeypatch) -> None:
    """Re-minting over a configured token would invalidate whatever the operator wired
    up — the exact breakage `deck.api_key` learned to persist against."""
    from navig.gateway.server import GatewayConfig

    cfg = GatewayConfig({"gateway": {"auth": {"token": "already-set"}}})

    assert cfg.auth_token == "already-set"


def test_the_raw_section_is_kept_for_the_later_mint() -> None:
    """`start()` writes the minted token back into the same dict the rest of the boot
    path reads, so it needs a handle on it."""
    from navig.gateway.server import GatewayConfig

    raw = {"gateway": {"port": 1234}}
    assert GatewayConfig(raw).raw_gateway_cfg is raw["gateway"]


# ---------------------------------------------------------------------------
# The hole this closes
# ---------------------------------------------------------------------------


class _Cfg:
    def __init__(self, token) -> None:
        self.auth_token = token


class _GW:
    def __init__(self, token) -> None:
        self.config = _Cfg(token)


class _Req:
    def __init__(self, headers: dict | None = None) -> None:
        self.headers = headers or {}


def test_an_unauthenticated_caller_cannot_answer_an_approval() -> None:
    """The concrete escalation: enumerate the agent's pending approvals and answer them."""
    from navig.gateway.routes.common import require_bearer_auth

    denied = require_bearer_auth(_Req(), _GW("a-real-token"))

    assert denied is not None, (
        "a caller with no credential reached an approval-decision route"
    )
    assert denied.status == 401


def test_the_401_says_where_to_get_the_token() -> None:
    """A refusal the operator cannot act on is a dead end."""
    import json

    from navig.gateway.routes.common import require_bearer_auth

    denied = require_bearer_auth(_Req(), _GW("a-real-token"))
    body = json.loads(denied.body.decode())

    assert "gateway.auth.token" in json.dumps(body)


def test_the_right_token_is_accepted() -> None:
    from navig.gateway.routes.common import require_bearer_auth

    ok = require_bearer_auth(
        _Req({"Authorization": "Bearer a-real-token"}), _GW("a-real-token")
    )
    assert ok is None


def test_a_wrong_token_is_rejected() -> None:
    from navig.gateway.routes.common import require_bearer_auth

    denied = require_bearer_auth(
        _Req({"Authorization": "Bearer wrong"}), _GW("a-real-token")
    )
    assert denied is not None and denied.status == 401


def test_the_cli_sends_the_minted_token(monkeypatch, tmp_path) -> None:
    """The mint is only transparent if the CLI actually picks it up FROM A FILE.

    This test used to stub `_load_global_config` and assert the header came back --
    i.e. it proved the helper reads the reader it happens to read. It passed for
    months while the real CLI was locked out of the operator's own gateway with
    HTTP 401 on all seven `navig cron` commands, because the real reader returned
    the PYDANTIC-VALIDATED config, which silently drops every key the schema does
    not declare -- and `gateway.auth` is not declared.

    A mock of the broken reader cannot see that. So this now writes a real
    config.yaml and reads it through a real ConfigManager: the only shape of this
    test that can fail when the CLI is actually locked out.
    """
    from navig import gateway_client
    from navig.config import ConfigManager

    # Built with chr(10) rather than an escape so the YAML stays readable here.
    (tmp_path / "config.yaml").write_text(
        chr(10).join(
            ["gateway:", "  auth:", "    token: T", "  mesh_token: irrelevant", ""]
        ),
        encoding="utf-8",
    )
    # NAVIG_CONFIG_DIR is what moves `global_config_dir`, which is the dir this
    # reader actually opens. `ConfigManager(config_dir=...)` moves hosts_dir, NOT the
    # global config -- a distinction that has bitten this suite before.
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    real_manager = ConfigManager(config_dir=tmp_path)
    assert real_manager.global_config_dir == tmp_path, (
        f"the test is not reading the file it wrote: {real_manager.global_config_dir}"
    )
    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: real_manager)

    headers = gateway_client.gateway_request_headers()
    assert headers.get("Authorization") == "Bearer T", (
        "the CLI could not read a token that is plainly present in config.yaml -- "
        "the operator is locked out of their own gateway"
    )
