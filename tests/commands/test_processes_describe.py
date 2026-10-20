"""`navig procs` / `kill --dry-run` enrichment: project, kind, owner, detached.

Regression for 2026-10-03: a lagging box had ~100 node.exe and nothing in navig
could say which project each came from — `kill node --dry-run` printed pid+name
only, so the operator was left to guess which dev servers were leftovers.
"""

from __future__ import annotations

import os
import sys

import pytest

from navig.commands import processes as P


@pytest.fixture(autouse=True)
def _clear_cache():
    P.repo_label.cache_clear()
    yield
    P.repo_label.cache_clear()


# ── project_of ───────────────────────────────────────────────────────────────


def test_project_of_checkout_and_subdir(tmp_path):
    repo = tmp_path / "schema"
    (repo / ".git").mkdir(parents=True)
    (repo / "apps" / "webapp").mkdir(parents=True)
    assert P.project_of(str(repo)) == "schema"
    assert P.project_of(str(repo / "apps" / "webapp")) == "schema"


def test_project_of_linked_worktree_reads_repo_colon_slug(tmp_path):
    main = tmp_path / "schema"
    (main / ".git" / "worktrees" / "scene-studio2").mkdir(parents=True)
    wt = main / ".dev" / "worktrees" / "scene-studio2"
    (wt / "apps" / "api").mkdir(parents=True)
    (wt / ".git").write_text(
        f"gitdir: {main / '.git' / 'worktrees' / 'scene-studio2'}\n", encoding="utf-8"
    )
    assert P.project_of(str(wt / "apps" / "api")) == "schema:scene-studio2"


def test_project_of_worktree_outside_repo_still_named_after_main(tmp_path):
    main = tmp_path / "navig"
    (main / ".git" / "worktrees" / "hotfix").mkdir(parents=True)
    wt = tmp_path / "elsewhere" / "hotfix"
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'hotfix'}", encoding="utf-8")
    assert P.project_of(str(wt)) == "navig:hotfix"


def test_project_of_no_repo_falls_back_to_folder():
    # pytest's basetemp lives inside this repo, so use a path off the filesystem root.
    d = os.path.join(os.path.abspath(os.sep), "navig-test-no-such-dir", "scratch")
    assert P.project_of(d) == "scratch"
    assert P.project_of("") == ""


def test_project_from_cmdline_when_cwd_is_unreadable(tmp_path):
    # Windows: an elevated parent's child exposes its cmdline but not its cwd.
    main = tmp_path / "schema"
    (main / ".git" / "worktrees" / "search-map").mkdir(parents=True)
    wt = main / ".dev" / "worktrees" / "search-map"
    shim = wt / "apps" / "webapp" / "node_modules" / ".bin"
    shim.mkdir(parents=True)
    (wt / ".git").write_text(
        f"gitdir: {main / '.git' / 'worktrees' / 'search-map'}", encoding="utf-8"
    )
    off_repo = os.path.join(os.path.abspath(os.sep), "navig-test-no-such-dir", "npx-cli.js")
    cmd = ["node.exe", off_repo, str(shim / ".." / "next" / "dist" / "bin" / "next"), "dev"]
    assert P.project_from_cmdline(cmd) == "schema:search-map"


def test_project_from_cmdline_ignores_paths_outside_repos():
    off_repo = os.path.join(os.path.abspath(os.sep), "navig-test-no-such-dir", "npx-cli.js")
    assert P.project_from_cmdline(["node.exe", off_repo, "next", "dev", "-p", "3000"]) == ""
    assert P.project_from_cmdline([]) == ""


# ── process_kind ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cmdline,expected",
    [
        (
            [
                "node.exe",
                r"E:\s\node_modules\.pnpm\next@16.3.3_x\node_modules\next\dist\server\lib\start-server.js",
            ],
            "next",
        ),
        (
            ["node", "node_modules/.bin/../@cloudflare/wrangler/bin/wrangler.js", "dev"],
            "@cloudflare/wrangler",
        ),
        (["node", r"E:\s\apps\webapp\node_modules\.bin\\..\next\dist\bin\next", "dev"], "next"),
        (
            ["node.exe", "--require", "ts-node/register/transpile-only", "src/schema-app.ts"],
            "schema-app",
        ),
        (["python.exe", "C:/x/.dev/tools/lyrics_find.py", "--all"], "lyrics_find"),
        (["python", "-m", "navig", "daemon"], "navig"),
        (["python", "-m", "pytest", "-q"], "pytest"),
        (["claude.exe", "--output-format", "stream-json", "--resume=abc", "--debug"], "claude"),
        (["node.exe", "--inspect", "server.js"], "server"),
        (["node.exe"], "node"),
        ([], ""),
    ],
)
def test_process_kind(cmdline, expected):
    assert P.process_kind(cmdline) == expected


# ── owner_of ─────────────────────────────────────────────────────────────────


_TABLE = {  # pid: (name, ppid)
    1: ("init", 0),
    10: ("vscodium", 1),
    20: ("claude", 10),
    30: ("bash", 20),
    40: ("node", 30),
    50: ("node", 99),  # parent 99 is gone -> detached
    70: ("cmd", 1),
    60: ("node", 70),  # "parent" 70 was created AFTER it: recycled pid -> detached
    80: ("node", 81),  # a pid cycle must terminate
    81: ("node", 80),
}
_CT = {
    1: 1.0,
    10: 100.0,
    20: 200.0,
    30: 300.0,
    40: 400.0,
    50: 500.0,
    60: 600.0,
    70: 900.0,
    80: 800.0,
    81: 800.0,
}


def test_owner_is_nearest_session():
    assert P.owner_of(40, _TABLE, dict(_CT)) == ("claude[20]", False)


def test_dead_parent_is_detached():
    assert P.owner_of(50, _TABLE, dict(_CT)) == ("", True)


def test_recycled_parent_pid_is_detached():
    assert P.owner_of(60, _TABLE, dict(_CT)) == ("", True)


def test_unknown_create_time_does_not_fake_detachment():
    ct = dict(_CT)
    ct[70] = 0.0  # unreadable -> no recycle verdict, keep walking
    assert P.owner_of(60, _TABLE, ct) == ("", False)


def test_cycle_terminates():
    assert P.owner_of(80, _TABLE, dict(_CT)) == ("", False)


def test_unknown_pid():
    assert P.owner_of(12345, _TABLE, dict(_CT)) == ("", False)


@pytest.mark.skipif(P.psutil is None, reason="psutil not installed")
def test_ppid_map_matches_psutil_for_self():
    assert P._ppid_map()[os.getpid()] == P.psutil.Process().ppid()


# ── summarize_by_project ─────────────────────────────────────────────────────


def _row(pid, project, mb, kind="next", owner="", detached=False, readable=True):
    return {
        "pid": pid,
        "project": project,
        "mem_mb": mb,
        "kind": kind,
        "owner": owner,
        "detached": detached,
        "readable": readable,
    }


def test_summarize_groups_and_sorts_by_memory():
    rows = [
        _row(1, "schema", 100, owner="code[5]"),
        _row(2, "schema:scene-studio2", 7000, detached=True),
        _row(3, "schema", 200, kind="wrangler", owner="code[5]"),
        _row(4, "", 50, readable=False),
    ]
    g = P.summarize_by_project(rows)
    assert [x["project"] for x in g] == ["schema:scene-studio2", "schema", "<unreadable>"]
    schema = g[1]
    assert schema["count"] == 2 and schema["mem_mb"] == 300
    assert schema["owners"] == ["code[5]"]
    assert schema["kinds"] == {"next": 1, "wrangler": 1}
    assert g[0]["detached"] == 1


# ── describe_processes on the live table (this pytest process) ───────────────


@pytest.mark.skipif(P.psutil is None, reason="psutil not installed")
def test_describe_self_is_readable_with_memory_and_project():
    me = {"pid": os.getpid(), "name": "python", "cmdline": [sys.executable, "-m", "pytest"]}
    rows = P.describe_processes([me])
    assert len(rows) == 1
    r = rows[0]
    assert r["readable"] is True
    assert r["mem_mb"] > 0
    assert r["cwd"] == os.getcwd()
    assert r["kind"] == "pytest"


@pytest.mark.skipif(P.psutil is None, reason="psutil not installed")
def test_describe_skips_vanished_pid():
    assert P.describe_processes([{"pid": 2**31 - 7, "name": "ghost", "cmdline": []}]) == []


# ── idle Claude Code sessions ────────────────────────────────────────────────
# 2026-10-03: 56 claude.exe held 35 GB; 29 were 3-5-day-old leftovers. The
# classifier must reap those and nothing a person or agent is still using.

H = 3600.0
NOW = 1_000_000.0
SID_OLD = "11111111-1111-1111-1111-111111111111"
SID_FRESH = "22222222-2222-2222-2222-222222222222"
CWD = r"c:\Users\me\spaces\company-space"
DIR = P.encode_project_dir(CWD)


def _session(pid, *, sid="", age_h=100.0, cwd=CWD):
    cmd = ["claude.exe", "--output-format", "stream-json"] + ([f"--resume={sid}"] if sid else [])
    return {"pid": pid, "name": "claude.exe", "cmdline": cmd, "cwd": cwd, "age_s": int(age_h * H),
            "mem_mb": 500.0, "project": "company-space", "readable": True}


def _classify(rows, by_sid=None, by_dir=None, children=None, self_chain=frozenset(), hours=24):
    return {r["pid"]: r for r in P.classify_claude_sessions(
        rows, hours, now=NOW, index=(by_sid or {}, by_dir or {}),
        children=children or {}, self_chain=set(self_chain))}


def test_session_id_parsed_from_both_flag_spellings():
    assert P.session_id_of(["claude.exe", f"--resume={SID_OLD}"]) == SID_OLD
    assert P.session_id_of(["claude.exe", "--session-id", SID_OLD.upper()]) == SID_OLD
    assert P.session_id_of(["claude.exe", "--resume"]) == ""


def test_project_dir_encoding_matches_claude_code():
    assert P.encode_project_dir(r"c:\Users\me\.navig\spaces\homelab-space") == \
        "c--users-me--navig-spaces-homelab-space"


def test_stale_transcript_is_idle():
    r = _classify([_session(1, sid=SID_OLD)], by_sid={SID_OLD: NOW - 90 * H})[1]
    assert r["verdict"] == "idle" and r["idle_h"] == 90


def test_recent_transcript_is_active():
    r = _classify([_session(1, sid=SID_FRESH)], by_sid={SID_FRESH: NOW - 2 * H})[1]
    assert r["verdict"] == "active"


def test_running_tool_is_active_however_stale():
    r = _classify([_session(1, sid=SID_OLD)], by_sid={SID_OLD: NOW - 90 * H},
                  children={1: ["conhost", "bash"]})[1]
    assert r["verdict"] == "active" and "bash" in r["reason"]


def test_console_host_alone_does_not_count_as_busy():
    r = _classify([_session(1, sid=SID_OLD)], by_sid={SID_OLD: NOW - 90 * H},
                  children={1: ["conhost"]})[1]
    assert r["verdict"] == "idle"


def test_reopened_tab_is_young_never_idle_even_if_transcript_is_old():
    # Resumed 2h ago, user has not typed yet: too young to judge, so never reaped —
    # and NOT called "active" either, which would claim activity from age alone.
    r = _classify([_session(1, sid=SID_OLD, age_h=2)], by_sid={SID_OLD: NOW - 90 * H})[1]
    assert r["verdict"] == "young"
    assert r["idle_h"] == 2.0, "idle time is capped by process age"
    assert "under" in r["reason"] and "transcript written" in r["reason"]


def test_missing_transcript_is_unknown_not_idle():
    assert _classify([_session(1, sid=SID_OLD)])[1]["verdict"] == "unknown"


def test_no_id_and_quiet_project_is_idle():
    r = _classify([_session(1)], by_dir={DIR: [("old", NOW - 80 * H)]})[1]
    assert r["verdict"] == "idle"


def test_no_id_with_unclaimed_recent_transcript_is_unknown():
    # That fresh transcript could be this very session's: never guess.
    r = _classify([_session(1)], by_dir={DIR: [("someone", NOW - 1 * H)]})[1]
    assert r["verdict"] == "unknown"


def test_recent_transcript_owned_by_another_live_session_does_not_block():
    rows = [_session(1), _session(2, sid=SID_FRESH)]
    out = _classify(rows, by_sid={SID_FRESH: NOW - 1 * H}, by_dir={DIR: [(SID_FRESH, NOW - 1 * H)]})
    assert out[1]["verdict"] == "idle" and out[2]["verdict"] == "active"


def test_no_id_and_unreadable_cwd_is_unknown():
    assert _classify([_session(1, cwd="")])[1]["verdict"] == "unknown"


def test_own_session_is_never_idle():
    r = _classify([_session(1, sid=SID_OLD)], by_sid={SID_OLD: NOW - 90 * H}, self_chain={1})[1]
    assert r["verdict"] == "self"


def test_threshold_is_configurable():
    rows = [_session(1, sid=SID_OLD)]
    assert _classify(rows, by_sid={SID_OLD: NOW - 30 * H}, hours=24)[1]["verdict"] == "idle"
    rows = [_session(1, sid=SID_OLD)]
    assert _classify(rows, by_sid={SID_OLD: NOW - 30 * H}, hours=48)[1]["verdict"] == "active"


def test_transcript_index_reads_every_config_dir(tmp_path):
    a = tmp_path / ".claude" / "projects" / DIR
    b = tmp_path / ".claude-second" / "projects" / DIR
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / f"{SID_OLD}.jsonl").write_text("{}\n", encoding="utf-8")
    (b / f"{SID_FRESH}.jsonl").write_text("{}\n", encoding="utf-8")
    by_sid, by_dir = P.transcript_index([tmp_path / ".claude", tmp_path / ".claude-second"])
    assert set(by_sid) == {SID_OLD, SID_FRESH}
    assert {s for s, _ in by_dir[DIR]} == {SID_OLD, SID_FRESH}


def test_claude_idle_is_a_known_group():
    assert "claude-idle" in P.list_known_groups()


# ── roles + machine view ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "role"),
    [
        ("ci-local", "CI gate"),
        ("ci-local-selection.test", "CI gate"),
        ("heavy-slot", "CI gate"),
        ("jest-worker", "test runner"),
        ("broadcast.test", "test runner"),
        ("x.spec.ts", "test runner"),
        ("typescript", "TypeScript"),
        ("tsx", "TypeScript"),
        ("wrangler", "dev server"),
        ("next", "dev server"),
        ("corepack", "package manager"),
        ("npm", "package manager"),
        ("claude", "agent"),
        ("some-new-tool", "some-new-tool"),  # an unknown kind is shown as itself, never "other"
        ("", "unknown"),
    ],
)
def test_role_of(kind, role):
    assert P.role_of(kind) == role


def test_summarize_by_role_groups_and_counts():
    rows = [
        {"pid": 1, "kind": "ci-local", "mem_mb": 100.0, "project": "navig:a", "detached": True},
        {"pid": 2, "kind": "ci-push", "mem_mb": 50.0, "project": "navig:b", "detached": False},
        {"pid": 3, "kind": "typescript", "mem_mb": 1000.0, "project": "schema", "detached": False},
    ]
    g = P.summarize_by_role(rows)
    assert [x["role"] for x in g] == ["TypeScript", "CI gate"], "biggest memory first"
    gate = g[1]
    assert gate["count"] == 2 and gate["mem_mb"] == 150.0 and gate["detached"] == 1
    assert gate["projects"] == ["navig:a", "navig:b"]
    assert gate["kinds"] == {"ci-local": 1, "ci-push": 1}


def test_machine_summary_shape_and_ranking():
    m = P.machine_summary(top=3, cpu_interval=0)
    for key in ("cpu_percent", "ram_total_gb", "ram_free_gb", "commit_used_gb", "commit_total_gb", "processes", "families"):
        assert key in m
    assert m["processes"] > 0, "the test process itself must be counted"
    fams = m["families"]
    assert 0 < len(fams) <= 3
    assert fams == sorted(fams, key=lambda f: -f["private_gb"]), "families ranked by private memory"


# ── Chrome channels + extensions-only ────────────────────────────────────────


@pytest.mark.parametrize(
    "exe,channel",
    [
        (r"C:\Program Files\Google\Chrome\Application\chrome.exe", "stable"),
        (r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe", "beta"),
        (r"C:\Program Files\Google\Chrome Dev\Application\chrome.exe", "dev"),
        (r"C:\Users\me\AppData\Local\Google\Chrome SxS\Application\chrome.exe", "canary"),
        ("/usr/lib/chromium/chromium", "chromium"),
    ],
)
def test_chrome_channel_from_install_folder(exe, channel):
    assert P.chrome_channel([exe, "--type=renderer"]) == channel


def _renderer(pid, exe, *, ext):
    cmd = [exe, "--type=renderer"] + (["--extension-process"] if ext else [])
    return {"pid": pid, "name": "chrome.exe", "cmdline": cmd}


def test_extensions_only_spares_tabs_and_filters_channels(monkeypatch):
    stable = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    beta = r"C:\Program Files\Google\Chrome Beta\Application\chrome.exe"
    dev = r"C:\Program Files\Google\Chrome Dev\Application\chrome.exe"
    fake = [
        _renderer(1, stable, ext=True), _renderer(2, stable, ext=False),
        _renderer(3, beta, ext=True), _renderer(4, dev, ext=True), _renderer(5, dev, ext=False),
    ]
    monkeypatch.setattr(
        P, "find_chrome_renderers",
        lambda include_extensions=False, include_automation=False: [dict(f) for f in fake],
    )
    assert sorted(p["pid"] for p in P.find_chrome_extensions()) == [1, 3, 4]
    assert sorted(p["pid"] for p in P.find_chrome_extensions({"beta", "dev"})) == [3, 4]
    assert P.find_processes_by_group("chrome-extensions")[0]["channel"] == "stable"
    assert "chrome-extensions" in P.list_known_groups()


class _FakeProc:
    def __init__(self, pid, cmd):
        self.pid = pid
        self.info = {"name": "chrome.exe", "cmdline": cmd}

    def cmdline(self):
        return self.info["cmdline"]

    def username(self):
        return ""

    def create_time(self):
        return 0.0

    def name(self):
        return "chrome.exe"


@pytest.mark.skipif(P.psutil is None, reason="psutil not installed")
def test_automation_browsers_are_spared_by_default(monkeypatch):
    exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    procs = [
        _FakeProc(1, [exe, "--type=renderer"]),  # the operator's tab
        _FakeProc(2, [exe, "--type=renderer", "--extension-process"]),  # the operator's extension
        _FakeProc(3, [exe, "--type=renderer", r"--user-data-dir=C:\Temp\cdp-profile-x1"]),  # a harness tab
        _FakeProc(4, [exe, "--type=renderer", "--extension-process", r"--user-data-dir=C:\x\.navig\cdp-profiles\s"]),
    ]
    monkeypatch.setattr(P.psutil, "process_iter", lambda attrs=None: iter(procs))
    assert sorted(p["pid"] for p in P.find_chrome_renderers(include_extensions=True)) == [1, 2]
    assert sorted(p["pid"] for p in P.find_chrome_renderers(True, include_automation=True)) == [1, 2, 3, 4]
    assert [p["pid"] for p in P.find_chrome_extensions()] == [2]
