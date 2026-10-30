"""`navig guard` — the ported watchdog logic, tested pure (no live processes).

Regression anchors: the PowerShell tasks these replace spent 25 days killing
orphans / runaway builds on the operator's box, and every protection rule in
them (session chain, never-kill, leases, recycled pids, young orphans) exists
because killing the wrong process cost real work at least once (2026-09-14:
the orphan sweep killed a navig daemon tree; 2026-10-03: runaway node pinned
the machine; stale leases must protect nothing or dev stacks die hourly).
"""

from __future__ import annotations

import collections
import json

from navig.commands import guard as G


def _table(*rows: tuple[str, str]) -> dict[int, tuple[str, int]]:
    """(pid, name, ppid) triples -> the snapshot table."""
    return {int(pid): (name, int(ppid)) for pid, name, ppid in rows}


NOW = 1_000_000.0


# ─── snapshot helpers ───────────────────────────────────────────────────────


def test_children_index_and_ancestors():
    table = _table((1, "system", 0), (10, "claude", 1), (20, "bash", 10), (21, "bash", 10))
    kids = G.children_index(table)
    assert sorted(kids[10]) == [20, 21]
    assert G.ancestors_of(20, table) == [20, 10, 1]
    # a cycle must terminate, not hang
    cyc = _table((5, "a", 6), (6, "b", 5))
    assert G.ancestors_of(5, cyc) == [5, 6]


def test_subtree_collects_descendants_that_are_live():
    table = _table((1, "bash", 99), (2, "node", 1), (3, "python", 2))
    out = G.subtree_of(1, G.children_index(table), set(table))
    assert sorted(out) == [1, 2, 3]
    assert G.subtree_of(1, G.children_index(table), {2}) == [2], "pids not in the snapshot drop out"


# ─── orphan classification ─────────────────────────────────────────────────


def _start_of(*pids: int, age_h: float = 2.0) -> dict[int, float]:
    return dict.fromkeys(pids, NOW - age_h * 3600)


def test_dev_tools_with_gone_parents_are_killed():
    # 99 = parent that no longer exists in the snapshot
    table = _table((10, "bash.exe", 99), (11, "node.exe", 10))
    kill, skipped = G.classify_orphans(
        table, {}, {}, min_age_min=30, start_of=_start_of(10, 11), now=NOW,
    )
    assert sorted(kill) == [10, 11], "the orphan shell AND its subtree die together"
    assert skipped == []


def test_non_dev_tools_and_live_parents_are_untouched():
    table = _table((10, "explorer", 0), (11, "svchost", 4), (12, "chrome", 10), (13, "bash", 12))
    kill, _ = G.classify_orphans(table, {}, {}, min_age_min=30, start_of=_start_of(10, 11, 12, 13), now=NOW)
    assert kill == [], "only devTool-named processes are candidates, and 13's parent lives"


def test_young_orphan_is_spared_and_unknown_age_is_not():
    table = _table((10, "sleep", 99), (11, "sleep", 98))
    start = _start_of(11, age_h=2.0)          # 10 has no create_time (access denied)
    start[10] = NOW - 60                      # 1 minute old -> spared
    kill, skipped = G.classify_orphans(table, {}, {}, min_age_min=30, start_of=start, now=NOW)
    assert kill == [11], "unknown age counts as old (as in the script); known-young is spared"
    assert skipped and "only 1 min old" in skipped[0][2]


def test_session_chain_is_spared_and_os_names_are_not_even_candidates():
    # `system` is never-kill, but also not in the dev-tool pattern — the same
    # redundancy the PS script carries; either gate alone keeps it alive.
    table = _table((10, "bash", 99), (11, "system", 99))
    kill, skipped = G.classify_orphans(
        table, {10: "session chain"}, {}, min_age_min=0, start_of=_start_of(10, 11), now=NOW,
    )
    assert kill == []
    reasons = {pid: why for pid, _name, why in skipped}
    assert reasons[10] == "session chain"
    assert 11 not in reasons, "non-dev-tool names are never candidates"


def test_lease_spared_with_reason():
    table = _table((10, "node", 99))
    kill, skipped = G.classify_orphans(
        table, {}, {10: "registered dev stack 'schema' (lease root 10)"},
        min_age_min=0, start_of=_start_of(10), now=NOW,
    )
    assert kill == [] and "dev stack" in skipped[0][2]


def test_an_orphan_root_holding_a_protected_descendant_is_not_killed():
    """kill_tree(root) takes the whole subtree, so skipping the protected child per-pid
    protected nothing: the orphan shell stayed on the kill list and took `claude` with it."""
    table = _table((10, "bash", 99), (11, "claude", 10), (12, "node", 10))
    kill, skipped = G.classify_orphans(
        table, {11: "navig pid-file root"}, {},
        min_age_min=0, start_of=_start_of(10, 11, 12), now=NOW,
    )
    assert 10 not in kill, "the shell's tree would kill the protected claude"
    assert kill == [12], "an unprotected sibling branch is still reaped on its own"
    reasons = {pid: why for pid, _name, why in skipped}
    assert reasons[11] == "navig pid-file root"
    assert "claude (11)" in reasons[10]


# ─── leases ─────────────────────────────────────────────────────────────────


def _lease(path, data: dict, *, mtime: float | None = None) -> str:
    import json
    from pathlib import Path

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data), encoding="utf-8")
    if mtime is not None:
        import os

        os.utime(path, (mtime, mtime))
    return str(path)


def test_live_lease_spares_root_child_and_launcher(tmp_path):
    table = _table((1, "pwsh", 99), (10, "node", 1), (11, "node", 10))
    f = _lease(tmp_path / "stack.json",
               {"pid": 10, "children": [11], "name": "my stack"},
               mtime=NOW - 3600)
    out = G.load_leases([tmp_path / "stack.json"], tmp_path / "nope", table,
                        {1: NOW - 7200, 10: NOW - 7200, 11: NOW - 7200},
                        G.children_index(table), now=NOW)
    assert out[10].startswith("registered dev stack 'my stack'")
    assert out[11].startswith("registered dev stack")
    assert out[1].startswith("launcher of registered dev stack")


def test_expired_and_dead_leases_protect_nothing(tmp_path):
    import json

    table = _table((10, "node", 99))
    start = {10: NOW - 7200}
    kids = G.children_index(table)
    exp = tmp_path / "expired.json"
    # NOW here is 1e6 (1970-01-12), so anything in January 1970 is long expired
    exp.write_text(json.dumps({"pid": 10, "expires": "1970-01-05T00:00:00Z"}), encoding="utf-8")
    dead = tmp_path / "dead.json"
    dead.write_text(json.dumps({"pid": 777777}), encoding="utf-8")   # not running
    garbage = tmp_path / "bad.json"
    garbage.write_text("{not json", encoding="utf-8")
    assert G.load_leases([exp, dead, garbage], None, table, start, kids, now=NOW) == {}


def test_recycled_pid_and_wrong_repo_lease_protect_nothing(tmp_path):
    import json

    table = _table((10, "node", 99))
    # recycled: root started AFTER the file was written
    rec = tmp_path / "recycled.json"
    rec.write_text(json.dumps({"pid": 10}), encoding="utf-8")
    # force mtime far behind the process start time
    import os

    os.utime(rec, (NOW - 999999, NOW - 999999))
    assert G.load_leases([rec], None, table, {10: NOW - 7200}, G.children_index(table), now=NOW) == {}
    # dev-run format: file outside <repo>\.dev\ of the repo it names
    wrong = tmp_path / "elsewhere" / "dev-run.pids.json"
    wrong.parent.mkdir()
    wrong.write_text(json.dumps({"pid": 10, "repo": r"E:\projects\apps\schema"}), encoding="utf-8")
    assert G.load_leases([wrong], None, table, {10: NOW - 7200}, G.children_index(table), now=NOW) == {}


def test_dev_run_lease_honoured_in_its_own_repo(tmp_path):
    import json

    repo = tmp_path / "schema"
    (repo / ".dev").mkdir(parents=True)
    table = _table((10, "node", 99))
    start = {10: NOW - 7200}
    f = repo / ".dev" / "dev-run.pids.json"
    f.write_text(json.dumps({"pid": 10, "repo": str(repo)}), encoding="utf-8")
    out = G.load_leases([f], None, table, start, G.children_index(table), now=NOW)
    assert out.get(10, "").startswith("registered dev stack")


def test_glob_lease_files_are_expanded(tmp_path):
    import json
    from pathlib import Path

    wt = tmp_path / ".dev" / "worktrees" / "one" / ".dev"
    wt.mkdir(parents=True)
    (wt / "dev-run.pids.json").write_text(json.dumps({"pid": 10}), encoding="utf-8")
    pattern = tmp_path / ".dev" / "worktrees" / "*" / ".dev" / "dev-run.pids.json"
    out = G.load_leases([Path("missing-lease.json"), pattern],
                        None, _table((10, "node", 99)), {10: NOW - 7200},
                        G.children_index(_table((10, "node", 99))), now=NOW)
    assert out, "the absolute worktree glob must resolve to the lease file"


# ─── watchdog verdict ──────────────────────────────────────────────────────


def test_verdict_thresholds_and_growth_gate():
    v = lambda mb, prev=0.0, **kw: G.guard_verdict(  # noqa: E731
        mb, warn_mb=8000, kill_mb=32768, prev_mb=prev, **kw)
    assert v(4000) == "keep"
    assert v(9000) == "warn", "first crossing warns"
    assert v(9000, prev=8500) == "keep", "below 1.5x growth: stay quiet"
    assert v(13000, prev=8500) == "warn", "1.5x growth re-warns"
    assert v(40000) == "kill"
    assert v(40000, protected=True) == "keep"
    assert v(40000, never_kill=True) == "keep", "never-kill names survive the ceiling"


def test_never_kill_covers_the_os_and_editors():
    for name in ("claude", "code", "svchost", "dwm", "lsass", "system"):
        assert name in G.NEVER_KILL


def test_dev_tool_pattern_matches_only_dev_tools():
    for name in ("bash", "sh.exe", "node", "Python.EXE", "pwsh", "sleep", "cmd", "git"):
        assert G.DEV_TOOL_RE.match(name), name
    for name in ("chrome", "explorer", "telegram", "code", "claude"):
        assert not G.DEV_TOOL_RE.match(name), name


def test_iso_expiry_parsing():
    assert G._parse_iso("2000-01-01T00:00:00Z") is not None
    assert G._parse_iso("not-a-date") is None


def test_lazy_create_times_are_honoured_by_leases(tmp_path):
    """Regression: `_CreateTimes` loads via __missing__, which dict.get bypasses —
    using .get() made every lease look dead (live selftest caught it 2026-10-13)."""
    import json
    import os

    pp = os.getppid()
    table = {pp: ("python", 0)}
    start = G._CreateTimes()  # empty on purpose: must populate on access
    f = tmp_path / "lazy.json"
    f.write_text(json.dumps({"pid": pp, "name": "lazy stack"}), encoding="utf-8")
    out = G.load_leases([f], None, table, start, G.children_index(table), now=NOW)
    assert pp in out, "the lazy create_time must be loaded and the lease honoured"
    assert out[pp].startswith("registered dev stack")


# ─── runaway / leaked / spin / cdp-dirs sweeps (ported 2026-10-14) ─────────


def _t(*rows):
    return _table(*rows)


def test_runaway_lease_holds_below_ceiling_and_kills_over_it():
    table = _t((10, "node", 99), (11, "node", 99), (12, "python", 99))
    foot = {10: 7000.0, 11: 40000.0, 12: 6500.0}          # only 10/12 above 6000
    lease = {10: "registered dev stack 'a'", 11: "registered dev stack 'a'"}
    r = G.sweep_runaway(table, set(), lease, foot, runaway_mb=6000, hard_mb=32768, dry_run=True)
    assert 10 not in r["killed"], "the leased 7 GB server is held, not killed"
    assert r["held"] and "below the 32768 MB hard ceiling" in r["held"][0], "7 GB leased server is HOLDed"
    assert 11 in r["killed"], "40 GB leased server kills anyway (over ceiling)"
    assert 12 in r["killed"], "6.5 GB unleased python dies"
    assert not any("session chain" in w for w in map(str, r["skipped"]))


def test_runaway_spares_session_chain_and_below_threshold():
    table = _t((10, "node", 99))
    r = G.sweep_runaway(table, {10}, {}, {10: 40000.0}, runaway_mb=6000, hard_mb=32768, dry_run=True)
    assert r["killed"] == [] and "session chain" in r["skipped"][0][2]
    r2 = G.sweep_runaway(table, set(), {}, {10: 5000.0}, runaway_mb=6000, hard_mb=32768, dry_run=True)
    assert r2["killed"] == [], "below the threshold nothing is even looked at"
    r3 = G.sweep_runaway(table, set(), {}, {}, runaway_mb=0, hard_mb=32768, dry_run=True)
    assert r3["killed"] == [], "runaway_mb=0 disables the pass"


def test_runaway_editor_descendant_exempt_until_ceiling():
    # 10 = an editor, 20 its dev server child, 21 an unleased stranger
    table = _t((10, "code", 0), (20, "node", 10), (21, "node", 99))
    foot = {20: 9000.0, 21: 9000.0}
    r = G.sweep_runaway(table, set(), {}, foot, runaway_mb=6000, hard_mb=32768, dry_run=True)
    assert 21 in r["killed"] and 20 not in r["killed"], "editor descendants are exempt below the ceiling"
    assert any("editor descendant" in why for _pid, _n, why in r["skipped"])
    big = {20: 33000.0}
    r2 = G.sweep_runaway(table, set(), {}, big, runaway_mb=6000, hard_mb=32768, dry_run=True)
    assert 20 in r2["killed"], "over the hard ceiling the editor exemption dies too"


def test_aggregate_pressure_counts_split_process_families_without_killing():
    table = _t(
        (10, "chrome", 0),
        (11, "chrome", 10),
        (20, "code", 0),
        (30, "telegram", 0),
        (40, "node", 0),
    )
    footprints = {10: 9000.0, 11: 8500.0, 20: 7000.0, 30: 3000.0, 40: 50000.0}
    hits = G.aggregate_pressure(
        table,
        footprints,
        {"chrome": 16000, "code": 8000, "telegram": 8000},
    )
    assert hits == [{"name": "chrome", "count": 2, "mb": 17500.0, "threshold_mb": 16000}]
    assert "killed" not in hits[0], "interactive pressure is diagnostic, never a kill instruction"


def test_has_editor_ancestor():
    table = _t((10, "vscodium", 0), (20, "node", 10), (21, "node", 99))
    assert G.has_editor_ancestor(20, table) is True
    assert G.has_editor_ancestor(21, table) is False


def test_parse_user_data_dir_and_owner_state(tmp_path):
    udd = r"C:\Temp\cdp-profile-AbC"
    assert G.parse_user_data_dir('chrome.exe --user-data-dir="C:\\Temp\\cdp-profile-AbC" --headless') == udd
    assert G.parse_user_data_dir("chrome.exe --headless") is None
    table = _t((7, "python", 0))
    start = {7: NOW - 5000}
    # no owner.json at all -> kill signal (old harness)
    assert G.owner_state(str(tmp_path / "nope"), table, start) == "no owner.json"
    assert G.owner_state(None, table, start) == "no owner.json"
    # live owner: the process started BEFORE the claim — genuine owner
    (tmp_path / "owner.json").write_text(json.dumps({"pid": 7, "startedAt": int((NOW - 500) * 1000)}), encoding="utf-8")
    assert G.owner_state(str(tmp_path), table, start) == "alive"
    # dead owner
    (tmp_path / "owner.json").write_text(json.dumps({"pid": 424242}), encoding="utf-8")
    assert G.owner_state(str(tmp_path), table, start) == "owner dead"
    # recycled: the pid started AFTER the claim — a stranger, not the owner
    (tmp_path / "owner.json").write_text(json.dumps({"pid": 7, "startedAt": int((NOW - 10) * 1000)}), encoding="utf-8")
    fresh = {7: NOW - 2}
    assert G.owner_state(str(tmp_path), table, fresh) == "pid recycled"


def test_leaked_browsers_owner_gates_the_kill():
    table = _t((7, "python", 0), (1, "chrome", 0), (2, "chrome", 0), (3, "chrome", 0), (4, "chrome", 0))
    start = {1: NOW - 3600, 2: NOW - 3600, 3: NOW - 60, 4: NOW - 3600}
    cmds = {
        1: "chrome --headless --remote-debugging-port=0 --user-data-dir=C:\\Temp\\cdp-profile-x",
        2: "chrome --headless --remote-debugging-port=0 --user-data-dir=C:\\Users\\me\\.navig\\cdp-profiles\\sessions\\s1",
        3: "chrome --headless --remote-debugging-port=0 --user-data-dir=C:\\Temp\\cdp-profile-y",
        4: "chrome --headless --remote-debugging-port=0 --type=renderer",
    }
    states = {1: "owner dead", 2: "owner dead", 3: "alive", 4: "owner dead"}
    r = G.sweep_leaked_browsers(cmds, table, start, min_age_min=15, dry_run=True,
                                now=NOW, owner_fn=lambda udd, t, s: states[1])
    assert r["killed"] == [1], "dead owner + old enough -> kill; navig profile spared; young spared; renderer ignored"
    names = {why for _p, _n, why in r["skipped"]}
    assert any("navig cdp profile" in w for w in names)
    assert any("min old" in w for w in names)
    # owner alive -> spared
    r2 = G.sweep_leaked_browsers(cmds, table, start, min_age_min=15, dry_run=True,
                                 now=NOW, owner_fn=lambda udd, t, s: "alive")
    assert r2["killed"] == []


def test_spinners_need_all_four_conditions(monkeypatch):
    # 10: editor WITH a child (never a candidate), 20: leaf spinner (dies),
    # 31: 3-day-old VSCodium LEAF helper — the case this sweep exists for (dies),
    # 30: OS-critical leaf (never dies), 40: too young / weak spinners (no match).
    table = _t((10, "vscodium", 0), (20, "node", 10), (31, "vscodium", 99), (30, "explorer", 0), (40, "node", 99))
    start = {10: NOW - 72 * 3600, 20: NOW - 72 * 3600, 31: NOW - 72 * 3600,
             30: NOW - 72 * 3600, 40: NOW - 3600}
    spin = lambda: {10: (100.0, 30.0), 20: (100.0, 30.0), 31: (100.0, 30.0),
                    30: (100.0, 30.0), 40: (100.0, 0.5)}                      # noqa: E731
    r = G.sample_spinners(measure=spin, child_counts={10: 1, 20: 0, 31: 0, 30: 0, 40: 0},
                          table=table, start_of=start, protected=set(),
                          min_pct=80, min_age_h=48, min_cpu_h=24, dry_run=True, now=NOW)
    assert r["killed"] == [20, 31], "only leaf processes match — and a stuck editor LEAF helper dies"
    skipped = {w for _p, _n, w in r["skipped"]}
    assert any("OS-critical" in w for w in skipped), "explorer never dies here"
    # a running WSL VM is leaf-shaped, ancient and hot — the 2026-10-14 dry-run
    # caught vmmemwsl at 382%/415h; it must never be a candidate
    table2 = dict(table)
    table2[50] = ("vmmemwsl", 0)
    start2 = dict(start); start2[50] = NOW - 72 * 3600
    spin2 = lambda: {50: (382.0, 78.5)}                                        # noqa: E731
    r6 = G.sample_spinners(measure=spin2, child_counts={50: 0}, table=table2,
                           start_of=start2, protected=set(), min_pct=80,
                           min_age_h=48, min_cpu_h=24, dry_run=True, now=NOW)
    assert r6["killed"] == [] and any("OS-critical" in w for _p, _n, w in r6["skipped"])
    # not burning enough
    weak = lambda: {20: (40.0, 30.0)}                                          # noqa: E731
    r2 = G.sample_spinners(measure=weak, child_counts={20: 0}, table=table, start_of=start,
                           protected=set(), min_pct=80, min_age_h=48, min_cpu_h=24,
                           dry_run=True, now=NOW)
    assert r2["killed"] == []
    # too young
    young = lambda: {20: (100.0, 30.0)}                                        # noqa: E731
    r3 = G.sample_spinners(measure=young, child_counts={20: 0}, table=table,
                           start_of={20: NOW - 3600}, protected=set(),
                           min_pct=80, min_age_h=48, min_cpu_h=24, dry_run=True, now=NOW)
    assert r3["killed"] == [], "a 1-hour-old spinner is a runaway build, not a stuck helper"
    # too little accumulated CPU, and protected
    lowcpu = lambda: {20: (100.0, 1.0)}                                        # noqa: E731
    r4 = G.sample_spinners(measure=lowcpu, child_counts={20: 0}, table=table, start_of=start,
                           protected=set(), min_pct=80, min_age_h=48, min_cpu_h=24,
                           dry_run=True, now=NOW)
    assert r4["killed"] == [] and not r4["skipped"], "under the CPU-hours floor it never even judges"
    # ...but a fully-spinning protected process is skipped as session chain
    spinning = lambda: {20: (100.0, 30.0)}                                    # noqa: E731
    r5 = G.sample_spinners(measure=spinning, child_counts={20: 0}, table=table, start_of=start,
                           protected={20}, min_pct=80, min_age_h=48, min_cpu_h=24,
                           dry_run=True, now=NOW)
    assert r5["killed"] == [] and any("session chain" in w for _p, _n, w in r5["skipped"])


def test_cdp_dirs_keeps_live_profiles_and_deletes_dead_ones(tmp_path):
    tmp_root = tmp_path / "Temp"; sessions = tmp_path / "sessions"
    live = tmp_root / "cdp-live"; dead = tmp_root / "cdp-dead"
    kept_sess = sessions / "s-kept"; dead_sess = sessions / "s-dead"
    for d in (live, dead, kept_sess, dead_sess):
        d.mkdir(parents=True)
        (d / "x.bin").write_bytes(b"0" * 1024)
    cmds = {1: "chrome.exe --user-data-dir=" + str(live)}
    # no chrome at all (empty cmds): every dir is provably idle
    r = G.sweep_cdp_dirs(dry_run=False, chrome_cmds={}, stop_fn=None,
                         tmp_root=tmp_root, sessions_root=sessions)
    assert not live.exists() and not dead.exists() and not dead_sess.exists()
    assert r["freed_mb"] >= 0
    # running browser's profile survives a second run's siblings... recreate
    live.mkdir(); (live / "x.bin").write_bytes(b"0")
    dead2 = tmp_root / "cdp-dead2"; dead2.mkdir(); (dead2 / "x.bin").write_bytes(b"0")
    r2 = G.sweep_cdp_dirs(dry_run=False, chrome_cmds=cmds, stop_fn=None,
                          tmp_root=tmp_root, sessions_root=sessions)
    assert live.exists(), "a profile in use by a running browser is never deleted"
    assert not dead2.exists()
    assert str(live) in [k for k in r2["kept"]] or live in r2["kept"] or any("cdp-live" in k for k in r2["kept"])
    # cmdlines unavailable -> delete nothing
    r3 = G.sweep_cdp_dirs(dry_run=False, chrome_cmds=None, stop_fn=None,
                          tmp_root=tmp_root, sessions_root=sessions)
    assert r3["skipped"] and live.exists()


# ─── review regressions (PR #1909 / #1947) ─────────────────────────────────


def test_kill_mb_zero_never_kills():
    assert G.guard_verdict(100, warn_mb=8000, kill_mb=0, prev_mb=0) == "keep"
    assert G.guard_verdict(90000, warn_mb=8000, kill_mb=0, prev_mb=0) == "warn", \
        "--kill-mb 0 disables killing; the warn threshold still applies"


def test_deprioritize_uses_psutil_nice(monkeypatch):
    class FakeProc:
        def __init__(self, pid):
            self.pid = pid
            self.info = {"name": "node.exe"}
            self.calls = []

        def nice(self, value=None):
            if value is None:
                return 32  # NORMAL_PRIORITY_CLASS
            self.calls.append(value)

    procs = [FakeProc(5)]

    class FakePsutil:
        NORMAL_PRIORITY_CLASS = 32
        BELOW_NORMAL_PRIORITY_CLASS = 16384
        NoSuchProcess = AccessDenied = RuntimeError

        @staticmethod
        def process_iter(_attrs):
            return procs

    monkeypatch.setattr(G, "psutil", FakePsutil)
    monkeypatch.setattr(G.sys, "platform", "win32")
    assert G.deprioritize(("node",), protected=set()) == 1
    assert procs[0].calls == [16384]


def test_leaked_browser_with_unreadable_owner_is_spared():
    table = _t((1, "chrome", 0))
    cmds = {1: r"chrome --headless --remote-debugging-port=0 --user-data-dir=C:\Temp\cdp-profile-x"}
    r = G.sweep_leaked_browsers(cmds, table, {1: NOW - 3600}, min_age_min=15, dry_run=True,
                                now=NOW, owner_fn=lambda udd, t, s: "unreadable")
    assert r["killed"] == []
    assert any("unreadable" in why for _p, _n, why in r["skipped"])


def test_cdp_dirs_never_uses_a_relative_temp_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.chdir(tmp_path)
    victim = tmp_path / "Temp" / "cdp-project-data"
    victim.mkdir(parents=True)
    G.sweep_cdp_dirs(dry_run=False, chrome_cmds={}, stop_fn=None,
                     sessions_root=tmp_path / "no-sessions")
    assert victim.exists(), "an unset LOCALAPPDATA must not turn ./Temp into the sweep root"


def test_chrome_argv_with_spaces_keeps_the_whole_profile_path():
    udd = r"C:\Users\John Doe\p"
    cmd = G._join_argv(["chrome.exe", "--user-data-dir=" + udd, "--headless"])
    assert G.parse_user_data_dir(cmd) == udd
    assert "--headless" in cmd
    assert G.parse_user_data_dir(G._join_argv(["chrome.exe", r"--user-data-dir=C:\Temp\x"])) == r"C:\Temp\x"


def test_spinner_pass_spares_leased_processes(monkeypatch):
    seen = {}

    def fake_spinners(**kw):
        seen["protected"] = kw["protected"]
        return {"killed": [], "skipped": [], "hits": []}

    monkeypatch.setattr(G, "snapshot_table", lambda: _t((77, "node", 0)))
    monkeypatch.setattr(G, "_CreateTimes", lambda: collections.defaultdict(lambda: NOW))
    monkeypatch.setattr(G, "navig_tree_pids", lambda *a: {})
    monkeypatch.setattr(G, "load_leases", lambda *a: {77: "lease dev-stack"})
    monkeypatch.setattr(G.psutil, "process_iter", lambda *a: [])
    monkeypatch.setattr(G, "_chrome_cmdlines", lambda: ({}, True))
    monkeypatch.setattr(G, "sample_spinners", fake_spinners)
    G.sweep_once(dry_run=True, echo=False, lease_files=[], cdp_dirs=False)
    assert 77 in seen["protected"], "a leased pid is protected from the spinner sweep too"
