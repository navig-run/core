"""Unit tests for navig.identity.entity — pure deterministic derivation.

All functions are seed-driven with Python's random.Random, so the same seed
always produces the same output.  Zero I/O, zero network, fully hermetic.
"""

from __future__ import annotations

import random
from dataclasses import fields

import pytest

from navig.identity.entity import (
    ENTITY_ARCHETYPES,
    PALETTES,
    SUBSYSTEMS,
    NaviEntity,
    derive_entity,
    generate_machine_name,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestEntityConstants:
    def test_entity_archetypes_is_list(self) -> None:
        assert isinstance(ENTITY_ARCHETYPES, list)

    def test_entity_archetypes_non_empty(self) -> None:
        assert len(ENTITY_ARCHETYPES) > 0

    def test_entity_archetypes_are_strings(self) -> None:
        for a in ENTITY_ARCHETYPES:
            assert isinstance(a, str)

    def test_palettes_is_dict(self) -> None:
        assert isinstance(PALETTES, dict)

    def test_palettes_non_empty(self) -> None:
        assert len(PALETTES) > 0

    def test_palette_values_are_three_element_lists(self) -> None:
        for key, val in PALETTES.items():
            assert isinstance(val, list), f"{key}: value is not a list"
            assert len(val) == 3, f"{key}: expected 3 colors, got {len(val)}"

    def test_palette_colors_are_hex(self) -> None:
        for key, val in PALETTES.items():
            for color in val:
                assert color.startswith("#"), f"{key}: {color!r} not a hex color"

    def test_subsystems_is_list(self) -> None:
        assert isinstance(SUBSYSTEMS, list)

    def test_subsystems_non_empty(self) -> None:
        assert len(SUBSYSTEMS) > 0

    def test_subsystems_are_strings(self) -> None:
        for s in SUBSYSTEMS:
            assert isinstance(s, str)

    def test_known_subsystems_present(self) -> None:
        for expected in ("vault", "gateway"):
            assert expected in SUBSYSTEMS


# ---------------------------------------------------------------------------
# generate_machine_name
# ---------------------------------------------------------------------------


class TestGenerateMachineName:
    def test_returns_string(self) -> None:
        assert isinstance(generate_machine_name("abc123"), str)

    def test_deterministic_same_seed(self) -> None:
        a = generate_machine_name("seed_x")
        b = generate_machine_name("seed_x")
        assert a == b

    def test_different_seeds_likely_different(self) -> None:
        names = {generate_machine_name(str(i)) for i in range(50)}
        # With 20 prefixes × 24 suffixes = 480 combos; 50 draws should produce > 1 unique
        assert len(names) > 1

    def test_no_separator_in_name(self) -> None:
        # Name is PREFIX+SUFFIX with no dash
        name = generate_machine_name("nosep")
        assert "-" not in name

    def test_name_is_uppercase(self) -> None:
        name = generate_machine_name("upper_test")
        assert name == name.upper()

    def test_non_empty(self) -> None:
        assert len(generate_machine_name("x")) > 0


# ---------------------------------------------------------------------------
# NaviEntity dataclass
# ---------------------------------------------------------------------------


class TestNaviEntityDataclass:
    def test_is_dataclass(self) -> None:
        field_names = {f.name for f in fields(NaviEntity)}
        assert "seed" in field_names
        assert "name" in field_names
        assert "archetype" in field_names
        assert "palette_key" in field_names
        assert "sigil_matrix" in field_names
        assert "sigil_compact" in field_names
        assert "subsystem_order" in field_names
        assert "resonance" in field_names

    def test_field_count(self) -> None:
        assert len(fields(NaviEntity)) == 8


# ---------------------------------------------------------------------------
# derive_entity
# ---------------------------------------------------------------------------


class TestDeriveEntity:
    _SEED = "deadbeef1234"

    def test_returns_navi_entity(self) -> None:
        entity = derive_entity(self._SEED)
        assert isinstance(entity, NaviEntity)

    def test_deterministic(self) -> None:
        a = derive_entity(self._SEED)
        b = derive_entity(self._SEED)
        assert a.name == b.name
        assert a.archetype == b.archetype
        assert a.palette_key == b.palette_key
        assert a.resonance == b.resonance
        assert a.subsystem_order == b.subsystem_order

    def test_seed_stored(self) -> None:
        entity = derive_entity(self._SEED)
        assert entity.seed == self._SEED

    def test_name_format(self) -> None:
        # Expected format: ARCHETYPE-XXXX (where XXXX is first 4 chars of seed uppercased)
        entity = derive_entity(self._SEED)
        parts = entity.name.split("-")
        assert len(parts) == 2
        assert parts[1] == self._SEED[:4].upper()

    def test_archetype_in_entity_archetypes(self) -> None:
        entity = derive_entity(self._SEED)
        assert entity.archetype in ENTITY_ARCHETYPES

    def test_name_starts_with_archetype_upper(self) -> None:
        entity = derive_entity(self._SEED)
        assert entity.name.startswith(entity.archetype.upper())

    def test_palette_key_in_palettes(self) -> None:
        entity = derive_entity(self._SEED)
        assert entity.palette_key in PALETTES

    def test_resonance_in_range(self) -> None:
        entity = derive_entity(self._SEED)
        assert 0 <= entity.resonance <= 100

    def test_resonance_within_60_to_99(self) -> None:
        # As per the implementation: rng.randint(60, 99)
        entity = derive_entity(self._SEED)
        assert 60 <= entity.resonance <= 99

    def test_subsystem_order_is_permutation_of_subsystems(self) -> None:
        entity = derive_entity(self._SEED)
        assert sorted(entity.subsystem_order) == sorted(SUBSYSTEMS)

    def test_subsystem_order_no_duplicates(self) -> None:
        entity = derive_entity(self._SEED)
        assert len(set(entity.subsystem_order)) == len(SUBSYSTEMS)

    def test_sigil_matrix_is_9x9(self) -> None:
        entity = derive_entity(self._SEED)
        assert len(entity.sigil_matrix) == 9
        for row in entity.sigil_matrix:
            assert len(row) == 9

    def test_sigil_compact_is_5x5(self) -> None:
        entity = derive_entity(self._SEED)
        assert len(entity.sigil_compact) == 5
        for row in entity.sigil_compact:
            assert len(row) == 5

    def test_sigil_matrix_rows_are_lists(self) -> None:
        entity = derive_entity(self._SEED)
        for row in entity.sigil_matrix:
            assert isinstance(row, list)

    def test_sigil_matrix_cells_are_strings(self) -> None:
        entity = derive_entity(self._SEED)
        for row in entity.sigil_matrix:
            for cell in row:
                assert isinstance(cell, str)

    def test_sigil_matrix_is_symmetric(self) -> None:
        """Left half mirrored to right — row[i] == row[N-1-i] for non-centre cols."""
        entity = derive_entity(self._SEED)
        for row in entity.sigil_matrix:
            n = len(row)
            for i in range(n // 2):
                assert row[i] == row[n - 1 - i], f"Row not symmetric: {row}"

    def test_sigil_compact_is_symmetric(self) -> None:
        entity = derive_entity(self._SEED)
        for row in entity.sigil_compact:
            n = len(row)
            for i in range(n // 2):
                assert row[i] == row[n - 1 - i]

    def test_different_seeds_give_different_names(self) -> None:
        names = {derive_entity(f"seed_{i:04d}").name for i in range(20)}
        assert len(names) > 1

    def test_sigil_matrix_and_compact_differ(self) -> None:
        """Different RNG sub-seeds; they should almost never be identical (statistically)."""
        entity = derive_entity("uniqueseed99")
        # Just check they are different sizes — guaranteed
        assert len(entity.sigil_matrix) != len(entity.sigil_compact)

    @pytest.mark.parametrize("seed", ["a", "0" * 64, "hello-world", "abc123def456"])
    def test_various_seed_formats(self, seed: str) -> None:
        entity = derive_entity(seed)
        assert isinstance(entity, NaviEntity)
        assert len(entity.sigil_matrix) == 9
