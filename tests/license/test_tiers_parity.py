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
    entry = r"(\w+)\s*:\s*([^,\n]*)"
    out: dict[str, int] = {}
    for key, raw in re.findall(entry, body):
        value = raw.strip()
        # The WHOLE value must be a plain integer literal. A prefix match read
        # `plus: 5e2` as 5 and `399.99` as 399, so drift passed as parity.
        if not re.fullmatch(r"\d[\d_]*", value):
            raise ValueError(f"{name}.{key}: unsupported numeric literal {value!r}")
        out[key] = int(value.replace("_", ""))
    # Anything that is not a `key: value` entry (a spread, a quoted key, a
    # computed key) is a value this parser would otherwise silently drop.
    leftover = re.sub(entry, "", body).replace(",", "").strip()
    if leftover:
        raise ValueError(f"{name}: unparsed entries {leftover!r}")
    return out


def _ts_string_array_record(text: str, name: str) -> dict[str, set[str]] | None:
    """`const NAME = { key: ['a', 'b'], … }` → {key: {'a','b'}}. None if absent."""
    m = re.search(rf"{name}\b[^=]*=\s*\{{(.*?)\n\}};", text, re.S)
    if not m:
        return None
    body = re.sub(r"//[^\n]*", "", m.group(1))
    literal = r"""['"]([a-z_:][a-z0-9_:.-]*)['"]"""
    entry = r"(\w+):\s*\[(.*?)\]"
    out: dict[str, set[str]] = {}
    for tier, arr in re.findall(entry, body, re.S):
        # Only string literals may remain: `['core_ops', ...EXTRA_CAPS]` parsed
        # as {'core_ops'} while billing granted everything in EXTRA_CAPS.
        rest = re.sub(literal, "", arr).replace(",", "").strip()
        if rest:
            raise ValueError(f"{name}.{tier}: unparsed capability expression {rest!r}")
        out[tier] = set(re.findall(literal, arr))
    leftover = re.sub(entry, "", body, flags=re.S).replace(",", "").strip()
    if leftover:
        raise ValueError(f"{name}: unparsed entries {leftover!r}")
    return out


@pytest.mark.parametrize("value", ["5e2", "399.99", "0x10", "5 * 2", "LIMIT"])
def test_number_record_rejects_a_partially_parsed_literal(value: str) -> None:
    text = f"const T: Record<Tier, number> = {{\n  free: 1,\n  plus: {value},\n}};\n"
    with pytest.raises(ValueError, match="plus"):
        _ts_number_record(text, "T")


def test_number_record_rejects_an_entry_it_cannot_read() -> None:
    text = "const T = {\n  free: 1,\n  ...EXTRA,\n};\n"
    with pytest.raises(ValueError, match="unparsed entries"):
        _ts_number_record(text, "T")


def test_number_record_reads_plain_and_underscored_integers() -> None:
    text = "const T = {\n  // note\n  free: 1,\n  pro: 74_900, // trailing\n};\n"
    assert _ts_number_record(text, "T") == {"free": 1, "pro": 74900}


@pytest.mark.parametrize(
    "arr", ["'core_ops', ...EXTRA_CAPS", "'core_ops', EXTRA", "'core_ops', `echo`"]
)
def test_string_array_record_rejects_a_non_literal_element(arr: str) -> None:
    text = f"const C = {{\n  free: [{arr}],\n}};\n"
    with pytest.raises(ValueError, match="unparsed capability expression"):
        _ts_string_array_record(text, "C")


def test_string_array_record_rejects_a_non_array_entry() -> None:
    text = "const C = {\n  free: ['core_ops'],\n  plus: PLUS_CAPS,\n};\n"
    with pytest.raises(ValueError, match="unparsed entries"):
        _ts_string_array_record(text, "C")


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
    # Exact key sets, both directions: a tier billing can mint but the spec does
    # not know is drift too, and the per-tier loop below only walks the spec.
    for label, record in (("TIER_HOST_LIMIT", limits), ("TIER_CAPABILITIES", caps)):
        assert set(record) == set(expected), (
            f"billing.ts {label} tiers drift from tiers.json — "
            f"missing {sorted(set(expected) - set(record))}, "
            f"extra {sorted(set(record) - set(expected))}"
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
# list on the pricing page. Drift here quotes a price we do not charge or
# promises a capability the tier does not grant.
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
    # Pass + Max, annual + monthly. (Was 5 before the Max lifetime was retired.)
    assert compared >= 4, f"only {compared} price points compared"


def test_www_copy_host_limits_match_spec() -> None:
    """A host count SHOWN on the pricing page must be the one the licence grants.

    Enterprise is sold by contract (its number rides in the token as
    `contract_hosts`), so the page shows no fixed number for it — `null` there
    is an asserted contract, not a skip. Every other tier must show exactly its
    spec number.
    """
    tiers = _www()
    assert tiers["enterprise"]["hosts"] is None, (
        "Enterprise hosts are negotiated per contract; the page must not quote a fixed number"
    )
    compared = 0
    for tier, t in _spec()["live"]["tiers"].items():
        if tier == "enterprise":
            continue
        shown = tiers[tier]["hosts"]
        assert shown is not None, f"copy.ts {tier} shows no host count"
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


def test_no_live_tier_is_sold_as_a_lifetime() -> None:
    """Harbor tiers are subscriptions only — in the spec, the billing table and on the page.

    The Max lifetime SKU ($1,080) was retired before launch: it capped revenue
    from the heaviest users while their relay/AI cost kept running. Licences
    already issued `one_time` still verify (keys.py), but nothing may SELL one:
    a lifetime price reappearing in any of these three places would quote or
    charge a product that no longer exists.
    """
    for tier, prices in _spec()["live"]["prices_usd"].items():
        if tier.startswith("_"):
            continue
        assert "lifetime" not in prices, f"tiers.json prices {tier} as a lifetime"

    text = _read("services/api/src/lib/billing.ts")
    if text is None:
        pytest.skip("services/api/src/lib/billing.ts not present in this checkout")
    cents = _ts_number_record(text, "TIER_PERPETUAL_PRICE_CENTS")
    assert cents, "could not parse TIER_PERPETUAL_PRICE_CENTS from billing.ts"
    live_sold = set(cents) & set(_spec()["live"]["tiers"])
    assert not live_sold, f"billing.ts still prices live tiers as perpetual: {sorted(live_sold)}"

    for tier, t in _www().items():
        assert t["lifetime"] is None, f"copy.ts quotes a lifetime price for {tier}"
