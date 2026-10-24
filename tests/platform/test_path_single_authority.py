"""One authority per config/state file — the resolver must return the WRITER's path.

A resolver that prefers a file nothing writes is a split brain waiting for a creator: the
moment a second layout appears (a migration, a script, a doc-follower), ``navig paths`` /
doctor / Telegram report one file while the runtime keeps reading another. That is the
#192 class this suite is built around (see ``test_debug_log_single_source.py``).

The contract encoded here, for every resolver below:

  * the AUTHORITY file is the one the runtime writer creates — it wins whenever it exists,
    including when the other layout exists too;
  * the alternative layout is a READ FALLBACK only, used when the authority is absent;
  * with neither file, the resolver answers the authority path, so the first write and
    every reader agree by construction.

The companion test at the bottom pins the WRITERS themselves (ConfigManager,
ConfigSingleton, the onboarding engine, the genesis loader, the sigil store) to the path
the resolver returns — otherwise this file would only prove that two resolvers agree with
each other, not that code writes where they point.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.platform import paths

#: (resolver, authority relative to config_dir, alternative layout relative to config_dir)
LAYOUTS: list[tuple[object, str, str]] = [
    (paths.global_config_path, "config.yaml", "config/config.yaml"),
    (paths.genesis_json_path, "genesis.json", "state/genesis.json"),
    (paths.onboarding_json_path, "onboarding.json", "state/onboarding.json"),
]


@pytest.fixture
def cfg(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """An isolated brain whose ONLY isolation is NAVIG_CONFIG_DIR (as a real second brain)."""
    for var in (
        "NAVIG_DATA_DIR",
        "NAVIG_LOG_DIR",
        "NAVIG_CACHE_DIR",
        "NAVIG_STORE_DIR",
        "NAVIG_PACKAGES_DIR",
        "NAVIG_STACK_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    brain = tmp_path / "brain" / ".navig"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(brain))
    brain.mkdir(parents=True, exist_ok=True)
    return brain


def _write(path: Path, text: str = "x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.mark.parametrize(("resolver", "authority", "fallback"), LAYOUTS)
def test_no_file_yet_resolves_to_the_writers_path(
    cfg: Path, resolver, authority: str, fallback: str
) -> None:
    """Nothing on disk → the WRITER's path, so the first write creates what readers use."""
    assert resolver() == cfg / authority


@pytest.mark.parametrize(("resolver", "authority", "fallback"), LAYOUTS)
def test_alternative_layout_is_only_a_read_fallback(
    cfg: Path, resolver, authority: str, fallback: str
) -> None:
    """The alternative layout is honoured ONLY when the authority file is absent."""
    _write(cfg / fallback)
    assert resolver() == cfg / fallback

    _write(cfg / authority)
    assert resolver() == cfg / authority, (
        f"{resolver.__name__} preferred {fallback} over the file the runtime writes "
        f"({authority}) — two answers to one question"
    )


def test_entity_authority_is_state_with_root_as_legacy_fallback(cfg: Path) -> None:
    """entity.json is the one file whose WRITER already uses state/ — state wins both-ways."""
    assert paths.entity_json_path() == cfg / "state" / "entity.json"

    _write(cfg / "entity.json")
    assert paths.entity_json_path() == cfg / "entity.json"  # legacy-only install

    _write(cfg / "state" / "entity.json")
    assert paths.entity_json_path() == cfg / "state" / "entity.json"


def test_config_manager_writes_the_resolver_path(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """ConfigManager — the config WRITER — resolves the same file the resolver reports."""
    from navig.config import ConfigManager

    manager = ConfigManager(config_dir=cfg)
    assert manager.global_config_path == paths.global_config_path()


def test_config_writer_follows_the_resolver_in_a_fallback_only_layout(cfg: Path) -> None:
    """B4: a layout with ONLY ``config/config.yaml`` must not split reader and writer.

    Before this, the resolver returned the fallback while ConfigManager still wrote
    ``<cfg>/config.yaml`` — so a read showed the fallback file and a save created a second
    file at the root (two answers again). The config pipeline now uses the resolver, so the
    writer materialises the very file that was read.
    """
    fallback = cfg / "config" / "config.yaml"
    _write(fallback, "gateway:\n  port: 8789\n")

    resolver = paths.global_config_path()
    assert resolver == fallback

    from navig.config import ConfigManager
    from navig.core.shared_config import ConfigSingleton

    manager = ConfigManager(config_dir=cfg)
    assert manager.global_config_path == resolver
    assert ConfigSingleton().global_config_path == resolver

    manager.set_global("retention.sessions_days", 45)

    assert resolver.is_file(), "the save must land in the file the resolver reports"
    assert "retention" in resolver.read_text(encoding="utf-8")
    assert not (cfg / "config.yaml").exists(), "a fallback-only layout must not grow a second file"


def test_shared_config_reports_the_resolver_path(cfg: Path) -> None:
    """ConfigSingleton (the global config surface) agrees with the resolver."""
    from navig.core.shared_config import ConfigSingleton

    assert ConfigSingleton().global_config_path == paths.global_config_path()


def test_genesis_only_state_layout_is_a_documented_read_view(cfg: Path) -> None:
    """genesis.json's writer is root-only; a state/-only layout is a READ view until then.

    Pinned deliberately: without a writer change (out of this branch's scope) the resolver
    honours a future-layout file for READS, while the genesis writer materialises the root
    authority on first run — after which root wins on every read (see the both-layout case
    in ``test_alternative_layout_is_only_a_read_fallback``).
    """
    state_only = cfg / "state" / "genesis.json"
    _write(state_only, "{}")

    assert paths.genesis_json_path() == state_only
    assert not (cfg / "genesis.json").exists()


def test_genesis_writer_creates_the_resolver_path(cfg: Path) -> None:
    """The genesis loader is the writer; it must create the file the resolver names."""
    from navig.onboarding import genesis

    created = genesis.load_or_create(cfg, name="path-contract-test")
    assert created is not None
    assert (cfg / genesis.GENESIS_FILENAME).is_file()
    assert paths.genesis_json_path() == cfg / genesis.GENESIS_FILENAME


def test_onboarding_writer_uses_the_resolver_filename(cfg: Path) -> None:
    """The onboarding engine writes ``navig_dir / ARTIFACT_FILENAME`` — the authority."""
    from navig.onboarding import engine

    assert paths.onboarding_json_path() == cfg / engine.ARTIFACT_FILENAME


def test_entity_writer_uses_the_resolver(cfg: Path) -> None:
    """sigil_store resolves ``paths.entity_json_path()`` — one source for the identity file."""
    from navig.identity import sigil_store

    assert sigil_store._identity_path() == paths.entity_json_path()


def test_trash_dir_is_per_brain_and_overridable(cfg: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The recycle bin lives under the brain's config dir, and NAVIG_TRASH_DIR can move it."""
    assert paths.trash_dir() == cfg / ".trash"

    custom = cfg.parent / "custom-trash"
    monkeypatch.setenv("NAVIG_TRASH_DIR", str(custom))
    assert paths.trash_dir() == custom
