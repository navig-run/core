"""Ideas (IDEAS.md by stage) and the Idea Fabric (patterns, sessions) — LLM mocked."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.projects import ideas
from navig.projects.todos import TodoError

IDEAS_MD = """# Ideas

Some intro text.
- not an idea (above the first stage)

## Inbox
- Dark mode for the storefront #ux
  - users asked twice
- Weekly seller leaderboard #growth

## Later
- Native mobile app

## Rejected
- Crypto payments
"""


@pytest.fixture()
def with_ideas(project: Path) -> Path:
    (project / ".navig" / "plans" / "IDEAS.md").write_bytes(IDEAS_MD.encode())
    return project


def _by(root: Path) -> dict[str, dict]:
    return {i["text"]: i for i in ideas.list_ideas(root)["ideas"]}


def test_parse_stages_aliases_tags_notes(with_ideas: Path) -> None:
    state = ideas.list_ideas(with_ideas)
    assert state["path"] == ".navig/plans/IDEAS.md"
    items = _by(with_ideas)
    assert set(items) == {"Dark mode for the storefront #ux", "Weekly seller leaderboard #growth",
                          "Native mobile app", "Crypto payments"}
    assert items["Dark mode for the storefront #ux"]["tags"] == ["ux"]
    assert items["Dark mode for the storefront #ux"]["notes"] == ["users asked twice"]
    assert items["Native mobile app"]["stage"] == "parked"      # "Later" is an alias
    assert items["Crypto payments"]["stage"] == "dropped"       # "Rejected" too
    assert state["counts"] == {"inbox": 2, "exploring": 0, "parked": 1, "promoted": 0, "dropped": 1}


def test_ideas_never_count_as_todos(with_ideas: Path) -> None:
    from navig.projects.docs import index_docs
    from navig.projects.todos import extract_todos

    (with_ideas / ".navig" / "plans" / "IDEAS.md").write_bytes(b"# Ideas\n\n## Inbox\n- [ ] not a todo\n")
    assert not any(t["path"].endswith("IDEAS.md") for t in extract_todos(with_ideas))
    group = {d["path"]: d["group"] for d in index_docs(with_ideas)}
    assert group[".navig/plans/IDEAS.md"] == "ideas"


def test_add_creates_the_file_and_the_stage(project: Path) -> None:
    res = ideas.add_idea(project, "Referral codes #growth", notes=["from a user call"])
    text = (project / ".navig" / "plans" / "IDEAS.md").read_text(encoding="utf-8")
    assert "## Inbox\n\n- Referral codes #growth\n  - from a user call\n" in text
    assert res["idea"]["stage"] == "inbox" and res["idea"]["tags"] == ["growth"]
    ideas.add_idea(project, "Gift cards", stage="exploring")
    assert text.index("## Inbox") < (project / ".navig" / "plans" / "IDEAS.md").read_text(encoding="utf-8").index("## Exploring")


def test_move_keeps_notes_and_order(with_ideas: Path) -> None:
    dark = _by(with_ideas)["Dark mode for the storefront #ux"]
    res = ideas.move_idea(with_ideas, dark["line"], "exploring", expect=dark["fingerprint"])
    assert res["idea"]["stage"] == "exploring" and res["idea"]["notes"] == ["users asked twice"]
    text = (with_ideas / ".navig" / "plans" / "IDEAS.md").read_text(encoding="utf-8")
    # A new Exploring section is placed in board order: after Inbox, before Later.
    assert text.index("## Inbox") < text.index("## Exploring") < text.index("## Later")
    assert text.count("users asked twice") == 1


def test_stale_fingerprint_is_refused(with_ideas: Path) -> None:
    dark = _by(with_ideas)["Dark mode for the storefront #ux"]
    p = with_ideas / ".navig" / "plans" / "IDEAS.md"
    p.write_bytes(b"# moved\n" + p.read_bytes())
    with pytest.raises(TodoError) as exc:
        ideas.move_idea(with_ideas, dark["line"], "parked", expect=dark["fingerprint"])
    assert exc.value.status == 409


def test_promote_adds_a_todo_and_files_the_idea(with_ideas: Path) -> None:
    board = _by(with_ideas)["Weekly seller leaderboard #growth"]
    res = ideas.promote_idea(with_ideas, board["line"], ".navig/plans/TASKS.md", heading="Growth",
                             expect=board["fingerprint"])
    assert res["todo"]["text"] == "Weekly seller leaderboard #growth"
    assert res["idea"]["stage"] == "promoted" and res["idea"]["promoted_to"] == ".navig/plans/TASKS.md"
    assert "- [ ] Weekly seller leaderboard #growth" in (with_ideas / ".navig/plans/TASKS.md").read_text(encoding="utf-8")


def test_brief_is_saved_beside_and_linked(with_ideas: Path) -> None:
    dark = _by(with_ideas)["Dark mode for the storefront #ux"]
    res = ideas.save_brief(with_ideas, dark["line"], "## Problem\nToo bright.\n", expect=dark["fingerprint"])
    assert res["brief_path"] == ".navig/plans/ideas/dark-mode-for-the-storefront.md"
    assert res["idea"]["brief"] == "ideas/dark-mode-for-the-storefront.md"
    assert "Too bright." in (with_ideas / res["brief_path"]).read_text(encoding="utf-8")
    assert any(d["path"] == res["brief_path"] for d in ideas.list_ideas(with_ideas)["docs"])


def test_adopt_plan_follows_the_numbering_convention(with_ideas: Path) -> None:
    plans = with_ideas / ".navig" / "plans"
    (plans / "07-backlog.md").write_bytes(b"# Backlog\n")
    board = _by(with_ideas)["Weekly seller leaderboard #growth"]
    res = ideas.adopt_plan(with_ideas, "# Seller leaderboard\n\n## Goal\nx\n", line=board["line"],
                           expect=board["fingerprint"])
    assert res["path"] == ".navig/plans/08-seller-leaderboard.md"
    assert res["idea"]["promoted_to"] == ".navig/plans/08-seller-leaderboard.md"


def test_crlf_ideas_file_stays_crlf(with_ideas: Path) -> None:
    p = with_ideas / ".navig" / "plans" / "IDEAS.md"
    p.write_bytes(IDEAS_MD.replace("\n", "\r\n").encode())
    ideas.add_idea(with_ideas, "Something new")
    raw = p.read_bytes()
    assert raw.count(b"\r\n") == raw.count(b"\n")


# ── schema-style folders ─────────────────────────────────────────────────────


def test_schema_folder_conventions(project: Path) -> None:
    from navig.projects.docs import index_docs
    from navig.projects.todos import extract_todos

    plans = project / ".navig" / "plans"
    (plans / "archive").mkdir()
    (plans / "archive" / "old.md").write_bytes(b"# Old\n- [ ] stale task\n")
    (project / ".navig" / "refs").mkdir()
    (project / ".navig" / "refs" / "competitor.md").write_bytes(b"# Competitor\n")
    (project / ".navig" / "proposed").mkdir()
    (project / ".navig" / "proposed" / "p.md").write_bytes(b"# Proposal\n")
    (plans / "00-MASTER-ROADMAP.md").write_bytes(b"# Master\n")
    (plans / "02-loop.md").write_bytes(b"# Loop\n")
    (plans / "10-later.md").write_bytes(b"# Later\n")
    idx = index_docs(project)
    groups = {d["path"]: d["group"] for d in idx}
    assert groups[".navig/plans/archive/old.md"] == "archive"
    assert groups[".navig/refs/competitor.md"] == "refs"
    assert groups[".navig/proposed/p.md"] == "proposed"
    paths = [d["path"] for d in idx]
    assert paths.index(".navig/plans/00-MASTER-ROADMAP.md") < paths.index(".navig/plans/02-loop.md") \
        < paths.index(".navig/plans/10-later.md")
    assert paths[-1] == ".navig/plans/archive/old.md" or groups[paths[-1]] in ("archive", "inbox")
    assert not any(t["text"] == "stale task" for t in extract_todos(project))  # history, not work


# ── the Idea Fabric ──────────────────────────────────────────────────────────


@pytest.fixture()
def llm(monkeypatch: pytest.MonkeyPatch):
    answers: list[str] = []
    seen: list[dict] = []

    def fake(messages, **kw):
        seen.append({"system": messages[0]["content"], "user": messages[1]["content"], **kw})
        return answers.pop(0)

    import navig.llm.generate as gen

    monkeypatch.setattr(gen, "llm_generate", fake)
    return answers, seen


def test_builtin_patterns_and_a_project_override(project: Path) -> None:
    from navig.projects.patterns import get_pattern, list_patterns

    ids = [p["id"] for p in list_patterns(project)]
    assert ids[:5] == ["brainstorm", "harvest", "expand", "critique", "plan"]
    (project / ".navig" / "patterns").mkdir()
    (project / ".navig" / "patterns" / "pricing.md").write_text(
        "---\ntitle: Pricing angle\ninput: idea\noutput: doc\n---\nPrice it.\n", encoding="utf-8")
    (project / ".navig" / "patterns" / "broken.md").write_text("---\noutput: nonsense\n---\nx\n", encoding="utf-8")
    p = get_pattern("pricing", project)
    assert p["scope"] == "project" and p["input"] == "idea"
    assert "broken" not in [x["id"] for x in list_patterns(project)]


def test_brainstorm_dedupes_against_ideas_and_todos(with_ideas: Path, llm) -> None:
    from navig.agent.project_analyst import run_pattern
    from navig.projects.patterns import get_pattern

    answers, seen = llm
    answers.append(json.dumps({"ideas": [
        {"text": "Dark mode for the storefront #ux", "why": "dup"},
        {"text": "Wire the checkout", "why": "already a todo"},
        {"text": "Flash-sale countdown widget for partners", "why": "distribution", "tags": ["Growth!"]},
    ]}))
    out = run_pattern(get_pattern("brainstorm", with_ideas), {"markdown": "PACK"}, focus="growth",
                      existing_ideas=[i["text"] for i in ideas.list_ideas(with_ideas)["ideas"]],
                      existing_todos=["Wire the checkout"])
    assert [i["text"] for i in out["ideas"]] == ["Flash-sale countdown widget for partners"]
    assert out["ideas"][0]["tags"] == ["growth"]
    assert "FOCUS: growth" in seen[0]["user"] and seen[0]["temperature"] == 0.7


def test_sessions_only_list_plans_about_this_project(project: Path, tmp_path: Path, monkeypatch) -> None:
    from navig.projects.sessions import find_sessions, read_source

    home = tmp_path / "claude"
    (home / "plans").mkdir(parents=True)
    (home / "plans" / "ours.md").write_text(f"# Payout retries\nWork in {project}\nLater: export CSV\n", encoding="utf-8")
    (home / "plans" / "theirs.md").write_text("# Another project\nE:\\elsewhere\n", encoding="utf-8")
    slug_dir = home / "projects" / str(project.resolve()).replace(":", "-").replace("\\", "-").replace("/", "-")
    (slug_dir / "memory").mkdir(parents=True)
    (slug_dir / "memory" / "pricing-note.md").write_text("# Pricing note\nTry annual plans\n", encoding="utf-8")
    (slug_dir / "memory" / "MEMORY.md").write_text("index", encoding="utf-8")
    (slug_dir / "a.jsonl").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))

    found = find_sessions(project)
    ids = {s["id"] for s in found["sources"]}
    assert ids == {"plan:ours.md", f"memory:{slug_dir.name}/pricing-note.md"}
    assert found["transcripts"] == 1
    label, text = read_source(project, "plan:ours.md")
    assert "export CSV" in text
    with pytest.raises((PermissionError, FileNotFoundError)):
        read_source(project, "plan:theirs.md")
    with pytest.raises((PermissionError, FileNotFoundError)):
        read_source(project, "plan:../secrets.md")


@pytest.mark.integration
async def test_fabric_routes_end_to_end(with_ideas: Path, llm) -> None:
    pytest.importorskip("aiohttp")
    from aiohttp import web
    from aiohttp.test_utils import TestClient, TestServer

    from navig.gateway.deck.routes import projects as routes
    from navig.projects.model import add_project

    answers, _seen = llm
    pid = add_project(with_ideas)["id"]
    app = web.Application()
    routes.register(app)
    async with TestClient(TestServer(app)) as client:
        res = await client.get(f"/api/deck/projects/{pid}/ideas")
        state = (await res.json())["data"]
        dark = next(i for i in state["ideas"] if i["text"].startswith("Dark"))

        answers.append("## Problem\nToo bright at night.\n")
        res = await client.post(f"/api/deck/projects/{pid}/patterns/run",
                                json={"pattern": "expand", "line": dark["line"], "expect": dark["fingerprint"]})
        body = await res.json()
        assert res.status == 200, body
        run = body["data"]
        assert run["kind"] == "pattern" and run["output"]["kind"] == "doc"
        assert not (with_ideas / ".navig/plans/ideas").exists()  # a proposal writes nothing

        res = await client.post(f"/api/deck/projects/{pid}/ideas/brief",
                                json={"line": dark["line"], "expect": dark["fingerprint"],
                                      "markdown": run["output"]["markdown"]})
        assert res.status == 200
        assert (with_ideas / ".navig/plans/ideas/dark-mode-for-the-storefront.md").is_file()

        res = await client.post(f"/api/deck/projects/{pid}/patterns/run", json={"pattern": "harvest"})
        assert res.status == 400  # a harvest needs sources
        res = await client.post(f"/api/deck/projects/{pid}/ideas/move",
                                json={"line": dark["line"], "stage": "nope"})
        assert res.status == 400
        res = await client.get(f"/api/deck/projects/{pid}/sessions")
        assert res.status == 200 and "runs" in (await res.json())["data"]


def _link_dir(link: Path, target: Path) -> bool:
    """A directory link without admin rights: a junction on Windows, a symlink elsewhere."""
    import os
    import sys

    try:
        if sys.platform == "win32":
            import _winapi

            _winapi.CreateJunction(str(target), str(link))
        else:
            os.symlink(target, link, target_is_directory=True)
        return True
    except (OSError, AttributeError):
        return False


def test_a_root_plans_link_does_not_list_every_plan_twice(project: Path, tmp_path: Path) -> None:
    """Schema keeps `plans/` at the root as a link to `.navig/plans/` — measured: every plan twice."""
    from navig.projects.docs import index_docs
    from navig.projects.todos import extract_todos

    if not _link_dir(project / "plans", project / ".navig" / "plans"):
        pytest.skip("cannot create a directory link here")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.md").write_bytes(b"# Not this project\n")
    linked_out = _link_dir(project / ".navig" / "plans" / "elsewhere", outside)

    paths = [d["path"] for d in index_docs(project)]
    assert paths.count(".navig/plans/DEV_PLAN.md") == 1
    assert "plans/DEV_PLAN.md" not in paths
    assert len([t for t in extract_todos(project) if t["text"] == "Wire the checkout"]) == 1
    if linked_out:
        assert not any("secret" in p for p in paths)  # a link must not lead the index OUT


def test_cli_ideas_and_fabric(with_ideas: Path, llm) -> None:
    from typer.testing import CliRunner

    from navig.commands.project import project_app
    from navig.projects.model import add_project

    pid = add_project(with_ideas)["id"]
    runner = CliRunner()
    res = runner.invoke(project_app, ["ideas", pid, "--add", "Partner API #b2b"])
    assert res.exit_code == 0, res.output
    res = runner.invoke(project_app, ["ideas", pid, "--json"])
    assert any(i["text"] == "Partner API #b2b" for i in json.loads(res.output)["ideas"])

    res = runner.invoke(project_app, ["fabric", "--project", pid])
    assert res.exit_code == 0 and "brainstorm" in res.output and "harvest" in res.output

    answers, _seen = llm
    answers.append(json.dumps({"ideas": [{"text": "Seller onboarding checklist", "why": "fewer drop-offs"}]}))
    res = runner.invoke(project_app, ["fabric", "brainstorm", "--project", pid, "--apply"])
    assert res.exit_code == 0, res.output
    assert "Seller onboarding checklist" in (with_ideas / ".navig/plans/IDEAS.md").read_text(encoding="utf-8")

    res = runner.invoke(project_app, ["fabric", "expand", "--project", pid, "--idea", "nothing-matches"])
    assert res.exit_code == 1
    res = runner.invoke(project_app, ["fabric", "harvest", "--project", pid])
    assert res.exit_code == 1  # no --sessions → refused, not an empty run
