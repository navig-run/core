"""Parity guard for the canonical tier/entitlement spec.

`navig/license/tiers.json` is the monorepo's single source of truth. The `live`
section (NAVIG Harbor: free/plus/max/team/enterprise) is what we sell + enforce,
and it is mirrored, by necessity, across three separately-built packages:

  * Python  — ``navig/license/quota.py``                 (daemon)
  * TS      — ``services/api/src/lib/billing.ts``           (billing Worker)
  * TS      — ``apps/deck/lib/license.ts``              (Deck client)

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


def test_quota_contains_legacy_tier_values() -> None:
    """Legacy tiers carry VALUES too, and an old token is still verified against them.

    Only the live tiers were value-checked, so a retired tier's host_limit could drift
    in quota.py and nothing would notice — while every pre-Harbor licence still in the
    wild is graded by exactly those numbers.
    """
    legacy = _spec()["legacy"]["tiers"]
    assert legacy, "spec has no legacy tiers — this test would assert nothing"
    for name, t in legacy.items():
        assert name in quota.TIER_HOST_LIMIT, f"quota.py no longer recognizes legacy {name}"
        assert quota.TIER_HOST_LIMIT[name] == t["host_limit"], f"legacy {name} host_limit drift"
        assert set(quota.TIER_CAPABILITIES[name]) == set(t["capabilities"]), (
            f"legacy {name} capabilities drift"
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
    """The mirror's source, or None when this checkout genuinely does not carry it.

    `core/` is ALSO published standalone (github.com/navig-run/core), where the TS
    siblings do not exist — so a skip is legitimate there. It is NOT legitimate in
    the monorepo, and telling the two apart is the whole point: these two paths
    said `navig-api/` and `navig-deck/` (the pre-monorepo sibling repos) from the
    migration until 2026-08-06, so both tests skipped on every run for months.
    A skip reads exactly like a pass in the summary, and CLAUDE.md meanwhile cites
    this file as the thing that stops the tier spec drifting across four systems.

    So: if the workspace the file belongs to IS present, a missing file is a STALE
    PATH and must fail. Only a checkout without that workspace at all may skip.
    """
    p = _REPO_ROOT / rel
    if p.is_file():
        return p.read_text(encoding="utf-8")
    workspace = _REPO_ROOT / Path(rel).parts[0] / Path(rel).parts[1]
    if workspace.is_dir():
        pytest.fail(
            f"{rel} is missing but {workspace.relative_to(_REPO_ROOT)} exists — the path is "
            f"stale, not absent. Point it at the file's new home; do not let this skip."
        )
    return None


def test_billing_ts_covers_live_tiers_and_modules() -> None:
    text = _read("services/api/src/lib/billing.ts")
    if text is None:
        pytest.skip("services/api/src/lib/billing.ts not present in this checkout")
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
    text = _read("apps/deck/lib/license.ts")
    if text is None:
        pytest.skip("apps/deck/lib/license.ts not present in this checkout")
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


# ── TypeScript VALUES ↔ spec (not just the union NAMES) ─────────────────────
#
# The union tests above prove the TS side knows the same tier and module NAMES.
# They say nothing about the numbers, and `billing.ts` is where a purchased
# licence is actually minted: `runPipeline` writes `TIER_HOST_LIMIT[tier]` into
# the licences row AND into the signed token, while the daemon later enforces
# with `quota.py`'s copy. Those two disagreeing means a buyer pays for one host
# count and gets another, silently.
#
# `quota.py`'s values WERE checked; the TypeScript side's were not — so the one
# language that mints the licence was the one nothing verified. billing.ts's own
# header meanwhile claims "Mirrors the canonical spec … for tier → host_limit /
# capabilities … Do not drift these — the parity test enforces it." Until now
# that sentence was false.


def _ts_number_record(text: str, name: str) -> dict[str, int] | None:
    """`const NAME: Record<..> = { key: 123, … }` → {key: 123}. None if absent."""
    m = re.search(rf"{name}\b[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
    if not m:
        return None
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return {k: int(v.replace("_", "")) for k, v in re.findall(r"(\w+):\s*(\d[\d_]*)", body)}


def _ts_string_array_record(text: str, name: str) -> dict[str, set[str]] | None:
    """`const NAME = { key: ['a', 'b'], … }` → {key: {'a','b'}}. None if absent."""
    m = re.search(rf"{name}\b[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
    if not m:
        return None
    body = re.sub(r"//[^\n]*", "", m.group(1))
    return {
        tier: set(re.findall(r"""['"]([a-z_:][a-z0-9_:.-]*)['"]""", arr))
        for tier, arr in re.findall(r"(\w+):\s*\[(.*?)\]", body, re.S)
    }


def test_billing_ts_tier_values_match_spec() -> None:
    text = _read("services/api/src/lib/billing.ts")
    if text is None:
        pytest.skip("services/api/src/lib/billing.ts not present in this checkout")
    spec = _spec()
    expected = {**spec["live"]["tiers"], **spec["legacy"]["tiers"]}

    limits = _ts_number_record(text, "TIER_HOST_LIMIT")
    caps = _ts_string_array_record(text, "TIER_CAPABILITIES")
    # A regex that matches nothing must FAIL, not quietly compare zero pairs —
    # that is the exact shape that let the two tests above skip for months.
    assert limits, "could not parse TIER_HOST_LIMIT from billing.ts"
    assert caps, "could not parse TIER_CAPABILITIES from billing.ts"
    assert len(limits) >= len(expected), (
        f"parsed only {len(limits)} host limits from billing.ts, spec has {len(expected)} tiers"
    )

    for name, t in expected.items():
        assert name in limits, f"billing.ts TIER_HOST_LIMIT missing {name}"
        assert limits[name] == t["host_limit"], (
            f"billing.ts {name} host_limit is {limits[name]}, spec says {t['host_limit']} — "
            f"a buyer would be minted a licence for the wrong number of hosts"
        )
        assert name in caps, f"billing.ts TIER_CAPABILITIES missing {name}"
        assert caps[name] == set(t["capabilities"]), (
            f"billing.ts {name} capabilities drift — "
            f"missing {sorted(set(t['capabilities']) - caps[name])}, "
            f"extra {sorted(caps[name] - set(t['capabilities']))}"
        )


def test_license_ts_module_prices_match_spec() -> None:
    """The deck DISPLAYS these. Drift here quotes a price we do not charge."""
    text = _read("apps/deck/lib/license.ts")
    if text is None:
        pytest.skip("apps/deck/lib/license.ts not present in this checkout")
    spec_prices = _spec()["module_onetime_usd"]
    assert spec_prices, "spec has no module prices — this test would assert nothing"

    prices = _ts_number_record(text, "MODULE_ONETIME_USD")
    assert prices, "could not parse MODULE_ONETIME_USD from license.ts"
    assert prices == spec_prices, (
        f"license.ts module price drift — spec {spec_prices}, license.ts {prices}"
    )


def test_license_ts_free_status_matches_spec() -> None:
    """`FREE_STATUS` is what an unlicensed deck renders as the user's entitlement."""
    text = _read("apps/deck/lib/license.ts")
    if text is None:
        pytest.skip("apps/deck/lib/license.ts not present in this checkout")
    free = _spec()["live"]["tiers"]["free"]

    m = re.search(r"FREE_STATUS\b[^=]*=\s*\{(.*?)\n\};", text, re.S)
    assert m, "could not find FREE_STATUS in license.ts"
    body = m.group(1)

    hl = re.search(r"host_limit:\s*(\d+)", body)
    assert hl, "FREE_STATUS has no host_limit"
    assert int(hl.group(1)) == free["host_limit"], (
        f"FREE_STATUS host_limit is {hl.group(1)}, spec says {free['host_limit']}"
    )

    arr = re.search(r"capabilities:\s*\[(.*?)\]", body, re.S)
    assert arr, "FREE_STATUS has no capabilities array"
    caps = set(re.findall(r"""['"]([a-z_]+)['"]""", arr.group(1)))
    assert caps == set(free["capabilities"]), (
        f"FREE_STATUS capabilities {sorted(caps)} != spec {sorted(free['capabilities'])}"
    )


# ── The marketing site ↔ spec (the numbers a buyer reads BEFORE paying) ──────
#
# `web/www/content/copy.ts` is the fifth mirror, and the only one a customer
# sees before money changes hands: the price, the host count, and the module
# list on the pricing page. `billing.ts` already asserts in prose that
# TIER_PERPETUAL_PRICE_CENTS "must stay in sync with deckTiers.oneTimeUsd" —
# nothing checked it. Drift here quotes a price we do not charge or promises a
# capability the tier does not grant.
#
# Capabilities are checked as a SUBSET, not equality: every tier deliberately
# omits `echo` from its marketing list (it is granted by tier but sold and
# marketed as its own app, and is excluded from PurchasableModule for the same
# reason). Under-promising is a copy decision; OVER-promising is the bug.


def _www_tiers(text: str) -> dict[str, dict] | None:
    """Parse `export const deckTiers = [ { id: 'plus', … }, … ]`."""
    arr = re.search(r"export const deckTiers\s*=\s*\[(.*?)\n\]", text, re.S)
    if not arr:
        return None
    out: dict[str, dict] = {}
    for chunk in re.split(r"\n    id:\s*'", arr.group(1))[1:]:
        tier = chunk[: chunk.index("'")]

        def num(field: str, c: str = chunk) -> int | None:
            m = re.search(rf"{field}:\s*(null|\d+)", c)
            return None if not m or m.group(1) == "null" else int(m.group(1))

        mods = re.search(r"modules:\s*\[(.*?)\]", chunk, re.S)
        out[tier] = {
            "annual": num("annualUsd"),
            "monthly": num("monthlyUsd"),
            "lifetime": num("oneTimeUsd"),
            "hosts": num("hosts"),
            "modules": set(re.findall(r"'([a-z_]+)'", mods.group(1))) if mods else None,
        }
    return out


def _www() -> dict[str, dict]:
    text = _read("web/www/content/copy.ts")
    if text is None:
        pytest.skip("web/www/content/copy.ts not present in this checkout")
    tiers = _www_tiers(text)
    # A restructured file must FAIL here, not silently compare an empty dict.
    assert tiers, "could not parse `deckTiers` from web/www/content/copy.ts"
    live = set(_spec()["live"]["tiers"])
    assert live <= set(tiers), f"copy.ts deckTiers missing live tiers {live - set(tiers)}"
    return tiers


def test_www_copy_prices_match_spec() -> None:
    """The listed price must be the price we charge."""
    spec_prices = {
        k: v for k, v in _spec()["live"]["prices_usd"].items() if not k.startswith("_")
    }
    assert spec_prices, "spec has no prices — this test would assert nothing"
    tiers = _www()

    compared = 0
    for tier, want in spec_prices.items():
        got = tiers[tier]
        for field, value in want.items():
            assert got[field] == value, (
                f"copy.ts {tier} {field} price is {got[field]}, spec says {value} — "
                f"the pricing page would quote a price we do not charge"
            )
            compared += 1
    assert compared >= 5, f"only {compared} price points compared"


def test_www_copy_host_limits_match_spec() -> None:
    """A host count SHOWN on the pricing page must be the one the licence grants.

    `null` is the deliberate contact-us marker (Enterprise quotes no number), so
    only a displayed number is pinned.
    """
    tiers = _www()
    compared = 0
    for tier, t in _spec()["live"]["tiers"].items():
        shown = tiers[tier]["hosts"]
        if shown is None:
            continue  # contact-us tier: no number on the page to be wrong
        assert shown == t["host_limit"], (
            f"copy.ts {tier} advertises {shown} hosts, spec grants {t['host_limit']}"
        )
        compared += 1
    assert compared >= 4, f"only {compared} host counts compared — parser drift?"


def test_www_copy_never_promises_an_ungranted_capability() -> None:
    """Subset, not equality — under-promising is copy, over-promising is a bug."""
    tiers = _spec()["live"]["tiers"]
    www = _www()
    for tier, t in tiers.items():
        listed = www[tier]["modules"]
        assert listed is not None, f"copy.ts {tier} has no modules list"
        extra = listed - set(t["capabilities"])
        assert not extra, (
            f"copy.ts advertises {sorted(extra)} for {tier}, which the tier does not grant"
        )


def test_billing_ts_perpetual_price_matches_spec() -> None:
    """The lifetime price in cents must be the spec's dollars × 100.

    Closes the third leg: spec `live.prices_usd.max.lifetime` → billing.ts
    `TIER_PERPETUAL_PRICE_CENTS.max` (what upgrade-credit arithmetic subtracts)
    → `deckTiers.oneTimeUsd` (what the pricing page quotes). All three now move
    together. The legacy entries have no spec counterpart — those tiers are no
    longer sold and carry no `prices_usd` — so only the live ones are pinned.
    """
    text = _read("services/api/src/lib/billing.ts")
    if text is None:
        pytest.skip("services/api/src/lib/billing.ts not present in this checkout")
    cents = _ts_number_record(text, "TIER_PERPETUAL_PRICE_CENTS")
    assert cents, "could not parse TIER_PERPETUAL_PRICE_CENTS from billing.ts"

    prices = {k: v for k, v in _spec()["live"]["prices_usd"].items() if not k.startswith("_")}
    compared = 0
    for tier, want in prices.items():
        if "lifetime" not in want:
            continue  # no lifetime SKU for this tier
        assert tier in cents, f"billing.ts has no perpetual price for {tier}"
        assert cents[tier] == want["lifetime"] * 100, (
            f"billing.ts {tier} perpetual price is {cents[tier]}c, "
            f"spec says ${want['lifetime']} ({want['lifetime'] * 100}c) — "
            f"upgrade credit would subtract the wrong amount"
        )
        compared += 1
    assert compared >= 1, "no lifetime price compared — spec shape changed?"
