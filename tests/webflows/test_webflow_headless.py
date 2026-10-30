"""Phase D/E orchestration: repair evidence, checkpoints, resume, and the needs-you pause.

All four against the real local fixture shop (a real Chromium per test):

* ``apply_repairs`` moves only locator keys and marks provenance — pure;
* a changed site → ``repair=True`` → incident + a NEW revision, origins untouched;
* an interrupted run leaves a checkpoint and RESUMES where it left off (the
  side-effecting click is skipped, not re-executed);
* a vault step nobody may fill pauses as ``needs_user`` (exit 4), sends the one pause
  notification, keeps its checkpoint — and resumes cleanly once the person acted.
"""

from __future__ import annotations

import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

webflows = pytest.importorskip("navig.webflows")

from navig.webflows import SCHEMA, WebflowStore, validate_webflow  # noqa: E402
from navig.webflows.headless import apply_repairs, exit_code, run_headless  # noqa: E402
from navig.webflows.runs import load as load_cp  # noqa: E402
from navig.webflows.runs import resume_plan

FIXTURE = Path(__file__).resolve().parents[3] / "apps" / "dock" / "extension" / "harness" / "webflow-fixture"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture(scope="module")
def site():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Quiet, directory=str(FIXTURE)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path


def _go(coro):
    """asyncio.run that treats a missing Chromium as an environment gap, not a pass."""
    try:
        return asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001
        if "Executable doesn't exist" in str(exc):
            pytest.skip("Playwright Chromium is not installed (playwright install chromium)")
        raise


def _doc(site: str, **over) -> dict:
    raw = {
        "schema": SCHEMA, "name": "Interruptible order", "origins": [site],
        "start_url": f"{site}/order.html",
        "params": [{"name": "customer", "type": "string", "required": True}],
        "steps": [
            {"action": "fill", "testid": "customer-name", "value": "{{customer}}"},
            {"action": "fill", "testid": "plan", "value": "pro"},
            {"action": "click", "testid": "next", "fallbacks": [{"role": "button", "name": "Next"}],
             "expect": {"url_contains": "/confirm.html"}},
            {"action": "fill", "testid": "terms", "value": True},
            {"action": "click", "role": "button", "name": "Place order", "expect": {"url_contains": "/done.html"}},
            {"action": "assert", "has_text": "Thank you"},  # static on done.html: survives any session
        ],
    }
    raw.update(over)
    v = validate_webflow(raw)
    assert v.ok, v.errors
    return v.doc


# ── Phase D: repair ───────────────────────────────────────────────────────────


def test_apply_repairs_moves_only_locators_and_marks_provenance(site: str):
    doc = _doc(site)
    doc["steps"][2]["role"] = "button"  # the recorded shape with fallbacks
    fixed = apply_repairs(doc, [{"index": 2, "to": {"testid": "next-step"}, "why": "the test id drifted"}])
    step = fixed["steps"][2]
    assert step["testid"] == "next-step" and "role" not in step and "fallbacks" not in step
    assert step["expect"] == doc["steps"][2]["expect"], "the postcondition stands"
    assert fixed["origins"] == doc["origins"] and fixed["start_url"] == doc["start_url"]
    assert fixed["params"] == doc["params"]
    assert fixed["provenance"]["source"] == "repair"
    assert "role" in doc["steps"][2], "the original document is never mutated"
    assert apply_repairs(doc, [{"index": 99, "to": {"css": "#x"}, "why": "?"}])["steps"][2] == doc["steps"][2]


def test_a_repaired_run_records_an_incident_and_saves_a_revision(site: str, _isolated_config):
    store = WebflowStore()
    # Recorded before the rename: ?v=3 changes the Next button's testid/id/text.
    saved = store.save(_doc(site, name="Pre-rename order", start_url=f"{site}/order.html?v=3"))
    out = _go(run_headless(saved, {"customer": "Ada"}, repair=True))
    assert out["status"] == "verified", out
    assert out["repairs"] and out["repairs"][0]["to"] == {"testid": "next-step"}
    assert out["saved_revision"] == 2

    r2 = store.get(saved["id"])
    assert r2["steps"][2]["testid"] == "next-step"
    assert r2["provenance"]["source"] == "repair"
    assert r2["origins"] == saved["origins"], "a repair never widens where a flow may act"

    incidents = _isolated_config / "cfg" / "perf" / "config_incidents.jsonl"
    text = incidents.read_text(encoding="utf-8") if incidents.exists() else ""
    assert "webflow.locator_repair" in text, "every automated repair is an incident"
    assert "next-step" in text, "the incident carries the evidence"


def test_without_repair_the_same_site_change_fails(site: str, _isolated_config):
    saved = WebflowStore().save(_doc(site, name="Failing order", start_url=f"{site}/order.html?v=3"))
    out = _go(run_headless(saved, {"customer": "Ada"}))
    assert out["status"] == "failed"
    assert out.get("saved_revision") is None
    assert WebflowStore().list()[0].revision == 1, "no silent write without --repair"


# ── Phase E: interruption → resume ────────────────────────────────────────────


def test_an_interrupted_run_resumes_where_it_left_off(site: str, _isolated_config):
    store = WebflowStore()
    doc = store.save(_doc(site, name="Resumable order"))
    flow_dir = store._dir(doc["id"])

    def die_after_the_click(res):
        if res.index == 3:  # terms step done → page is at confirm.html
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _go(run_headless(doc, {"customer": "Ada"}, on_step=die_after_the_click, run_id="wfrun_intr0001"))

    cp = load_cp(flow_dir, "wfrun_intr0001")
    assert cp and cp["status"] == "interrupted" and cp["next_index"] == 4
    assert cp["last_url"].endswith("/confirm.html"), "the checkpoint knows the page it left"
    assert [s["id"] for s in cp["steps"]] == ["s1", "s2", "s3", "s4"]

    out = _go(run_headless(doc, {"customer": "Grace"}, resume="wfrun_intr0001"))
    assert out["status"] == "verified", out
    assert out["resumed"]["from"].endswith("/confirm.html")
    # Page-aware resume: the two order-page fills and the navigation are PROVEN done
    # (their expect holds on the restored page) → skipped; the terms fill lives on THIS
    # page and the fresh browser lost it → RE-EXECUTED (idempotent); then 4, 5, 6 run.
    assert out["resumed"]["skipped"] == 3, out["resumed"]
    assert [st.get("category") for st in out["steps"][:3]] == ["skipped"] * 3
    assert out["steps"][3]["index"] == 3 and out["steps"][3]["ok"] and not out["steps"][3].get("category"),         "the same-page state setting step was restored, not skipped"
    assert [st["index"] for st in out["steps"]] == list(range(6))

    done = load_cp(flow_dir, "wfrun_intr0001")
    assert done["status"] == "verified" and done["next_index"] == 6


def test_a_finished_run_is_not_resumable(site: str, _isolated_config):
    doc = WebflowStore().save(_doc(site, name="Finished order"))
    out = _go(run_headless(doc, {"customer": "Ada"}, run_id="wfrun_done0001"))
    assert out["status"] == "verified"
    cp = load_cp(WebflowStore()._dir(doc["id"]), "wfrun_done0001")
    assert cp["status"] == "verified"
    assert resume_plan(cp, doc) is None, "a terminal checkpoint must not be resumed"


# ── Phase E: pause + notify instead of pretending ─────────────────────────────


def test_a_vault_step_needs_the_person_pauses_notifies_and_resumes(site: str, _isolated_config, monkeypatch):
    import navig.notify as notify_mod

    dispatched: list[dict] = []

    async def fake_dispatch(kind, title, body, *, priority=None, data=None):
        dispatched.append({"kind": kind, "title": title, "body": body, "data": data})
        return "sent"

    monkeypatch.setattr(notify_mod, "dispatch", fake_dispatch)

    from navig.browser.webflow_runner import StepFailed

    raw = _doc(site, name="Pausing order")
    raw["params"].append({"name": "account_password", "type": "secret",
                          "vault": {"domain": "127.0.0.1", "username": "ada"}})
    raw["steps"].insert(1, {"action": "fill", "testid": "password", "value": "{{account_password}}",
                            "sensitive": True})
    doc = WebflowStore().save(validate_webflow(raw).doc)
    flow_dir = WebflowStore()._dir(doc["id"])

    async def refuse(param, page_url):
        raise StepFailed("no_credential", "no vault login ada for 127.0.0.1")

    out = _go(run_headless(doc, {"customer": "Ada"}, allow_vault=True,
                           secret_provider_override=refuse, run_id="wfrun_pause001"))
    assert out["status"] == "needs_user", out
    assert exit_code(out["status"]) == 4
    assert out["steps"][1]["category"] == "no_credential"
    assert len(dispatched) == 1 and dispatched[0]["kind"] == "webflow_needs_user"
    assert "--resume wfrun_pause001" in dispatched[0]["body"], "the message says how to continue"
    assert "hunter" not in json.dumps(dispatched), "no value, ever"

    cp = load_cp(flow_dir, "wfrun_pause001")
    assert cp["status"] == "needs_user" and cp["next_index"] == 1, "the failed step stays next — it is retried"
    assert resume_plan(cp, doc) is not None, "a needs-you run is resumable"

    # The person acted (the login exists now): resume completes and does NOT notify again.
    async def grant(param, page_url):
        return "typed-after-asking"

    out2 = _go(run_headless(doc, {"customer": "Ada"}, allow_vault=True,
                            secret_provider_override=grant, resume="wfrun_pause001"))
    assert out2["status"] == "verified", out2
    # Nothing is skipped on the start page: the completed name fill is idempotent and
    # re-executed on the fresh page (the value is given again), then the run continues.
    assert out2["resumed"]["skipped"] == 0, out2["resumed"]
    assert out2["steps"][0]["ok"] and not out2["steps"][0].get("category")
    assert load_cp(flow_dir, "wfrun_pause001")["status"] == "verified"
    assert len(dispatched) == 1, "resuming must not notify again"


def test_a_resume_that_cannot_prove_where_it_left_off_fails_closed(site: str, _isolated_config):
    # A completed CLICK with no recorded outcome (no expect) and a restored page that
    # matches no recorded outcome: refuse — never re-click, never skip silently.
    raw = _doc(site, name="Unprovable order")
    raw["steps"][2].pop("expect", None)
    doc = WebflowStore().save(validate_webflow(raw).doc)

    def die_after_the_click(res):
        if res.index == 2:
            raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        _go(run_headless(doc, {"customer": "Ada"}, on_step=die_after_the_click, run_id="wfrun_stale0001"))

    out = _go(run_headless(doc, {"customer": "Ada"}, resume="wfrun_stale0001"))
    assert out["status"] == "failed"
    failed = next(st for st in out["steps"] if not st["ok"])
    assert failed["category"] == "stale" and "without --resume" in failed["error"]



# ── §9: a challenge pauses the run with its reason (detection, never evasion) ──


def test_a_challenge_page_pauses_as_needs_user_with_the_reason(site: str, _isolated_config, monkeypatch):
    import navig.notify as notify_mod

    dispatched: list[dict] = []

    async def fake_dispatch(kind, title, body, *, priority=None, data=None):
        dispatched.append({"kind": kind, "body": body})
        return "sent"

    monkeypatch.setattr(notify_mod, "dispatch", fake_dispatch)

    from navig.webflows.runs import resume_plan

    raw = _doc(site, name="Challenged order", start_url=f"{site}/challenge.html")
    # The recorded step targets a control this page hides behind a check.
    raw["steps"] = [{"action": "click", "role": "button", "name": "Next", "timeout_ms": 900},
                    {"action": "assert", "has_text": "Thank you"}]
    doc = WebflowStore().save(validate_webflow(raw).doc)
    flow_dir = WebflowStore()._dir(doc["id"])

    out = _go(run_headless(doc, {"customer": "Ada"}, run_id="wfrun_chall001"))
    assert out["status"] == "needs_user", out
    failed = out["steps"][0]
    assert failed["category"] == "challenge" and failed["challenge"] == "CAPTCHA"
    assert "CAPTCHA" in failed["error"], "the report keeps why, not just the class"
    assert exit_code(out["status"]) == 4

    assert len(dispatched) == 1 and dispatched[0]["kind"] == "webflow_needs_user"
    assert "CAPTCHA" in dispatched[0]["body"] and "--resume wfrun_chall001" in dispatched[0]["body"]

    cp = load_cp(flow_dir, "wfrun_chall001")
    assert cp["status"] == "needs_user"
    assert cp["steps"][0]["category"] == "challenge", "checkpoint evidence agrees with the report"
    assert resume_plan(cp, doc) is not None, "paused runs stay resumable once the person acted"



# ── closing fixes: revision alignment, clean engine escape ─────────────────────


def test_resume_refuses_a_flow_edited_after_the_run_started(site: str, _isolated_config):
    from navig.webflows import WebflowError
    from navig.webflows.runs import write as write_cp

    store = WebflowStore()
    doc = store.save(_doc(site, name="Before the edit"))                       # r1
    store.save({**doc, "name": "Renamed after the run"}, wf_id=doc["id"])    # r2
    write_cp(store._dir(doc["id"]), {"run_id": "wfrun_rev0001", "flow_id": doc["id"],
                                     "revision": 1, "next_index": 2, "status": "interrupted",
                                     "last_url": f"{site}/confirm.html"})
    edited = store.get(doc["id"])
    assert edited["revision"] == 2

    # NO browser is launched: the guard fires before the controller starts.
    with pytest.raises(WebflowError, match="started on revision 1"):
        _go(run_headless(edited, {"customer": "Ada"}, resume="wfrun_rev0001"))

    # The CLI's answer is spelled out in the error for a human (and the flag to use).
    try:
        _go(run_headless(edited, {"customer": "Ada"}, resume="wfrun_rev0001"))
    except Exception as exc:  # noqa: BLE001 — re-read the message for the flag
        assert "--revision 1" in str(exc)


def test_an_unreadable_start_page_is_a_clean_error(_isolated_config):
    """The engine's start navigation escape becomes our error (exit 2), not a traceback."""
    from navig.webflows import WebflowError

    raw = {"schema": SCHEMA, "name": "Dead start", "origins": ["http://127.0.0.1:1"],
           "start_url": "http://127.0.0.1:1/x",
           "params": [], "steps": [{"action": "click", "role": "button", "name": "Next"}]}
    doc = validate_webflow(raw).doc
    with pytest.raises(WebflowError, match="could not continue"):
        _go(run_headless(doc, {}))
