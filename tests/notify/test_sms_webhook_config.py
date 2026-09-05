"""Tests for Twilio inbound-webhook auto-configuration.

`resolve_public_base` had no direct coverage. It read `cloud.public_url` through
`navig.core.Config()`, and BOTH the ConfigSingleton and the cached ConfigManager serve
the snapshot they loaded at process start — freshness is a deliberate opt-in
(`refresh_global_config`), not automatic. So in the long-lived daemon,
`navig config set cloud.public_url <domain>` had no effect until a restart: the read
returned the boot-time value, execution fell through to the rotating quick-tunnel URL,
and Twilio kept being re-pointed at `*.trycloudflare.com`.

That is the exact churn the module docstring tells the operator to avoid by setting
that very key — documentation disagreeing with implementation.
"""

from __future__ import annotations

import pytest

from navig.notify import sms_webhook_config as swc


class _RotatingTunnel:
    """A cloud manager whose public URL changes on every daemon restart."""

    URL = "https://random-words-1234.trycloudflare.com"

    def snapshot(self) -> dict:
        return {"public_url": self.URL}


class _Gateway:
    cloud_manager = _RotatingTunnel()


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """Point the whole config layer at a throwaway dir and reset its caches.

    NAVIG_CONFIG_DIR must be set BEFORE anything resolves config_dir(), and the
    process-wide ConfigManager/ConfigSingleton caches have to be dropped or a manager
    built by an earlier test leaks the wrong directory in.
    """
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

    from navig import config as config_mod
    from navig.core import shared_config

    config_mod.reset_config_manager()
    monkeypatch.setattr(shared_config.ConfigSingleton, "_instance", None, raising=False)

    def _write(public_url: str) -> None:
        (tmp_path / "config.yaml").write_text(
            f"cloud:\n  public_url: '{public_url}'\n", encoding="utf-8"
        )

    _write("")
    yield _write
    config_mod.reset_config_manager()
    monkeypatch.setattr(shared_config.ConfigSingleton, "_instance", None, raising=False)


def test_falls_back_to_the_tunnel_when_no_stable_domain_is_set(isolated_config):
    assert swc.resolve_public_base(_Gateway()) == _RotatingTunnel.URL


def test_a_stable_domain_set_at_runtime_is_picked_up_without_a_restart(isolated_config):
    """THE REGRESSION: the documented remedy must not require a daemon restart."""
    gw = _Gateway()
    assert swc.resolve_public_base(gw) == _RotatingTunnel.URL  # precondition

    isolated_config("https://ops.example.com")  # `navig config set …` from the CLI

    assert swc.resolve_public_base(gw) == "https://ops.example.com", (
        "a stable cloud.public_url set while the daemon runs was ignored, so Twilio "
        "kept being re-pointed at the rotating quick-tunnel URL"
    )


def test_a_stable_domain_wins_over_the_tunnel(isolated_config):
    isolated_config("https://ops.example.com")
    assert swc.resolve_public_base(_Gateway()) == "https://ops.example.com"


def test_trailing_slashes_and_whitespace_are_normalised(isolated_config):
    isolated_config("  https://ops.example.com/  ")
    assert swc.resolve_public_base(_Gateway()) == "https://ops.example.com"


def test_no_domain_and_no_gateway_yields_none(isolated_config):
    assert swc.resolve_public_base(None) is None


def test_a_non_http_tunnel_value_is_rejected(isolated_config):
    """Guards against handing Twilio something that isn't a URL."""

    class _Bad:
        def snapshot(self) -> dict:
            return {"public_url": "not-a-url"}

    class _GW:
        cloud_manager = _Bad()

    assert swc.resolve_public_base(_GW()) is None


async def test_auto_configure_reports_a_missing_public_url_actionably(isolated_config):
    """No public URL is a configuration gap, not a silent no-op."""
    result = await swc.auto_configure(None)

    assert result["ok"] is False
    assert result["reason"] == "no_public_url"
    assert "cloud.public_url" in result["hint"]
