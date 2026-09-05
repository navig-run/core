"""An interrupted MCP call may already have taken effect — don't re-send it.

`MCPClient.call_tool` caught `ConnectionError` from the transport, reconnected, and
**re-sent the identical request**. But the request was already written and drained
before the reader died, so the server may well have executed it. Re-sending applies a
write twice: an issue filed twice, a message sent twice, a payment taken twice.

"Outcome unknown" is a distinct state from "it failed", and collapsing them is the bug.
Only a tool the server declared read-only may be retried; an unannotated tool is
treated as having a side effect, because a server that did not annotate did not tell us
it was safe.

The read/write judgement comes from the same classifier the approval gate uses, so
"safe to retry" and "runs without asking" cannot drift apart.
"""

from __future__ import annotations

import pytest

from navig.agent.mcp_client import MCPClient, MCPServerConfig, MCPToolSpec


class _Transport:
    """Fails the first send with ConnectionError, records every attempt."""

    is_alive = True

    def __init__(self) -> None:
        self.sends: list[dict] = []

    async def send_request(self, method: str, params: dict):
        self.sends.append(params)
        if len(self.sends) == 1:
            raise ConnectionError("reader terminated")
        return {"content": [{"type": "text", "text": "ok"}], "isError": False}


def _client(*, annotations: dict | None) -> tuple[MCPClient, _Transport]:
    client = MCPClient(MCPServerConfig(name="acme", transport="stdio", command=["x"]))
    transport = _Transport()
    client._transport = transport  # noqa: SLF001 — no public inject seam
    client._connected = True  # noqa: SLF001
    client._tools = [  # noqa: SLF001
        MCPToolSpec(
            name="do_thing",
            description="",
            input_schema={},
            server_name="acme",
            annotations=annotations or {},
        )
    ]
    return client, transport


@pytest.fixture(autouse=True)
def _never_actually_reconnect(monkeypatch):
    """A reconnect that succeeds, so the only thing under test is whether we re-send."""

    async def _ok(self):
        return True

    monkeypatch.setattr(MCPClient, "reconnect", _ok)


async def test_an_interrupted_write_is_not_re_sent() -> None:
    client, transport = _client(annotations=None)

    with pytest.raises(ConnectionError, match="may or may not have taken effect"):
        await client.call_tool("do_thing", {"amount": 100})

    assert len(transport.sends) == 1, (
        "The call was re-sent after the transport died. The first attempt had already "
        "been written, so the server may have executed it twice."
    )


async def test_the_error_tells_the_operator_what_to_do() -> None:
    client, _ = _client(annotations=None)

    with pytest.raises(ConnectionError) as exc:
        await client.call_tool("do_thing", {})

    assert "Check the server before trying it again" in str(exc.value)


async def test_an_interrupted_declared_read_IS_retried() -> None:
    """A read has no effect to duplicate, so failing it would be needless."""
    client, transport = _client(annotations={"readOnlyHint": True})

    result = await client.call_tool("do_thing", {})

    assert len(transport.sends) == 2
    assert "ok" in result


@pytest.mark.parametrize("annotations", [{"readOnlyHint": "true"}, {"readOnlyHint": 1}])
async def test_a_stringly_read_hint_is_not_a_read(annotations) -> None:
    """Same `is True` discipline as the approval gate: a server that did not follow the
    spec did not annotate."""
    client, transport = _client(annotations=annotations)

    with pytest.raises(ConnectionError):
        await client.call_tool("do_thing", {})

    assert len(transport.sends) == 1


async def test_an_unknown_tool_is_not_retried() -> None:
    """Cannot classify ⇒ do not retry."""
    client, transport = _client(annotations={"readOnlyHint": True})
    client._tools = []  # noqa: SLF001

    with pytest.raises(ConnectionError):
        await client.call_tool("do_thing", {})

    assert len(transport.sends) == 1
