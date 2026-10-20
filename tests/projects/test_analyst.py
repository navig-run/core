"""The project analyst (LLM mocked): it PROPOSES, and every write stays guarded."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.agent import project_analyst as pa


@pytest.fixture()
def llm(monkeypatch: pytest.MonkeyPatch):
    """Capture prompts and return canned answers keyed by the analysis kind."""
    calls: list[dict] = []
    answers: dict[str, str] = {}

    def fake(messages, **kw):
        system = messages[0]["content"]
        calls.append({"system": system, "user": messages[1]["content"], **kw})
        for key, answer in answers.items():
            if key in system:
                return answer
        raise AssertionError("unexpected prompt")

    import navig.llm.generate as gen

    monkeypatch.setattr(gen, "llm_generate", fake)
    return calls, answers


BRIEF = json.dumps({
    "summary": "A flash-sale shop.", "state": "Payments in progress.",
    "next_actions": ["Finish Stripe", "Ship login", "Write tests", "a fourth one"],
    "risks": ["deploy:prod has no guard"], "stale_docs": [{"path": ".navig/plans/ROADMAP.md", "why": "old"}],
})


def _pack(project: Path) -> dict:
    from navig.projects.context_pack import build_pack

    return build_pack(project, name="shop")


def test_brief_is_parsed_capped_and_rendered(project: Path, llm) -> None:
    calls, answers = llm
    answers["act on in one minute"] = BRIEF  # a phrase unique to the brief prompt
    from navig.projects.docs import index_docs

    facts = pa.doc_freshness(project, index_docs(project))
    assert {f["path"] for f in facts} >= {".navig/plans/VISION.md", ".navig/plans/ROADMAP.md"}
    out = pa.brief(_pack(project), facts)
    assert out["next_actions"] == ["Finish Stripe", "Ship login", "Write tests"]  # capped at 3
    assert out["stale_docs"] == [{"path": ".navig/plans/ROADMAP.md", "why": "old"}]
    assert "**A flash-sale shop.**" in out["markdown"]
    assert "FACTS (measured" in calls[0]["user"] and "Sell things fast." in calls[0]["user"]


def test_a_fenced_or_chatty_json_answer_still_parses(project: Path, llm) -> None:
    _calls, answers = llm
    answers["act on in one minute"] = "```json\n" + BRIEF + "\n```"
    assert pa.brief(_pack(project), [])["summary"] == "A flash-sale shop."


def test_reanalyze_proposes_and_never_writes(project: Path, llm) -> None:
    _calls, answers = llm
    doc = project / ".navig" / "plans" / "ROADMAP.md"
    before = doc.read_bytes()
    answers["Change content, not shape"] = "# Roadmap\n\n- [x] M1\n- [x] M2\n"
    out = pa.reanalyze_doc(_pack(project), ".navig/plans/ROADMAP.md", before.decode())
    assert out["changed"] is True and out["proposed"].endswith("- [x] M2\n")
    assert doc.read_bytes() == before


def test_extract_todos_drops_what_is_already_tracked(llm) -> None:
    _calls, answers = llm
    answers["NOT already tracked"] = json.dumps({"todos": [
        {"text": "Wire the checkout!", "heading": "Payments", "source": "b.md"},  # already tracked
        {"text": "Add rate limiting", "heading": "API", "source": "b.md"},
        {"text": "add  rate limiting", "heading": "API", "source": "c.md"},  # duplicate of the above
    ]})
    out = pa.extract_todos([("b.md", "notes")], ["Wire the checkout"])
    assert [t["text"] for t in out["todos"]] == ["Add rate limiting"]


def test_no_backend_is_a_clean_unavailable_error(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.llm.generate as gen

    def boom(messages, **kw):
        raise ConnectionError("no provider")

    monkeypatch.setattr(gen, "llm_generate", boom)
    monkeypatch.setattr(pa, "_is_unavailable", lambda exc: True)
    with pytest.raises(pa.AnalystUnavailableError):
        pa.brief(_pack(project), [])


def test_a_missing_prompt_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.prompts.loader as loader

    monkeypatch.setattr(loader, "load_prompt", lambda slug: f"Warning: Prompt {slug} not found.")
    with pytest.raises(RuntimeError, match="missing"):
        pa._prompt("project/analyst_brief")


def test_every_analyst_prompt_ships_in_the_builtin_store() -> None:
    from navig.prompts.loader import load_prompt

    for slug in ("project/analyst_brief", "project/analyst_reanalyze_doc", "project/analyst_extract_todos"):
        assert not load_prompt(slug).startswith("Warning:"), slug


@pytest.mark.integration
async def test_analyze_route_stores_a_proposal_and_accept_is_guarded(project: Path, llm) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import projects as routes
    from navig.projects.model import add_project

    _calls, answers = llm
    answers["Change content, not shape"] = "# Roadmap\n\n- [x] M1\n- [x] M2\n"
    pid = add_project(project)["id"]
    app = web.Application()
    routes.register(app)
    async with TestClient(TestServer(app)) as client:
        res = await client.post(f"/api/deck/projects/{pid}/analyze",
                                json={"kind": "doc", "doc": ".navig/plans/ROADMAP.md"})
        body = await res.json()
        assert res.status == 200, body
        a = body["data"]
        assert a["status"] == "proposed" and a["target"] == ".navig/plans/ROADMAP.md"
        base = a["output"]["base_fingerprint"]

        # Someone edits the doc while the proposal is on screen → accepting must 409.
        (project / ".navig" / "plans" / "ROADMAP.md").write_bytes(b"# Roadmap\n\n- [x] M1\n- [ ] M2\n- [ ] M3\n")
        res = await client.post(f"/api/deck/projects/{pid}/doc",
                                json={"path": a["target"], "content": a["output"]["proposed"], "expect": base})
        assert res.status == 409

        res = await client.post(f"/api/deck/projects/{pid}/analyses/{a['id']}/status", json={"status": "rejected"})
        assert res.status == 200
        res = await client.get(f"/api/deck/projects/{pid}/analyses")
        assert (await res.json())["data"][0]["status"] == "rejected"

        res = await client.post(f"/api/deck/projects/{pid}/analyze", json={"kind": "nope"})
        assert res.status == 400


async def test_project_context_tool(project: Path) -> None:
    from navig.agent.tools.plan_tools import ProjectContextTool
    from navig.projects.model import add_project

    pid = add_project(project)["id"]
    res = await ProjectContextTool().run({"project": pid})
    assert res.success, res.output
    assert "Sell things fast." in res.output["context"] and res.output["project"] == pid
    bad = await ProjectContextTool().run({"project": "nope"})
    assert bad.success is False
