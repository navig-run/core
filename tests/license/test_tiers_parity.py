"""Parity guard for the canonical tier/entitlement spec.

`navig/license/tiers.json` is the monorepo's single source of truth for
tiers, capabilities, host limits and module SKUs. The shipped model is
duplicated, by necessity, across three separately-built packages:

  * Python  — ``navig/license/quota.py``                 (daemon)
  * TS      — ``navig-api/src/lib/billing.ts``           (billing Worker)
  * TS      — ``navig-deck/lib/license.ts``              (Deck client)

They MUST agree. This test asserts quota.py matches the spec exactly, and
(when the sibling repos are present in the monorepo checkout) that the TS
type unions list the same tier and module names — which is what historically
drifted: ``billing.ts`` was missing the ``plus`` tier and the ``echo`` module.

See docs/BUSINESS-PLAN.md for the consolidation plan.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from navig.license import quota

_SPEC_PATH = Path(quota.__file__).with_name("tiers.json")
_REPO_ROOT = Path(__file__).resolve().parents[3]  # tests/license/<f> -> navig-core -> repo


def _spec() -> dict:
    return json.loads(_SPEC_PATH.read_text(encoding="utf-8"))


# ── spec self-consistency ───────────────────────────────────────────────────

def test_spec_is_internally_consistent() -> None:
    spec = _spec()
    modules = set(spec["modules"])
    assert set(spec["purchasable_modules"]) <= modules
    assert set(spec["module_onetime_usd"]) <= set(spec["purchasable_modules"])
    legacy = spec["legacy"]
    assert set(legacy["tier_order"]) == set(legacy["tiers"])
    for name, t in legacy["tiers"].items():
        assert set(t["capabilities"]) <= modules, f"{name} has unknown capability"
    # Harbor target maps every legacy tier to a real Harbor tier.
    harbor = spec["harbor"]
    for src, dst in harbor["legacy_to_harbor"].items():
        if src.startswith("_"):
            continue
        assert src in legacy["tiers"], f"map source {src} not a legacy tier"
        assert dst in harbor["tiers"], f"map target {dst} not a harbor tier"


# ── Python (quota.py) ↔ spec.legacy ─────────────────────────────────────────

def test_quota_modules_match_spec() -> None:
    assert list(quota.ALL_MODULES) == _spec()["modules"]


def test_quota_tiers_match_spec() -> None:
    legacy = _spec()["legacy"]["tiers"]
    assert set(quota.TIER_HOST_LIMIT) == set(legacy)
    assert set(quota.TIER_CAPABILITIES) == set(legacy)
    for name, t in legacy.items():
        assert quota.TIER_HOST_LIMIT[name] == t["host_limit"], f"{name} host_limit drift"
        assert set(quota.TIER_CAPABILITIES[name]) == set(t["capabilities"]), (
            f"{name} capabilities drift"
        )


# ── TypeScript unions ↔ spec.legacy (cross-language guard) ──────────────────

def _ts_union_members(text: str, type_name: str) -> set[str] | None:
    """Extract the quoted lowercase members of a `type <name> = 'a' | 'b';` union."""
    m = re.search(rf"type\s+{type_name}\s*=\s*(.*?);", text, re.S)
    if not m:
        return None
    return set(re.findall(r"""['"]([a-z_]+)['"]""", m.group(1)))


def _read(rel: str) -> str | None:
    p = _REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.is_file() else None


def test_billing_ts_tier_and_module_unions_match_spec() -> None:
    text = _read("navig-api/src/lib/billing.ts")
    if text is None:
        pytest.skip("navig-api/src/lib/billing.ts not present in this checkout")
    spec = _spec()
    legacy_tiers = set(spec["legacy"]["tiers"])
    modules = set(spec["modules"])

    tiers = _ts_union_members(text, "Tier")
    assert tiers is not None, "could not find `type Tier` in billing.ts"
    assert tiers == legacy_tiers, (
        f"billing.ts Tier union drift — missing {legacy_tiers - tiers}, "
        f"extra {tiers - legacy_tiers}"
    )

    mods = _ts_union_members(text, "Module")
    assert mods is not None, "could not find `type Module` in billing.ts"
    assert mods == modules, (
        f"billing.ts Module union drift — missing {modules - mods}, extra {mods - modules}"
    )


def test_license_ts_tier_and_module_unions_match_spec() -> None:
    text = _read("navig-deck/lib/license.ts")
    if text is None:
        pytest.skip("navig-deck/lib/license.ts not present in this checkout")
    spec = _spec()
    legacy_tiers = set(spec["legacy"]["tiers"])
    modules = set(spec["modules"])

    tiers = _ts_union_members(text, "TierName")
    assert tiers is not None, "could not find `type TierName` in license.ts"
    assert tiers == legacy_tiers, (
        f"license.ts TierName union drift — missing {legacy_tiers - tiers}, "
        f"extra {tiers - legacy_tiers}"
    )

    mods = _ts_union_members(text, "ModuleName")
    assert mods is not None, "could not find `type ModuleName` in license.ts"
    assert mods == modules, (
        f"license.ts ModuleName union drift — missing {modules - mods}, extra {mods - modules}"
    )
