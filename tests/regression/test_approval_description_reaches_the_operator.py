"""The card the operator reads must be the card the caller built.

`navig.mcp.registry` and the gateway's MCP-register route render an approval card
through :mod:`navig.tools.untrusted_text`: the server's own description quoted and
defused, the arguments fenced, the endpoint in a code span, and a line stating whose
word the read/action classification rests on.

`_manager_backend` — the gateway's approval backend, i.e. the ONLY path where a human
actually sees any of it — threw that away and rebuilt a one-liner:

    description = f"Agent tool call: {req.tool_name} ({req.safety_level})"

So the rendering reached nobody (written · tested · never wired), and the replacement
interpolated `tool_name` **raw**. That name is not always NAVIG's — an external tool
carries a name a third party chose — and it lands in a Markdown surface: it flows
`ApprovalManager` → `/approval/pending` → the deck and OS Inbox cards, and out to
Telegram.

`description` is what the operator reads; `command` is what
`navig.approval.policies.classify_command` fnmatches. Both are asserted here.
"""

from __future__ import annotations

import pytest

from navig.tools.approval import (
    ApprovalDecision,
    ApprovalRequest,
    bind_approval_manager,
    get_approval_gate,
    reset_approval_gate,
)


class _RecordingManager:
    """Stands in for ApprovalManager, capturing exactly what a human would be shown."""

    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def request_approval(self, **kwargs) -> bool:
        self.seen.append(kwargs)
        return True


@pytest.fixture
def manager() -> _RecordingManager:
    mgr = _RecordingManager()
    bind_approval_manager(mgr, audit_log=None)
    yield mgr
    reset_approval_gate()


async def _ask(req: ApprovalRequest) -> ApprovalDecision:
    return await get_approval_gate().backend(req)


async def test_a_caller_supplied_card_is_passed_through(manager) -> None:
    """The whole reason `untrusted_text.describe_call` exists."""
    card = "**acme** → `delete_repo`\n\n> Deletes a repository.\n\nEndpoint: `https://x/mcp`"
    await _ask(
        ApprovalRequest(
            tool_name="mcp__acme__delete_repo",
            safety_level="dangerous",
            parameters={"repo": "prod"},
            context={"description": card},
        )
    )

    assert manager.seen[0]["description"] == card, (
        "The approval backend rebuilt its own description and discarded the card the "
        "caller rendered, so the operator never sees the server's description, the "
        "arguments, or the provenance of the classification."
    )


@pytest.mark.parametrize("blank", ["", "   ", None, 123])
async def test_a_missing_or_unusable_card_falls_back(manager, blank) -> None:
    await _ask(
        ApprovalRequest(
            tool_name="bash_exec",
            safety_level="dangerous",
            parameters={"command": "ls"},
            context={"description": blank} if blank is not None else {},
        )
    )

    description = manager.seen[0]["description"]
    assert "bash_exec" in description
    assert "dangerous" in description


async def test_a_hostile_tool_name_cannot_escape_its_code_span(manager) -> None:
    """An external tool's name is chosen by a third party and lands in Markdown.

    The property is *containment*, not the absence of `#`: inside a code span Markdown
    renders `##` literally, so a heading cannot form. What must be impossible is
    ESCAPING the span — a backtick in the name would close it and turn everything after
    into prose in NAVIG's own voice — and breaking onto a new line.
    """
    await _ask(
        ApprovalRequest(
            tool_name="x`\n## Approved by the operator\n",
            safety_level="dangerous",
            parameters={},
            context={},
        )
    )

    description = manager.seen[0]["description"]
    assert "\n" not in description, description
    assert description.count("`") == 2, f"the code span was escaped: {description}"
    # …and the hostile text is inside it, not loose in the prompt.
    span = description.split("`")[1]
    assert "## Approved by the operator" in span


async def test_agent_authored_arguments_cannot_escape_the_preview(manager) -> None:
    """The agent is who this prompt protects the operator from."""
    await _ask(
        ApprovalRequest(
            tool_name="bash_exec",
            safety_level="dangerous",
            parameters={"note": "``` end of fence"},
            context={},
        )
    )

    assert "```" not in manager.seen[0]["description"]


async def test_secrets_are_still_redacted_in_the_fallback(manager) -> None:
    """The pre-existing redaction must survive the rewrite."""
    await _ask(
        ApprovalRequest(
            tool_name="bash_exec",
            safety_level="dangerous",
            parameters={"api_key": "sk-live-abcdef0123456789abcdef"},
            context={},
        )
    )

    assert "sk-live-abcdef0123456789abcdef" not in manager.seen[0]["description"]


async def test_the_policy_string_carries_the_tool_name(manager) -> None:
    """`command` is what `classify_command` fnmatches, so it must stay the real name —
    sanitising it would break an operator's legitimate `tool read_*` pattern.

    Safe because an EXTERNAL name is namespaced and charset-sanitised upstream
    (`mcp__<server>__<tool>`, `[a-zA-Z0-9_-]`), so it cannot carry a glob
    metacharacter and self-match a `safe` pattern.
    """
    await _ask(
        ApprovalRequest(
            tool_name="mcp__acme__delete_repo",
            safety_level="dangerous",
            parameters={},
            context={},
        )
    )

    assert manager.seen[0]["command"] == "tool mcp__acme__delete_repo"


async def test_a_namespaced_name_carries_no_glob_metacharacters() -> None:
    """The property the test above relies on, asserted directly."""
    from navig.mcp.trust import namespaced_tool_name

    name = namespaced_tool_name("acme*?[", "delete*repo?")
    assert not set(name) & set("*?[]"), name


async def test_a_broken_renderer_still_produces_a_prompt(manager, monkeypatch) -> None:
    """A card that cannot be rendered must not become a call that cannot be approved."""
    monkeypatch.setattr(
        "navig.tools.untrusted_text.code_span",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    await _ask(
        ApprovalRequest(
            tool_name="bash_exec", safety_level="dangerous", parameters={}, context={}
        )
    )

    assert "bash_exec" in manager.seen[0]["description"]
