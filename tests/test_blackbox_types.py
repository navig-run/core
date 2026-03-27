import json
from datetime import datetime, timezone

from navig.blackbox.types import BlackboxEvent, Bundle, EventType


def test_blackbox_event_create():
    event = BlackboxEvent.create(
        event_type=EventType.COMMAND, payload={"cmd": "status"}, tags=["cli", "test"]
    )

    assert event.id is not None
    assert event.event_type == EventType.COMMAND
    assert event.payload == {"cmd": "status"}
    assert event.tags == ["cli", "test"]
    assert event.source == "navig"
    assert event.timestamp is not None
    assert event.severity() == 2


def test_blackbox_event_severity():
    assert BlackboxEvent.create(EventType.CRASH, {}).severity() == 6
    assert BlackboxEvent.create(EventType.ERROR, {}).severity() == 5

    # Test fallback
    # Not using EventType enum but a string to test .get fallback
    class MockEvent(BlackboxEvent):
        pass

    ev = MockEvent(id="1", event_type="unknown", timestamp=datetime.now(), payload={})
    assert ev.severity() == 0


def test_blackbox_event_serialization():
    dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    original = BlackboxEvent(
        id="test1234",
        event_type=EventType.WARNING,
        timestamp=dt,
        payload={"msg": "watch out"},
        tags=["fw"],
        source="system",
    )

    json_str = original.to_json()
    assert "test1234" in json_str
    assert "watch out" in json_str

    parsed_dict = json.loads(json_str)
    recreated = BlackboxEvent.from_dict(parsed_dict)

    assert recreated.id == original.id
    assert recreated.event_type == original.event_type
    assert recreated.timestamp == original.timestamp
    assert recreated.payload == original.payload
    assert recreated.tags == original.tags
    assert recreated.source == original.source


def test_bundle_methods():
    bundle = Bundle(
        id="b1",
        created_at=datetime.now(),
        navig_version="1.0",
        events=[BlackboxEvent.create(EventType.SYSTEM, {}) for _ in range(5)],
        crash_reports=[{"error": "1"}, {"error": "2"}],
        log_tails={},
        manifest_hash="abc",
    )

    assert bundle.event_count() == 5
    assert bundle.crash_count() == 2


def test_bundle_compute_hash():
    content = b'{"test": 123}'
    hash_val = Bundle.compute_hash(content)
    import hashlib

    assert hash_val == hashlib.sha256(content).hexdigest()
