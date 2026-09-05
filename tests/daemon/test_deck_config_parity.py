"""`_deck_config()` must supply every key `register_deck_routes` actually reads.

It is a hand-written whitelist, so a key the consumer reads but the builder omits
does not fail — the consumer silently sees its DEFAULT. Two had drifted:

  api_key       — absent meant `deck_key.resolve("")` reported the config as WIPED
                  and "restored" the key from the vault mirror on EVERY
                  registration: a config write, a "the config was wiped, not
                  freshly installed" warning, and a `deck_key_restored` incident
                  each time. Measured on the operator's machine 2026-09-05: 84 of
                  them, one every ~130s, while `deck.api_key` was present in
                  config the whole time. Nothing was wiped; the reader never asked
                  for the key.

                  The dangerous half is the branch next door: with no vault mirror
                  that same path MINTS a new key, which moves the Lighthouse tenant
                  and silently kills the bot, the Mini App and every ingest URL.

  telegram_only — absent meant the remote lockdown defaulted to OFF on this
                  surface, so `deck.telegram_only=true` was not honoured here even
                  though the gateway-server path (which passes the raw dict)
                  honoured it.

The guard derives the requirement from the CONSUMER rather than pinning a list, so
adding a `deck_cfg.get("something")` to `register_deck_routes` fails here until the
builder supplies it.
"""

from __future__ import annotations

import ast
from pathlib import Path

_CORE = Path(__file__).resolve().parents[2]
_CONSUMER = _CORE / "navig" / "gateway" / "deck" / "__init__.py"
_BUILDER = _CORE / "navig" / "daemon" / "telegram_worker.py"


def _keys_the_consumer_reads() -> set[str]:
    """Every literal key read off the `deck_cfg` parameter in register_deck_routes."""
    tree = ast.parse(_CONSUMER.read_text(encoding="utf-8"))
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "get":
            continue
        base = node.func.value
        if not (isinstance(base, ast.Name) and base.id == "deck_cfg"):
            continue
        if node.args and isinstance(node.args[0], ast.Constant):
            k = node.args[0].value
            if isinstance(k, str):
                out.add(k)
    return out


def _keys_the_builder_supplies() -> set[str]:
    """The literal keys of the dict `_deck_config()` returns."""
    tree = ast.parse(_BUILDER.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_deck_config":
            for sub in ast.walk(node):
                if isinstance(sub, ast.Return) and isinstance(sub.value, ast.Dict):
                    return {
                        k.value
                        for k in sub.value.keys
                        if isinstance(k, ast.Constant) and isinstance(k.value, str)
                    }
    return set()


def test_the_scan_finds_both_sides() -> None:
    """Anti-vacuity floor: an empty set on either side makes the real test pass
    while comparing nothing."""
    consumer = _keys_the_consumer_reads()
    builder = _keys_the_builder_supplies()
    assert len(consumer) >= 4, f"only found {consumer} — the consumer scan is broken"
    assert len(builder) >= 5, f"only found {builder} — the builder scan is broken"
    # The two that actually drifted must be visible to the scan.
    assert "api_key" in consumer and "telegram_only" in consumer, consumer


def test_the_builder_supplies_every_key_the_consumer_reads() -> None:
    consumer = _keys_the_consumer_reads()
    builder = _keys_the_builder_supplies()
    missing = sorted(consumer - builder)
    assert not missing, (
        f"_deck_config() omits {missing}, which register_deck_routes reads off "
        "deck_cfg. A hand-written whitelist does not fail on a missing key — the "
        "consumer silently gets its DEFAULT. For api_key that means the deck key "
        "is treated as WIPED (restore-from-vault every registration, or a NEW key "
        "minted, which moves the Lighthouse tenant); for telegram_only it means "
        "the remote lockdown is off."
    )


def test_api_key_is_not_coerced_away() -> None:
    """It must be passed through as-is: a coercion here would corrupt the credential."""
    src = _BUILDER.read_text(encoding="utf-8")
    assert '"api_key": deck_cfg.get("api_key", "")' in src, (
        "api_key must be passed through unchanged from the raw deck config"
    )
