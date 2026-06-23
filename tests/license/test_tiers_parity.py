"""Parity guard for the canonical tier/entitlement spec.

`navig/license/tiers.json` is the monorepo's single source of truth. The `live`
section (NAVIG Harbor: free/plus/max/team/enterprise) is what we sell + enforce,
and it is mirrored, by necessity, across three separately-built packages:

  * Python  — ``navig/license/quota.py``                 (daemon)
  * TS      — ``navig-api/src/lib/billing.ts``           (billing Worker)
  * TS      — ``navig-deck/lib/license.ts``              (Deck client)

They MUST agree on the live tiers. quota.py + the TS unions may ALSO carry the
retired ``legacy`` names (kept recognized for back-compat verification) — those
are allowed as extras; only the live set is enforced for equality.

See docs/BUSINESS-PLAN.md + docs/MONETIZATION.md.
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
    assert set(spec["module_onetime_usd"]) <= modules
    live = spec["live"]
    assert set(live["tier_order"]) == set(live["tiers"])
    for name, t in live["tiers"].items():
        assert set(t["capabilities"]) <= modules, f"live {name} has unknown capability"
    # legacy_to_harbor maps each retired tier UP to a real live tier.
    for src, dst in live["legacy_to_harbor"].items():
        if src.startswith("_"):
            continue
        assert dst in live["tiers"], f"legacy_to_harbor {src}->{dst} not a live tier"
        assert src in spec["legacy"]["tiers"], f"{src} not a legacy tier"
    for name, t in spec["legacy"]["tiers"].items():
        assert set(t["capabilities"]) <= modules, f"legacy {name} has unknown capability"


# ── Python (quota.py) ↔ spec.live ───────────────────────────────────────────

def test_quota_modules_match_spec() -> None:
    assert list(quota.ALL_MODULES) == _spec()["modules"]


def test_quota_contains_live_tiers() -> None:
    live = _spec()["live"]["tiers"]
    for name, t in live.items():
        assert name in quota.TIER_HOST_LIMIT, f"quota.py missing live tier {name}"
        assert quota.TIER_HOST_LIMIT[name] == t["host_limit"], f"{name} host_limit drift"
        assert set(quota.TIER_CAPABILITIES[name]) == set(t["capabilities"]), (
            f"{name} capabilities drift"
        )


def test_quota_legacy_map_matches_spec() -> None:
    legacy_map = {
        k: v for k, v in _spec()["live"]["legacy_to_harbor"].items() if not k.startswith("_")
    }
    for src, dst in legacy_map.items():
        assert src in quota.TIER_HOST_LIMIT, f"quota.py no longer recognizes legacy {src}"
        assert quota.LEGACY_TO_HARBOR.get(src) == dst, f"LEGACY_TO_HARBOR {src} drift"


# ── TypeScript unions ⊇ spec.live (cross-language guard) ────────────────────

def _ts_union_members(text: str, type_name: str) -> set[str] | None:
    """Extract the quoted lowercase members of a `type <name> = 'a' | 'b';` union."""
    m = re.search(rf"type\s+{type_name}\s*=\s*(.*?);", text, re.S)
    if not m:
        return None
    return set(re.findall(r"""['"]([a-z_]+)['"]""", m.group(1)))


def _read(rel: str) -> str | None:
    p = _REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.is_file() else None


def test_billing_ts_covers_live_tiers_and_modules() -> None:
    text = _read("navig-api/src/lib/billing.ts")
    if text is None:
        pytest.skip("navig-api/src/lib/billing.ts not present in this checkout")
    spec = _spec()
    live = set(spec["live"]["tiers"])
    modules = set(spec["modules"])

    tiers = _ts_union_members(text, "Tier")
    assert tiers is not None, "could not find `type Tier` in billing.ts"
    assert live <= tiers, f"billing.ts Tier union missing live tiers {live - tiers}"

    mods = _ts_union_members(text, "Module")
    assert mods is not None, "could not find `type Module` in billing.ts"
    assert mods == modules, (
        f"billing.ts Module union drift — missing {modules - mods}, extra {mods - modules}"
    )


def test_license_ts_covers_live_tiers_and_modules() -> None:
    text = _read("navig-deck/lib/license.ts")
    if text is None:
        pytest.skip("navig-deck/lib/license.ts not present in this checkout")
    spec = _spec()
    live = set(spec["live"]["tiers"])
    modules = set(spec["modules"])

    tiers = _ts_union_members(text, "TierName")
    assert tiers is not None, "could not find `type TierName` in license.ts"
    assert live <= tiers, f"license.ts TierName union missing live tiers {live - tiers}"

    mods = _ts_union_members(text, "ModuleName")
    assert mods is not None, "could not find `type ModuleName` in license.ts"
    assert mods == modules, (
        f"license.ts ModuleName union drift — missing {modules - mods}, extra {mods - modules}"
    )
