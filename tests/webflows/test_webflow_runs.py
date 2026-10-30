"""Run checkpoints: whitelisted fields only, atomic writes, refuse-to-clobber reads.

The two properties this module exists for: a resume has enough state to continue
(next_index + a sanitized page URL inside the flow's origins), and no parameter value —
secret or not — can end up in the file, whatever the caller hands over.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.webflows import WebflowError
from navig.webflows.runs import list_runs, load, new_run_id, resume_plan, sanitize_url, write

FLOW_DIR = Path("/tmp/unused")  # only used via _path validation in most tests


def test_a_run_id_is_generated_and_validated():
    rid = new_run_id()
    assert rid.startswith("wfrun_") and 10 <= len(rid) <= 30
    for bad in ("../escape", "run_1", "wfrun_UPPER", "wfrun_", ""):
        with pytest.raises(WebflowError):
            write(Path("/tmp/x"), {"run_id": bad, "flow_id": "wf_a", "status": "running"})


def test_sanitize_url_strips_secrets_fragments_and_rejects_non_http():
    assert sanitize_url("https://shop.com/a?x=1&token=abc&page=2#access_token=t") == "https://shop.com/a?x=1&page=2"
    assert sanitize_url("http://127.0.0.1:8080/order.html?v=3") == "http://127.0.0.1:8080/order.html?v=3"
    assert sanitize_url("file:///etc/passwd") == ""
    assert sanitize_url("javascript:alert(1)") == ""
    assert sanitize_url("not a url") == ""


def test_write_then_load_round_trips_and_drops_everything_else(tmp_path: Path):
    cp = {"run_id": new_run_id(), "flow_id": "wf_shop", "revision": 3, "next_index": 4,
          "status": "interrupted", "last_url": "https://shop.com/checkout?token=zz",
          "steps": [{"id": "s1", "action": "click", "ok": True, "ms": 12},
                    {"id": "s2", "ok": False, "category": "not_found", "ms": 900, "error": "raw error text",
                     "value": "hunter2"}],
          # hostile/oversized extras the writer must drop:
          "values": {"pw": "hunter2"}, "password": "hunter2", "note": "x" * 50}
    stored = write(tmp_path, cp)
    assert stored["status"] == "interrupted" and stored["next_index"] == 4
    text = (tmp_path / "runs" / f"{cp['run_id']}.json").read_text(encoding="utf-8")
    assert "hunter2" not in text and "error" not in json.loads(text)["steps"][1]
    assert json.loads(text)["steps"][1] == {"id": "s2", "ok": False, "category": "not_found", "ms": 900}
    assert "token" not in stored["last_url"]
    got = load(tmp_path, cp["run_id"])
    assert got["run_id"] == cp["run_id"] and got["revision"] == 3


def test_a_corrupt_checkpoint_is_refused_not_overwritten(tmp_path: Path):
    rid = new_run_id()
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / f"{rid}.json").write_text("{not json", encoding="utf-8")
    assert load(tmp_path, rid) is None
    with pytest.raises(WebflowError, match="refusing to overwrite"):
        write(tmp_path, {"run_id": rid, "flow_id": "wf_shop", "status": "running"})
    assert (runs / f"{rid}.json").read_text(encoding="utf-8") == "{not json"


def test_status_and_shape_are_validated(tmp_path: Path):
    base = {"run_id": new_run_id(), "flow_id": "wf_shop", "status": "running"}
    with pytest.raises(WebflowError, match="unknown run status"):
        write(tmp_path, {**base, "status": "done-ish"})
    with pytest.raises(WebflowError, match="next_index"):
        write(tmp_path, {**base, "next_index": -1})
    with pytest.raises(WebflowError, match="flow id"):
        write(tmp_path, {**base, "flow_id": ""})


def test_list_runs_is_newest_first_and_skips_corrupt(tmp_path: Path):
    older = write(tmp_path, {"run_id": new_run_id(now=__import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)),
                             "flow_id": "wf_shop", "status": "failed"})
    newer = write(tmp_path, {"run_id": new_run_id(now=__import__("datetime").datetime(2026, 6, 1, tzinfo=__import__("datetime").timezone.utc)),
                             "flow_id": "wf_shop", "status": "interrupted"})
    (tmp_path / "runs" / "wfrun_zzzzzzz.json").write_text("{}", encoding="utf-8")
    rows = list_runs(tmp_path)
    assert [r["run_id"] for r in rows] == [newer["run_id"], older["run_id"]]
    assert rows[0]["status"] == "interrupted"


DOC = {"id": "wf_shop", "start_url": "https://shop.com/a", "origins": ["https://shop.com"],
       "steps": [{"id": "s1"}, {"id": "s2"}]}


def test_resume_plan_continues_only_where_it_may():
    cp = {"flow_id": "wf_shop", "status": "interrupted", "next_index": 1, "last_url": "https://shop.com/a?token=x"}
    assert resume_plan(cp, DOC) == (1, "https://shop.com/a")
    assert resume_plan({**cp, "flow_id": "wf_other"}, DOC) is None, "another flow's run"
    assert resume_plan({**cp, "status": "verified"}, DOC) is None, "terminal runs never resume"
    assert resume_plan({**cp, "next_index": 99}, DOC) is None, "past the end"
    assert resume_plan({**cp, "last_url": "https://evil.com/"}, DOC) is None, "outside origins"
    assert resume_plan({**cp, "last_url": ""}, DOC) == (1, "https://shop.com/a"), "no url → the start page"
