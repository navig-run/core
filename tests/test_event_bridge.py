"""
Tests for navig.event_bridge — the unified event pipeline.

Covers:
- EventEnvelope serialisation
- SubscriptionFilter matching (topics, severities, sources, globs)
- EventBridge push pipeline (broadcast, filtering, dedup, dead-socket cleanup)
- NervousSystem → Bridge normalisation
- SystemEventQueue → Bridge normalisation
- Direct push
- History and stats
- Client management (register, unregister, update filter)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from typing import Any, Dict, List

import pytest

from navig.event_bridge import (
    _EVENT_TYPE_TOPIC_MAP,
    EventBridge,
    EventEnvelope,
    Severity,
    SubscriptionFilter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(
    topic: str = "agent.heartbeat",
    source: str = "heart",
    severity: Severity = Severity.INFO,
    data: Dict[str, Any] | None = None,
    origin: str = "nervous_system",
) -> EventEnvelope:
    return EventEnvelope(
        id="test001",
        topic=topic,
        source=source,
        severity=severity,
        timestamp=datetime.now(),
        data=data or {},
        origin=origin,
    )


class FakeWebSocket:
    """Minimal WebSocket mock that records sent messages."""

    def __init__(self, *, fail: bool = False):
        self.sent: List[str] = []
        self._fail = fail

    async def send(self, data: str) -> None:
        if self._fail:
            raise ConnectionError("socket dead")
        self.sent.append(data)


# ---------------------------------------------------------------------------
# EventEnvelope tests
# ---------------------------------------------------------------------------


class TestEventEnvelope:
    def test_to_jsonrpc_notification(self):
        env = _make_envelope(topic="agent.started", source="heart")
        notif = env.to_jsonrpc_notification()
        assert notif["jsonrpc"] == "2.0"
        assert notif["method"] == "navig.event.agent.started"
        assert "id" not in notif  # notifications have no id
        assert notif["params"]["topic"] == "agent.started"
        assert notif["params"]["source"] == "heart"
        assert notif["params"]["severity"] == "info"

    def test_to_dict_roundtrip(self):
        env = _make_envelope(data={"key": "val"})
        d = env.to_dict()
        assert d["data"] == {"key": "val"}
        assert d["origin"] == "nervous_system"
        assert d["id"] == "test001"


# ---------------------------------------------------------------------------
# SubscriptionFilter tests
# ---------------------------------------------------------------------------


class TestSubscriptionFilter:
    def test_accept_all(self):
        filt = SubscriptionFilter.accept_all()
        env = _make_envelope(topic="anything.here", severity=Severity.CRITICAL)
        assert filt.matches(env) is True

    def test_topic_exact(self):
        filt = SubscriptionFilter(topics={"agent.heartbeat"})
        assert filt.matches(_make_envelope(topic="agent.heartbeat")) is True
        assert filt.matches(_make_envelope(topic="agent.started")) is False

    def test_topic_glob(self):
        filt = SubscriptionFilter(topics={"agent.*"})
        assert filt.matches(_make_envelope(topic="agent.heartbeat")) is True
        assert filt.matches(_make_envelope(topic="agent.started")) is True
        assert filt.matches(_make_envelope(topic="host.check")) is False

    def test_severity_filter(self):
        filt = SubscriptionFilter(severities={Severity.ERROR, Severity.CRITICAL})
        assert filt.matches(_make_envelope(severity=Severity.ERROR)) is True
        assert filt.matches(_make_envelope(severity=Severity.INFO)) is False

    def test_source_filter(self):
        filt = SubscriptionFilter(sources={"heart"})
        assert filt.matches(_make_envelope(source="heart")) is True
        assert filt.matches(_make_envelope(source="eyes")) is False

    def test_combined_filter(self):
        filt = SubscriptionFilter(
            topics={"agent.*"},
            severities={Severity.WARNING, Severity.ERROR},
            sources={"heart"},
        )
        # Must match ALL criteria
        assert (
            filt.matches(
                _make_envelope(
                    topic="agent.heartbeat", severity=Severity.WARNING, source="heart"
                )
            )
            is True
        )
        # Wrong severity
        assert (
            filt.matches(
                _make_envelope(
                    topic="agent.heartbeat", severity=Severity.INFO, source="heart"
                )
            )
            is False
        )
        # Wrong source
        assert (
            filt.matches(
                _make_envelope(
                    topic="agent.heartbeat", severity=Severity.WARNING, source="eyes"
                )
            )
            is False
        )

    def test_serialisation_roundtrip(self):
        filt = SubscriptionFilter(
            topics={"agent.*", "host.check"},
            severities={Severity.ERROR},
            sources={"heart"},
        )
        d = filt.to_dict()
        filt2 = SubscriptionFilter.from_dict(d)
        assert filt2.topics == filt.topics
        assert filt2.severities == filt.severities
        assert filt2.sources == filt.sources


# ---------------------------------------------------------------------------
# EventBridge tests
# ---------------------------------------------------------------------------


class TestEventBridge:
    @pytest.mark.asyncio
    async def test_push_to_single_client(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws)

        count = await bridge.push(_make_envelope())
        assert count == 1
        assert len(ws.sent) == 1

        payload = json.loads(ws.sent[0])
        assert payload["method"] == "navig.event.agent.heartbeat"

    @pytest.mark.asyncio
    async def test_push_to_multiple_clients(self):
        bridge = EventBridge(debounce_seconds=0)
        ws1 = FakeWebSocket()
        ws2 = FakeWebSocket()
        bridge.register_client(ws1)
        bridge.register_client(ws2)

        count = await bridge.push(_make_envelope())
        assert count == 2
        assert len(ws1.sent) == 1
        assert len(ws2.sent) == 1

    @pytest.mark.asyncio
    async def test_filtered_client_receives_nothing(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws, SubscriptionFilter(topics={"host.*"}))

        count = await bridge.push(_make_envelope(topic="agent.heartbeat"))
        assert count == 0
        assert len(ws.sent) == 0

    @pytest.mark.asyncio
    async def test_filtered_client_receives_matching(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws, SubscriptionFilter(topics={"agent.*"}))

        count = await bridge.push(_make_envelope(topic="agent.heartbeat"))
        assert count == 1
        assert len(ws.sent) == 1

    @pytest.mark.asyncio
    async def test_dead_socket_pruned(self):
        bridge = EventBridge(debounce_seconds=0)
        dead_ws = FakeWebSocket(fail=True)
        live_ws = FakeWebSocket()
        bridge.register_client(dead_ws)
        bridge.register_client(live_ws)

        count = await bridge.push(_make_envelope())
        assert count == 1  # only live_ws
        assert bridge.client_count == 1  # dead one pruned

    @pytest.mark.asyncio
    async def test_unregister_client(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws)
        assert bridge.client_count == 1

        bridge.unregister_client(ws)
        assert bridge.client_count == 0

    @pytest.mark.asyncio
    async def test_update_client_filter(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws, SubscriptionFilter(topics={"host.*"}))

        # agent event should NOT reach client
        await bridge.push(_make_envelope(topic="agent.heartbeat"))
        assert len(ws.sent) == 0

        # Update filter to accept agent events
        bridge.update_client_filter(ws, SubscriptionFilter(topics={"agent.*"}))
        # Use a different topic to avoid dedup window collision
        await bridge.push(_make_envelope(topic="agent.started"))
        assert len(ws.sent) == 1

    @pytest.mark.asyncio
    async def test_push_direct(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws)

        count = await bridge.push_direct(
            topic="custom.test",
            source="test_runner",
            data={"foo": "bar"},
        )
        assert count == 1
        payload = json.loads(ws.sent[0])
        assert payload["params"]["topic"] == "custom.test"
        assert payload["params"]["origin"] == "direct"

    @pytest.mark.asyncio
    async def test_history(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws)

        for i in range(5):
            await bridge.push(_make_envelope(topic=f"test.event{i}"))

        history = bridge.get_history(limit=3)
        assert len(history) == 3
        assert history[-1].topic == "test.event4"

    @pytest.mark.asyncio
    async def test_history_filter_by_topic(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws)

        await bridge.push(_make_envelope(topic="agent.heartbeat"))
        await bridge.push(_make_envelope(topic="host.check"))
        await bridge.push(_make_envelope(topic="agent.started"))

        agent_events = bridge.get_history(topic="agent.*")
        assert len(agent_events) == 2

    @pytest.mark.asyncio
    async def test_stats(self):
        bridge = EventBridge(debounce_seconds=0)
        ws = FakeWebSocket()
        bridge.register_client(ws)

        await bridge.push(_make_envelope())
        stats = bridge.get_stats()
        assert stats["events_received"] >= 1
        assert stats["events_broadcast"] >= 1
        assert stats["active_clients"] == 1
        assert stats["ns_attached"] is False
        assert stats["eq_attached"] is False

    @pytest.mark.asyncio
    async def test_no_clients_no_crash(self):
        bridge = EventBridge(debounce_seconds=0)
        count = await bridge.push(_make_envelope())
        assert count == 0

    @pytest.mark.asyncio
    async def test_oversized_payload_truncated(self):
        bridge = EventBridge(debounce_seconds=0, max_payload_bytes=100)
        ws = FakeWebSocket()
        bridge.register_client(ws)

        big_data = {"big": "x" * 500}
        await bridge.push(_make_envelope(data=big_data))
        assert len(ws.sent) == 1
        payload = json.loads(ws.sent[0])
        assert payload["params"].get("truncated") is True


# ---------------------------------------------------------------------------
# Normalisation tests
# ---------------------------------------------------------------------------


class TestNormalisation:
    def test_ns_event_normalisation(self):
        """Simulate NervousSystem Event → EventEnvelope."""
        bridge = EventBridge()

        # Fake NervousSystem Event
        class FakeType:
            name = "HEARTBEAT"

        class FakePriority:
            name = "HIGH"

        class FakeEvent:
            type = FakeType()
            priority = FakePriority()
            source = "heart"
            data = {"bpm": 60}
            timestamp = datetime.now()
            id = "ns001"

        envelope = bridge._normalise_ns_event(FakeEvent())
        assert envelope.topic == "agent.heartbeat"
        assert envelope.severity == Severity.WARNING  # HIGH → WARNING
        assert envelope.source == "heart"
        assert envelope.origin == "nervous_system"

    def test_eq_event_normalisation(self):
        """Simulate SystemEventQueue SystemEvent → EventEnvelope."""
        bridge = EventBridge()

        class FakePriority:
            name = "CRITICAL"

        class FakeSystemEvent:
            id = "eq001"
            event_type = "host_check"
            payload = {"source": "cron_service", "host": "prod-1"}
            priority = FakePriority()
            timestamp = datetime.now()

        envelope = bridge._normalise_eq_event(FakeSystemEvent())
        assert envelope.topic == "host.check"
        assert envelope.severity == Severity.CRITICAL
        assert envelope.source == "cron_service"
        assert envelope.origin == "system_event_queue"

    def test_event_type_topic_map_complete(self):
        """All EventType enum members should have a topic mapping."""
        from navig.agent.nervous_system import EventType

        for et in EventType:
            assert (
                et.name in _EVENT_TYPE_TOPIC_MAP
            ), f"EventType.{et.name} missing from _EVENT_TYPE_TOPIC_MAP"


# ---------------------------------------------------------------------------
# Integration: NervousSystem → Bridge
# ---------------------------------------------------------------------------


class TestNervousSystemIntegration:
    @pytest.mark.asyncio
    async def test_ns_event_reaches_bridge_client(self):
        """End-to-end: NervousSystem.emit() → EventBridge → WebSocket client."""
        from navig.agent.nervous_system import EventType, NervousSystem

        ns = NervousSystem()
        bridge = EventBridge(debounce_seconds=0)
        bridge.attach_nervous_system(ns)

        ws = FakeWebSocket()
        bridge.register_client(ws)

        await ns.emit(EventType.HEARTBEAT, source="heart", data={"bpm": 60})

        # Allow async handlers to settle
        await asyncio.sleep(0.05)

        assert len(ws.sent) >= 1
        payload = json.loads(ws.sent[0])
        assert payload["method"] == "navig.event.agent.heartbeat"
        assert payload["params"]["data"]["bpm"] == 60

        bridge.detach_all()

    @pytest.mark.asyncio
    async def test_ns_filtered_client_skips_events(self):
        """Client subscribed to 'host.*' should not see agent events."""
        from navig.agent.nervous_system import EventType, NervousSystem

        ns = NervousSystem()
        bridge = EventBridge(debounce_seconds=0)
        bridge.attach_nervous_system(ns)

        ws = FakeWebSocket()
        bridge.register_client(ws, SubscriptionFilter(topics={"host.*"}))

        await ns.emit(EventType.HEARTBEAT, source="heart")
        await asyncio.sleep(0.05)

        assert len(ws.sent) == 0
        bridge.detach_all()


# ---------------------------------------------------------------------------
# Integration: SystemEventQueue → Bridge
# ---------------------------------------------------------------------------


class TestSystemEventQueueIntegration:
    @pytest.mark.asyncio
    async def test_eq_event_reaches_bridge_client(self):
        """End-to-end: SystemEventQueue.emit() → EventBridge → WebSocket client."""
        import tempfile
        from pathlib import Path

        from navig.gateway.system_events import SystemEventQueue

        with tempfile.TemporaryDirectory() as tmpdir:
            eq = SystemEventQueue(storage_path=Path(tmpdir))
            await eq.start()

            bridge = EventBridge(debounce_seconds=0)
            bridge.attach_event_queue(eq)

            ws = FakeWebSocket()
            bridge.register_client(ws)

            await eq.emit("host_check", payload={"host": "prod-1"})

            # Allow processing
            await asyncio.sleep(0.2)

            assert len(ws.sent) >= 1
            payload = json.loads(ws.sent[0])
            assert "host.check" in payload["method"]

            bridge.detach_all()
            await eq.stop()
