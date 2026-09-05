"""`configured_channels()` must honour a channel the operator turned off.

`raw_config` here is the GLOBAL config dict, and `navig config set whatsapp.enabled
false` stores the *string* "false" — `bool("false")` is True, so WhatsApp kept
reporting itself as configured. Measured through the real function before the fix:

    configured_channels({"whatsapp": {"enabled": "false"}})  -> [..., 'WhatsApp']

`navig gateway status` (commands/gateway.py) already coerced this; channel_config
is documented as the mirror of that view and had drifted, so the two surfaces
disagreed about the same config.
"""
from __future__ import annotations

import pytest

from navig.messaging.channel_config import configured_channels


@pytest.mark.parametrize("value", ["false", "False", "no", "off", "0", False])
def test_a_disabled_whatsapp_is_not_reported_as_configured(value: object) -> None:
    assert "WhatsApp" not in configured_channels({"whatsapp": {"enabled": value}})


@pytest.mark.parametrize("value", ["true", "yes", "on", "1", True])
def test_an_enabled_whatsapp_is_still_reported(value: object) -> None:
    """The partner assertion — a fix that just stopped reporting WhatsApp would
    pass the test above and break the feature."""
    assert "WhatsApp" in configured_channels({"whatsapp": {"enabled": value}})


@pytest.mark.parametrize("value", ["false", "off"])
def test_the_env_style_spelling_is_coerced_too(value: str) -> None:
    """WHATSAPP_ENABLED comes from the environment, so it is a string by definition
    — the spelling most likely to carry "false"."""
    assert "WhatsApp" not in configured_channels(
        {"whatsapp": {"WHATSAPP_ENABLED": value}}
    )


def test_the_bridges_spelling_is_covered_by_the_same_read() -> None:
    """WhatsApp config is also accepted under `bridges.whatsapp`; the coercion must
    apply there too, since it is the same read."""
    cfg = {"bridges": {"whatsapp": {"enabled": "false"}}}
    assert "WhatsApp" not in configured_channels(cfg)
    cfg = {"bridges": {"whatsapp": {"enabled": "true"}}}
    assert "WhatsApp" in configured_channels(cfg)


def test_channel_config_agrees_with_gateway_status() -> None:
    """These two surfaces are documented mirrors ("Add a channel there → add it
    here"). They drifted once; this pins the shared truth table so the CLI status
    view and the messaging registry cannot disagree about the same config again."""
    from navig.core.coerce import coerce_bool

    for value in ("false", "no", "off", "true", "on", "1", True, False):
        wa_cfg = {"enabled": value}
        gateway_says = coerce_bool(wa_cfg.get("enabled"), default=False) or coerce_bool(
            wa_cfg.get("WHATSAPP_ENABLED"), default=False
        )
        channel_config_says = "WhatsApp" in configured_channels({"whatsapp": wa_cfg})
        assert gateway_says == channel_config_says, value
