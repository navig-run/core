"""
Tests for navig.onboarding.genesis — GenesisData creation, determinism, immutability.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


# ── 1. Node ID is deterministic given same inputs ────────────────────────────

def test_node_id_is_deterministic(tmp_path: Path) -> None:
    from navig.onboarding.genesis import _derive_node_id

    id1 = _derive_node_id("2024-01-01T00:00:00")
    id2 = _derive_node_id("2024-01-01T00:00:00")
    assert id1 == id2
    assert id1.startswith("navig_")
    assert len(id1) == len("navig_") + 6


# ── 2. Avatar seed is deterministic ──────────────────────────────────────────

def test_avatar_seed_is_deterministic() -> None:
    from navig.onboarding.genesis import _derive_avatar_seed

    seed1 = _derive_avatar_seed("navig_abc123", "Alice", "2024-01-01T00:00:00")
    seed2 = _derive_avatar_seed("navig_abc123", "Alice", "2024-01-01T00:00:00")
    assert seed1 == seed2
    assert isinstance(seed1, str)
    assert len(seed1) == 64  # SHA-256 hex


# ── 3. load_or_create is idempotent — second call returns same genesis ────────

def test_load_or_create_is_idempotent(tmp_path: Path) -> None:
    from navig.onboarding.genesis import load_or_create

    g1 = load_or_create(tmp_path, "my-node")
    g2 = load_or_create(tmp_path, "my-node")

    assert g1.nodeId == g2.nodeId
    assert g1.bornAt == g2.bornAt

    # genesis.json written
    assert (tmp_path / "genesis.json").exists()


# ── 4. genesis.json is immutable — second call with different name keeps original ─

def test_genesis_json_immutable_after_first_write(tmp_path: Path) -> None:
    from navig.onboarding.genesis import load_or_create

    g1 = load_or_create(tmp_path, "first-name")
    g2 = load_or_create(tmp_path, "different-name")

    # Name from second call is ignored; original preserved
    assert g2.nodeId == g1.nodeId
    assert g2.bornAt == g1.bornAt


# ── 5. render_qr_terminal and render_genesis_banner never raise ──────────────

def test_render_functions_never_raise(tmp_path: Path) -> None:
    from navig.onboarding.genesis import load_or_create, render_genesis_banner, render_qr_terminal

    genesis = load_or_create(tmp_path, "test-node")

    # These must not raise under any terminal environment
    try:
        qr = render_qr_terminal(genesis)
        assert isinstance(qr, str)
    except Exception as exc:
        pytest.fail(f"render_qr_terminal raised: {exc}")

    try:
        banner = render_genesis_banner(genesis)
        assert isinstance(banner, str)
    except Exception as exc:
        pytest.fail(f"render_genesis_banner raised: {exc}")
