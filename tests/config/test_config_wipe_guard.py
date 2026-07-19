"""A failed READ must never become a destructive WRITE.

`_load_global_config()` returns `{}` on ANY load failure — a YAML error, or a
transient read of a half-written file. The process then holds an empty config, and
the very next `_save_global_config()` writes that `{}` straight to disk: config.yaml
is truncated and the pickle cache is poisoned with the empty dict too.

That is how `deck.api_key` vanished twice in one night on the operator's machine.
Because the key IS the Lighthouse tenant (sha256(key) selects the edge Durable
Object), the gateway then minted a NEW identity, and the bot went 100% deaf while
every health light stayed green — the edge happily queues and acks 202 for a tenant
nobody is attached to.

Two invariants are pinned here:
  1. an EMPTY config is never written over a populated file, and
  2. a failed load is loud and RECOVERABLE (last-known-good), not silently empty.
"""

from __future__ import annotations

import pickle

import pytest
import yaml

from navig.config import ConfigManager

POPULATED = {
    "version": 5,
    "deck": {"api_key": "navig_the_installs_identity"},
    "telegram": {"business": {"enabled": True}},
}


def _cfg_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _write_config(cfg_dir, data: dict) -> None:
    (cfg_dir / "config.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")


def _read_config(cfg_dir) -> dict:
    return yaml.safe_load((cfg_dir / "config.yaml").read_text(encoding="utf-8")) or {}


# ── 1. the wipe guard ────────────────────────────────────────────────────────


def test_an_empty_config_is_never_written_over_a_populated_file(monkeypatch, tmp_path):
    """THE bug. A failed load left `{}` in memory; saving it truncated everything."""
    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)

    cm = ConfigManager()
    cm._save_global_config({})  # what a post-load-failure save actually did

    survived = _read_config(d)
    assert survived == POPULATED, "an empty config must NEVER overwrite real settings"
    assert survived["deck"]["api_key"] == "navig_the_installs_identity"


def test_the_pickle_cache_is_not_poisoned_by_the_refused_write(monkeypatch, tmp_path):
    """The save also rewrites the cache. If the wipe were allowed through, the cache
    would carry the empty dict into the next cold boot as 'last known good'."""
    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)

    cm = ConfigManager()
    cm._save_global_config(dict(POPULATED))  # a healthy save seeds the cache
    cm._save_global_config({})  # the wipe attempt — must be refused

    cached = pickle.loads((d / ".config_cache.pkl").read_bytes())
    assert cached["_config"], "the cache must not be poisoned with an empty config"
    assert cached["_config"]["deck"]["api_key"] == "navig_the_installs_identity"


def test_a_real_save_still_works(monkeypatch, tmp_path):
    """The guard must not be so eager that it blocks legitimate writes."""
    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)

    cm = ConfigManager()
    updated = dict(POPULATED) | {"cloud": {"mode": "lighthouse"}}
    cm._save_global_config(updated)

    assert _read_config(d)["cloud"]["mode"] == "lighthouse"


def test_a_first_run_may_still_write_an_empty_config(monkeypatch, tmp_path):
    """No file on disk = a genuine first run. There is nothing to protect, so an empty
    write must be allowed — otherwise bootstrapping a new install would be blocked."""
    d = _cfg_dir(monkeypatch, tmp_path)
    assert not (d / "config.yaml").exists()

    ConfigManager()._save_global_config({})

    assert (d / "config.yaml").exists()


def test_a_comments_only_file_is_not_treated_as_populated(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml").write_text("# just a comment\n\n", encoding="utf-8")

    assert ConfigManager._on_disk_config_is_populated(d / "config.yaml") is False


def test_an_UNPARSEABLE_file_counts_as_populated(monkeypatch, tmp_path):
    """The guard exists BECAUSE the parser failed, so it must not ask the parser
    whether the file matters. A corrupt file with content is exactly what we must
    refuse to overwrite."""
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml").write_text("deck: {api_key: 'unterminated\n\t\tbad: [", encoding="utf-8")

    assert ConfigManager._on_disk_config_is_populated(d / "config.yaml") is True


# ── 2. a failed load must be recoverable, not silently empty ─────────────────


def test_a_corrupt_config_recovers_the_last_known_good(monkeypatch, tmp_path):
    """A transient YAML error must not present as 'the user has no settings'."""
    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)

    cm = ConfigManager()
    cm._save_global_config(dict(POPULATED))  # seeds the cache with a good copy

    (d / "config.yaml").write_text("deck: {api_key: 'unterminated\n  bad: [", encoding="utf-8")

    recovered = ConfigManager()._load_global_config(validate=False)

    assert recovered.get("deck", {}).get("api_key") == "navig_the_installs_identity"


def test_a_corrupt_config_is_preserved_for_diagnosis(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)
    ConfigManager()._save_global_config(dict(POPULATED))

    broken = "deck: {api_key: 'unterminated\n  bad: ["
    (d / "config.yaml").write_text(broken, encoding="utf-8")

    ConfigManager()._load_global_config(validate=False)

    backup = d / "config.yaml.corrupt"
    assert backup.exists(), "the unreadable file must be preserved, not silently discarded"
    assert backup.read_text(encoding="utf-8") == broken
    # and the original is left exactly as it was — we never destroy the evidence
    assert (d / "config.yaml").read_text(encoding="utf-8") == broken


def test_no_cache_to_recover_from_still_leaves_the_file_alone(monkeypatch, tmp_path):
    """Worst case: corrupt YAML and no cache. We run on defaults for this boot — but
    the user's file on disk must survive untouched so they can fix it."""
    d = _cfg_dir(monkeypatch, tmp_path)
    broken = "deck: {api_key: 'unterminated\n  bad: ["
    (d / "config.yaml").write_text(broken, encoding="utf-8")

    cm = ConfigManager()
    loaded = cm._load_global_config(validate=False)
    assert loaded == {}

    # …and the guard still refuses to let that empty config destroy the file.
    cm._save_global_config({})
    assert (d / "config.yaml").read_text(encoding="utf-8") == broken


# ── 3. the DIRECT safe_load_yaml callers must refuse too ─────────────────────
#
# `navig bridge connect` / `rotate-token` do NOT go through ConfigManager — they
# read and write ~/.navig/config.yaml directly via `safe_load_yaml(...) or {}` +
# `atomic_write_yaml`. So none of the ConfigManager guards above protect them: a
# single transient read (safe_load_yaml returns None for "unreadable" exactly as
# it does for "empty") made the next write persist a config holding ONLY the
# gateway keys — wiping deck.api_key and everything else. Same class, other door.


def _import_bridge():
    from navig.commands import bridge

    return bridge


def _simulate_unreadable_config(monkeypatch):
    """Make the yaml_io read primitive fail as a persistent Windows lock would.

    This is the REAL trigger — a sharing violation that survives the retries — and it
    exercises the shared ``load_yaml_for_update`` guard the direct callers now delegate
    to, rather than stubbing a specific module's ``safe_load_yaml`` symbol.
    """

    def _locked(*_a, **_k):
        raise PermissionError("[WinError 32] file in use by another process")

    monkeypatch.setattr("navig.core.yaml_io.read_text_retrying", _locked)


def test_bridge_load_config_for_write_returns_a_populated_dict(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)

    assert _import_bridge()._load_config_for_write(d / "config.yaml") == POPULATED


def test_bridge_load_config_for_write_missing_file_is_a_fresh_start(monkeypatch, tmp_path):
    """No file = a legitimate first `bridge connect`. Nothing to protect → {}."""
    d = _cfg_dir(monkeypatch, tmp_path)

    assert _import_bridge()._load_config_for_write(d / "config.yaml") == {}


def test_bridge_load_config_for_write_refuses_an_unreadable_populated_file(monkeypatch, tmp_path):
    """THE guard: the file is on disk WITH content but the read returned None (a lock
    / half-written swap). Refuse — do not hand back {} for a caller to write."""
    import typer

    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)
    _simulate_unreadable_config(monkeypatch)

    with pytest.raises(typer.Exit):
        _import_bridge()._load_config_for_write(d / "config.yaml")


def test_bridge_connect_refuses_to_wipe_an_unreadable_config(monkeypatch, tmp_path):
    """THE bug, end to end. A transient read during `bridge connect` must NOT collapse
    the config down to just the gateway keys — deck.api_key must survive untouched."""
    import typer

    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)
    _simulate_unreadable_config(monkeypatch)

    with pytest.raises(typer.Exit):
        _import_bridge().bridge_connect(
            port=8789, bind="127.0.0.1", generate_token=True, json_output=True
        )

    survived = _read_config(d)
    assert survived == POPULATED, "an unreadable config must never be overwritten"
    assert survived["deck"]["api_key"] == "navig_the_installs_identity"


def test_bridge_rotate_token_refuses_to_wipe_an_unreadable_config(monkeypatch, tmp_path):
    import typer

    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)
    _simulate_unreadable_config(monkeypatch)

    with pytest.raises(typer.Exit):
        _import_bridge().bridge_rotate_token(json_output=True)

    assert _read_config(d) == POPULATED


def test_bridge_connect_preserves_deck_key_on_a_healthy_config(monkeypatch, tmp_path):
    """The guard must not block the legitimate write: connect updates the gateway keys
    while leaving deck.api_key (and the rest of the config) intact."""
    d = _cfg_dir(monkeypatch, tmp_path)
    _write_config(d, POPULATED)

    _import_bridge().bridge_connect(
        port=8789, bind="127.0.0.1", generate_token=True, json_output=True
    )

    survived = _read_config(d)
    assert survived["deck"]["api_key"] == "navig_the_installs_identity"  # preserved
    assert survived["telegram"]["business"]["enabled"] is True  # preserved
    assert survived["gateway"]["port"] == 8789  # updated
    assert survived["gateway"]["enabled"] is True
