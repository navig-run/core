"""A blocked tool must stay blocked however the operator spelled it.

`ToolRouter` stored `blocked_tools` / `require_confirmation` exactly as written in
config, while `execute()` tested the CANONICAL name from the registry. So a policy of

    blocked_tools: ["web-fetch"]      # or the documented alias "fetch"

matched nothing and the tool RAN. Verified against the real router before the fix — it
got as far as a DNS lookup (`socket.gaierror: getaddrinfo failed`) for a tool the
operator had blocked. Only the exact canonical spelling denied it.

Both sides now go through `canonical_tool_key`, the single answer to "are these two
strings the same tool?" — so a gate and whatever populates it cannot disagree.

The direction is fail-CLOSED: normalising can only make MORE names match the policy the
operator wrote, never fewer. `blocked_tools: ["search"]` now blocks `web_search`, which
is exactly what the documented alias means.
"""
from __future__ import annotations

import pytest

from navig.tools.router import TOOL_ALIASES, ToolRouter, canonical_tool_key
from navig.tools.schemas import ToolCallAction, ToolResultStatus

# Every spelling of the same tool an operator could plausibly put in config.
SPELLINGS = ["web_fetch", "web-fetch", "WEB_FETCH", "Web-Fetch", "  web_fetch  ", "fetch"]


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_blocked_tool_is_denied_however_it_was_spelled(spelling: str) -> None:
    router = ToolRouter(safety_policy={"blocked_tools": [spelling]})

    result = router.execute(
        ToolCallAction(tool="web_fetch", parameters={"url": "http://example.invalid"})
    )

    assert result.status is ToolResultStatus.DENIED, (
        f"blocked_tools=[{spelling!r}] did not block web_fetch — the tool would run"
    )


@pytest.mark.parametrize("spelling", SPELLINGS)
def test_a_confirmation_gate_fires_however_it_was_spelled(spelling: str) -> None:
    """The same mismatch silently skipped a HUMAN CONFIRMATION gate."""
    router = ToolRouter(safety_policy={"require_confirmation": [spelling]})

    result = router.execute(
        ToolCallAction(tool="web_fetch", parameters={"url": "http://example.invalid"})
    )

    assert result.status is ToolResultStatus.NEEDS_CONFIRMATION


def test_an_unrelated_tool_is_not_swept_up() -> None:
    """The partner. Normalising must not widen a policy onto tools the operator did
    not name — a fix that blocked everything would satisfy every test above."""
    router = ToolRouter(safety_policy={"blocked_tools": ["web-fetch"]})

    result = router.execute(ToolCallAction(tool="web_search", parameters={"query": "x"}))

    assert result.status is not ToolResultStatus.DENIED


def test_an_empty_policy_blocks_nothing() -> None:
    router = ToolRouter(safety_policy={})
    assert router._blocked == set()
    assert router._require_confirmation == set()


def test_blank_entries_are_dropped_rather_than_matching_everything() -> None:
    """An empty string canonicalises to "" — harmless as a set member, but keeping it
    is noise that reads like a real entry in a policy dump."""
    router = ToolRouter(safety_policy={"blocked_tools": ["", "  ", "web_fetch"]})

    assert router._blocked == {"web_fetch"}


# ── the shared canonicaliser ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("web-fetch", "web_fetch"),
        ("WEB_FETCH", "web_fetch"),
        ("  Web-Fetch  ", "web_fetch"),
        ("fetch", "web_fetch"),  # documented alias
        ("search", "web_search"),
        ("unknown_tool", "unknown_tool"),  # passthrough, not None
    ],
)
def test_canonical_tool_key(raw: str, expected: str) -> None:
    assert canonical_tool_key(raw) == expected


def test_canonical_key_does_not_require_the_registry() -> None:
    """The policy is built BEFORE tools are registered. If this helper needed the
    registry it would drop every entry and re-open the hole from the other side."""
    assert canonical_tool_key("not_a_registered_tool_at_all") == "not_a_registered_tool_at_all"


def test_every_alias_target_survives_canonicalisation() -> None:
    """Anti-vacuity: aliases are the interesting half of the mapping. If TOOL_ALIASES
    ever held a target that itself needs normalising, `canonical_tool_key` would be
    one pass short and a policy naming that alias would silently miss."""
    for alias, target in TOOL_ALIASES.items():
        assert canonical_tool_key(alias) == target
        assert canonical_tool_key(target) == target, (
            f"alias target {target!r} is not already canonical — one pass is not enough"
        )


def test_the_registry_normaliser_uses_the_same_key() -> None:
    """The two must not drift: `execute()` gets its canonical name from the registry,
    the policy from `canonical_tool_key`. Different logic here is the original bug."""
    from navig.tools.router import get_tool_registry

    registry = get_tool_registry()
    registry.initialize()

    for raw in ("web-fetch", "fetch", "WEB_FETCH"):
        assert registry.normalize_tool_name(raw) == canonical_tool_key(raw)
