"""The PATH-headroom row: a ceiling whose failure names the wrong culprit.

cmd.exe truncates PATH at 8191 chars. Past that, every command resolved through a shell
fails with "'x' is not recognized" — naming a tool that IS installed and whose directory
IS on PATH. That misdirection is the whole point of the row: the operator's `npm run
dev:anchor` died on `vite` while `node_modules/.bin/vite.CMD` was present and its dir was
on PATH three times over, and the real cause was 76 leaked dead entries pushing a nested
npm chain past the ceiling. Nothing reported it, because nothing was looking.

Thresholds are absolute character counts, so these build a real PATH out of real
directories rather than asserting against a mocked number.
"""

from __future__ import annotations

import os

import pytest

from navig.commands import doctor


def _dirs_until(tmp_path, min_len: int, max_len: int | None = None) -> str:
    """Build a PATH of real, existing directories whose joined length lands in range."""
    entries: list[str] = []
    i = 0
    while len(os.pathsep.join(entries)) < min_len:
        d = tmp_path / f"d{i:04d}_{'x' * 30}"
        d.mkdir(exist_ok=True)
        entries.append(str(d))
        i += 1
        if i > 5000:  # pragma: no cover - runaway guard
            raise AssertionError("could not reach target PATH length")
    joined = os.pathsep.join(entries)
    if max_len is not None:
        assert len(joined) <= max_len, f"overshot: {len(joined)} > {max_len}"
    return joined


# Captured BEFORE the autouse fixture can stub it, so the reader's own behaviour is
# still testable from inside a suite that stubs it everywhere else.
_REAL_REGISTRY_READER = doctor._user_path_from_registry


@pytest.fixture(autouse=True)
def _as_windows(monkeypatch):
    """The 8191 ceiling is a cmd.exe property; the row is Windows-only."""
    monkeypatch.setattr(doctor.os, "name", "nt")
    # Stub the registry read too. Without it every assertion below would depend on
    # whatever the developer's real user PATH happens to contain that day.
    monkeypatch.setattr(doctor, "_user_path_from_registry", lambda: "")


def _registry(monkeypatch, entries):
    """Drive the PERSISTENT user PATH — the one `clean-path` can rewrite."""
    monkeypatch.setattr(
        doctor, "_user_path_from_registry", lambda: os.pathsep.join(entries)
    )


def _row(rows):
    assert len(rows) == 1, f"expected exactly one PATH row, got {rows}"
    icon, ok, text = rows[0][0], rows[0][1], rows[0][2]
    return icon, ok, text


# ── platform scoping ─────────────────────────────────────────────────────────


def test_contributes_no_row_off_windows(monkeypatch):
    """A green row about a limit that does not exist would be noise, not information."""
    monkeypatch.setattr(doctor.os, "name", "posix")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    assert doctor.check_path_health() == []


# ── the honesty invariant ────────────────────────────────────────────────────


def test_unreadable_path_warns_and_is_never_green(monkeypatch):
    monkeypatch.setenv("PATH", "")
    icon, ok, text = _row(doctor.check_path_health())
    assert ok is False, "could-not-verify must never render ✓"
    assert icon == doctor._WARN
    assert "could not be read" in text or "empty" in text


# ── the three headroom states ────────────────────────────────────────────────


def test_comfortable_headroom_is_green(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _dirs_until(tmp_path, 500, max_len=2000))
    icon, ok, text = _row(doctor.check_path_health())
    assert ok is True
    assert icon == doctor._OK
    assert "under the 8191 limit" in text


def test_tight_headroom_warns_before_anything_breaks(tmp_path, monkeypatch):
    # inside the reserve (< 2000 free) but still under the ceiling: nothing has failed
    # yet, which is exactly when the operator can still act cheaply.
    target = doctor._CMD_PATH_LIMIT - 1000
    monkeypatch.setenv("PATH", _dirs_until(tmp_path, target, max_len=doctor._CMD_PATH_LIMIT))
    icon, ok, text = _row(doctor.check_path_health())
    assert ok is False
    assert icon == doctor._WARN
    assert "under cmd.exe's 8191 limit" in text
    assert "nested tooling" in text


def test_over_the_ceiling_is_an_error_not_a_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _dirs_until(tmp_path, doctor._CMD_PATH_LIMIT + 200))
    icon, ok, text = _row(doctor.check_path_health())
    assert ok is False
    assert icon == doctor._ERR, "already-truncated resolution is a failure, not a caution"
    assert "OVER cmd.exe's 8191 limit" in text


# ── the reclaimable part: what the operator can actually delete ──────────────


def test_counts_missing_and_duplicate_entries(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    missing = [str(tmp_path / f"gone{i}") for i in range(3)]
    entries = [str(real), *missing, str(other), str(real)]  # last one duplicates `real`
    monkeypatch.setenv("PATH", str(real))
    _registry(monkeypatch, entries)

    _icon, ok, text = _row(doctor.check_path_health())
    assert ok is True, "a short PATH is healthy even when it holds junk"
    assert "3 missing + 1 duplicate entries in your user PATH" in text


def test_trailing_separator_and_case_do_not_create_false_duplicates(tmp_path, monkeypatch):
    """`C:\\Foo` and `c:\\foo\\` are the same directory; counting them twice would invent
    reclaimable space that deleting cannot recover."""
    a = tmp_path / "Alpha"
    a.mkdir()
    b = tmp_path / "Beta"
    b.mkdir()
    _registry(monkeypatch, [str(a), str(b)])
    _icon, _ok, clean = _row(doctor.check_path_health())
    assert "duplicate" not in clean

    _registry(monkeypatch, [str(a), str(b), str(a).upper() + os.sep])
    _icon, _ok, dup = _row(doctor.check_path_health())
    assert "1 duplicate" in dup


def test_blank_segments_are_ignored(tmp_path, monkeypatch):
    """A trailing `;` is ubiquitous on Windows and is not a missing directory."""
    real = tmp_path / "real"
    real.mkdir()
    _registry(monkeypatch, [str(real), "", ""])
    monkeypatch.setenv("PATH", str(real))
    _icon, ok, text = _row(doctor.check_path_health())
    assert ok is True
    assert "missing" not in text


# ── wiring: a check that runs in no section is documentation ─────────────────


def test_row_is_wired_into_the_runtime_section(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", _dirs_until(tmp_path, 500, max_len=2000))
    monkeypatch.setattr(doctor, "check_runtime", lambda: [])
    for name in (
        "check_config", "check_storage", "check_databases", "check_vault",
        "check_cache_dir", "check_sockets", "check_formations", "check_skills",
        "check_gateway", "check_event_processor", "check_gateway_auth",
        "check_ai_providers", "check_identity", "check_media_tools", "check_wiring",
        "check_daemon_freshness",
    ):
        monkeypatch.setattr(doctor, name, lambda *a, **k: [], raising=False)

    sections = dict(doctor._collect_sections())
    runtime = sections.get("Runtime", [])
    assert any("PATH is" in row[2] for row in runtime), (
        f"the PATH row is not reachable from _collect_sections: {runtime}"
    )


# ── the repair half: `navig doctor clean-path` ───────────────────────────────
#
# The row above reports how much of PATH is reclaimable; without a way to reclaim it the
# advice is a dead end. These cover the prune logic, the write path, and — because this
# repo has shipped hints naming commands that do not exist — that the command the row
# points at actually resolves in the real Click tree.

import sys

from typer.testing import CliRunner

from navig.commands.doctor import doctor_app, partition_path_entries


class _FakeKey:
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _FakeWinreg:
    """Just enough winreg to exercise the read/write path without touching the machine."""

    HKEY_CURRENT_USER = 1
    KEY_READ = 0x20019
    KEY_SET_VALUE = 0x2
    REG_SZ = 1
    REG_EXPAND_SZ = 2

    def __init__(self, value: str, kind: int = 2):
        self.value = value
        self.kind = kind
        self.written: tuple[int, str] | None = None

    def OpenKey(self, _root, _sub, _res, _access):  # noqa: N802 - winreg's own name
        return _FakeKey()

    def QueryValueEx(self, _key, _name):  # noqa: N802
        return self.value, self.kind

    def SetValueEx(self, _key, _name, _res, kind, value):  # noqa: N802
        self.written = (kind, value)


def _install(monkeypatch, tmp_path, raw: str, kind: int = 2) -> _FakeWinreg:
    fake = _FakeWinreg(raw, kind)
    monkeypatch.setitem(sys.modules, "winreg", fake)
    monkeypatch.setattr(doctor, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor, "_broadcast_environment_change", lambda: None)
    return fake


# ── the pure partition ───────────────────────────────────────────────────────


def test_partition_keeps_only_what_resolves(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    gone = tmp_path / "gone"
    kept, removed = partition_path_entries([str(real), str(gone), str(real)])
    assert kept == [str(real)]
    assert removed == [(str(gone), "missing"), (str(real), "duplicate")]


def test_partition_is_order_preserving(tmp_path):
    """PATH order is precedence — a prune that reorders silently changes which tool wins."""
    a, b, c = (tmp_path / n for n in ("a", "b", "c"))
    for d in (a, b, c):
        d.mkdir()
    kept, _ = partition_path_entries([str(c), str(a), str(b)])
    assert kept == [str(c), str(a), str(b)]


# ── the command ──────────────────────────────────────────────────────────────


def test_dry_run_reports_but_never_writes(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    raw = os.pathsep.join([str(real), str(tmp_path / "gone"), str(real)])
    fake = _install(monkeypatch, tmp_path, raw)

    res = CliRunner().invoke(doctor_app, ["clean-path"])
    assert res.exit_code == 0, res.output
    assert fake.written is None, "dry-run must not touch the registry"
    assert "missing" in res.output and "duplicate" in res.output
    assert "--apply" in res.output


def test_apply_writes_pruned_value_and_preserves_value_kind(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    raw = os.pathsep.join([str(real), str(tmp_path / "gone"), str(real)])
    fake = _install(monkeypatch, tmp_path, raw, kind=_FakeWinreg.REG_EXPAND_SZ)

    res = CliRunner().invoke(doctor_app, ["clean-path", "--apply"])
    assert res.exit_code == 0, res.output
    assert fake.written is not None
    kind, value = fake.written
    assert value == str(real), "the pruned PATH must keep exactly the resolvable entries"
    assert kind == _FakeWinreg.REG_EXPAND_SZ, (
        "rewriting a REG_EXPAND_SZ PATH as REG_SZ turns every %VAR% into a dead literal"
    )


def test_apply_backs_up_the_original_first(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    raw = os.pathsep.join([str(real), str(tmp_path / "gone")])
    _install(monkeypatch, tmp_path, raw)

    res = CliRunner().invoke(doctor_app, ["clean-path", "--apply"])
    assert res.exit_code == 0, res.output
    backups = list((tmp_path / "backups").glob("path-user-*.txt"))
    assert len(backups) == 1, f"expected one backup, got {backups}"
    assert backups[0].read_text(encoding="utf-8") == raw, "the backup must be the ORIGINAL"


def test_refuses_to_write_an_empty_path(tmp_path, monkeypatch):
    """Every entry missing means something is wrong with the reading, not the machine."""
    raw = os.pathsep.join([str(tmp_path / "gone1"), str(tmp_path / "gone2")])
    fake = _install(monkeypatch, tmp_path, raw)

    res = CliRunner().invoke(doctor_app, ["clean-path", "--apply"])
    assert res.exit_code == 1
    assert fake.written is None, "an empty result must never reach the registry"


def test_clean_path_is_a_noop_when_nothing_is_reclaimable(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    fake = _install(monkeypatch, tmp_path, str(real))
    res = CliRunner().invoke(doctor_app, ["clean-path", "--apply"])
    assert res.exit_code == 0
    assert fake.written is None
    assert "already clean" in res.output


def test_json_output_is_machine_readable(tmp_path, monkeypatch):
    import json

    real = tmp_path / "real"
    real.mkdir()
    raw = os.pathsep.join([str(real), str(tmp_path / "gone")])
    _install(monkeypatch, tmp_path, raw)

    res = CliRunner().invoke(doctor_app, ["clean-path", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output)
    assert payload["applied"] is False
    assert payload["before_chars"] == len(raw)
    assert [r["reason"] for r in payload["removed"]] == ["missing"]


def test_off_windows_says_so_and_exits_nonzero(monkeypatch):
    monkeypatch.setattr(doctor.os, "name", "posix")
    res = CliRunner().invoke(doctor_app, ["clean-path"])
    assert res.exit_code == 1
    assert "Windows-only" in res.output


# ── advice must name a command that exists ───────────────────────────────────


def test_the_command_the_row_points_at_actually_resolves():
    """This repo has shipped hints naming commands that do not exist. Resolve it through
    the REAL Click tree rather than trusting the string."""
    import typer as _typer

    group = _typer.main.get_command(doctor_app)
    assert "clean-path" in getattr(group, "commands", {}), (
        "the PATH health row tells the operator to run `navig doctor clean-path`"
    )


def test_row_points_at_the_fix_only_when_there_is_something_to_reclaim(tmp_path, monkeypatch):
    real = tmp_path / "real"
    real.mkdir()
    _registry(monkeypatch, [str(real)])
    _icon, _ok, clean = _row(doctor.check_path_health())
    assert "reclaim:" not in clean, "offering a cleanup that frees nothing wastes trust"

    _registry(monkeypatch, [str(real), str(tmp_path / "gone")])
    _icon, _ok, dirty = _row(doctor.check_path_health())
    assert "reclaim: navig doctor clean-path" in dirty


# ── headroom and reclaimable are measured on DIFFERENT paths, on purpose ─────


def test_process_path_junk_is_never_offered_as_reclaimable(tmp_path, monkeypatch):
    """Found live: the row read this process's PATH for both numbers, so a shell that had
    injected duplicates into its own environment (npm prepends ~7 node_modules/.bin dirs
    per nesting level) made the row advertise 490 reclaimable chars — and `clean-path`
    then correctly answered "already clean". Advice that dead-ends.

    Headroom must still come from the process PATH: that string is what a shell actually
    resolves against, and it is what breaks.
    """
    real = tmp_path / "real"
    real.mkdir()
    # this shell's PATH carries junk no command can remove...
    monkeypatch.setenv(
        "PATH", os.pathsep.join([str(real), str(real), str(tmp_path / "shell-injected")])
    )
    # ...while the persistent user PATH is clean
    _registry(monkeypatch, [str(real)])

    _icon, ok, text = _row(doctor.check_path_health())
    assert ok is True
    assert "reclaim:" not in text, (
        "clean-path cannot remove what a shell injected into its own environment; "
        f"offering it here dead-ends in 'already clean' — got: {text}"
    )


def test_headroom_still_reflects_the_process_path(tmp_path, monkeypatch):
    """The counterpart: a bloated process PATH must still warn even when the persistent
    user PATH is spotless — the ceiling is hit by the string the shell holds."""
    _registry(monkeypatch, [])
    monkeypatch.setenv(
        "PATH", _dirs_until(tmp_path, doctor._CMD_PATH_LIMIT - 1000, max_len=doctor._CMD_PATH_LIMIT)
    )
    icon, ok, text = _row(doctor.check_path_health())
    assert ok is False and icon == doctor._WARN
    assert "under cmd.exe's 8191 limit" in text
    assert "reclaim:" not in text, "nothing to reclaim, so do not send them to clean-path"


def test_an_unreadable_registry_claims_nothing_either_way(tmp_path, monkeypatch):
    """`None` means "I could not look". Reporting 0 reclaimable would be a claim, and
    reporting a number would be an invention."""
    real = tmp_path / "real"
    real.mkdir()
    monkeypatch.setenv("PATH", str(real))
    monkeypatch.setattr(doctor, "_user_path_from_registry", lambda: None)

    _icon, ok, text = _row(doctor.check_path_health())
    assert ok is True
    assert "reclaim:" not in text and "missing" not in text


def test_registry_reader_never_raises(monkeypatch):
    """A health check must never crash the doctor — a registry that will not open is a
    reason to say less, not to fail the run."""
    import builtins

    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "winreg":
            raise OSError("registry unavailable")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert _REAL_REGISTRY_READER() is None
