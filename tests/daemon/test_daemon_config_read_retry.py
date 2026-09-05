"""The daemon config READ must ride out a transient OS lock, not collapse to defaults.

`_load_config` used a raw `read_text()`, so a transient lock at daemon boot (an AV/backup
agent, a read landing mid-`os.replace`) fell into `except OSError → DEFAULT_DAEMON_CONFIG`
— running that session with the DEFAULT feature toggles (gateway/scheduler/ports) instead
of the operator's config. It now reads through `read_text_retrying` (which retries transient
locks and only raises when they persist), degrading to defaults only on a genuine, lasting
failure. Companion to `save_default_config`'s write-repair guard (already lock-safe).
"""

from __future__ import annotations

import json

import navig.daemon.entry as entry


def test_load_config_reads_through_the_retrying_reader(monkeypatch, tmp_path):
    cfg_path = tmp_path / "daemon.json"
    cfg_path.write_text(
        json.dumps({"gateway": {"enabled": False}, "custom": 1}), encoding="utf-8"
    )
    monkeypatch.setattr(entry, "_daemon_config_path", lambda: cfg_path)

    calls: list = []
    real = entry.read_text_retrying

    def _spy(p, **kw):
        calls.append(p)
        return real(p, **kw)

    monkeypatch.setattr(entry, "read_text_retrying", _spy)

    got = entry._load_config()
    assert got == {"gateway": {"enabled": False}, "custom": 1}  # the real config, not defaults
    assert calls, "read_text_retrying was not used — a transient lock would collapse to defaults"


def test_load_config_degrades_to_defaults_only_on_a_persistent_read_failure(monkeypatch, tmp_path):
    cfg_path = tmp_path / "daemon.json"
    cfg_path.write_text(json.dumps({"custom": 1}), encoding="utf-8")
    monkeypatch.setattr(entry, "_daemon_config_path", lambda: cfg_path)

    def _boom(*_a, **_k):
        raise OSError("sharing violation that outlived the retries")

    monkeypatch.setattr(entry, "read_text_retrying", _boom)
    assert entry._load_config() == entry.DEFAULT_DAEMON_CONFIG  # graceful degrade, never a crash


def test_save_default_config_does_not_repair_over_a_transiently_locked_file(monkeypatch, tmp_path):
    """A transient lock during the repair check must NOT overwrite the config with defaults."""
    cfg_path = tmp_path / "daemon.json"
    cfg_path.write_text(json.dumps({"custom": "keep me"}), encoding="utf-8")
    on_disk = cfg_path.read_bytes()
    monkeypatch.setattr(entry, "_daemon_config_path", lambda: cfg_path)

    def _boom(*_a, **_k):
        raise OSError("sharing violation")

    monkeypatch.setattr(entry, "read_text_retrying", _boom)
    entry.save_default_config()

    assert cfg_path.read_bytes() == on_disk  # config left intact — not repaired to defaults
