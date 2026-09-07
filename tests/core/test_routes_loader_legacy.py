"""Pins the legacy `routes.yaml` shapes the loader must accept.

The canonical schema is `channels:` with `agents:`/`keywords:`. Almost nothing on disk
was written that way: space scaffolding emitted `routes:`/`channel:`/`owner:`/`triggers:`
instead, so on a real install 13 of 16 routes.yaml files parsed to an EMPTY config. Those
spaces looked configured and routed nothing — `inbox reroute` exited 0 having done
nothing, which is the worst kind of failure because it never announces itself.

Each test below is one shape actually found on disk, named for where it came from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

from navig.inbox.routes_loader import load  # noqa: E402


def _write(root: Path, body: str) -> Path:
    dest = root / ".navig" / "inbox"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "routes.yaml").write_text(body, encoding="utf-8")
    return root


# ── the canonical shape must keep working ────────────────────────────────────


def test_canonical_channels_shape_is_unchanged(temp_dir):
    """company-business-space / human-space — the 3 files that already worked."""
    root = _write(
        Path(temp_dir) / "canonical",
        """
channels:
  - id: ecom
    name: "#ecommerce"
    agents: [merchandiser]
    keywords: [listing, sku]
    priority: high
defaults:
  sla_hours: 12
  unrouted_fallback: ops-manager
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert [c.id for c in cfg.channels] == ["ecom"]
    assert cfg.channels[0].agents == ["merchandiser"]
    assert cfg.channels[0].keywords == ["listing", "sku"]
    assert cfg.defaults.sla_hours == 12
    assert cfg.defaults.unrouted_fallback == "ops-manager"


# ── legacy shapes ────────────────────────────────────────────────────────────


def test_routes_with_handlers_and_default(temp_dir):
    """sensei-monks / doctor-soma: `routes:` + `channel:` + `handlers:` + `default:`."""
    root = _write(
        Path(temp_dir) / "sensei",
        """
version: "1.0"
routes:
  - channel: "#lyrics"
    description: "Song lyrics, rhyme ideas"
    handlers: ["lyricist"]
    default: true
  - channel: "#beats"
    handlers: ["beat-architect"]
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert [c.id for c in cfg.channels] == ["#lyrics", "#beats"]
    assert cfg.channels[0].agents == ["lyricist"]
    # `default: true` names where unrouted content goes.
    assert cfg.defaults.unrouted_fallback == "lyricist"


def test_routes_with_owner_and_triggers(temp_dir):
    """homelab / research / system: `owner:` + `triggers: [{keyword: [...]}]`."""
    root = _write(
        Path(temp_dir) / "homelab",
        """
schema_version: "1.0"
routes:
  - channel: "#discovery"
    owner: beacon
    triggers:
      - keyword: [scan, probe]
      - formation: discovery-sweep
    response_mode: immediate
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert cfg.channels[0].id == "#discovery"
    assert cfg.channels[0].agents == ["beacon"]
    # only `keyword` entries are terms — a formation is not a search term
    assert cfg.channels[0].keywords == ["scan", "probe"]


def test_channels_with_handler_and_tags(temp_dir):
    """miztizm: right top-level key, but `handler:`/`tags:` instead of agents/keywords."""
    root = _write(
        Path(temp_dir) / "miztizm",
        """
channels:
  - id: "#content"
    name: Content Ideas
    handler: cyber-alchemist
    tags: [content, ideas, planning]
  - id: "#lab"
    handler: cyber-alchemist
    multi_agent: [studio-engineer, code-narrator]
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert cfg.channels[0].agents == ["cyber-alchemist"]
    assert cfg.channels[0].keywords == ["content", "ideas", "planning"]
    # handler + multi_agent combine, in that order, without duplicates
    assert cfg.channels[1].agents == ["cyber-alchemist", "studio-engineer", "code-narrator"]


def test_routes_with_pattern_and_fallback_block(temp_dir):
    """somaleto: `pattern:` is a regex alternation, and `fallback:` is a top-level block."""
    root = _write(
        Path(temp_dir) / "somaleto",
        """
routes:
  - pattern: "dashcam|brain|recording"
    channel: "#dashcam"
    priority: high
fallback:
  channel: "#ideas"
  action: flag-for-review
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert cfg.channels[0].id == "#dashcam"
    assert cfg.channels[0].keywords == ["dashcam", "brain", "recording"]
    assert cfg.channels[0].priority == "high"
    assert cfg.defaults.unrouted_fallback == "#ideas"


def test_formation_is_not_treated_as_an_agent(temp_dir):
    """dev-space has both `owner:` and `formation:` — only the owner handles content."""
    root = _write(
        Path(temp_dir) / "dev",
        """
channels:
  - id: code
    owner: engineer
    formation: build-squad
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert cfg.channels[0].agents == ["engineer"]
    assert "build-squad" not in cfg.channels[0].agents


# ── contract: never raise, degrade quietly ───────────────────────────────────


def test_explicit_defaults_win_over_an_inferred_fallback(temp_dir):
    root = _write(
        Path(temp_dir) / "both",
        """
routes:
  - channel: "#a"
    handlers: [alpha]
    default: true
defaults:
  unrouted_fallback: chosen-by-hand
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert cfg.defaults.unrouted_fallback == "chosen-by-hand"


def test_channels_wins_when_both_keys_are_present(temp_dir):
    root = _write(
        Path(temp_dir) / "both-keys",
        """
channels:
  - id: real
    agents: [a]
routes:
  - channel: ignored
    owner: b
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert [c.id for c in cfg.channels] == ["real"]


def test_entry_without_an_id_is_skipped_not_fatal(temp_dir):
    root = _write(
        Path(temp_dir) / "partial",
        """
routes:
  - owner: nameless
  - channel: "#ok"
    owner: fine
""",
    )
    cfg = load(root)
    assert cfg is not None
    assert [c.id for c in cfg.channels] == ["#ok"]


def test_malformed_yaml_returns_none_and_does_not_raise(temp_dir):
    root = _write(Path(temp_dir) / "broken", "channels: [unclosed\n")
    assert load(root) is None


def test_missing_file_returns_none(temp_dir):
    root = Path(temp_dir) / "empty"
    root.mkdir(parents=True, exist_ok=True)
    assert load(root) is None
