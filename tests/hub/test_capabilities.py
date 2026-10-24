"""Tests for navig.hub.capabilities — the Unified Capability Index.

The index is a pure fold over Store items: these tests therefore feed it
SYNTHETIC StoreItems (no plugin loading, no vault, no network) and assert the
grouping, rollup, secret-redaction and isolation rules. One real-store smoke
test with a scan floor proves the readers still wire up; if a collector
silently returns nothing, the floor fails instead of the index shipping empty.
"""

from __future__ import annotations

import json

from navig.hub.aggregator import StoreItem
from navig.hub.capabilities import capability_key, collect_capabilities


def _item(id_: str, kind: str, **kw) -> StoreItem:
    return StoreItem(id=id_, kind=kind, label=kw.pop("label", id_), **kw)


# ── capability_key: the grouping rule ────────────────────────────────────────


class TestCapabilityKey:
    def test_navig_prefix_and_separators_normalize(self):
        """plugin:navig-perplexity, module:perplexity and connector:perplexity
        must fold into ONE key — that is the whole point of the index."""
        plugin = _item("plugin:navig-perplexity", "plugin")
        module = _item("module:perplexity", "module")
        connector = _item("connector:perplexity", "connector")
        assert capability_key(plugin) == capability_key(module) == capability_key(connector)
        assert capability_key(plugin) == "perplexity"

    def test_provider_wins_for_skills_and_mcp(self):
        """A skill shipped inside a plugin belongs to the plugin's capability,
        not to a capability named after the individual skill file."""
        skill = _item("skill:perplexity-delegate", "skill", provider="navig-perplexity")
        assert capability_key(skill) == "perplexity"

    def test_local_skill_keeps_its_own_identity(self):
        skill = _item("skill:python-cooking", "skill")
        assert capability_key(skill) == "pythoncooking"

    def test_case_and_punctuation_fold(self):
        a = _item("plugin:Navig-Media", "plugin")
        b = _item("module:media", "module")
        assert capability_key(a) == capability_key(b) == "media"


# ── grouping and descriptor shape ────────────────────────────────────────────


def _perplexity_group() -> list[StoreItem]:
    return [
        _item(
            "module:perplexity",
            "module",
            label="Perplexity",
            description="Ask the desktop app over CDP.",
            state="wired",
            detail={"kind": "app", "category": "grow", "surfaces": ["cli:perplexity"]},
        ),
        _item(
            "plugin:navig-perplexity",
            "plugin",
            state="wired",
            detail={"format": "pip", "commands": ["perplexity"], "error": ""},
        ),
        _item(
            "connector:perplexity",
            "connector",
            label="Perplexity AI",
            description="Sonar API.",
            state="unwired",
            source="ai_research",
            detail={
                "status": "disconnected",
                "auth_mode": "api_key",
                "requires_oauth": False,
                "key_fields": ["api_key"],
                "can_search": True,
                "can_fetch": False,
                "can_act": False,
            },
        ),
        _item("skill:perplexity-delegate", "skill", state="wired", provider="navig-perplexity"),
        _item("skill:perplexity-research", "skill", state="wired", provider="navig-perplexity"),
    ]


class TestGrouping:
    def test_multi_backend_capability_folds_once(self):
        caps, errors = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        assert errors == []
        px = next(c for c in caps if c.id == "perplexity")
        kinds = {b.kind for b in px.backends}
        assert kinds == {"module", "plugin", "connector"}
        assert len(px.backends) == 3, "skills are associations, not backends"

    def test_skills_listed_not_duplicated_as_backends(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        assert px.skills == ["skill:perplexity-delegate", "skill:perplexity-research"]
        assert px.overlaps == [], "two skills of one plugin are not a duplicate registration"

    def test_state_rollup_prefers_wired(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        # module + plugin wired, connector unwired → capability wired.
        assert px.state == "wired"

    def test_broken_backend_stays_visible_in_health(self):
        items = _perplexity_group() + [
            _item(
                "connector:other",
                "connector",
                state="broken",
                detail={"error": "boom", "can_search": True, "can_fetch": False, "can_act": False},
            ),
        ]
        caps, _ = collect_capabilities(items=items, mcp_probe=False)
        other = next(c for c in caps if c.id == "other")
        assert other.state == "broken"
        assert other.health == [{"backend": "connector:other", "error": "boom"}]

    def test_standalone_skill_still_gets_a_descriptor(self):
        caps, _ = collect_capabilities(
            items=[_item("skill:python-cooking", "skill", state="wired")], mcp_probe=False
        )
        assert [c.id for c in caps] == ["pythoncooking"]
        assert caps[0].backends[0].kind == "skill"
        assert caps[0].state == "wired"

    def test_deterministic_ordering(self):
        a, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        b, _ = collect_capabilities(items=list(reversed(_perplexity_group())), mcp_probe=False)
        assert [c.id for c in a] == [c.id for c in b]
        assert [x.source_id for x in a[0].backends] == [x.source_id for x in b[0].backends]


# ── derived metadata ─────────────────────────────────────────────────────────


class TestDerivedMetadata:
    def test_connector_auth_names_not_values(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        conn = next(b for b in px.backends if b.kind == "connector")
        assert conn.auth == {
            "mode": "api_key",
            "requires_oauth": False,
            "key_fields": ["api_key"],
        }
        assert conn.permissions == {"read": True, "write": False}
        assert conn.execution == "remote"

    def test_no_secret_values_in_serialised_descriptor(self):
        """The mission's redaction rule: names, never values."""
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        blob = json.dumps([c.to_dict() for c in caps])
        assert "pplx-" not in blob
        assert "sk-" not in blob
        # The only auth-adjacent strings are field NAMES.
        assert '"key_fields": ["api_key"]' in blob

    def test_connector_mcp_tools_follow_capability_flags(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        # can_search=True, can_fetch=False, can_act=False → ONE tool.
        assert px.mcp_tools == ["connector_perplexity_search"]

    def test_cli_commands_come_from_the_manifest(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        assert "navig perplexity ask" in px.cli_commands
        assert all(c.startswith("navig perplexity") for c in px.cli_commands)
        # The connector backend must NOT drag the whole `navig connector` tree in.
        assert not any(c.startswith("navig connector ") for c in px.cli_commands)

    def test_display_name_prefers_module_label(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        assert px.display_name == "Perplexity"

    def test_capability_type_from_connector_domain(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        assert px.capability_type == "ai_research"

    def test_execution_rollup_mixed(self):
        caps, _ = collect_capabilities(items=_perplexity_group(), mcp_probe=False)
        px = next(c for c in caps if c.id == "perplexity")
        assert px.execution == "mixed"  # local desktop + remote API


# ── isolation and honesty ────────────────────────────────────────────────────


class TestIsolation:
    def test_a_broken_item_never_kills_the_index(self):
        class _Explosion:
            id = "plugin:boom"
            kind = "plugin"
            label = "boom"
            state = "wired"
            degraded = False
            provider = ""
            source = ""
            version = ""
            description = ""
            locked = system = standalone = False
            actions: list[str] = []

            @property
            def detail(self):
                raise RuntimeError("manifest exploded")

        items = [*_perplexity_group(), _Explosion()]  # type: ignore[list-item]
        caps, errors = collect_capabilities(items=items, mcp_probe=False)  # type: ignore[arg-type]
        assert any(c.id == "perplexity" for c in caps), "one bad item must not kill the fold"
        assert any("boom" in e["source"] for e in errors)

    def test_overlaps_flag_same_kind_collisions(self):
        items = [
            _item("plugin:navig-obs", "plugin", state="wired"),
            _item("plugin:obs", "plugin", state="wired"),
        ]
        caps, _ = collect_capabilities(items=items, mcp_probe=False)
        assert len(caps) == 1
        assert caps[0].overlaps == [
            {"kind": "plugin", "sources": ["plugin:navig-obs", "plugin:obs"]}
        ]


# ── real-store smoke (scan floor) ────────────────────────────────────────────


class TestRealStore:
    """One integration test against the live store: proves every collector is
    still wired. The floors are deliberately conservative but non-vacuous —
    an empty or silently-broken reader fails them."""

    def test_real_store_folds_with_scan_floor(self):
        caps, errors = collect_capabilities(mcp_probe=False)
        assert len(caps) >= 50, f"capability index collapsed: only {len(caps)} capabilities"
        multi = [c for c in caps if len(c.backends) > 1]
        assert len(multi) >= 3, "plugin+module pairs should group into multi-backend capabilities"
        # The reference integration must be present on any install with the
        # connector registered (bootstrap registers it unconditionally).
        ids = {c.id for c in caps}
        assert "perplexity" in ids, "the Perplexity reference capability vanished"
        px = next(c for c in caps if c.id == "perplexity")
        kinds = {b.kind for b in px.backends}
        assert "connector" in kinds, "the Sonar backend must appear"
        # No secrets, ever.
        blob = json.dumps([c.to_dict() for c in caps])
        assert "pplx-" not in blob
