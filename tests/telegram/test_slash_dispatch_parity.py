"""A parameterised slash command must never silently lose its argument.

There are two places that turn a `_SLASH_REGISTRY` entry into a handler call:
the live dispatcher in ``TelegramChannel`` and ``_build_slash_handlers``. The
live one forwards ``text`` (the raw ``/lang Russian``); the other one did not,
so every handler that reads its argument from ``text`` would have received ``""``
and behaved as though the user had sent the command bare — ``/lang Russian``
would report the current language instead of setting it, with no error anywhere.

That second dispatcher is currently unwired, which is exactly why it drifted.
These tests pin the contract so it cannot drift again, and so a *new*
parameterised command can't be added against a dispatcher that drops its input.
"""

from __future__ import annotations

import inspect

import pytest

from navig.gateway.channels import telegram_commands as tc

pytestmark = pytest.mark.integration

def _parameterised_commands() -> list[tuple[str, str]]:
    """Every registry command whose handler reads a user-supplied ``text``.

    DERIVED, never hardcoded. A literal list would have said ``{"lang"}`` — the
    command that happened to prompt this — while **41** handlers actually declare
    `text`: /run, /search, /remindme, /weather, /space, /skill, /trace and the
    rest. Scoping a guard to the example that revealed the bug is how the same
    bug survives one directory over.
    """
    found: list[tuple[str, str]] = []
    for entry in tc._SLASH_REGISTRY:
        if not entry.handler:
            continue
        method = getattr(tc.TelegramCommandsMixin, entry.handler, None)
        if method is None:
            continue
        try:
            params = inspect.signature(method).parameters
        except (ValueError, TypeError):
            continue
        if "text" in params:
            found.append((entry.command, entry.handler))
    return found


#: A discovery that silently returns nothing looks exactly like a clean run, so
#: assert a floor well below the measured 41 rather than trusting the scan.
_MIN_PARAMETERISED = 20


def test_registry_dispatcher_forwards_text():
    """`_build_slash_handlers` must accept and forward `text`."""
    sig = inspect.signature(tc.TelegramCommandsMixin._build_slash_handlers)
    assert "text" in sig.parameters, (
        "a dispatcher that cannot receive `text` hands every parameterised "
        "command an empty argument"
    )

    src = inspect.getsource(tc.TelegramCommandsMixin._build_slash_handlers)
    assert '"text": text' in src, (
        "`text` is accepted but not placed in the handler context — the argument "
        "still never reaches the handler"
    )


def test_the_scan_actually_finds_the_parameterised_commands():
    """Guards the guard: a scan that returns nothing would pass every test below
    while checking absolutely nothing."""
    found = _parameterised_commands()
    assert len(found) >= _MIN_PARAMETERISED, (
        f"only {len(found)} parameterised commands discovered — the scan is broken, "
        "not the registry"
    )
    names = {c for c, _ in found}
    # A few by name, spanning different feature areas, so a scan that silently
    # narrows to one module still fails.
    for expected in ("lang", "run", "search", "remindme"):
        assert expected in names, f"/{expected} reads text but was not discovered"


def test_every_parameterised_handler_can_receive_its_argument():
    """Both dispatchers filter kwargs by signature, so a handler that omits the
    parameter is silently called WITHOUT it rather than raising — the argument
    just vanishes and the command behaves as though it were sent bare."""
    missing = [
        f"{cmd} -> {handler}"
        for cmd, handler in _parameterised_commands()
        if "text" not in inspect.signature(getattr(tc.TelegramCommandsMixin, handler)).parameters
    ]
    assert not missing, f"handlers that cannot receive their argument: {missing}"


def test_lang_is_registered_and_visible():
    """An unregistered command falls through to the chat model, which will
    happily invent a confirmation for something it never did."""
    entry = next((e for e in tc._SLASH_REGISTRY if e.command == "lang"), None)
    assert entry is not None
    assert entry.visible, "/lang should appear in the Telegram command list"
    assert entry.usage, "a parameterised command needs a usage hint in /help"
