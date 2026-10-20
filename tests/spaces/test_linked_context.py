"""Linked context: deny beats include, junctions cannot escape, budgets hold.

The contract under test:
- ``include`` picks, ``exclude`` drops, DEFAULT_INCLUDE applies when none given;
- the deny list wins over any include (vault / sessions / state / env / creds / db / log);
- containment is on RESOLVED paths — a real NTFS junction found under the root but
  pointing elsewhere is rejected (Windows; skipped where junctions cannot be made);
- freshest files win the per-Space cap; the total budget stops accumulation;
- identical bytes from two Spaces appear once (content-hash dedupe);
- ``gather_linked`` orders parent before links, collects unavailable targets with
  reasons, and passes ``parent_cycle`` through — never raising.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from navig.spaces import linked_context as lc
from navig.spaces import registry
from navig.spaces.relations import Relations, SpaceLink


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    f = tmp_path / "spaces.json"
    monkeypatch.setattr(registry, "_registry_file", lambda: f)
    return f


def _space(root: Path, sid: str) -> Path:
    p = root / f"{sid}-space"
    (p / ".navig").mkdir(parents=True, exist_ok=True)
    (p / ".navig" / "space.json").write_text(
        f'{{"id": "{sid}", "name": "{sid}", "version": "1.0.0"}}\n', encoding="utf-8"
    )
    return p


def _put(path: Path, text: str = "x", *, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return path


# ---------------------------------------------------------------------------
# Path policy
# ---------------------------------------------------------------------------


def test_deny_segments_and_basenames():
    assert lc.is_denied("vault/keys.md")
    assert lc.is_denied(".navig/sessions/s1.md")
    assert lc.is_denied(".navig/state/board.json")
    assert lc.is_denied("finance/q3.xlsx")   # segment rule — the extension is irrelevant
    assert lc.is_denied("medical/notes.md")  # segment list: finance/medical/contacts ride too
    assert lc.is_denied(".env")
    assert lc.is_denied("app/.env.local")
    assert lc.is_denied("config/credentials.yaml")
    assert lc.is_denied("cache/data.db")
    assert lc.is_denied("run.log")
    assert lc.is_denied("notes.md") is False  # ordinary knowledge flows


def test_containment_rejects_paths_outside_the_root(tmp_path):
    root = tmp_path / "space"
    (root / ".navig").mkdir(parents=True)
    inside = _put(root / "in.md")
    outside = _put(tmp_path / "out.md")
    assert lc.contained(inside, root) is True
    assert lc.contained(outside, root) is False


# ---------------------------------------------------------------------------
# Candidate collection
# ---------------------------------------------------------------------------


def test_include_picks_exclude_drops(tmp_path):
    root = tmp_path / "s"
    _put(root / "plans" / "a.md", "A")
    _put(root / "plans" / "b.md", "B")
    _put(root / "other" / "c.txt", "C")

    got = lc.collect_candidates("s", root, include=("plans/*.md",), exclude=("plans/b.md",))
    assert [i.rel for i in got] == ["plans/a.md"]


def test_default_include_applies_when_none_given(tmp_path):
    root = tmp_path / "s"
    _put(root / "README.md")
    _put(root / "wiki" / "deep.md")  # *.md crosses / (fnmatch) — knowledge surface
    _put(root / "src" / "main.py")  # not knowledge — stays out by default

    rels = {i.rel for i in lc.collect_candidates("s", root)}
    assert "README.md" in rels
    assert "wiki/deep.md" in rels
    assert "src/main.py" not in rels


def test_deny_beats_include(tmp_path):
    root = tmp_path / "s"
    _put(root / "vault" / "plan.md")          # denied segment, matches include
    _put(root / ".navig" / "sessions" / "s.md")
    _put(root / ".navig" / "state" / "x.md")
    _put(root / ".env")                        # include "*" below still denies
    _put(root / "db" / "cache.sqlite")
    _put(root / "run.log")
    _put(root / "keep" / "plan.md")            # the one that should survive

    got = lc.collect_candidates("s", root, include=("*",))
    assert [i.rel for i in got] == ["keep/plan.md"], f"deny leaked: {[i.rel for i in got]}"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_a_junction_that_escapes_the_root_is_rejected(tmp_path):
    root = tmp_path / "space"
    (root / ".navig").mkdir(parents=True)
    outside = tmp_path / "elsewhere"
    _put(outside / "secret.md", "do not leak")
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(root / "linked"), str(outside)], capture_output=True, check=False
    )
    if made.returncode != 0:
        pytest.skip(f"could not create a junction: {made.stderr!r}")

    got = lc.collect_candidates("s", root, include=("*",))
    assert got == [], f"a junction escape leaked into context: {[i.rel for i in got]}"


def test_freshest_win_the_item_cap(tmp_path):
    root = tmp_path / "s"
    for i in range(5):
        _put(root / f"n{i}.md", mtime=1_600_000_000 + i * 1000)

    got = lc.collect_candidates("s", root, include=("*.md",), budget=lc.ContextBudget(max_items_per_space=2))
    assert [i.rel for i in got] == ["n4.md", "n3.md"]  # newest first, then capped


def test_total_budget_stops_accumulation(tmp_path, monkeypatch):
    """charge = min(size, per_source); once total is full, later Spaces contribute nothing."""
    _isolate(tmp_path, monkeypatch)
    own = _space(tmp_path, "alpha")
    b = _space(tmp_path, "beta")
    g = _space(tmp_path, "gamma")
    for sid in ("alpha", "beta", "gamma"):
        registry.register(tmp_path / f"{sid}-space", id=sid, name=sid.title())
    for i in range(4):  # 4 candidates × 500 chars in beta — UNIQUE content per file,
        # or content-hash dedupe eats three of them and the budget never fills
        _put(b / f"n{i}.md", f"{i:04d}" + ("y" * 496), mtime=1_600_000_000 + i)
    _put(g / "late.md", "late" + ("z" * 496))

    budget = lc.ContextBudget(max_chars_per_source=500, max_total_chars=1_000, max_items_per_space=10)
    rel = Relations(links=(SpaceLink(space="beta"), SpaceLink(space="gamma")))
    ctx = lc.gather_linked(own, relations=rel, budget=budget)

    assert ctx.chars_total == 1_000          # exactly two 500-char charges fit
    assert len(ctx.items) == 2               # beta offered 4 — only 2 admitted
    assert {i.space for i in ctx.items} == {"beta"}
    assert all(i.space != "gamma" for i in ctx.items), "budget full — gamma must not sneak in"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------


def test_gather_orders_parent_then_links_and_reports_unavailable(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    own = _space(tmp_path, "alpha")
    parent = _space(tmp_path, "beta")
    linked = _space(tmp_path, "gamma")
    for sid in ("alpha", "beta", "gamma"):
        registry.register(tmp_path / f"{sid}-space", id=sid, name=sid.title())
    _put(parent / "p.md", "from parent")
    _put(linked / "l.md", "from link")

    rel = Relations(parent="beta", links=(SpaceLink(space="gamma"), SpaceLink(space="ghost"),))
    ctx = lc.gather_linked(own, relations=rel)

    assert [i.source for i in ctx.items] == ["parent", "link"]
    assert [i.space for i in ctx.items] == ["beta", "gamma"]
    assert ctx.unavailable == [("ghost", "unknown-id")]
    assert ctx.parent_cycle is False
    assert ctx.chars_total > 0


def test_identical_bytes_from_two_spaces_appear_once(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    own = _space(tmp_path, "alpha")
    b = _space(tmp_path, "beta")
    g = _space(tmp_path, "gamma")
    for sid in ("alpha", "beta", "gamma"):
        registry.register(tmp_path / f"{sid}-space", id=sid, name=sid.title())
    _put(b / "same.md", "identical content")
    _put(g / "same-copy.md", "identical content")
    _put(b / "unique.md", "only here")

    rel = Relations(links=(SpaceLink(space="beta"), SpaceLink(space="gamma")))
    ctx = lc.gather_linked(own, relations=rel)

    digests = [i.content_sha1 for i in ctx.items if i.content_sha1]
    assert len(digests) == len(set(digests)), "the same bytes were counted twice"
    assert len(ctx.items) == 2  # same.md + unique.md — the copy deduped away


def test_parent_cycle_is_flagged_not_followed(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    own = _space(tmp_path, "alpha")
    other = _space(tmp_path, "beta")
    registry.register(own, id="alpha", name="Alpha")
    registry.register(other, id="beta", name="Beta")
    # Hand-write the cycle the write path refuses: alpha -> beta -> alpha
    import json

    for sid, parent_id in (("alpha", "beta"), ("beta", "alpha")):
        p = tmp_path / f"{sid}-space" / ".navig" / "space.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["relations"] = {"parent": parent_id}
        p.write_text(json.dumps(data), encoding="utf-8")

    rel = Relations(parent="beta")
    ctx = lc.gather_linked(own, relations=rel)
    assert ctx.parent_cycle is True
    # The parent's FILES still flow once (available) — the cycle is reported, not followed.
    assert all(i.space == "beta" for i in ctx.items)
