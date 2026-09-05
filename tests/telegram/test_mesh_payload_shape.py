"""The mesh handlers were written against an API shape that never existed.

Wiring them (#1044) made them reachable; it did not make them work. Tracing what they
actually call found:

  * `/mesh/peers` answers through `json_ok(...)`, so the body is the gateway's standard
    `{ok, data, error}` envelope — never the payload. `for p in peers:` therefore iterated
    the envelope's KEYS and called `.get("role")` on the string "ok".
  * the payload inside is `{"self": {...}, "peers": [...]}`, not a bare list.
  * `/mesh/election/state`, `/mesh/status`, `/mesh/config` and `/mesh/handoff` are not
    routes this gateway serves at all — every one answered 404.

So all four commands failed, `/nodes` included. These tests pin the handlers against the
shape `navig.mesh.registry.to_api_dict()` really produces, so the client and the route
cannot drift apart again silently.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.gateway.channels.telegram_mesh import TelegramMeshMixin

pytestmark = pytest.mark.integration


# Exactly what routes/mesh.py returns: json_ok(registry.to_api_dict()).
def _envelope(self_role="leader", peers=None):
    return {
        "ok": True,
        "error": None,
        "data": {
            "self": {
                "node_id": "navig-me-a3f2",
                "hostname": "workstation",
                "role": self_role,
                "load": 0.42,
                "capabilities": ["llm", "shell"],
                "is_self": True,
            },
            "peers": peers if peers is not None else [
                {
                    "node_id": "navig-srv01-b7c1",
                    "hostname": "srv01",
                    "role": "standby",
                    "load": 0.10,
                    "capabilities": ["docker"],
                }
            ],
        },
    }


class _Chan(TelegramMeshMixin):
    def __init__(self, body):
        self._body = body
        self.sent: list[str] = []

    async def _mesh_get(self, path):          # the real one unwraps; mirror that here
        assert path == "/mesh/peers", f"handler called {path}, which this gateway does not serve"
        return TelegramMeshMixin._unwrap(self._body)

    async def send_message(self, chat_id, text, **kw):
        self.sent.append(text)
        return {"message_id": 1}


def test_the_envelope_is_unwrapped():
    assert TelegramMeshMixin._unwrap(_envelope())["self"]["hostname"] == "workstation"
    # A bare payload (no envelope) must pass through untouched.
    assert TelegramMeshMixin._unwrap({"self": {}, "peers": []}) == {"self": {}, "peers": []}


def test_nodes_lists_real_peers_and_does_not_iterate_the_envelope():
    ch = _Chan(_envelope())
    asyncio.run(ch._handle_mesh_nodes(1))
    out = "\n".join(ch.sent)
    assert "srv01" in out, "the peer never made it into the message"
    assert "workstation" in out, "the operator's own node belongs in the list"
    assert "ok" not in out.split(), "the envelope leaked into the output"
    assert "couldn't fetch" not in out, f"handler failed: {out}"


def test_nodes_still_reports_a_single_node_mesh():
    """Empty `peers` is not 'nothing discovered' — this node is up and is in the payload."""
    ch = _Chan(_envelope(peers=[]))
    asyncio.run(ch._handle_mesh_nodes(1))
    out = "\n".join(ch.sent)
    assert "workstation" in out
    assert "no mesh peers" not in out


def test_leader_is_derived_from_the_peers_route():
    ch = _Chan(_envelope(self_role="leader"))
    asyncio.run(ch._handle_mesh_leader(1))
    out = "\n".join(ch.sent)
    assert "workstation" in out and "leader" in out.lower()


def test_leader_says_so_when_no_one_is_elected():
    body = _envelope(self_role="standby")
    ch = _Chan(body)
    asyncio.run(ch._handle_mesh_leader(1))
    assert "no leader" in "\n".join(ch.sent).lower()


def test_mesh_status_reports_role_and_peer_count():
    ch = _Chan(_envelope())
    asyncio.run(ch._handle_mesh_toggle(1, "/mesh"))
    out = "\n".join(ch.sent)
    assert "leader" in out and "peers: 1" in out


@pytest.mark.parametrize("cmd,expect", [("/mesh on", "true"), ("/mesh off", "false")])
def test_toggle_says_it_cannot_and_names_what_does_work(cmd, expect):
    """`/mesh/config` is a 404. Silently 'succeeding' would be the worst outcome; a
    generic failure is the second worst. Name the real way to do it."""
    ch = _Chan(_envelope())
    asyncio.run(ch._handle_mesh_toggle(1, cmd))
    out = "\n".join(ch.sent)
    assert "navig config set mesh.enabled" in out
    assert expect in out


def test_switch_reports_unavailable_rather_than_rejected():
    """It used to POST to a 404 and render 'handoff rejected: unknown' — which reads as
    the mesh refusing, not as the feature being absent."""
    ch = _Chan(_envelope())
    asyncio.run(ch._handle_mesh_switch(1, "/switch srv01"))
    out = "\n".join(ch.sent)
    assert "srv01" in out
    assert "rejected" not in out.lower()
    assert "isn't available" in out or "not available" in out


def test_no_handler_calls_a_route_this_gateway_does_not_serve():
    """The check that would have caught all of this before it shipped."""
    import inspect
    import re

    src = inspect.getsource(TelegramMeshMixin)
    called = set(re.findall(r"_mesh_(?:get|post)\(\s*[\"'](/[^\"']+)[\"']", src))
    assert called == {"/mesh/peers"}, (
        f"handlers call {sorted(called)}; only /mesh/peers is registered in "
        "navig/gateway/routes/mesh.py"
    )
