"""`navig install <asset>` → the loader MUST scan where it wrote, or content silently vanishes.

Every store_dir()-installed asset type writes under ``store_dir()/<type>``
(``commands/install.py:_dest_for``). If that type's discovery loader does not include
``store_dir()/<type>`` in its read-roots, an installed asset is written to disk and then
**silently never loads** — no error, it just isn't there.

That exact mismatch shipped for **formations** (installed formations never loaded, #373) and
is a recurring trap ("new installable type/loader → read-roots MUST include store_dir()/<type>").
This test locks the install-destination ↔ loader-root round-trip for every store_dir() type so
the class can't regress for any of them.
"""

from __future__ import annotations

import pytest

from navig.commands.install import _dest_for


def _formation_roots() -> list:
    from navig.formations import loader

    loader._FORMATIONS_ROOTS = []  # drop any roots a prior test cached into the module global
    return loader._get_formations_roots()


def _prompt_roots() -> list:
    from navig.prompts.registry import get_prompt_dirs

    return [d for d, _scope in get_prompt_dirs()]


def _skill_roots() -> list:
    from navig.skills.loader import get_skill_dirs

    return get_skill_dirs()


def _block_roots() -> list:
    from navig.blocks.loader import get_block_dirs

    return get_block_dirs()


# asset_type (as `navig install` classifies it) -> its discovery-root function
_CASES = [
    ("formation", _formation_roots),
    ("prompt", _prompt_roots),
    ("skill", _skill_roots),
    ("block", _block_roots),
]


@pytest.mark.parametrize("asset_type, roots_fn", _CASES, ids=[c[0] for c in _CASES])
def test_loader_scans_the_install_destination(asset_type, roots_fn, tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))

    # The directory `navig install <asset_type>` actually writes into.
    install_dir = _dest_for(asset_type, "roundtrip-probe").parent
    install_dir.mkdir(parents=True, exist_ok=True)  # loaders gate roots on existence

    roots = {p.resolve() for p in roots_fn()}
    assert install_dir.resolve() in roots, (
        f"the {asset_type} loader does not scan its install destination {install_dir} — a "
        f"`navig install`ed {asset_type} would silently never load (the #373 class). Add "
        f"store_dir()/{install_dir.name} to its read-roots. Scanned: {sorted(map(str, roots))}"
    )


def test_these_types_still_install_under_the_user_store(monkeypatch, tmp_path):
    """The other half of the round-trip: these types must install UNDER store_dir() — which is
    the whole reason each loader has to scan there. If an install dest moves to config_dir()
    without the loader following, the pair silently breaks."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    from navig.platform.paths import store_dir

    store = store_dir().resolve()
    for asset_type, _ in _CASES:
        dest = _dest_for(asset_type, "x").resolve()
        assert store in dest.parents, f"{asset_type} no longer installs under store_dir(): {dest}"
