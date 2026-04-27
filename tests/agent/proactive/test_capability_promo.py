"""
Unit tests for navig/agent/proactive/capability_promo.py

Covers: FeatureInfo defaults, FEATURE_REGISTRY, CapabilityPromoter
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# Patch heavy imports before loading the module
with patch("navig.agent.proactive.user_state.UserStateTracker._load_state"):
    from navig.agent.proactive.capability_promo import (
        FEATURE_REGISTRY,
        CapabilityPromoter,
        FeatureInfo,
    )


# ──────────────────────────────────────────────────────────────────────
# FeatureInfo dataclass
# ──────────────────────────────────────────────────────────────────────


class TestFeatureInfo:
    def test_required_fields_stored(self):
        fi = FeatureInfo(
            key="db_dump",
            name="DB Backup",
            description="Backs up databases.",
            example_command="navig db dump mydb -o backup.sql",
            category="database",
        )
        assert fi.key == "db_dump"
        assert fi.name == "DB Backup"
        assert fi.description == "Backs up databases."
        assert fi.example_command == "navig db dump mydb -o backup.sql"
        assert fi.category == "database"

    def test_defaults(self):
        fi = FeatureInfo(
            key="x", name="X", description="d", example_command="cmd", category="cat"
        )
        assert fi.prerequisites == []
        assert fi.when_to_suggest == ""
        assert fi.min_interactions == 5
        assert fi.priority == 5

    def test_custom_priority(self):
        fi = FeatureInfo(
            key="x", name="X", description="d", example_command="cmd",
            category="cat", priority=9,
        )
        assert fi.priority == 9

    def test_prerequisites_stored(self):
        fi = FeatureInfo(
            key="db_dump", name="DB Backup", description="d", example_command="cmd",
            category="db", prerequisites=["db"],
        )
        assert fi.prerequisites == ["db"]


# ──────────────────────────────────────────────────────────────────────
# FEATURE_REGISTRY
# ──────────────────────────────────────────────────────────────────────


class TestFeatureRegistry:
    def test_registry_is_non_empty(self):
        assert len(FEATURE_REGISTRY) > 0

    def test_all_entries_are_feature_info(self):
        for item in FEATURE_REGISTRY:
            assert isinstance(item, FeatureInfo)

    def test_all_keys_are_unique(self):
        keys = [f.key for f in FEATURE_REGISTRY]
        assert len(keys) == len(set(keys))

    def test_all_have_example_commands(self):
        for f in FEATURE_REGISTRY:
            assert f.example_command.strip(), f"Feature '{f.key}' has no example_command"

    def test_known_keys_present(self):
        keys = {f.key for f in FEATURE_REGISTRY}
        assert "host_monitor" in keys
        assert "db_dump" in keys
        assert "docker_stats" in keys
        assert "flow" in keys

    def test_flow_has_high_min_interactions(self):
        flow = next(f for f in FEATURE_REGISTRY if f.key == "flow")
        assert flow.min_interactions >= 10

    def test_scaffold_has_high_min_interactions(self):
        scaffold = next(f for f in FEATURE_REGISTRY if f.key == "scaffold")
        assert scaffold.min_interactions >= 10


# ──────────────────────────────────────────────────────────────────────
# CapabilityPromoter helpers
# ──────────────────────────────────────────────────────────────────────


def _make_state(
    *,
    total_messages: int = 100,
    features_used: dict | None = None,
) -> MagicMock:
    """Build a minimal UserStateTracker-like mock."""
    state = MagicMock()
    state.stats.total_messages = total_messages
    state.stats.features_used = features_used or {}
    return state


class TestCapabilityPromoter:
    # ── __init__ ──────────────────────────────────────────────────────

    def test_default_features_is_registry(self):
        p = CapabilityPromoter()
        assert p.features is FEATURE_REGISTRY

    def test_custom_features_accepted(self):
        custom = [
            FeatureInfo(key="a", name="A", description="d", example_command="cmd", category="c")
        ]
        p = CapabilityPromoter(features=custom)
        assert p.features is custom

    def test_promotion_history_starts_empty(self):
        p = CapabilityPromoter()
        assert p._promotion_history == []

    def test_max_history_default(self):
        p = CapabilityPromoter()
        assert p._max_history == 20

    # ── get_all_feature_keys ─────────────────────────────────────────

    def test_get_all_feature_keys_matches_registry(self):
        p = CapabilityPromoter()
        keys = p.get_all_feature_keys()
        assert keys == [f.key for f in FEATURE_REGISTRY]

    def test_get_all_feature_keys_custom(self):
        custom = [
            FeatureInfo(key="a", name="A", description="d", example_command="cmd", category="c"),
            FeatureInfo(key="b", name="B", description="d", example_command="cmd", category="c"),
        ]
        p = CapabilityPromoter(features=custom)
        assert p.get_all_feature_keys() == ["a", "b"]

    # ── _build_promotion_message ─────────────────────────────────────

    def test_build_promotion_message_contains_name(self):
        p = CapabilityPromoter()
        fi = FeatureInfo(
            key="test", name="My Feature", description="Does stuff.",
            example_command="navig test run", category="cat",
        )
        msg = p._build_promotion_message(fi)
        assert "My Feature" in msg

    def test_build_promotion_message_contains_command(self):
        p = CapabilityPromoter()
        fi = FeatureInfo(
            key="test", name="My Feature", description="Does stuff.",
            example_command="navig test run", category="cat",
        )
        msg = p._build_promotion_message(fi)
        assert "navig test run" in msg

    def test_build_promotion_message_contains_description(self):
        p = CapabilityPromoter()
        fi = FeatureInfo(
            key="test", name="My Feature", description="Does stuff.",
            example_command="navig test run", category="cat",
        )
        msg = p._build_promotion_message(fi)
        assert "Does stuff." in msg

    # ── _score_candidates ────────────────────────────────────────────

    def test_score_candidates_returns_empty_when_not_enough_interactions(self):
        p = CapabilityPromoter()
        # All features have min_interactions >= 5; give 0 interactions
        state = _make_state(total_messages=0)
        result = p._score_candidates(state)
        assert result == []

    def test_score_candidates_excludes_heavily_used(self):
        fi = FeatureInfo(
            key="used_a_lot", name="A", description="d", example_command="cmd",
            category="cat", min_interactions=1, priority=9,
        )
        p = CapabilityPromoter(features=[fi])
        state = _make_state(total_messages=50, features_used={"used_a_lot": 10})
        result = p._score_candidates(state)
        assert result == []

    def test_score_candidates_excludes_unmet_prerequisites(self):
        fi = FeatureInfo(
            key="needs_db", name="A", description="d", example_command="cmd",
            category="cat", prerequisites=["db"], min_interactions=1,
        )
        p = CapabilityPromoter(features=[fi])
        # features_used doesn't contain "db"
        state = _make_state(total_messages=50, features_used={})
        result = p._score_candidates(state)
        assert result == []

    def test_score_candidates_includes_prereqs_met(self):
        fi = FeatureInfo(
            key="needs_db", name="A", description="d", example_command="cmd",
            category="cat", prerequisites=["db"], min_interactions=1,
        )
        p = CapabilityPromoter(features=[fi])
        state = _make_state(total_messages=50, features_used={"db": 5})
        result = p._score_candidates(state)
        assert len(result) == 1
        assert result[0][0].key == "needs_db"

    def test_score_candidates_novelty_bonus(self):
        fi_used = FeatureInfo(
            key="already_used", name="A", description="d", example_command="cmd",
            category="cat", min_interactions=1, priority=5,
        )
        fi_new = FeatureInfo(
            key="never_used", name="B", description="d", example_command="cmd",
            category="cat", min_interactions=1, priority=5,
        )
        p = CapabilityPromoter(features=[fi_used, fi_new])
        state = _make_state(total_messages=50, features_used={"already_used": 2})
        result = p._score_candidates(state)
        # never_used should score higher due to novelty bonus (+3 vs +1)
        keys = [f.key for f, _ in result]
        assert keys[0] == "never_used"

    # ── get_promotion ─────────────────────────────────────────────────

    def test_get_promotion_returns_none_when_no_candidates(self):
        p = CapabilityPromoter()
        state = _make_state(total_messages=0)
        msg, key = p.get_promotion(state)
        assert msg is None
        assert key is None

    def test_get_promotion_returns_message_and_key(self):
        fi = FeatureInfo(
            key="promoted", name="P", description="Promotes stuff.",
            example_command="navig p run", category="cat", min_interactions=1,
        )
        p = CapabilityPromoter(features=[fi])
        state = _make_state(total_messages=50)
        msg, key = p.get_promotion(state)
        assert key == "promoted"
        assert "P" in msg

    def test_get_promotion_tracks_history(self):
        fi = FeatureInfo(
            key="tracked", name="T", description="d",
            example_command="navig t", category="cat", min_interactions=1,
        )
        p = CapabilityPromoter(features=[fi])
        state = _make_state(total_messages=50)
        p.get_promotion(state)
        assert "tracked" in p._promotion_history

    def test_get_promotion_history_capped_at_max(self):
        fi = FeatureInfo(
            key="cap", name="C", description="d",
            example_command="navig c", category="cat", min_interactions=1,
        )
        p = CapabilityPromoter(features=[fi])
        p._max_history = 3
        state = _make_state(total_messages=50)
        for _ in range(10):
            p.get_promotion(state)
        assert len(p._promotion_history) <= 3
