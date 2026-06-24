"""
Tests for navig.memory.user_profile — pure dataclass round-trips and logic.
"""

from __future__ import annotations

import pytest

from navig.memory.user_profile import (
    InteractionStats,
    MemoryNote,
    TechnicalContext,
    UserIdentity,
    UserPreferences,
    UserProfile,
    WorkPatterns,
)


# ─── MemoryNote ───────────────────────────────────────────────────────────────


def test_memory_note_to_dict_roundtrip():
    note = MemoryNote(
        timestamp="2024-01-15T12:00:00",
        category="work_patterns",
        content="Prefers Friday deployments",
        source="agent",
    )
    d = note.to_dict()
    restored = MemoryNote.from_dict(d)
    assert restored.timestamp == note.timestamp
    assert restored.category == note.category
    assert restored.content == note.content
    assert restored.source == note.source


def test_memory_note_default_source():
    note = MemoryNote(timestamp="2024-01-01", category="misc", content="test")
    assert note.source == "agent"


def test_memory_note_to_markdown_contains_fields():
    note = MemoryNote(
        timestamp="2024-06-01",
        category="preferences",
        content="Likes concise answers",
        source="user",
    )
    md = note.to_markdown()
    assert "2024-06-01" in md
    assert "preferences" in md
    assert "Likes concise answers" in md
    assert "user" in md
    assert md.strip().endswith("---")


def test_memory_note_to_markdown_format():
    note = MemoryNote(timestamp="T", category="C", content="Detail", source="system")
    md = note.to_markdown()
    assert "###" in md


# ─── UserIdentity ─────────────────────────────────────────────────────────────


def test_user_identity_defaults_are_none():
    identity = UserIdentity()
    assert identity.name is None
    assert identity.role is None
    assert identity.timezone is None
    assert identity.location is None


def test_user_identity_roundtrip():
    identity = UserIdentity(name="Alex", role="DevOps", timezone="UTC", location="Berlin")
    d = identity.to_dict()
    restored = UserIdentity.from_dict(d)
    assert restored.name == "Alex"
    assert restored.role == "DevOps"
    assert restored.timezone == "UTC"
    assert restored.location == "Berlin"


def test_user_identity_to_dict_excludes_none():
    identity = UserIdentity(name="Sam")
    d = identity.to_dict()
    assert "name" in d
    assert "role" not in d or d.get("role") is None  # should be excluded


def test_user_identity_from_dict_partial():
    restored = UserIdentity.from_dict({"name": "Jo"})
    assert restored.name == "Jo"
    assert restored.role is None


# ─── WorkPatterns ─────────────────────────────────────────────────────────────


def test_work_patterns_defaults():
    wp = WorkPatterns()
    assert wp.active_hours == []
    assert wp.deploy_preferences == {}
    assert wp.common_tasks == []


def test_work_patterns_roundtrip():
    wp = WorkPatterns(
        active_hours=["9-17 EST"],
        deploy_preferences={"preferred_day": "friday"},
        common_tasks=["docker restart"],
    )
    restored = WorkPatterns.from_dict(wp.to_dict())
    assert restored.active_hours == ["9-17 EST"]
    assert restored.deploy_preferences == {"preferred_day": "friday"}
    assert restored.common_tasks == ["docker restart"]


def test_work_patterns_from_dict_empty():
    wp = WorkPatterns.from_dict({})
    assert wp.active_hours == []


# ─── TechnicalContext ─────────────────────────────────────────────────────────


def test_technical_context_defaults():
    tc = TechnicalContext()
    assert tc.stack == []
    assert tc.managed_hosts == []
    assert tc.primary_projects == []
    assert tc.preferences == []


def test_technical_context_roundtrip():
    tc = TechnicalContext(
        stack=["Laravel", "Docker"],
        managed_hosts=["prod-01"],
        primary_projects=["my-saas"],
        preferences=["Python", "CLI"],
    )
    restored = TechnicalContext.from_dict(tc.to_dict())
    assert restored.stack == ["Laravel", "Docker"]
    assert restored.managed_hosts == ["prod-01"]


# ─── UserPreferences ──────────────────────────────────────────────────────────


def test_user_preferences_defaults():
    prefs = UserPreferences()
    assert prefs.communication_style is None
    assert prefs.alert_thresholds == {}
    assert prefs.confirmation_required_for == []


def test_user_preferences_roundtrip():
    prefs = UserPreferences(
        communication_style="concise",
        alert_thresholds={"disk_warning": 80},
        confirmation_required_for=["delete"],
    )
    restored = UserPreferences.from_dict(prefs.to_dict())
    assert restored.communication_style == "concise"
    assert restored.alert_thresholds == {"disk_warning": 80}
    assert restored.confirmation_required_for == ["delete"]


def test_user_preferences_from_dict_empty():
    prefs = UserPreferences.from_dict({})
    assert prefs.communication_style is None


# ─── InteractionStats ─────────────────────────────────────────────────────────


def test_interaction_stats_defaults():
    stats = InteractionStats()
    assert stats.total_sessions == 0
    assert stats.total_commands == 0
    assert stats.most_used_commands == {}
    assert stats.last_active is None


def test_interaction_stats_roundtrip():
    stats = InteractionStats(
        total_sessions=5,
        total_commands=42,
        most_used_commands={"navig": 20, "run": 10},
        last_active="2024-01-15T12:00:00",
        first_seen="2023-12-01",
    )
    restored = InteractionStats.from_dict(stats.to_dict())
    assert restored.total_sessions == 5
    assert restored.total_commands == 42
    assert restored.most_used_commands == {"navig": 20, "run": 10}


def test_interaction_stats_record_command_increments_total():
    stats = InteractionStats()
    stats.record_command("navig run ls")
    assert stats.total_commands == 1


def test_interaction_stats_record_command_tracks_base_cmd():
    stats = InteractionStats()
    stats.record_command("navig run ls")
    assert stats.most_used_commands.get("navig", 0) == 1


def test_interaction_stats_record_command_accumulates():
    stats = InteractionStats()
    for _ in range(3):
        stats.record_command("navig host list")
    assert stats.most_used_commands["navig"] == 3
    assert stats.total_commands == 3


def test_interaction_stats_record_command_updates_last_active():
    stats = InteractionStats()
    assert stats.last_active is None
    stats.record_command("navig db list")
    assert stats.last_active is not None


def test_interaction_stats_record_empty_command():
    stats = InteractionStats()
    # Should not raise
    stats.record_command("")
    assert stats.total_commands == 1
    assert "unknown" in stats.most_used_commands


def test_interaction_stats_from_dict_partial():
    stats = InteractionStats.from_dict({"total_sessions": 3})
    assert stats.total_sessions == 3
    assert stats.total_commands == 0


# ─── UserProfile ──────────────────────────────────────────────────────────────


def test_user_profile_defaults():
    profile = UserProfile()
    assert isinstance(profile.identity, UserIdentity)
    assert isinstance(profile.work_patterns, WorkPatterns)
    assert isinstance(profile.technical_context, TechnicalContext)
    assert isinstance(profile.preferences, UserPreferences)
    assert isinstance(profile.stats, InteractionStats)
    assert profile.goals == []
    assert profile.notes == []


def test_user_profile_to_dict_schema_version():
    profile = UserProfile()
    d = profile.to_dict()
    assert d["schema_version"] == 1


def test_user_profile_to_dict_roundtrip():
    profile = UserProfile()
    profile.identity.name = "Test User"
    profile.goals = ["ship v2"]
    d = profile.to_dict()
    restored = UserProfile.from_dict(d)
    assert restored.identity.name == "Test User"
    assert restored.goals == ["ship v2"]


def test_user_profile_to_dict_includes_notes():
    profile = UserProfile()
    note = MemoryNote(timestamp="2024-01-01", category="test", content="hello")
    profile.notes.append(note)
    d = profile.to_dict()
    assert len(d["notes"]) == 1
    assert d["notes"][0]["content"] == "hello"


def test_user_profile_from_dict_empty_notes():
    profile = UserProfile.from_dict({})
    assert profile.notes == []


def test_user_profile_load_from_tmp(tmp_path, monkeypatch):
    """load() with an explicit path creates a new empty profile when file missing."""
    profile_path = tmp_path / "user_profile.json"
    # File doesn't exist yet — should create fresh profile
    profile = UserProfile.load(profile_path=profile_path)
    assert isinstance(profile, UserProfile)
    assert profile._profile_path == profile_path


def test_user_profile_save_creates_file(tmp_path, monkeypatch):
    """save() persists a JSON file to disk."""
    profile_path = tmp_path / "user_profile.json"
    profile = UserProfile.load(profile_path=profile_path)
    profile.identity.name = "Persisted"
    profile.save()
    assert profile_path.exists()
    import json
    data = json.loads(profile_path.read_text())
    assert data["identity"]["name"] == "Persisted"
