"""`navig doctor` → Browsers: disk held by profiles that can be reclaimed without losing a login.

`~/.navig/cdp-profiles` reached 11.7 GB and was read for months as "logins we can't
touch"; 8.4 GB of it was Chrome's on-device model, twice, and the logins were 7 MB.
`navig cdp profile usage` says so — if someone remembers to run it. The doctor row turns
that command into a light that comes on.
"""

from __future__ import annotations

from unittest import mock

import pytest

from navig.browser import cdp_actions as A
from navig.commands import doctor as D

MB = 1024 * 1024


def _usage(named=(), orphans=()):
    return {"named": list(named), "orphans": list(orphans), "sessions": [], "total_bytes": 0}


def _named(name, *, real=False, running=False):
    return {"name": name, "user_data_dir": f"/p/{name}", "bytes": 0, "last_used": 0,
            "real": real, "running": running}


@pytest.fixture
def regen(monkeypatch):
    """Drive `regenerable_bytes` from a name → bytes table, no disk."""
    table: dict[str, int] = {}
    monkeypatch.setattr(A, "regenerable_bytes", lambda udd: table.get(udd.rsplit("/", 1)[-1], 0))
    return table


def _rows(usage):
    with mock.patch.object(A, "profile_usage", lambda: usage):
        return D.check_browser_profiles()


# ── quiet when lean ───────────────────────────────────────────────────────────


def test_a_lean_store_says_nothing(regen):
    regen["a"] = 10 * MB
    assert _rows(_usage(named=[_named("a")])) == []


def test_below_the_threshold_is_not_worth_a_line(regen):
    regen["a"] = D._PROFILE_RECLAIM_THRESHOLD - 1
    assert _rows(_usage(named=[_named("a")])) == []


# ── lights up ─────────────────────────────────────────────────────────────────


def test_regenerable_data_over_the_threshold_warns_and_names_vacuum(regen):
    regen["a"] = 4 * 1024 * MB  # the on-device model
    regen["b"] = 300 * MB
    rows = _rows(_usage(named=[_named("a"), _named("b")]))
    assert len(rows) == 1
    name, ok, msg = rows[0]
    assert ok is False, "reclaimable disk is a warning, never a green tick"
    assert "Browser profiles" in name or "Browser profiles" in msg
    assert "vacuum --all" in msg
    assert "login" in msg, "the row must say logins are kept, or nobody will dare run it"
    assert "2 profile" in msg


def test_orphaned_dirs_warn_and_name_prune_with_a_real_name(regen):
    rows = _rows(_usage(orphans=[{"name": "gaze-books", "path": "/p/gaze-books", "bytes": 221 * MB},
                                 {"name": "promptlib", "path": "/p/promptlib", "bytes": 212 * MB}]))
    assert len(rows) == 1
    _, ok, msg = rows[0]
    assert ok is False
    assert "2 dir(s)" in msg and "prune gaze-books" in msg


def test_both_rows_when_both_apply(regen):
    regen["a"] = 1024 * MB
    rows = _rows(_usage(named=[_named("a")], orphans=[{"name": "x", "path": "/p/x", "bytes": MB}]))
    assert len(rows) == 2


# ── what it refuses to count ──────────────────────────────────────────────────


def test_real_and_running_profiles_are_not_counted_as_reclaimable(regen):
    """`vacuum` refuses them, so a row that counts them promises disk it cannot free."""
    regen["real"] = 4 * 1024 * MB
    regen["live"] = 4 * 1024 * MB
    regen["ok"] = 10 * MB
    rows = _rows(_usage(named=[_named("real", real=True), _named("live", running=True), _named("ok")]))
    assert rows == []


# ── honesty ───────────────────────────────────────────────────────────────────


def test_a_failed_check_is_a_warning_never_a_hidden_section():
    """`return []` on failure hides the section, which reads as 'nothing to reclaim' — the
    exact state this check exists to end. Same rule as `check_browsers`."""
    def _boom():
        raise RuntimeError("registry unreadable")

    with mock.patch.object(A, "profile_usage", _boom):
        rows = D.check_browser_profiles()
    assert len(rows) == 1
    _, ok, msg = rows[0]
    assert ok is False
    assert "COULD NOT VERIFY" in msg


def test_the_section_is_wired_into_the_report():
    """A check nothing calls protects nothing."""
    import ast
    import inspect

    src = inspect.getsource(D)
    tree = ast.parse(src)
    called = {n.func.id for n in ast.walk(tree)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "check_browser_profiles" in called, "doctor never calls check_browser_profiles"
