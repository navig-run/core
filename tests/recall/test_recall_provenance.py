"""Provenance substrate: git observations, lifecycle derivation rules, worktree folding
and commit-prefix matching — the deterministic core of the memory engine.

The derivation table runs against a FAKE probe (pure, no I/O); a real temp git repository
proves the git layer itself (ancestor verdicts, metadata-only rows, graceful ``None``
everywhere git cannot answer).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from navig.recall import gitprov
from navig.recall.lifecycle import GitProbe, derive
from navig.recall.model import Evidence, Record
from navig.recall.projects import ProjectResolver
from navig.recall.session_links import LINKS_VERSION, target_key
from navig.recall.store import RecallStore

ALPHA = r"E:\proj\alpha"
HAS_GIT = shutil.which("git") is not None

pytestmark = pytest.mark.skipif(not HAS_GIT, reason="git is required for provenance tests")


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return out.stdout


@pytest.fixture
def repo(tmp_path: Path) -> dict[str, str]:
    """A real repo: commit A on main, commit B on an unmerged feature branch."""
    r = tmp_path / "gitrepo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    (r / "one.txt").write_text("one\n", encoding="utf-8")
    _git(r, "add", "one.txt")
    _git(r, "commit", "-q", "-m", "first")
    a = _git(r, "rev-parse", "HEAD").strip()
    _git(r, "checkout", "-q", "-b", "feature")
    (r / "two.txt").write_text("two\n", encoding="utf-8")
    _git(r, "add", "two.txt")
    _git(r, "commit", "-q", "-m", "second")
    b = _git(r, "rev-parse", "HEAD").strip()
    _git(r, "checkout", "-q", "main")
    return {"root": str(r), "merged": a, "unmerged": b}


# ── gitprov against a real repository ───────────────────────


def test_gitprov_reads_metadata_and_verdicts(repo) -> None:
    root = repo["root"]
    meta = gitprov.commit_meta(root, repo["merged"])
    assert meta is not None and meta["subject"] == "first"
    assert meta["author"] == "t" and meta["author_ts"]
    assert [f["path"] for f in meta["files"]] == ["one.txt"]
    # metadata only: names and status, never content
    assert all(set(f) <= {"status", "path", "old_path"} for f in meta["files"])
    assert gitprov.is_ancestor(root, repo["merged"], "main") is True
    assert gitprov.is_ancestor(root, repo["unmerged"], "main") is False
    assert gitprov.default_branch(root) == "main"
    tl = gitprov.toplevel(root)
    assert tl and Path(tl).resolve() == Path(root).resolve()
    assert gitprov.commits_touching(root, "one.txt") == [repo["merged"]]
    assert gitprov.commits_touching(root, "two.txt") == []
    # a sha git cannot see → None/False, not an exception
    assert gitprov.commit_meta(root, "f" * 40) is None
    assert gitprov.commit_exists(root, "f" * 40) is False


def test_gitprov_degrades_outside_a_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # pytest's basetemp lives INSIDE this repo (.dev/tmp), so git would walk up to the
    # worktree root — a ceiling makes "plain" honestly not-a-repository.
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", tmp_path.as_posix())
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitprov.toplevel(plain) is None
    assert gitprov.default_branch(plain) is None
    assert gitprov.commit_meta(plain, "a" * 40) is None
    # git runs but exits 128 ("not a repository") — that is NO verdict, not "False"
    assert gitprov.is_ancestor(plain, "a" * 40, "main") is None
    assert gitprov.commit_exists(plain, "a" * 40) is None
    assert gitprov.commits_touching(plain, "x.py") == []


def test_git_probe_caches_and_reports_rows(repo) -> None:
    probe = GitProbe(repo["root"])
    assert probe.available and probe.branch == "main"
    meta, merged = probe.examine(repo["merged"])
    assert meta is not None and merged is True
    meta2, merged2 = probe.examine(repo["unmerged"])
    assert meta2 is not None and merged2 is False
    unknown, merged_unknown = probe.examine("f" * 40)
    assert unknown is None and merged_unknown is None
    # second call is cached (same objects)
    assert probe.examine(repo["merged"])[0] is meta
    rows = probe.rows(project="alpha")
    by_sha = {r["sha"]: r for r in rows}
    assert by_sha[repo["merged"]]["merged"] == "yes"
    assert by_sha[repo["unmerged"]]["merged"] == "no"
    assert by_sha[repo["merged"]]["project"] == "alpha"
    assert by_sha[repo["merged"]]["git_root"]


def test_git_probe_without_root_is_unavailable() -> None:
    probe = GitProbe(None)
    assert probe.available is False
    assert probe.examine("a" * 40) == (None, None)
    assert probe.rows() == []


# ── lifecycle derivation table (pure; fake probe) ───────────


class _FakeProbe:
    def __init__(self, table: dict[str, tuple[dict | None, bool | None]], available: bool = True):
        self.table = table
        self.root = "fake"
        self._available = available

    @property
    def available(self) -> bool:
        return self._available

    def examine(self, sha: str):
        return self.table.get(sha, (None, None))


def _rec(**kw) -> Record:
    kw.setdefault("id", "rk-x")
    kw.setdefault("type", "feature")
    kw.setdefault("title", "The feature")
    return Record(**kw)


_SHA = "a" * 12
_META = {"sha": _SHA, "subject": "s", "author": "t", "author_ts": "2026-01-01",
         "committer_ts": "2026-01-01", "files": []}


@pytest.mark.parametrize(
    "name,rec,commits,touched,events,superseded,probe,want",
    [
        ("deploy event wins", _rec(status="implemented", status_basis="user"),
         [f"commit:{_SHA}"], True, [{"kind": "deployed"}], False,
         _FakeProbe({_SHA: (_META, True)}), ("deployed", "observed")),
        ("abandon event", _rec(), [], False, [{"kind": "abandoned"}], False, None,
         ("abandoned", "observed")),
        ("failed event", _rec(), [], False, [{"kind": "failure"}], False, None,
         ("failed", "observed")),
        ("status superseded", _rec(status="superseded"), [], False, [], False, None,
         ("superseded", "observed")),
        ("incoming supersedes link", _rec(status="confirmed"), [], False, [], True, None,
         ("superseded", "observed")),
        ("claim-built but verify says absent", _rec(status="implemented", status_basis="assistant",
                                                   impl="not_found"),
         [], False, [], False, None, ("failed", "inferred")),
        ("impl verified", _rec(impl="verified"), [], False, [], False, None,
         ("verified", "observed")),
        ("merged never implies deployed", _rec(status="implemented", status_basis="user"),
         [f"commit:{_SHA}"], True, [], False, _FakeProbe({_SHA: (_META, True)}),
         ("merged", "observed")),
        ("commit git sees, not merged", _rec(), [f"commit:{_SHA}"], False, [], False,
         _FakeProbe({_SHA: (_META, False)}), ("committed", "observed")),
        ("stale commit link is inferred", _rec(), [f"commit:{_SHA}"], False, [], False,
         _FakeProbe({_SHA: (None, None)}), ("committed", "inferred")),
        ("git down → inferred, never observed", _rec(), [f"commit:{_SHA}"], True, [], False,
         None, ("committed", "inferred")),
        ("touched but nothing committed", _rec(status="confirmed", status_basis="user"),
         [], True, [], False, None, ("implemented_uncommitted", "inferred")),
        ("proposal status", _rec(status="proposed"), [], False, [], False, None,
         ("proposed", "inferred")),
        # the attribution law: an agent's claim of "verified" is never an observed lifecycle
        ("agent claim never observed", _rec(status="verified", status_basis="assistant"),
         [], False, [], False, _FakeProbe({_SHA: (_META, True)}), ("unknown", "unknown")),
    ],
    ids=[c[0] for c in [
        ("deploy event wins",) * 8, ("abandon event",) * 8, ("failed event",) * 8,
        ("status superseded",) * 8, ("incoming supersedes link",) * 8,
        ("claim-built but verify says absent",) * 8, ("impl verified",) * 8,
        ("merged never implies deployed",) * 8, ("commit git sees, not merged",) * 8,
        ("stale commit link is inferred",) * 8, ("git down → inferred, never observed",) * 8,
        ("touched but nothing committed",) * 8, ("proposal status",) * 8,
        ("agent claim never observed",) * 8,
    ]],
)
def test_lifecycle_derivation_table(name, rec, commits, touched, events, superseded, probe, want) -> None:
    got = derive(rec, commits=commits, touched=touched, events=events,
                 superseded=superseded, probe=probe)
    assert got == want, name


# ── worktree folding (all three conventions) ────────────────


@pytest.fixture
def resolver() -> ProjectResolver:
    return ProjectResolver(entries=[("alpha", ALPHA), ("beta", r"E:\proj\beta")],
                           home=r"C:\Users\someone")


def test_navig_worktrees_folds_to_the_project(resolver) -> None:
    ref = resolver.resolve(r"E:\proj\alpha\.navig_worktrees\wt1\core\mod.py")
    assert ref.id == "alpha" and ref.root.replace("\\", "/").lower() == "e:/proj/alpha"


def test_navig_worktrees_files_share_the_main_checkout_key(resolver) -> None:
    assert target_key(r"E:\proj\alpha\.navig_worktrees\wt1\core\mod.py", resolver) == "file:alpha/core/mod.py"
    assert target_key(r"E:\proj\alpha\.dev\worktrees\feat-x\core\mod.py", resolver) == "file:alpha/core/mod.py"
    assert target_key(r"E:\proj\alpha\core\mod.py", resolver) == "file:alpha/core/mod.py"


def test_links_version_bumped_for_the_new_fold() -> None:
    assert LINKS_VERSION >= 3


def test_target_key_is_case_and_separator_stable(resolver) -> None:
    assert target_key(r"e:\PROJ\alpha\src\x.py", resolver) == "file:alpha/src/x.py"
    assert target_key(r"E:/proj/alpha/src/x.py", resolver) == "file:alpha/src/x.py"
    assert target_key(r"E:\proj\alpha\src\..\core\y.py", resolver) == "file:alpha/core/y.py"
    assert target_key(r"E:\elsewhere\x.py", resolver) is None


# ── commit prefix matching (sessions_for) ───────────────────


def _store_with_commit_links(tmp_path: Path) -> tuple[RecallStore, str, str]:
    st = RecallStore(tmp_path / "links.db")
    long_sid, short_sid = "cc:11111111-1111-1111-1111-111111111111", "cc:22222222-2222-2222-2222-222222222222"
    st.upsert_source({"source_id": long_sid, "adapter": "claude_code", "project": "alpha",
                      "title": "long", "meta": {}})
    st.upsert_source({"source_id": short_sid, "adapter": "claude_code", "project": "alpha",
                      "title": "short", "meta": {}})
    st.add_links([(long_sid, "commit:abc123456789", "committed"),
                  (short_sid, "commit:def9876", "committed")])
    return st, long_sid, short_sid


def test_sessions_match_commit_prefixes_both_ways(tmp_path: Path) -> None:
    st, long_sid, short_sid = _store_with_commit_links(tmp_path)
    # 7-char query vs 12-char stored
    hits = st.sessions_for("commit:abc1234")
    assert [s["source_id"] for s, _ in hits] == [long_sid]
    # full 12-char query vs 12-char stored
    assert [s["source_id"] for s, _ in st.sessions_for("commit:abc123456789")] == [long_sid]
    # 12-char query vs 7-char stored (the vice-versa case)
    assert [s["source_id"] for s, _ in st.sessions_for("commit:def9876554433")] == [short_sid]
    # exact 7-char vs 7-char stored
    assert [s["source_id"] for s, _ in st.sessions_for("commit:def9876")] == [short_sid]
    # a different sha matches nothing
    assert st.sessions_for("commit:000000000000") == []
    # non-commit keys stay exact
    assert st.sessions_for("file:alpha/none.py") == []


# ── evidence locator round-trip ─────────────────────────────


def test_evidence_locator_round_trips_through_vault(tmp_path: Path) -> None:
    from navig.recall.vault import parse, render  # noqa: PLC0415

    rec = Record(id="rk-loc", type="decision", title="From a file", project="alpha",
                 lifecycle="committed",
                 attrs={"lifecycle_basis": "observed"},
                 evidence=[Evidence(source_id="changelog:grabnite", msg_id="L10-14",
                                    speaker="imported_doc", excerpt="the entry",
                                    locator='{"lines": [10, 14], "file_sha256": "ab12"}')])
    back = parse(render(rec))
    assert back.lifecycle == "committed"
    assert back.attrs.get("lifecycle_basis") == "observed"
    assert back.evidence[0].locator == '{"lines": [10, 14], "file_sha256": "ab12"}'
    # store round-trip too
    st = RecallStore(tmp_path / "loc.db")
    st.upsert_record(rec)
    got = st.get_record("rk-loc")
    assert got is not None and got.evidence[0].locator.startswith("{")
    assert got.lifecycle == "committed"
