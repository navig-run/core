"""The first-party registry repo was renamed (navig-run/community → navig-run/registry).

Three readers decide something from that name, and each must treat the old name as OUR
repository: the install parser (an old command still installs), the Block trust check
(an old spec is still first-party, not refused) and the Bay paywall (an old spec is still
priced, never installed ungated).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from navig import registry_repo as rr

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("spec", "expected"),
    [
        ("github:navig-run/community/blocks/x", "github:navig-run/registry/blocks/x"),
        ("block:navig-run/community/blocks/x@v2", "block:navig-run/registry/blocks/x@v2"),
        ("https://raw.githubusercontent.com/navig-run/community/main/a.json",
         "https://raw.githubusercontent.com/navig-run/registry/main/a.json"),
        ("github:navig-run/community", "github:navig-run/registry"),
        ("github:navig-run/registry/x", "github:navig-run/registry/x"),       # already current
        ("navig-run/community-extras/y", "navig-run/community-extras/y"),   # another repository
        ("github:someone/community/x", "github:someone/community/x"),       # another owner
    ],
)
def test_normalize_spec_rewrites_only_our_old_name(spec: str, expected: str) -> None:
    assert rr.normalize_spec(spec) == expected


def test_first_party_is_our_repo_under_either_name_and_nothing_else() -> None:
    assert rr.is_first_party("navig-run", "registry")
    assert rr.is_first_party("navig-run", "community")
    assert rr.is_first_party("Navig-Run", "Registry")  # GitHub names are case-insensitive
    assert not rr.is_first_party("navig-run", "community-extras")
    assert not rr.is_first_party("someone", "registry")
    assert not rr.is_first_party(None, "registry")


def test_an_old_install_command_installs_from_the_new_repo() -> None:
    from navig.commands.install import _parse_spec

    parsed = _parse_spec("github:navig-run/community/spaces/dev-space")
    assert (parsed["owner"], parsed["repo"], parsed["type"], parsed["id"]) == (
        "navig-run", "registry", "space", "dev-space")
    other = _parse_spec("github:someone/community/spaces/x")
    assert other["repo"] == "community"  # someone else's repository keeps its name


def test_an_old_block_spec_is_still_first_party() -> None:
    from navig.blocks import policy

    assert policy.check_install_trust("navig-run", "community", dev_untrusted=False) == "first-party"
    assert policy.check_install_trust("navig-run", "registry", dev_untrusted=False) == "first-party"
    with pytest.raises(policy.TrustError, match="github:navig-run/registry/blocks"):
        policy.check_install_trust("someone", "registry", dev_untrusted=False)


def test_the_paywall_prices_an_old_spec_exactly_like_a_new_one(monkeypatch, tmp_path) -> None:
    """A spec naming the old repo is OUR registry: it must reach the price check, never pass."""
    import navig.gateway.deck.routes.catalog as cat

    root = tmp_path / "registry"
    (root / "spaces").mkdir(parents=True)
    (root / "spaces" / "registry.json").write_text(json.dumps({"spaces": [
        {"id": "paid-thing", "marketplace": {"price": 900, "capability": "item:paid-thing"}},
    ]}), encoding="utf-8")
    monkeypatch.setattr(cat, "_community_root", lambda: root)
    new = cat._find_market_pricing("github:navig-run/registry/spaces/paid-thing")
    old = cat._find_market_pricing("github:navig-run/community/spaces/paid-thing")
    assert new is not None and new[0] == "paid-thing"
    assert old == new, "an old-name spec skipped the price check — it would install ungated"
    assert cat._find_market_pricing("github:someone/registry/spaces/paid-thing") is None


def test_only_this_module_still_spells_the_old_address() -> None:
    """New code builds the address from navig.registry_repo; the old one appears nowhere else."""
    out = subprocess.run(
        ["git", "grep", "-l", "navig-run/community", "--", "core/navig", "plugins", "services", "apps",
         "web/www", "registry", "packages"],
        cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8", check=False,
    )
    files = sorted(set(out.stdout.split()) - {"core/navig/registry_repo.py"})
    assert out.returncode in (0, 1), out.stderr
    assert files == [], f"the old registry address is still spelled here: {files}"
