"""Hermetic unit tests for navig.event_bridge pure data classes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from navig.event_bridge import (
    EventEnvelope,
    Severity,
    SubscriptionFilter,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_TS = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)


def _make_envelope(
    topic: str = "agent.heartbeat",
    source: str = "heart",
    severity: Severity = Severity.INFO,
    data: dict | None = None,
    origin: str = "nervous_system",
    id: str = "evt-001",
) -> EventEnvelope:
    return EventEnvelope(
        id=id,
        topic=topic,
        source=source,
        severity=severity,
        timestamp=_TS,
        data=data or {},
        origin=origin,
    )


# ---------------------------------------------------------------------------
# Severity enum
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_values(self):
        assert Severity.DEBUG == "debug"
        assert Severity.INFO == "info"
        assert Severity.WARNING == "warning"
        assert Severity.ERROR == "error"
        assert Severity.CRITICAL == "critical"

    def test_is_str_subclass(self):
        assert isinstance(Severity.INFO, str)


# ---------------------------------------------------------------------------
# EventEnvelope.to_dict
# ---------------------------------------------------------------------------


class TestEventEnvelopeToDict:
    def test_keys_present(self):
        env = _make_envelope()
        d = env.to_dict()
        assert set(d.keys()) == {"id", "topic", "source", "severity", "timestamp", "data", "origin"}

    def test_severity_serialised_as_string(self):
        env = _make_envelope(severity=Severity.ERROR)
        assert env.to_dict()["severity"] == "error"

    def test_timestamp_is_isoformat(self):
        env = _make_envelope()
        ts_str = env.to_dict()["timestamp"]
        # Should round-trip via fromisoformat
        parsed = datetime.fromisoformat(ts_str)
        assert parsed.year == 2024

    def test_data_passthrough(self):
        env = _make_envelope(data={"cpu": 42})
        assert env.to_dict()["data"] == {"cpu": 42}

    def test_topic_passthrough(self):
        env = _make_envelope(topic="monitor.alert")
        assert env.to_dict()["topic"] == "monitor.alert"


# ---------------------------------------------------------------------------
# EventEnvelope.to_jsonrpc_notification
# ---------------------------------------------------------------------------


class TestEventEnvelopeToJsonrpc:
    def test_top_level_keys(self):
        env = _make_envelope()
        notif = env.to_jsonrpc_notification()
        assert notif["jsonrpc"] == "2.0"
        assert "method" in notif
        assert "params" in notif
        assert "id" not in notif  # no id in notification

    def test_method_includes_topic(self):
        env = _make_envelope(topic="agent.heartbeat")
        assert env.to_jsonrpc_notification()["method"] == "navig.event.agent.heartbeat"

    def test_params_includes_all_fields(self):
        env = _make_envelope(severity=Severity.WARNING, data={"key": "val"})
        params = env.to_jsonrpc_notification()["params"]
        assert params["severity"] == "warning"
        assert params["data"] == {"key": "val"}

    def test_params_id_matches_envelope_id(self):
        env = _make_envelope(id="test-id-123")
        params = env.to_jsonrpc_notification()["params"]
        assert params["id"] == "test-id-123"


# ---------------------------------------------------------------------------
# SubscriptionFilter.accept_all
# ---------------------------------------------------------------------------


class TestSubscriptionFilterAcceptAll:
    def test_accepts_any_topic(self):
        f = SubscriptionFilter.accept_all()
        assert f.matches(_make_envelope(topic="some.topic"))

    def test_accepts_any_severity(self):
        f = SubscriptionFilter.accept_all()
        for sev in Severity:
            assert f.matches(_make_envelope(severity=sev))

    def test_empty_filter_sets(self):
        f = SubscriptionFilter.accept_all()
        assert len(f.topics) == 0
        assert len(f.severities) == 0
        assert len(f.sources) == 0


# ---------------------------------------------------------------------------
# SubscriptionFilter.matches — topic filter
# ---------------------------------------------------------------------------


class TestSubscriptionFilterTopicMatching:
    def test_exact_topic_match(self):
        f = SubscriptionFilter(topics={"agent.heartbeat"})
        assert f.matches(_make_envelope(topic="agent.heartbeat"))
        assert not f.matches(_make_envelope(topic="agent.stopped"))

    def test_glob_topic_matches(self):
        f = SubscriptionFilter(topics={"agent.*"})
        assert f.matches(_make_envelope(topic="agent.heartbeat"))
        assert f.matches(_make_envelope(topic="agent.stopped"))
        assert not f.matches(_make_envelope(topic="monitor.alert"))

    def test_multiple_topics(self):
        f = SubscriptionFilter(topics={"agent.heartbeat", "monitor.alert"})
        assert f.matches(_make_envelope(topic="monitor.alert"))
        assert not f.matches(_make_envelope(topic="system.info"))


# ---------------------------------------------------------------------------
# SubscriptionFilter.matches — severity filter
# ---------------------------------------------------------------------------


class TestSubscriptionFilterSeverityMatching:
    def test_included_severity_passes(self):
        f = SubscriptionFilter(severities={Severity.ERROR, Severity.CRITICAL})
        assert f.matches(_make_envelope(severity=Severity.ERROR))

    def test_excluded_severity_blocked(self):
        f = SubscriptionFilter(severities={Severity.ERROR})
        assert not f.matches(_make_envelope(severity=Severity.INFO))


# ---------------------------------------------------------------------------
# SubscriptionFilter.matches — source filter
# ---------------------------------------------------------------------------


class TestSubscriptionFilterSourceMatching:
    def test_included_source_passes(self):
        f = SubscriptionFilter(sources={"heart"})
        assert f.matches(_make_envelope(source="heart"))

    def test_excluded_source_blocked(self):
        f = SubscriptionFilter(sources={"heart"})
        assert not f.matches(_make_envelope(source="cron_service"))


# ---------------------------------------------------------------------------
# SubscriptionFilter.to_dict / from_dict roundtrip
# ---------------------------------------------------------------------------


class TestSubscriptionFilterSerialization:
    def test_to_dict_keys(self):
        f = SubscriptionFilter(
            topics={"agent.heartbeat"},
            severities={Severity.INFO},
            sources={"heart"},
        )
        d = f.to_dict()
        assert set(d.keys()) == {"topics", "severities", "sources"}

    def test_to_dict_severities_as_strings(self):
        f = SubscriptionFilter(severities={Severity.WARNING, Severity.ERROR})
        sev_strings = f.to_dict()["severities"]
        assert "warning" in sev_strings
        assert "error" in sev_strings

    def test_from_dict_roundtrip(self):
        original = SubscriptionFilter(
            topics={"agent.*"},
            severities={Severity.INFO, Severity.ERROR},
            sources={"heart"},
        )
        d = original.to_dict()
        restored = SubscriptionFilter.from_dict(d)
        assert restored.topics == original.topics
        assert restored.severities == original.severities
        assert restored.sources == original.sources

    def test_from_dict_empty(self):
        f = SubscriptionFilter.from_dict({})
        assert f.topics == set()
        assert f.severities == set()
        assert f.sources == set()
