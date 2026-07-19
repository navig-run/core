"""A daemon that heals itself and tells nobody looks exactly like a daemon that is fine.

The config/identity layer now survives things that used to be catastrophic: a refused
config wipe, a load that falls back to the last known-good cache, a deck.api_key restored
from the vault mirror. Each recovery is correct — and each is also a symptom that will
recur. Left as log lines in a file nobody reads, they are invisible, which is precisely
how the original outage went unnoticed: the bot was 100% deaf while every light was green.

So each rescue leaves a durable trace, and `navig doctor` reports it under Config Health.
"""

from __future__ import annotations

import yaml

from navig.core import incidents

POPULATED = {"version": 5, "deck": {"api_key": "navig_the_installs_identity"}}


def _cfg_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    return tmp_path


def _rows(results) -> str:
    return " | ".join(text for _icon, _ok, text in results)


def _all_ok(results) -> bool:
    return all(ok for _icon, ok, _text in results)


# ── the rescues must leave a trace ───────────────────────────────────────────


def test_a_refused_wipe_is_recorded(monkeypatch, tmp_path):
    """The #168 guard fires silently today. A refused wipe means a load failed upstream —
    survivable, but never routine."""
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml").write_text(yaml.safe_dump(POPULATED), encoding="utf-8")

    from navig.config import ConfigManager

    ConfigManager()._save_global_config({})  # the wipe attempt

    events = [e["event"] for e in incidents.recent(limit=10)]
    assert incidents.WIPE_REFUSED in events


def test_a_failed_load_and_its_recovery_are_recorded(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml").write_text(yaml.safe_dump(POPULATED), encoding="utf-8")

    from navig.config import ConfigManager

    ConfigManager()._save_global_config(dict(POPULATED))  # seed the known-good cache
    (d / "config.yaml").write_text("deck: {api_key: 'unterminated\n  bad: [", encoding="utf-8")

    ConfigManager()._load_global_config(validate=False)

    events = [e["event"] for e in incidents.recent(limit=10)]
    assert incidents.LOAD_FAILED in events
    assert incidents.RECOVERED_FROM_CACHE in events, "a silent rescue is the thing we are fixing"


def test_recording_never_breaks_the_path_it_observes(monkeypatch, tmp_path):
    """A health note is not worth an outage. If the incident log cannot be written, the
    config layer must carry on regardless."""
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml").write_text(yaml.safe_dump(POPULATED), encoding="utf-8")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr("navig.core.yaml_io.log_shadow_anomaly", boom)

    from navig.config import ConfigManager

    ConfigManager()._save_global_config({})  # must still refuse the wipe, not explode

    assert yaml.safe_load((d / "config.yaml").read_text(encoding="utf-8")) == POPULATED


# ── doctor makes them legible ────────────────────────────────────────────────


def test_doctor_is_quiet_on_a_healthy_install(monkeypatch, tmp_path):
    """The empty state matters: a row that is always shouting is a row people learn to
    ignore. No incidents → one green line."""
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml").write_text(yaml.safe_dump(POPULATED), encoding="utf-8")
    monkeypatch.setattr("navig.cloud.deck_key.is_mirrored", lambda: True)

    from navig.commands.doctor import check_config_health

    results = check_config_health()
    rows = _rows(results)

    assert _all_ok(results), "a healthy install must not raise a flag"
    assert "none" in rows.lower(), "it should say plainly that no rescue was needed"
    # …and the key row must actually have RUN — a config without a key skips it, so a
    # test that omits the key would pass even if this row were broken.
    assert "mirrored" in rows, "the armed safety net must be reported, not silently skipped"


def test_doctor_surfaces_a_recent_rescue(monkeypatch, tmp_path):
    _cfg_dir(monkeypatch, tmp_path)
    incidents.record(incidents.WIPE_REFUSED, path="config.yaml")

    from navig.commands.doctor import check_config_health

    results = check_config_health()

    assert not _all_ok(results), "a rescue must be visible, not merely logged"
    assert "EMPTY config" in _rows(results)


def test_doctor_surfaces_a_preserved_corrupt_file(monkeypatch, tmp_path):
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml.corrupt").write_text("broken", encoding="utf-8")

    from navig.commands.doctor import check_config_health

    results = check_config_health()

    assert not _all_ok(results)
    assert "config.yaml.corrupt" in _rows(results)


def test_doctor_warns_when_the_key_is_not_mirrored(monkeypatch, tmp_path):
    """An unarmed safety net is worth knowing about BEFORE the wipe, not after."""
    d = _cfg_dir(monkeypatch, tmp_path)
    (d / "config.yaml").write_text(yaml.safe_dump(POPULATED), encoding="utf-8")
    monkeypatch.setattr("navig.cloud.deck_key.is_mirrored", lambda: False)

    from navig.commands.doctor import check_config_health

    results = check_config_health()

    assert "NOT mirrored" in _rows(results)


def test_old_incidents_do_not_haunt_the_report(monkeypatch, tmp_path):
    """A wipe refused two months ago is history, not an open problem — and a row that
    never goes green again is a row nobody reads."""
    import json
    import time

    _cfg_dir(monkeypatch, tmp_path)
    incidents.record(incidents.WIPE_REFUSED, path="config.yaml")

    log = tmp_path / "perf" / f"{incidents.LOG_NAME}.jsonl"
    entry = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    entry["ts"] = time.time() - 90 * 86400  # backdate it 90 days
    log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    assert incidents.recent(limit=5) == [], "stale incidents must age out of the report"


def test_a_torn_last_line_never_breaks_the_health_check(monkeypatch, tmp_path):
    """The log is append-only and may be cut mid-write by a crash. A half-written line
    must be skipped, not turned into an exception inside `navig doctor`."""
    _cfg_dir(monkeypatch, tmp_path)
    incidents.record(incidents.WIPE_REFUSED, path="config.yaml")

    log = tmp_path / "perf" / f"{incidents.LOG_NAME}.jsonl"
    with log.open("a", encoding="utf-8") as fh:
        fh.write('{"ts": 123, "event": "trunc')  # a torn write

    assert len(incidents.recent(limit=5)) == 1, "the intact entry must still be read"
