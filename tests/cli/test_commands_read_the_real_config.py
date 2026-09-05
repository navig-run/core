"""Commands must read config the operator actually set, not a schema-filtered copy.

`ConfigManager._load_global_config()` ends in
`validate_global_config(...).model_dump()`, so it returns ONLY fields the Pydantic
schema declares. Measured against the real schema (not a mock -- see
`test_the_schema_really_drops_these`): of

    telegram · discord · whatsapp · comms · email · proactive · deploy

only `telegram` survives. Six configured subtrees vanish silently.

Consequences found by following the reader, each user-visible:

* `navig proactive status` -- `proactive_cfg` was ALWAYS `{}`, so it reported
  Calendar and Email as disabled no matter what was set. `proactive.calendar.enabled`
  and `proactive.email.enabled` are DOCUMENTED toggles: their own status command
  could not read them.
* `navig gateway status` -- read `discord` / `whatsapp` / `comms.matrix` / `email`
  from the filtered view, so a configured channel reported as unconfigured.
* `deploy` -- global deploy defaults never merged, and `deploy.history_keep` always
  resolved to the built-in 50, discarding the operator's retention setting.

Same defect as the one that left the CLI unable to find `gateway.auth.token` (all
seven `navig cron` commands answered 401).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_the_schema_really_drops_these() -> None:
    """Discriminator, measured against the REAL schema -- no stubs.

    This is what makes the other tests meaningful: it proves the filtered view
    genuinely loses these keys, so a reader that uses it cannot work. If the schema
    is later widened to declare them, this fails and the comments elsewhere should be
    revisited -- deliberately, rather than rotting into folklore.
    """
    from navig.core.config_schema import validate_global_config

    probe = {
        "telegram": {"bot_token": "x"},
        "discord": {"bot_token": "x"},
        "whatsapp": {"enabled": True},
        "comms": {"matrix": {"homeserver": "h"}},
        "email": {"smtp_host": "h"},
        "proactive": {"calendar": {"enabled": True}},
        "deploy": {"history_keep": 7},
    }
    validated = validate_global_config(probe, strict=False)
    survived = set(validated.model_dump()) if validated else set()

    assert "telegram" in survived, "fixture assumption changed: telegram used to survive"
    for key in ("discord", "whatsapp", "comms", "email", "proactive", "deploy"):
        assert key not in survived, (
            f"the schema now declares {key!r} -- this suite's premise has changed"
        )


def _cm(real: dict):
    """A manager whose two readers disagree exactly as the real one does."""
    return SimpleNamespace(
        get_global_config=lambda: real,
        _load_global_config=lambda *a, **k: {"telegram": real.get("telegram", {})},
    )


def test_proactive_status_sees_a_configured_toggle(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    """A documented toggle must be visible to the command that reports on it."""
    from navig.commands import proactive as proactive_cmd

    # proactive.py imports get_config_manager INSIDE the function (lazy-import rule),
    # so patch the source module, not this one's namespace.
    monkeypatch.setattr(
        "navig.config.get_config_manager",
        lambda: _cm({"proactive": {"calendar": {"enabled": True, "provider": "google"}}}),
    )
    proactive_cmd.proactive_status()
    out = capsys.readouterr().out

    assert "google" in out, (
        "`navig proactive status` did not see a calendar the operator enabled -- it read "
        "the schema-filtered view, where `proactive` does not exist"
    )


def test_deploy_history_keep_honours_the_operators_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    """`deploy.history_keep` must not silently resolve to the built-in default."""
    import navig.config as navig_config

    monkeypatch.setattr(navig_config, "get_config_manager", lambda: _cm({"deploy": {"history_keep": 7}}))

    cm = navig_config.get_config_manager()
    keep = int((cm.get_global_config() or {}).get("deploy", {}).get("history_keep", 50))
    assert keep == 7, "the operator's retention setting was discarded for the default"

    # ...and the old reader is exactly why it was discarded.
    stale = int(cm._load_global_config().get("deploy", {}).get("history_keep", 50))
    assert stale == 50, "discriminator: the filtered view cannot see deploy.history_keep"
