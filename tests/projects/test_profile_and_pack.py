"""Profile (scripts across workspaces, danger rules) and the budgeted context pack."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.projects import context_pack, profile


def test_scripts_from_root_and_workspace_packages(project: Path) -> None:
    p = profile.build_profile(project)
    keys = {s["key"] for s in p["scripts"]}
    assert "npm:.:dev" in keys
    assert "npm:apps/web:dev" in keys and "npm:apps/web:lint" in keys
    web_dev = profile.find_script(p, "npm:apps/web:dev")
    assert web_dev["run"] == "pnpm run dev" and web_dev["long_running"] is True
    assert p["packages"] == [{"name": "@shop/web", "cwd": "apps/web", "scripts": 2}]


@pytest.mark.parametrize(("name", "command", "danger"), [
    ("deploy:prod", "wrangler deploy", True),
    ("deploy:status:prod", "wrangler deployments list", False),
    ("store:publish:dry", "node publish.js --dry", False),
    ("release", "", True),
    ("clean", "rimraf dist", True),      # destructive body
    ("clean", "tsc --build --clean", False),
    ("dev", "vite", False),
    ("build", "git push --force origin main", True),
])
def test_danger_rules(name: str, command: str, danger: bool) -> None:
    assert profile.is_danger(name, command) is danger


def test_pack_respects_every_budget_and_reports_it(project: Path) -> None:
    pack = context_pack.build_pack(project, name="shop")
    rep = pack["report"]
    assert rep["total_injected"] <= context_pack.TOTAL_BUDGET
    for src in rep["sources"]:
        assert src["injected_chars"] <= src["budget"]
    sources = {s["source"] for s in pack["sections"]}
    assert {"identity", "plans", "todos", "agent", "readme", "profile", "outline"} <= sources
    md = pack["markdown"]
    assert "Sell things fast." in md  # .navig/plans/VISION.md, not the legacy copy
    assert "Old roadmap at the root" not in md  # the .navig/plans copy wins
    assert "Never log card data." in md
    assert "TOKEN=hunter2" not in md  # the vault never reaches a prompt
    assert "src/main.py: class App, def run" in md
    assert "src/index.ts: function start, const VERSION" in md


def test_truncation_keeps_head_and_tail() -> None:
    text = "HEAD" + "x" * 5000 + "TAIL"
    out, cut = context_pack._truncate(text, 400)
    assert cut and out.startswith("HEAD") and out.endswith("TAIL") and len(out) <= 400
    assert "chars omitted" in out


def test_pack_is_cached_until_an_input_changes(project: Path) -> None:
    from navig.projects.store import get_project_store

    store = get_project_store()
    first = context_pack.build_pack(project, name="shop", store=store)
    assert first["cached"] is False
    again = context_pack.build_pack(project, name="shop", store=store)
    assert again["cached"] is True and again["fingerprint"] == first["fingerprint"]

    import os
    import time

    doc = project / ".navig" / "plans" / "VISION.md"
    doc.write_bytes(b"# Shop vision\n\nSell things faster.\n")
    later = time.time() + 5
    os.utime(doc, (later, later))
    rebuilt = context_pack.build_pack(project, name="shop", store=store)
    assert rebuilt["cached"] is False and "Sell things faster." in rebuilt["markdown"]


def test_pin_writes_its_own_file_and_never_a_hand_written_one(project: Path) -> None:
    from navig.projects.docs import DocError

    pack = context_pack.build_pack(project, name="shop")
    res = context_pack.pin_pack(project, pack)
    pinned = project / context_pack.PIN_FILE
    assert res["path"] == context_pack.PIN_FILE and "Sell things fast." in pinned.read_text(encoding="utf-8")
    context_pack.pin_pack(project, pack)  # re-pinning our own file is fine

    pinned.write_bytes(b"# my own notes\n")
    with pytest.raises(DocError):
        context_pack.pin_pack(project, pack)
    assert pinned.read_bytes() == b"# my own notes\n"
