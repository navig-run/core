"""``navig doctor`` → Shared protocol: do the two repos still carry one protocol?

Measured 2026-10-09: Schema changed ``scripts/heavy-slot*.mjs`` first, and every
navig push failed with "the twins drifted" — with no signal shorter than running the
whole gate. This row says so before a push, using the same constants the test pins.

Each case builds a REAL repository for both sides (``git show origin/main:…`` is what
the row reads), so the byte handling — including CRLF normalisation — is exercised,
not mocked.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest

from navig.commands import doctor

FILES = ("scripts/heavy-slot-lib.mjs", "scripts/heavy-slot.mjs")
TWIN_TEST = "scripts/test/heavy-slot.test.mjs"
GIT = shutil.which("git")

pytestmark = pytest.mark.skipif(GIT is None, reason="git is not installed")


def _run(*args, cwd: Path) -> None:
    subprocess.run([GIT, "-C", str(cwd), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, name: str, files: dict[str, str], *, remote: bool) -> Path:
    """A real git repo holding ``files``. ``remote`` publishes them as origin/main."""
    root = tmp_path / name
    root.mkdir(parents=True)
    _run("init", "-q", "-b", "main", cwd=root)
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        # write_BYTES: text mode on Windows would translate "\n" into CRLF on top of
        # a body that already asks for CRLF, and the CRLF case then tests "\r\r\n".
        path.write_bytes(body.encode("utf-8"))
    _run("add", "-A", cwd=root)
    _run("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "p", cwd=root)
    if remote:
        _run("update-ref", "refs/remotes/origin/main", "HEAD", cwd=root)
    return root


def _pin(files: dict[str, str]) -> str:
    """The same construction as the twin test: NUL-joined, CRLF-normalised, sha256."""
    return hashlib.sha256(
        b"\0".join(files[f].replace("\r\n", "\n").encode("utf-8") for f in FILES)
    ).hexdigest()


def _test_file(pin: str, repo: str = "schema") -> str:
    names = ", ".join(f"'{f}'" for f in FILES)
    return (
        f"const TWIN_REPO = '{repo}';\n"
        f"const TWIN_FILES = [{names}];\n"
        f"const PROTOCOL_SHA256 = '{pin}';\n"
    )


@pytest.fixture
def ours(tmp_path: Path, monkeypatch) -> Path:
    """A navig checkout declaring the protocol, with matching local files."""
    files = {f: f"// v1\n{f}\n" for f in FILES}
    root = _repo(tmp_path, "navig", {**files, TWIN_TEST: _test_file(_pin(files))}, remote=False)
    monkeypatch.setattr(doctor, "_invocation_checkout_root", lambda: root)
    return root


def _twin_path(tmp_path: Path) -> Path:
    monkey_path = tmp_path / "schema"
    monkey_path.mkdir()
    return monkey_path


def _with_twin(monkeypatch, twin: Path) -> None:
    monkeypatch.setattr(doctor, "_twin_checkout", lambda root, repo_name: twin)


def _row():
    rows = doctor.check_shared_protocol()
    assert len(rows) == 1, f"expected exactly one verdict, got {len(rows)}"
    return rows[0]


# ── the happy path ───────────────────────────────────────────────────────────


def test_in_step_reads_a_tick(ours, tmp_path, monkeypatch):
    twin_files = {f: (ours / f).read_text(encoding="utf-8") for f in FILES}
    twin = _repo(tmp_path, "schema", twin_files, remote=True)
    _with_twin(monkeypatch, twin)
    icon, ok, line = _row()
    assert ok is True and icon == doctor._OK
    assert "identical to schema" in line


def test_the_pin_is_read_from_the_test_not_from_a_constant(ours, monkeypatch):
    """A test that pins something else must be what doctor checks."""
    assert doctor._twin_spec(ours / TWIN_TEST) == ("schema", _pin({f: f"// v1\n{f}\n" for f in FILES}), FILES)
    assert _twin_spec_unreadable(ours) is None


def _twin_spec_unreadable(ours: Path):
    (ours / TWIN_TEST).write_text("const TWIN_FILES = [];\n", encoding="utf-8")
    return doctor._twin_spec(ours / TWIN_TEST)


# ── the failure this exists for ──────────────────────────────────────────────


def test_a_twin_that_has_moved_fails_and_names_the_fix(ours, tmp_path, monkeypatch):
    """The 2026-10-09 case: Schema moved first and every push started failing."""
    moved = {f: f"// v2 — Schema's new lane\n{f}\n" for f in FILES}
    twin = _repo(tmp_path, "schema", {**moved, TWIN_TEST: _test_file(_pin(moved))}, remote=True)
    _with_twin(monkeypatch, twin)
    icon, ok, line = _row()
    assert ok is False and icon == doctor._ERR, "an out-of-step twin blocks every push"
    assert "twins drifted" in line and "PROTOCOL_SHA256" in line
    assert "scripts/heavy-slot-lib.mjs" in line, "the remedy must name what to copy"


def test_our_copy_changed_without_repinning_fails_first(ours, monkeypatch):
    """Before blaming the twin, say that THIS checkout is the one that drifted."""
    (ours / FILES[0]).write_text("// edited by a branch, pin untouched\n", encoding="utf-8")
    icon, ok, line = _row()
    assert ok is False and icon == doctor._ERR
    assert "PROTOCOL_SHA256" in line and "not updated" in line


def test_crlf_is_not_drift(ours, tmp_path, monkeypatch):
    """The two repos historically differ in line endings; the protocol ignores that."""
    twin_files = {f: (ours / f).read_text(encoding="utf-8").replace("\n", "\r\n") for f in FILES}
    twin = _repo(tmp_path, "schema", twin_files, remote=True)
    _with_twin(monkeypatch, twin)
    assert _row()[1] is True


# ── when there is nothing to say ─────────────────────────────────────────────


def test_no_twin_checkout_is_no_verdict(ours, monkeypatch):
    """The test skips without Schema on the machine; a skip is not a failure."""
    _with_twin(monkeypatch, None)
    assert doctor.check_shared_protocol() == []


def test_a_repo_without_the_test_says_nothing(monkeypatch, tmp_path):
    root = _repo(tmp_path, "navig", {"scripts/other.mjs": "x"}, remote=False)
    monkeypatch.setattr(doctor, "_invocation_checkout_root", lambda: root)
    assert doctor.check_shared_protocol() == []


def test_unreadable_twin_is_reported_as_unverified_not_as_in_step(ours, tmp_path, monkeypatch):
    twin = _repo(tmp_path, "schema", {"scripts/other.mjs": "x"}, remote=False)  # no origin/main
    _with_twin(monkeypatch, twin)
    icon, ok, line = _row()
    assert ok is False and icon == doctor._WARN, "an unreadable twin must never render a tick"
    assert "unverified" in line


# ── locating the twin ────────────────────────────────────────────────────────


def test_the_twin_is_a_sibling_of_the_main_checkout(tmp_path):
    main = _repo(tmp_path, "navig", {"scripts/x.mjs": "x"}, remote=False)
    twin = _repo(tmp_path, "schema", {"scripts/x.mjs": "x"}, remote=False)
    assert doctor._twin_checkout(main, "schema") == twin


def test_an_env_override_wins(tmp_path, monkeypatch):
    twin = _repo(tmp_path, "elsewhere", {"scripts/x.mjs": "x"}, remote=False)
    monkeypatch.setenv("HEAVY_SLOT_TWIN_REPO", str(twin))
    assert doctor._twin_checkout(tmp_path, "schema") == twin


def test_an_env_override_pointing_at_nothing_is_not_a_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("HEAVY_SLOT_TWIN_REPO", str(tmp_path / "nowhere"))
    assert doctor._twin_checkout(tmp_path, "schema") is None
