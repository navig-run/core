"""``navig webflow`` — the command group a person (or a goal) manages and replays flows with.

Import validates and never overwrites, export round-trips, ``list --match`` finds a flow by
what it does, and ``run`` refuses a secret passed as an argument or a secret nobody can fill.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.webflow import webflow_app
from navig.webflows import SCHEMA, WebflowStore, webflows_dir

SITE = "https://shop.com"
FLOW = {
    "schema": SCHEMA, "name": "Renew the shop subscription", "description": "Pay the monthly plan",
    "origins": [SITE], "start_url": f"{SITE}/account",
    "params": [{"name": "plan", "type": "string", "default": "pro"},
               {"name": "pw", "type": "secret", "vault": {"domain": "shop.com", "username": "ada"}}],
    "steps": [
        {"action": "select", "role": "combobox", "name": "Plan", "value": "{{plan}}"},
        {"action": "fill", "css": "#pw", "value": "{{pw}}", "sensitive": True},
        {"action": "click", "role": "button", "name": "Renew"},
        {"action": "assert", "has_text": "Renewed"},
    ],
}

runner = CliRunner()


@pytest.fixture(autouse=True)
def _config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("COLUMNS", "120")
    assert webflows_dir() == tmp_path / "cfg" / "webflows"
    return tmp_path


def _import(tmp_path: Path, doc: dict) -> str:
    f = tmp_path / "flow.webflow.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    r = runner.invoke(webflow_app, ["import", str(f)])
    assert r.exit_code == 0, r.output
    return WebflowStore().list()[0].id


def test_empty_library_says_how_to_record(_config):
    r = runner.invoke(webflow_app, ["list"])
    assert r.exit_code == 0 and "Record one in the NAVIG Dock" in r.output


def test_import_list_match_show_export_round_trip(_config):
    wf = _import(_config, {**FLOW, "id": "wf_someone-elses", "revision": 9})
    assert wf == "wf_renew-the-shop-subscription", "an import never keeps a foreign id"
    rows = json.loads(runner.invoke(webflow_app, ["list", "--json"]).output)
    assert [r["id"] for r in rows] == [wf] and rows[0]["revision"] == 1
    assert json.loads(runner.invoke(webflow_app, ["list", "--match", "monthly plan", "--json"]).output)[0]["id"] == wf
    assert json.loads(runner.invoke(webflow_app, ["list", "--match", "bank", "--json"]).output) == []
    show = runner.invoke(webflow_app, ["show", wf])
    assert show.exit_code == 0 and "vault: ada @ shop.com" in show.output and "Check the page shows" in show.output
    exported = runner.invoke(webflow_app, ["export", wf]).output
    again = _config / "again.json"
    again.write_text(exported, encoding="utf-8")
    assert runner.invoke(webflow_app, ["import", str(again)]).exit_code == 0
    docs = [WebflowStore().get(r.id) for r in WebflowStore().list()]
    strip = lambda d: {k: v for k, v in d.items() if k not in ("id", "created_at", "updated_at", "revision", "name")}  # noqa: E731
    assert strip(docs[0]) == strip(docs[1]), "export → import is stable"


def test_an_invalid_file_is_refused_with_every_reason(_config):
    bad = _config / "bad.json"
    bad.write_text(json.dumps({**FLOW, "start_url": "https://evil.com/"}), encoding="utf-8")
    r = runner.invoke(webflow_app, ["import", str(bad)])
    assert r.exit_code == 1 and "not one of this flow's origins" in r.output
    assert WebflowStore().list() == []


def test_run_refuses_a_secret_on_the_command_line(_config):
    wf = _import(_config, FLOW)
    r = runner.invoke(webflow_app, ["run", wf, "-p", "pw=hunter2"])
    assert r.exit_code == 2 and "Never pass a secret as an argument" in r.output


def test_a_secret_nobody_can_fill_pauses_with_a_notification(_config, monkeypatch):
    """Not a usage error: the flow is fine, a human is missing → exit 4 + one notify."""
    import navig.notify as notify_mod

    calls = []

    async def fake_dispatch(kind, title, body, *, priority=None, data=None):
        calls.append({"kind": kind, "body": body})
        return "sent"

    monkeypatch.setattr(notify_mod, "dispatch", fake_dispatch)
    wf = _import(_config, FLOW)
    r = runner.invoke(webflow_app, ["run", wf])  # CliRunner stdin is not a tty; no --allow-vault
    assert r.exit_code == 4, r.output
    assert "Paused: this run needs you." in r.output and "--allow-vault" in r.output
    assert len(calls) == 1 and calls[0]["kind"] == "webflow_needs_user"
    assert "hunter" not in str(calls)


def test_run_an_unknown_flow_is_exit_2(_config):
    r = runner.invoke(webflow_app, ["run", "wf_nope"])
    assert r.exit_code == 2 and "No webflow" in r.output


def test_delete_asks_unless_yes(_config):
    wf = _import(_config, FLOW)
    assert runner.invoke(webflow_app, ["delete", wf], input="n\n").exit_code == 1
    assert runner.invoke(webflow_app, ["delete", wf, "--yes"]).exit_code == 0
    assert WebflowStore().list() == []


def test_rollback_restores_an_old_revision_append_only(_config):
    wf = _import(_config, FLOW)
    store = WebflowStore()
    r1 = store.get(wf)
    store.save({**r1, "name": "Renamed meanwhile"}, wf_id=wf)          # r2
    assert [x.revision for x in store.list() if x.id == wf] == [2]

    r = runner.invoke(webflow_app, ["rollback", wf, "-r", "1"])
    assert r.exit_code == 0 and "r1 restored as r3" in r.output
    r3 = store.get(wf, 3)
    assert r3["name"] == FLOW["name"], "the old content, as a NEW revision"
    assert store.revisions(wf) == [1, 2, 3], "append-only: r2 is kept too (reversible)"


def test_rollback_of_a_missing_revision_is_exit_2(_config):
    wf = _import(_config, FLOW)
    assert runner.invoke(webflow_app, ["rollback", wf, "-r", "9"]).exit_code == 2


def test_runs_lists_checkpoints_and_json(_config):
    from datetime import datetime, timezone

    from navig.webflows.runs import new_run_id
    from navig.webflows.runs import write as write_cp

    wf = _import(_config, FLOW)
    rid = new_run_id()
    write_cp(WebflowStore()._dir(wf), {"run_id": rid, "flow_id": wf, "next_index": 3,
                                       "status": "interrupted", "started_at": datetime.now(timezone.utc).isoformat()})
    out = runner.invoke(webflow_app, ["runs", wf, "--json"])
    rows = json.loads(out.output)
    assert [r_["run_id"] for r_ in rows] == [rid] and rows[0]["status"] == "interrupted"
    table = runner.invoke(webflow_app, ["runs"])
    assert rid in table.output and "--resume" in table.output
    assert runner.invoke(webflow_app, ["runs", "wf_nope"]).exit_code == 2

def test_resume_continues_the_revision_the_run_started_on(_config, monkeypatch):
    """An edited flow resumes its OWN revision — observed through the pause notification."""
    from datetime import datetime, timezone

    import navig.notify as notify_mod
    from navig.webflows.runs import new_run_id
    from navig.webflows.runs import write as write_cp

    calls = []

    async def fake_dispatch(kind, title, body, *, priority=None, data=None):
        calls.append(body)
        return "sent"

    monkeypatch.setattr(notify_mod, "dispatch", fake_dispatch)
    wf = _import(_config, FLOW)                                   # r1, name "Route flow"
    store = WebflowStore()
    store.save({**store.get(wf), "name": "Renamed after the run"}, wf_id=wf)  # r2
    rid = new_run_id()
    write_cp(store._dir(wf), {"run_id": rid, "flow_id": wf, "revision": 1, "next_index": 1,
                              "status": "interrupted",
                              "started_at": datetime.now(timezone.utc).isoformat()})

    # Non-tty + an unfilled secret pauses BEFORE any browser — but AFTER the alignment,
    # so the pause names r1's flow, proving the aligned document was the one used.
    r = runner.invoke(webflow_app, ["run", wf, "--resume", rid])
    assert r.exit_code == 4, r.output
    assert len(calls) == 1 and FLOW["name"] in calls[0], calls
    assert "Renamed after the run" not in calls[0], "the pause must name the resumed revision's flow"

    # --resume + --revision naming different revisions is an error, not a silent choice.
    clash = runner.invoke(webflow_app, ["run", wf, "--resume", rid, "-r", "2"])
    assert clash.exit_code == 2 and "not r2" in clash.output


def test_resume_of_an_unknown_run_is_exit_2(_config):
    no_secret = {**FLOW, "params": [], "steps": [{"action": "click", "role": "button", "name": "Next"}]}
    wf = _import(_config, no_secret)
    r = runner.invoke(webflow_app, ["run", wf, "--resume", "wfrun_nope0000"])
    assert r.exit_code == 2 and "cannot be resumed" in r.output
