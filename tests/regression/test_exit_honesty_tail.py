"""A command that reports a failure must not exit 0 — the tail of the #358 sweep.

`tests/quality/test_command_exit_honesty.py` is the ratchet: it pins the SHAPE
(`ch.error(...)` then a bare return) across every swept module. These are the
behaviours behind the shapes swept here — the exit codes a script actually sees.

Each of these ran green before the fix:

    navig store status && ./deploy.sh          # over a BROKEN install
    navig proactive test && ./enable-it.sh     # over "Calendar test failed"
    navig webhook test <id> && ./announce.sh   # over an undelivered event
    navig quick remove typo && ./ok.sh         # over an action that did not exist
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer

# ── navig store status — broken installs are a fault, not a status line ─────────

def _store_item(state: str, kind: str = "plugin", item_id: str = "plugin:x"):
    return SimpleNamespace(
        id=item_id, kind=kind, state=state, label=item_id, description="",
        version="1", provider=None, system=False, standalone=False, locked=False,
        degraded=False, detail={},
    )


@pytest.mark.parametrize("json_output", [False, True])
def test_store_status_exits_one_when_something_is_broken(monkeypatch, json_output):
    """Both surfaces carry the verdict — `--json` is the one scripts read."""
    import navig.hub as hub
    from navig.commands.store import _store_status

    items = [_store_item("broken"), _store_item("wired", item_id="plugin:y")]
    monkeypatch.setattr(hub, "collect_store", lambda **kw: items)

    with pytest.raises(typer.Exit) as excinfo:
        _store_status(json_output=json_output)
    assert excinfo.value.exit_code == 1


@pytest.mark.parametrize("json_output", [False, True])
def test_store_status_exits_zero_when_nothing_is_broken(monkeypatch, json_output):
    """"0 broken" IS the answer — a healthy store must stay scriptable at exit 0."""
    import navig.hub as hub
    from navig.commands.store import _store_status

    monkeypatch.setattr(hub, "collect_store", lambda **kw: [_store_item("wired")])
    _store_status(json_output=json_output)   # no raise


# ── navig proactive test — a test command that cannot fail a script is useless ──

def test_proactive_test_exits_one_when_a_source_fails(monkeypatch, capsys):
    import navig.agent.proactive as proactive_mod
    from navig.commands.proactive import proactive_test

    class _Boom:
        async def list_events(self, *a, **kw):
            raise RuntimeError("calendar unreachable")

        async def list_unread(self, *a, **kw):
            raise RuntimeError("imap refused")

    monkeypatch.setattr(proactive_mod, "MockCalendar", _Boom)
    monkeypatch.setattr(proactive_mod, "MockEmail", _Boom)

    with pytest.raises(typer.Exit) as excinfo:
        proactive_test("all")
    assert excinfo.value.exit_code == 1
    out = capsys.readouterr().out
    # Both sources are still tried — one broken source must not hide the other's result.
    assert "Calendar test failed" in out
    assert "Email test failed" in out


def test_proactive_test_exits_zero_when_every_source_passes(monkeypatch):
    import navig.agent.proactive as proactive_mod
    from navig.commands.proactive import proactive_test

    class _Ok:
        async def list_events(self, *a, **kw):
            return []

        async def list_unread(self, *a, **kw):
            return []

    monkeypatch.setattr(proactive_mod, "MockCalendar", _Ok)
    monkeypatch.setattr(proactive_mod, "MockEmail", _Ok)
    proactive_test("all")   # no raise


# ── navig webhook test — "Test failed" then exit 0 ─────────────────────────────

def test_webhook_test_exits_one_on_an_undelivered_event(monkeypatch):
    import navig.commands.webhook as webhook_mod

    monkeypatch.setattr(
        webhook_mod, "_api", lambda *a, **kw: {"ok": False, "error": "connection refused"}
    )
    with pytest.raises(typer.Exit) as excinfo:
        webhook_mod.webhook_test("wh_1")
    assert excinfo.value.exit_code == 1


def test_webhook_test_exits_zero_on_delivery(monkeypatch):
    import navig.commands.webhook as webhook_mod

    monkeypatch.setattr(webhook_mod, "_api", lambda *a, **kw: {"ok": True})
    webhook_mod.webhook_test("wh_1")   # no raise


# ── navig quick remove — a typo'd name reported success ────────────────────────

def test_quick_remove_unknown_action_exits_two(monkeypatch, tmp_path):
    """The usage class: nothing was removed because nothing matched."""
    import navig.config as config_mod
    from navig.commands.suggest import quick_remove

    (tmp_path / "quick_actions.yaml").write_text("deploy: ./deploy.sh\n", encoding="utf-8")
    monkeypatch.setattr(
        config_mod, "get_config_manager",
        lambda: SimpleNamespace(global_config_dir=str(tmp_path)),
    )
    with pytest.raises(typer.Exit) as excinfo:
        quick_remove("deployy")
    assert excinfo.value.exit_code == 2
    # The refusal must not have touched the file.
    assert "deploy: ./deploy.sh" in (tmp_path / "quick_actions.yaml").read_text(encoding="utf-8")


def test_quick_remove_unreadable_file_exits_one(monkeypatch, tmp_path):
    """A REFUSAL to modify an unreadable file is a failure, not a no-op."""
    import navig.config as config_mod
    import navig.core.yaml_io as yaml_io
    from navig.commands.suggest import quick_remove

    monkeypatch.setattr(
        config_mod, "get_config_manager",
        lambda: SimpleNamespace(global_config_dir=str(tmp_path)),
    )

    def _unreadable(_path):
        raise yaml_io.ConfigReadError("locked by another process")

    monkeypatch.setattr(yaml_io, "load_yaml_for_update", _unreadable)
    with pytest.raises(typer.Exit) as excinfo:
        quick_remove("deploy")
    assert excinfo.value.exit_code == 1


# ── navig do — the helper that announced a failure and left the exit to a caller ─

def test_no_profile_helper_ends_the_command(monkeypatch):
    """`_exit_no_profile` raises rather than returning — that IS its contract now."""
    from navig.commands.do import _exit_no_profile

    profiles_mod = SimpleNamespace(list_profiles=lambda: [])
    with pytest.raises(typer.Exit) as excinfo:
        _exit_no_profile(profiles_mod)
    assert excinfo.value.exit_code == 1


# ── navig cortex — every way out of the agent loop except `done` is a failure ───

class _FakeDriver:
    def __init__(self):
        self.stopped = False

    async def start(self):
        return None

    async def stop(self):
        self.stopped = True

    async def navigate(self, url):
        return None

    async def wait_for_stable(self, timeout_ms=0):
        return None

    async def get_url(self):
        return "https://example.test/"


def _cortex_env(monkeypatch, decisions):
    """Drive run_cortex's AI loop with a scripted list of orchestrator decisions."""
    import navig.commands.cortex as cortex_mod

    driver = _FakeDriver()
    monkeypatch.setattr(cortex_mod, "get_browser", lambda **kw: driver)

    class _Orchestrator:
        def __init__(self, goal, driver):
            self._queue = list(decisions)
            self._ref_map = {}

        async def decide_next_action(self, **kw):
            return self._queue.pop(0) if self._queue else None

    monkeypatch.setattr(cortex_mod, "CortexOrchestrator", _Orchestrator)
    return cortex_mod, driver


def test_cortex_exits_one_when_the_goal_is_not_reached(monkeypatch):
    """A `fail` verdict printed ❌ and exited 0 — `navig cortex … && <next>` ran."""
    cortex_mod, driver = _cortex_env(
        monkeypatch, [{"action": "fail", "error": "login wall", "reason": ""}]
    )
    with pytest.raises(typer.Exit) as excinfo:
        cortex_mod.run_cortex(
            goal="buy it", start_url="https://example.test/", max_steps=3,
            headless=True, no_template=True,
        )
    assert excinfo.value.exit_code == 1
    assert driver.stopped, "the browser must be closed on every exit path"


def test_cortex_exits_zero_when_the_goal_is_reached(monkeypatch):
    cortex_mod, driver = _cortex_env(monkeypatch, [{"action": "done", "reason": "ok"}])
    cortex_mod.run_cortex(
        goal="buy it", start_url="https://example.test/", max_steps=3,
        headless=True, no_template=True,
    )   # no raise
    assert driver.stopped


def test_cortex_exits_one_when_the_step_budget_runs_out(monkeypatch):
    """Falling out of the loop without `done` is a failure, not a quiet finish."""
    cortex_mod, _ = _cortex_env(monkeypatch, [])   # no decision at all
    with pytest.raises(typer.Exit) as excinfo:
        cortex_mod.run_cortex(
            goal="buy it", start_url="https://example.test/", max_steps=1,
            headless=True, no_template=True,
        )
    assert excinfo.value.exit_code == 1
