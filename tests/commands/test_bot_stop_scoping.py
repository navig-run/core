"""`navig bot stop` / `bot status` must be scoped to OUR config dir.

Both commands used a hand-rolled machine-wide sweep (`Get-CimInstance` / `pgrep -f`
matching `navig gateway start` / `navig.daemon.*` / `telegram_worker`). `bot stop`
then `taskkill /T /F` / `pkill -f`'d EVERY match — so a `navig bot stop` run under a
different `NAVIG_CONFIG_DIR` force-killed the operator's live brain (the documented
catastrophe). They now route through the config-dir-scoped `single_instance` machinery.
"""

from __future__ import annotations

from pathlib import Path


def _silence(monkeypatch, sink: list | None = None):
    """Redirect console_helper output so tests don't spew; optionally capture it."""
    import navig.console_helper as ch_mod

    for name in ("success", "info", "warning", "error"):
        monkeypatch.setattr(
            ch_mod, name, (lambda m: sink.append(m)) if sink is not None else (lambda m: None)
        )


def test_bot_stop_routes_through_scoped_killer(monkeypatch):
    import navig.commands.gateway as gw
    import navig.daemon.single_instance as si
    from navig.platform import paths

    seen: dict = {}

    def fake_kill(patterns, **kw):
        seen["patterns"] = patterns
        seen["config_dir"] = kw.get("config_dir")
        return [111, 222]

    monkeypatch.setattr(si, "kill_other_instances", fake_kill)
    msgs: list = []
    _silence(monkeypatch, msgs)

    gw.bot_stop()

    # THE REGRESSION: a stop that doesn't pass config_dir swept machine-wide and could
    # kill the operator's live brain on a different config dir.
    assert seen.get("config_dir") is not None, "bot stop swept machine-wide (unscoped)"
    assert Path(seen["config_dir"]) == paths.config_dir()
    # Both daemon (entry + telegram_worker) and gateway roles are targeted.
    assert any("gateway" in p for p in seen["patterns"])
    assert any("daemon" in p for p in seen["patterns"])
    assert any("111" in m and "222" in m for m in msgs)


def test_bot_status_reports_only_our_brain(monkeypatch):
    import navig.commands.gateway as gw
    import navig.daemon.single_instance as si
    from navig.platform import paths

    mine = paths.config_dir().resolve()
    theirs = Path("/tmp/other-brain").resolve()

    monkeypatch.setattr(
        si,
        "process_table",
        lambda: [
            (101, "python -m navig.daemon.entry"),   # matches a pattern, OUR config dir
            (202, "python -m navig gateway start"),  # matches a pattern, a DIFFERENT brain
            (303, "python some_other_app.py"),       # no pattern match at all
        ],
    )
    monkeypatch.setattr(
        si, "config_dir_of", lambda pid: {101: mine, 202: theirs, 303: mine}.get(pid)
    )
    out: list = []
    _silence(monkeypatch, out)

    gw.bot_status()

    joined = " ".join(out)
    assert "101" in joined, "our own daemon must be reported as running"
    assert "202" not in joined, "a daemon on a DIFFERENT config dir must not be reported as ours"
    assert "303" not in joined
