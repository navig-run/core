"""`navig telemetry disable` said "Telemetry disabled." and disabled nothing.

There are two telemetry surfaces and they did not know about each other:

* ``navig/onboarding/telemetry.py`` sends the only ping NAVIG makes — one anonymous
  install event, fired from ``navig init`` and from onboarding. Its sole gate was the
  ``NAVIG_NO_TELEMETRY`` environment variable.
* ``navig/commands/telemetry.py`` is the discoverable control. ``disable`` wrote
  ``telemetry.enabled=false`` via ConfigManager — and **nothing anywhere read that
  key**. (``settings/resolver.py`` declares a differently-named ``navig.telemetry.enabled``
  default, also unread.)

So the sequence a privacy-minded user actually performs — ``navig telemetry disable``,
see the green "Telemetry disabled.", then ``navig init`` — still sent the ping. The
status command was wrong in the other direction too: it read that unconsumed key with
``default=False``, so a fresh install was told "disabled" while the ping would fire.

The payload is genuinely anonymous and fires once, so the harm is small. A control that
reports success while doing nothing is the part worth fixing.

Note the default is deliberately unchanged: only an EXPLICIT false opts out. An unset
key means "not answered", so this narrows collection and never widens it.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.onboarding.telemetry import (
    _OPT_OUT_VAR,
    ping_install_if_first_time,
    telemetry_opted_out_in_config,
)


@pytest.fixture
def unpinged(tmp_path, monkeypatch):
    """A machine that has never pinged, with no env opt-out set."""
    monkeypatch.delenv(_OPT_OUT_VAR, raising=False)
    marker = tmp_path / ".pinged"
    monkeypatch.setattr("navig.onboarding.telemetry._pinged_marker", lambda: marker)
    return marker


def _config(value):
    """A ConfigManager whose get() returns `value` for the telemetry key."""
    cm = MagicMock()
    cm.get.side_effect = lambda key, default=None: value if key == "telemetry.enabled" else default
    return cm


def _posted(monkeypatch) -> list:
    """Capture requests.post without touching the network."""
    calls: list = []
    fake = MagicMock()
    fake.post.side_effect = lambda *a, **k: calls.append((a, k))
    monkeypatch.setitem(__import__("sys").modules, "requests", fake)
    return calls


# ── the opt-out predicate ───────────────────────────────────────────────────────


def test_an_unset_key_is_not_an_opt_out() -> None:
    """Unset means "not answered" — the ping's existing default must not change."""
    with patch("navig.config.ConfigManager", return_value=_config(None)):
        assert telemetry_opted_out_in_config() is False


def test_explicit_false_is_an_opt_out() -> None:
    with patch("navig.config.ConfigManager", return_value=_config(False)):
        assert telemetry_opted_out_in_config() is True


def test_the_string_false_is_an_opt_out() -> None:
    """`navig config set telemetry.enabled false` stores the STRING "false" (truthy)."""
    with patch("navig.config.ConfigManager", return_value=_config("false")):
        assert telemetry_opted_out_in_config() is True


@pytest.mark.parametrize("value", ["no", "0", "off", "False"])
def test_other_falsey_spellings_opt_out(value: str) -> None:
    with patch("navig.config.ConfigManager", return_value=_config(value)):
        assert telemetry_opted_out_in_config() is True


@pytest.mark.parametrize("value", [True, "true", "yes", "1", "on"])
def test_truthy_values_do_not_opt_out(value) -> None:
    with patch("navig.config.ConfigManager", return_value=_config(value)):
        assert telemetry_opted_out_in_config() is False


def test_a_broken_config_never_blocks_init() -> None:
    """This runs inside `navig init` — it must not raise, whatever config does."""
    with patch("navig.config.ConfigManager", side_effect=RuntimeError("config on fire")):
        assert telemetry_opted_out_in_config() is False


def test_strict_mode_surfaces_a_broken_config() -> None:
    """The status command must not report a state it could not read.

    The ping fails open so it never blocks `navig init`; `navig telemetry` says
    "unknown" instead of claiming "enabled". Same fact, two correct answers.
    """
    with patch("navig.config.ConfigManager", side_effect=RuntimeError("config on fire")):
        with pytest.raises(RuntimeError):
            telemetry_opted_out_in_config(strict=True)


# ── the ping honours it ─────────────────────────────────────────────────────────


def test_disabling_in_config_stops_the_ping(unpinged, monkeypatch) -> None:
    """The whole bug: this used to send anyway."""
    calls = _posted(monkeypatch)

    with patch("navig.config.ConfigManager", return_value=_config(False)):
        ping_install_if_first_time()

    assert calls == []


def test_declining_does_not_burn_the_first_run_marker(unpinged, monkeypatch) -> None:
    """Opting out must not silently consume the one-time ping, like the env var."""
    _posted(monkeypatch)

    with patch("navig.config.ConfigManager", return_value=_config(False)):
        ping_install_if_first_time()

    assert not unpinged.exists()


def test_the_env_var_still_wins(unpinged, monkeypatch) -> None:
    calls = _posted(monkeypatch)
    monkeypatch.setenv(_OPT_OUT_VAR, "1")

    with patch("navig.config.ConfigManager", return_value=_config(True)):
        ping_install_if_first_time()

    assert calls == []


def test_the_ping_still_fires_by_default(unpinged, monkeypatch) -> None:
    """Guard the other direction: this fix must not turn telemetry off for everyone."""
    calls = _posted(monkeypatch)

    with patch("navig.config.ConfigManager", return_value=_config(None)):
        ping_install_if_first_time()

    assert len(calls) == 1
    assert unpinged.exists()


def test_the_payload_is_still_only_the_documented_fields(unpinged, monkeypatch) -> None:
    """The privacy contract in the module docstring, pinned."""
    calls = _posted(monkeypatch)

    with patch("navig.config.ConfigManager", return_value=_config(None)):
        ping_install_if_first_time()

    payload = calls[0][1]["json"]
    assert set(payload) == {"event", "platform", "arch", "python", "anon_id"}
    assert payload["event"] == "install"


def test_a_second_run_is_silent(unpinged, monkeypatch) -> None:
    calls = _posted(monkeypatch)

    with patch("navig.config.ConfigManager", return_value=_config(None)):
        ping_install_if_first_time()
        ping_install_if_first_time()

    assert len(calls) == 1
