"""`navig config set active_host` must record the host it actually replaced.

`navig host use dev` writes only ``cache/active_host.txt`` (``set_active_host``), never
``global_config["active_host"]``. Reading the previous host from global_config made the
ledger entry record ``None`` (not undoable) or a stale legacy value (undo restores the
wrong host).
"""

from __future__ import annotations

from types import SimpleNamespace

import navig.commands.config as config_mod


def _fake_cm(tmp_path, cached: str | None, legacy: str | None):
    cache = tmp_path / "active_host.txt"
    if cached is not None:
        cache.write_text(cached, encoding="utf-8")
    return SimpleNamespace(
        host_exists=lambda _n: True,
        global_config={"active_host": legacy} if legacy else {},
        active_host_file=cache,
        set_active_host=lambda *_a, **_k: None,
        update_global_config=lambda *_a, **_k: None,
    )


def _previous_recorded(monkeypatch, cm) -> list:
    seen: list = []
    monkeypatch.setattr(config_mod, "get_config_manager", lambda: cm)
    monkeypatch.setattr(config_mod, "_record_host_switch", lambda prev, new: seen.append((prev, new)))
    monkeypatch.setattr(config_mod.ch, "success", lambda *a, **k: None)
    config_mod.set_config("active_host", "prod")
    return seen


def test_previous_host_comes_from_the_host_use_cache(monkeypatch, tmp_path):
    cm = _fake_cm(tmp_path, cached="dev", legacy="stale-legacy")
    assert _previous_recorded(monkeypatch, cm) == [("dev", "prod")]


def test_legacy_global_key_is_the_fallback(monkeypatch, tmp_path):
    cm = _fake_cm(tmp_path, cached=None, legacy="old")
    assert _previous_recorded(monkeypatch, cm) == [("old", "prod")]
