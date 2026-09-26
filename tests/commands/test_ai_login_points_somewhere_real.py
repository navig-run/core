"""`navig ai login` must point at a command that exists.

Its OAuth registry (`OAUTH_PROVIDERS`) is EMPTY, so every invocation lands in the
unavailable branch. That branch used to say "OAuth authentication is not
currently available" and suggest pasting an API key — both wrong:
`navig connect login` is a working OAuth flow, and an operator with a Claude
subscription has no API key to paste.

Measured: an operator ran bare `navig ai login` twice in one session, got only
typer's "Missing argument 'PROVIDER'", and was never told where the working
command lives.
"""

from __future__ import annotations

import pytest

from navig.commands.ai import _CONNECT_TEMPLATE_FOR
from navig.providers.connect import CONNECTION_TEMPLATES


def test_every_advertised_template_actually_exists():
    """The phantom-hint class: advice naming something that does not exist."""
    missing = sorted({t for t in _CONNECT_TEMPLATE_FOR.values() if t not in CONNECTION_TEMPLATES})

    assert not missing, f"`navig ai login` would advertise unknown templates: {missing}"


def test_the_two_templates_the_bare_message_names_exist():
    """The no-argument path hardcodes these two, so they are pinned separately."""
    for template in ("claude-max", "chatgpt"):
        assert template in CONNECTION_TEMPLATES, f"bare guidance names unknown {template!r}"


@pytest.mark.parametrize(
    "typed,expected",
    [
        ("anthropic", "claude-max"),
        ("claude", "claude-max"),
        ("claude-max", "claude-max"),
        ("openai", "chatgpt"),
        ("chatgpt", "chatgpt"),
        # The OLD help text said "openai-codex"; someone copying it must still land.
        ("openai-codex", "chatgpt"),
    ],
)
def test_what_people_type_maps_to_the_right_template(typed, expected):
    assert _CONNECT_TEMPLATE_FOR[typed] == expected


def test_the_provider_argument_is_optional():
    """Bare `navig ai login` must give guidance, not a usage error — that is the
    exact invocation the operator ran twice."""
    import inspect

    from navig.commands.ai import ai_login

    default = inspect.signature(ai_login).parameters["provider"].default
    # typer.Argument(None, ...) → the OptionInfo carries the default.
    assert getattr(default, "default", default) is None, (
        "provider is still required, so bare `navig ai login` errors instead of helping"
    )
