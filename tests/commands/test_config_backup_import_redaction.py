"""Importing a redacted export must never overwrite live secrets with the placeholder.

An export made without --include-secrets replaces secrets with a redaction marker
("***REDACTED***" in an archive, "[REDACTED]" in JSON). `navig backup import <export>
--replace` used to save that config directly, writing the marker straight over the live
host/app credentials (secret loss). Now redacted values are restored from the live config
(or dropped for a fresh host) before saving.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from navig.commands import config_backup as cb

# --- _restore_redacted_secrets (pure) -----------------------------------------

def test_marker_restored_from_live_value():
    out = cb._restore_redacted_secrets(
        {"host": "1.2.3.4", "password": "[REDACTED]"},
        {"host": "1.2.3.4", "password": "real-secret"},
    )
    assert out == {"host": "1.2.3.4", "password": "real-secret"}


def test_archive_marker_also_restored():
    out = cb._restore_redacted_secrets({"api_key": "***REDACTED***"}, {"api_key": "sk-live"})
    assert out == {"api_key": "sk-live"}


def test_marker_dropped_when_no_live_value():
    # Fresh host / no live secret: never write the placeholder as a value.
    out = cb._restore_redacted_secrets({"token": "[REDACTED]", "host": "h"}, None)
    assert out == {"host": "h"}


def test_nested_secret_restored():
    out = cb._restore_redacted_secrets(
        {"db": {"password": "[REDACTED]", "name": "app"}},
        {"db": {"password": "real", "name": "app"}},
    )
    assert out == {"db": {"password": "real", "name": "app"}}


def test_non_secret_values_pass_through():
    src = {"host": "1.2.3.4", "port": 22, "enabled": True}
    assert cb._restore_redacted_secrets(src, None) == src


def test_marker_over_marker_is_dropped_not_kept():
    # If the live value is ALSO a marker, don't persist it — drop it.
    out = cb._restore_redacted_secrets({"password": "[REDACTED]"}, {"password": "***REDACTED***"})
    assert out == {}


# --- import_config --replace (integration) ------------------------------------

def test_import_replace_preserves_live_secret(tmp_path, monkeypatch):
    export = tmp_path / "export.json"
    export.write_text(json.dumps({
        "hosts": {"h1": {"host": "1.2.3.4", "password": "[REDACTED]"}},
        "apps": {},
    }))

    saved: dict = {}
    cm = MagicMock()
    cm.host_exists.return_value = True
    cm.load_host_config.return_value = {"host": "1.2.3.4", "password": "real-secret"}
    cm.save_host_config.side_effect = lambda name, cfg: saved.update({name: cfg})

    monkeypatch.setattr("navig.config.get_config_manager", lambda: cm)
    monkeypatch.setattr(cb, "_require_input_file", lambda opts: export)
    monkeypatch.setattr("navig.console_helper.confirm_operation", lambda **k: True)

    cb.import_config({"file": str(export), "merge": False, "yes": True})

    assert saved.get("h1", {}).get("password") == "real-secret"  # live secret preserved
    assert saved["h1"]["host"] == "1.2.3.4"
