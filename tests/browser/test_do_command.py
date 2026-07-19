"""Stage B — `navig do` bridge + safeguards (agent run mocked, no LLM)."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

import navig.commands.do as do_mod
from navig.commands.do import do_app

pytestmark = pytest.mark.integration


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr("navig.browser.targets.probe_port", lambda *a, **k: None)
    return tmp_path


def _stub_open(monkeypatch, port=9280):
    monkeypatch.setattr("navig.browser.cdp_actions.profile_open",
                        lambda name, headless=False: {"ok": True, "port": port, "name": name})

    # Neutralize the best-effort bring-to-front (no real browser in unit tests).
    def _fake_rt(coro):
        try:
            coro.close()
        except Exception:
            pass
        return {"ok": True}

    monkeypatch.setattr("navig.browser.cdp_runtime.run", _fake_rt)


def _capture_run(monkeypatch):
    captured = {}

    async def fake_run(task, session_key, max_iterations):
        captured["task"] = task
        captured["session_key"] = session_key
        return "DONE", None

    monkeypatch.setattr(do_mod, "_agent_run", fake_run)
    return captured


def test_do_bridges_profile_and_audits(cfg, monkeypatch):
    from navig.browser import profiles as p

    p.create_profile("cybesis")
    p.set_active("cybesis")
    _stub_open(monkeypatch, port=9280)
    bridged = {}
    monkeypatch.setattr("navig.agent.tools.browser_session.register_desktop_endpoint",
                        lambda key, url: bridged.update(key=key, url=url))
    captured = _capture_run(monkeypatch)

    r = CliRunner().invoke(do_app, ["open gmail and email bob"])
    assert r.exit_code == 0, r.output
    assert "DONE" in r.output
    # bridged to the profile's stable port under a stable do- key
    assert bridged["key"] == "do-cybesis"
    assert bridged["url"] == "http://127.0.0.1:9280"
    # default = SAFE MODE guardrail, original task preserved
    assert "SAFE MODE" in captured["task"] and "open gmail and email bob" in captured["task"]
    # audited (no secrets — just task + profile)
    audit = (cfg / "history" / "do.jsonl").read_text(encoding="utf-8")
    assert "cybesis" in audit and "open gmail" in audit


def test_do_surfaces_account_rotation(cfg, monkeypatch):
    from navig.browser import profiles as p

    p.create_profile("cybesis")
    p.set_active("cybesis")
    _stub_open(monkeypatch, port=9280)
    monkeypatch.setattr("navig.agent.tools.browser_session.register_desktop_endpoint",
                        lambda key, url: None)

    async def fake_run(task, session_key, max_iterations):
        return "REPLY", {"reason": "rate_limited", "to": "Claude B"}

    monkeypatch.setattr(do_mod, "_agent_run", fake_run)

    r = CliRunner().invoke(do_app, ["--yes", "do a thing"])
    assert r.exit_code == 0, r.output
    assert "REPLY" in r.output
    assert "Claude B" in r.output          # rotation surfaced, not a silent swap
    assert "rate_limited" in r.output


def test_do_dry_run_guardrail(cfg, monkeypatch):
    from navig.browser import profiles as p

    p.create_profile("cy")
    p.set_active("cy")
    _stub_open(monkeypatch)
    monkeypatch.setattr("navig.agent.tools.browser_session.register_desktop_endpoint",
                        lambda key, url: None)
    captured = _capture_run(monkeypatch)

    r = CliRunner().invoke(do_app, ["--dry-run", "send an email"])
    assert r.exit_code == 0, r.output
    assert "DRY-RUN" in captured["task"]


def test_do_yes_removes_guardrail(cfg, monkeypatch):
    from navig.browser import profiles as p

    p.create_profile("cy")
    p.set_active("cy")
    _stub_open(monkeypatch)
    monkeypatch.setattr("navig.agent.tools.browser_session.register_desktop_endpoint",
                        lambda key, url: None)
    captured = _capture_run(monkeypatch)

    r = CliRunner().invoke(do_app, ["--yes", "send an email to bob"])
    assert r.exit_code == 0, r.output
    assert "SAFE MODE" not in captured["task"] and "DRY-RUN" not in captured["task"]
    assert captured["task"].strip() == "send an email to bob"


def test_do_no_profile_errors_with_guidance(cfg):
    r = CliRunner().invoke(do_app, ["do something"])
    assert r.exit_code == 1
    assert "profile" in r.output.lower()


def test_do_requires_a_task(cfg):
    r = CliRunner().invoke(do_app, ["--dry-run"])  # option present, task missing
    assert r.exit_code == 1
    assert "task" in r.output.lower()


def test_do_sets_hard_safe_mode_during_run(cfg, monkeypatch):
    from navig.browser import profiles as p
    from navig.browser import safe_mode

    p.create_profile("cy")
    _stub_open(monkeypatch)
    monkeypatch.setattr("navig.agent.tools.browser_session.register_desktop_endpoint",
                        lambda key, url: None)
    seen = {}

    async def fake_run(task, session_key, max_iterations):
        seen["level"] = safe_mode.get_level()
        return "OK", None

    monkeypatch.setattr(do_mod, "_agent_run", fake_run)

    CliRunner().invoke(do_app, ["do it"])            # default → safe
    assert seen["level"] == "safe"
    assert safe_mode.get_level() == "off"            # reset afterwards

    CliRunner().invoke(do_app, ["--yes", "do it"])   # --yes → off (full autonomy)
    assert seen["level"] == "off"

    CliRunner().invoke(do_app, ["--dry-run", "do it"])  # --dry-run → dry_run
    assert seen["level"] == "dry_run"
    safe_mode.set_level("off")


def test_do_named_missing_profile_errors_even_headless(cfg):
    # an explicitly-named profile that doesn't exist is an error, even with --headless
    r = CliRunner().invoke(do_app, ["--profile", "ghost", "--headless", "do it"])
    assert r.exit_code == 1
    assert "ghost" in r.output


def test_do_headless_without_profile_runs(cfg, monkeypatch):
    # no active profile + --headless → agent uses its own browser (no bridge, no error)
    captured = _capture_run(monkeypatch)
    called = {}
    monkeypatch.setattr("navig.agent.tools.browser_session.register_desktop_endpoint",
                        lambda key, url: called.setdefault("bridged", True))
    r = CliRunner().invoke(do_app, ["--headless", "read example.com"])
    assert r.exit_code == 0, r.output
    assert "bridged" not in called  # no profile → no desktop bridge
    assert captured["session_key"] == "do-headless"
