"""
Unit tests for the A2A JSON-RPC message helpers (navig.mesh.a2a).

Pure-function tests — no gateway, no agent. Assert JSON-RPC 2.0 envelopes and
A2A Message parsing/building behave per spec.
"""

import pytest

from navig.mesh import a2a

pytestmark = pytest.mark.unit


def test_extract_text_joins_text_parts():
    msg = {
        "role": "user",
        "parts": [
            {"kind": "text", "text": "line one"},
            {"kind": "data", "data": {"ignored": True}},
            {"kind": "text", "text": "line two"},
        ],
    }
    assert a2a.extract_text(msg) == "line one\nline two"


def test_extract_text_empty_when_no_text():
    assert a2a.extract_text({"parts": [{"kind": "file", "file": {}}]}) == ""
    assert a2a.extract_text({}) == ""


def test_build_message_shape():
    m = a2a.build_message("hello", message_id="fixed")
    assert m["role"] == "agent"
    assert m["kind"] == "message"
    assert m["messageId"] == "fixed"
    assert m["parts"] == [{"kind": "text", "text": "hello"}]


def test_build_message_generates_id_when_absent():
    m = a2a.build_message("hi")
    assert isinstance(m["messageId"], str) and m["messageId"]


def test_rpc_result_envelope():
    env = a2a.rpc_result(7, {"ok": True})
    assert env == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}


def test_rpc_error_envelope_with_and_without_data():
    assert a2a.rpc_error(1, a2a.INVALID_PARAMS, "bad") == {
        "jsonrpc": "2.0",
        "id": 1,
        "error": {"code": a2a.INVALID_PARAMS, "message": "bad"},
    }
    env = a2a.rpc_error(1, a2a.INTERNAL_ERROR, "boom", data={"where": "x"})
    assert env["error"]["data"] == {"where": "x"}


@pytest.mark.parametrize(
    "body,ok",
    [
        ({"jsonrpc": "2.0", "method": "message/send", "id": 1}, True),
        ({"jsonrpc": "1.0", "method": "message/send"}, False),
        ({"jsonrpc": "2.0", "method": ""}, False),
        ({"jsonrpc": "2.0"}, False),
        ("not a dict", False),
    ],
)
def test_validate_request(body, ok):
    result, _reason = a2a.validate_request(body)
    assert result is ok
