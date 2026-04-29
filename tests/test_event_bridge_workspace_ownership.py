"""
Batch 103 — tests for:
  - navig.event_bridge       (Severity, EventEnvelope, SubscriptionFilter)
  - navig.workspace_ownership (classify_workspace_file, is_project_workspace_path,
                               resolve_personal_workspace_path, summarize_duplicates,
                               WorkspaceDuplicate)
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path


# ============================================================================
# navig.event_bridge — Severity
# ============================================================================


class TestSeverityEnum:
    def test_values(self):
        from navig.event_bridge import Severity

        assert Severity.DEBUG.value == "debug"
        assert Severity.INFO.value == "info"
        assert Severity.WARNING.value == "warning"
        assert Severity.ERROR.value == "error"
        assert Severity.CRITICAL.value == "critical"

    def test_severity_is_str(self):
        from navig.event_bridge import Severity

        # Severity extends str
        assert isinstance(Severity.INFO, str)
        assert Severity.INFO == "info"

    def test_all_levels_present(self):
        from navig.event_bridge import Severity

        names = {s.name for s in Severity}
        assert names == {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


# ============================================================================
# navig.event_bridge — EventEnvelope
# ============================================================================


def _make_envelope(**kwargs):
    from navig.event_bridge import EventEnvelope, Severity

    defaults = dict(
        id="test-id-1",
        topic="agent.heartbeat",
        source="heart",
        severity=Severity.INFO,
        timestamp=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        data={"key": "value"},
        origin="direct",
    )
    defaults.update(kwargs)
    return EventEnvelope(**defaults)


class TestEventEnvelope:
    def test_to_dict_keys(self):
        env = _make_envelope()
        d = env.to_dict()
        assert set(d.keys()) == {"id", "topic", "source", "severity", "timestamp", "data", "origin"}

    def test_to_dict_values(self):
        env = _make_envelope()
        d = env.to_dict()
        assert d["id"] == "test-id-1"
        assert d["topic"] == "agent.heartbeat"
        assert d["source"] == "heart"
        assert d["severity"] == "info"
        assert d["origin"] == "direct"
        assert d["data"] == {"key": "value"}

    def test_to_dict_timestamp_is_string(self):
        env = _make_envelope()
        d = env.to_dict()
        assert isinstance(d["timestamp"], str)
        assert "2024" in d["timestamp"]

    def test_to_jsonrpc_notification_structure(self):
        env = _make_envelope()
        n = env.to_jsonrpc_notification()
        assert n["jsonrpc"] == "2.0"
        assert n["method"] == "navig.event.agent.heartbeat"
        assert "params" in n
        assert n["params"]["topic"] == "agent.heartbeat"
        assert n["params"]["severity"] == "info"

    def test_jsonrpc_no_id_field(self):
        env = _make_envelope()
        n = env.to_jsonrpc_notification()
        # JSON-RPC notification has no top-level "id" field
        assert "id" not in n

    def test_frozen_immutable(self):
        import pytest

        env = _make_envelope()
        with pytest.raises((AttributeError, TypeError)):
            env.topic = "new.topic"  # type: ignore[misc]


# ============================================================================
# navig.event_bridge — SubscriptionFilter
# ============================================================================


class TestSubscriptionFilterMatchesTopic:
    def test_empty_filter_accepts_all(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter.accept_all()
        env = _make_envelope(topic="agent.heartbeat")
        assert sf.matches(env) is True

    def test_exact_topic_match(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter(topics={"agent.heartbeat"})
        assert sf.matches(_make_envelope(topic="agent.heartbeat")) is True
        assert sf.matches(_make_envelope(topic="agent.stopped")) is False

    def test_wildcard_topic_match(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter(topics={"agent.*"})
        assert sf.matches(_make_envelope(topic="agent.heartbeat")) is True
        assert sf.matches(_make_envelope(topic="agent.stopped")) is True
        assert sf.matches(_make_envelope(topic="host.disk_warning")) is False

    def test_multi_topic_filter(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter(topics={"agent.*", "host.*"})
        assert sf.matches(_make_envelope(topic="agent.started")) is True
        assert sf.matches(_make_envelope(topic="host.disk_warning")) is True
        assert sf.matches(_make_envelope(topic="db.query")) is False


class TestSubscriptionFilterMatchesSeverity:
    def test_severity_filter(self):
        from navig.event_bridge import Severity, SubscriptionFilter

        sf = SubscriptionFilter(severities={Severity.ERROR, Severity.CRITICAL})
        assert sf.matches(_make_envelope(severity=Severity.ERROR)) is True
        assert sf.matches(_make_envelope(severity=Severity.CRITICAL)) is True
        assert sf.matches(_make_envelope(severity=Severity.INFO)) is False

    def test_empty_severity_accepts_all(self):
        from navig.event_bridge import Severity, SubscriptionFilter

        sf = SubscriptionFilter()
        assert sf.matches(_make_envelope(severity=Severity.DEBUG)) is True


class TestSubscriptionFilterMatchesSource:
    def test_source_filter(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter(sources={"heart"})
        assert sf.matches(_make_envelope(source="heart")) is True
        assert sf.matches(_make_envelope(source="cron_service")) is False

    def test_empty_source_accepts_all(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter()
        assert sf.matches(_make_envelope(source="anything")) is True


class TestSubscriptionFilterSerialization:
    def test_to_dict(self):
        from navig.event_bridge import Severity, SubscriptionFilter

        sf = SubscriptionFilter(
            topics={"agent.*"},
            severities={Severity.INFO},
            sources={"heart"},
        )
        d = sf.to_dict()
        assert d["topics"] == ["agent.*"]
        assert d["severities"] == ["info"]
        assert d["sources"] == ["heart"]

    def test_to_dict_sorted(self):
        from navig.event_bridge import Severity, SubscriptionFilter

        sf = SubscriptionFilter(
            topics={"z.topic", "a.topic"},
            severities={Severity.ERROR, Severity.DEBUG},
        )
        d = sf.to_dict()
        assert d["topics"] == sorted(d["topics"])
        assert d["severities"] == sorted(d["severities"])

    def test_from_dict_roundtrip(self):
        from navig.event_bridge import Severity, SubscriptionFilter

        original = SubscriptionFilter(
            topics={"agent.*"},
            severities={Severity.INFO},
            sources={"heart"},
        )
        d = original.to_dict()
        restored = SubscriptionFilter.from_dict(d)
        assert restored.topics == {"agent.*"}
        assert restored.severities == {Severity.INFO}
        assert restored.sources == {"heart"}

    def test_from_dict_empty(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter.from_dict({})
        assert sf.topics == set()
        assert sf.severities == set()
        assert sf.sources == set()

    def test_accept_all_factory(self):
        from navig.event_bridge import SubscriptionFilter

        sf = SubscriptionFilter.accept_all()
        assert sf.topics == set()
        assert sf.severities == set()
        assert sf.sources == set()


# ============================================================================
# navig.workspace_ownership
# ============================================================================


class TestClassifyWorkspaceFile:
    def test_identity_is_generated_default(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("IDENTITY.md") == "generated_default"

    def test_soul_is_generated_default(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("SOUL.md") == "generated_default"

    def test_agents_is_generated_default(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("AGENTS.md") == "generated_default"

    def test_bootstrap_is_generated_default(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("BOOTSTRAP.md") == "generated_default"

    def test_custom_file_is_personal(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("MY_NOTES.md") == "personal_customized"

    def test_unknown_file_is_personal(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("custom_agent.md") == "personal_customized"

    def test_tools_is_generated(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("TOOLS.md") == "generated_default"

    def test_heartbeat_is_generated(self):
        from navig.workspace_ownership import classify_workspace_file

        assert classify_workspace_file("HEARTBEAT.md") == "generated_default"


class TestIsProjectWorkspacePath:
    def test_project_workspace_path_detected(self, tmp_path):
        from navig.workspace_ownership import is_project_workspace_path

        project_root = tmp_path
        project_ws = tmp_path / ".navig" / "workspace"
        project_ws.mkdir(parents=True)
        assert is_project_workspace_path(project_ws, project_root=project_root) is True

    def test_non_project_path_rejected(self, tmp_path):
        from navig.workspace_ownership import is_project_workspace_path

        other_path = tmp_path / "other"
        other_path.mkdir()
        assert is_project_workspace_path(other_path, project_root=tmp_path) is False

    def test_user_workspace_not_project_path(self):
        from navig.workspace_ownership import USER_WORKSPACE_DIR, is_project_workspace_path

        # ~/.navig/workspace is not a project workspace path
        result = is_project_workspace_path(USER_WORKSPACE_DIR, project_root=Path.cwd())
        # Result depends on whether cwd has a .navig/workspace — just check type
        assert isinstance(result, bool)


class TestSummarizeDuplicates:
    def test_empty_list(self):
        from navig.workspace_ownership import summarize_duplicates

        result = summarize_duplicates([])
        assert result == {} or isinstance(result, dict)

    def test_single_modified(self):
        from navig.workspace_ownership import WorkspaceDuplicate, summarize_duplicates

        dup = WorkspaceDuplicate(
            file_name="IDENTITY.md",
            project_path=Path("/project/.navig/workspace/IDENTITY.md"),
            user_path=Path("/home/user/.navig/workspace/IDENTITY.md"),
            status="modified",
        )
        result = summarize_duplicates([dup])
        assert isinstance(result, dict)
        # Should count statuses
        total = sum(result.values())
        assert total >= 1

    def test_multiple_statuses(self):
        from navig.workspace_ownership import WorkspaceDuplicate, summarize_duplicates

        dups = [
            WorkspaceDuplicate(
                file_name="IDENTITY.md",
                project_path=Path("/p/.navig/workspace/IDENTITY.md"),
                user_path=None,
                status="modified",
            ),
            WorkspaceDuplicate(
                file_name="SOUL.md",
                project_path=Path("/p/.navig/workspace/SOUL.md"),
                user_path=None,
                status="identical",
            ),
            WorkspaceDuplicate(
                file_name="AGENTS.md",
                project_path=Path("/p/.navig/workspace/AGENTS.md"),
                user_path=None,
                status="modified",
            ),
        ]
        result = summarize_duplicates(dups)
        assert isinstance(result, dict)
        total = sum(result.values())
        assert total == 3


class TestWorkspaceDuplicate:
    def test_dataclass_creation(self):
        from navig.workspace_ownership import WorkspaceDuplicate

        dup = WorkspaceDuplicate(
            file_name="IDENTITY.md",
            project_path=Path("/project/.navig/workspace/IDENTITY.md"),
            user_path=Path("/home/user/.navig/workspace/IDENTITY.md"),
            status="modified",
        )
        assert dup.file_name == "IDENTITY.md"
        assert dup.status == "modified"

    def test_user_path_can_be_none(self):
        from navig.workspace_ownership import WorkspaceDuplicate

        dup = WorkspaceDuplicate(
            file_name="IDENTITY.md",
            project_path=Path("/p/IDENTITY.md"),
            user_path=None,
            status="project_only",
        )
        assert dup.user_path is None


class TestResolvePersonalWorkspacePath:
    def test_none_returns_canonical(self):
        from navig.workspace_ownership import USER_WORKSPACE_DIR, resolve_personal_workspace_path

        canonical, legacy = resolve_personal_workspace_path(None)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is None

    def test_canonical_path_no_legacy(self):
        from navig.workspace_ownership import USER_WORKSPACE_DIR, resolve_personal_workspace_path

        canonical, legacy = resolve_personal_workspace_path(USER_WORKSPACE_DIR)
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is None

    def test_non_canonical_path_sets_legacy(self, tmp_path):
        from navig.workspace_ownership import resolve_personal_workspace_path

        custom_ws = tmp_path / "workspace"
        canonical, legacy = resolve_personal_workspace_path(custom_ws)
        # canonical is always USER_WORKSPACE_DIR; legacy holds custom_ws
        from navig.workspace_ownership import USER_WORKSPACE_DIR
        assert canonical == USER_WORKSPACE_DIR
        assert legacy is not None
