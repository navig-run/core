"""Every long-lived ``sqlite3.connect(...)`` in core must set a busy timeout.

A store that opens its connection with no busy timeout gets ``sqlite3.OperationalError:
database is locked`` **on the first blip** of contention — instead of waiting for the lock to
clear. On a multi-process install (the CLI writing while the daemon writes; a backup / antivirus
agent momentarily holding the file) that turns a transient lock into a hard failure, and on a
*read* path it degrades to a phantom-empty result (a live row read as absent — the #687 vault
class). WAL and an in-process lock do not help: WAL still serialises writers, and an in-process
``RLock`` only orders *threads*, never a second *process* on the same file.

The fix is one line — ``PRAGMA busy_timeout=5000`` (the canonical NAVIG default:
``storage/pragma_profiles.py``, ``store/base.py``, ``memory/key_facts.py``) — or an equivalent
``sqlite3.connect(..., timeout=N)`` (the connect-level busy timeout). This guard locks the door so
a new raw store can't reintroduce the class. It fixed a nine-store sweep (the ``memory/*`` stores,
``identity``, ``bot/stats``, the ``telegram_formatter`` fallback, and the two ``agent`` stores)
after the vault stores were hardened (#687/#688).

Scope: ``core/navig/`` only — that is where the store class lives. A file that legitimately opens
a connection with no busy timeout (the canonical Engine that applies pragmas via a profile; a
one-shot CLI/backup/read-only-external connect that is not a shared navig store) is an explicit
allowlist entry with a per-entry reason, ratcheted by ``test_allowlist_entries_still_match``.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]

# Keyed repo-relative -> reason. A file here opens a sqlite3 connection but legitimately does not
# set a busy timeout ITSELF. Ratcheted: if the file stops matching (gains a busy timeout, or the
# connect is removed), its entry must be deleted so a real regression can't hide behind it.
_ALLOWLIST: dict[str, str] = {
    "core/navig/storage/engine.py": (
        "the canonical Engine — its connect() applies busy_timeout via storage/pragma_profiles.py "
        "(every profile sets one); the second connect is a fresh backup-destination file with no "
        "contention."
    ),
    "core/navig/store/runtime.py": (
        "one-shot backup SOURCE read; sqlite's online-backup API retries busy pages itself, so a "
        "connection busy_timeout adds nothing."
    ),
    "core/navig/commands/db_local.py": (
        "one-shot `navig db` CLI connect on a user-ARBITRARY sqlite file, not a shared navig store; "
        "changing its lock-wait behaviour would change CLI semantics on foreign databases."
    ),
    "core/navig/importers/sources/firefox.py": (
        "read-only (mode=ro) one-shot import of an EXTERNAL browser DB, wrapped in its own error "
        "handling; not a navig store."
    ),
    "plugins/navig-games/navig_games/engine/library/gog.py": (
        "reads a PRIVATE per-call copy (tempfile.mkstemp) of the Galaxy DB, mode=ro — no other "
        "process can hold a lock on that file, so a busy timeout has nothing to wait for."
    ),
    "plugins/navig-games/navig_games/engine/library/amazon.py": (
        "same shape as gog.py: a PRIVATE per-call mkstemp copy opened mode=ro. (It used to use a "
        "FIXED temp filename, which two overlapping calls really did contend on — fixed by giving "
        "it a unique file rather than by waiting on a lock.)"
    ),
}


def _is_sqlite_connect(node: ast.AST) -> bool:
    """A call to ``sqlite3.connect(...)`` (the attribute form NAVIG uses everywhere)."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "sqlite3"
    )


def _connect_has_timeout_kwarg(tree: ast.AST) -> bool:
    """Any ``sqlite3.connect(..., timeout=N)`` — the connect-level busy timeout (seconds)."""
    for n in ast.walk(tree):
        if _is_sqlite_connect(n) and any(kw.arg == "timeout" for kw in n.keywords):
            return True
    return False


def _has_sqlite_connect(tree: ast.AST) -> bool:
    return any(_is_sqlite_connect(n) for n in ast.walk(tree))


def _is_offender(path: Path) -> bool:
    """Has a ``sqlite3.connect`` but sets NO busy timeout (neither a PRAGMA nor a timeout kwarg)."""
    try:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return False
    if not _has_sqlite_connect(tree):
        return False
    if "busy_timeout" in src or _connect_has_timeout_kwarg(tree):
        return False
    return True


def _scanned_roots() -> list[Path]:
    """core/navig AND every plugin package.

    This guard used to scan `core/navig` alone while four sibling guards in this directory
    already covered plugins — a scope drawn at a DIRECTORY rather than at the shape that makes
    a file the subject ("it opens a sqlite connection"). Plugins open sqlite too, and widening
    it immediately surfaced navig-games' library readers, one of which was copying a vendor DB
    to a FIXED temp filename that two overlapping calls contended on.
    """
    roots = [CORE]
    plugins = REPO / "plugins"
    if plugins.is_dir():
        roots += sorted(p for p in plugins.glob("navig-*") if p.is_dir())
    return roots


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            parts = set(f.parts)
            if {"tests", "test", "scaffold-templates", "build", "dist"} & parts:
                continue
            files.append(f)
    return files


# The scan must actually READ the tree. A guard whose scope silently breaks reports "no
# offenders" exactly like a clean repo — the failure mode this repo has already hit twice.
_MIN_FILES_SCANNED = 1200


def test_the_scan_actually_reads_the_tree():
    n = len(_python_files())
    assert n >= _MIN_FILES_SCANNED, (
        f"scanned only {n} files (expected >= {_MIN_FILES_SCANNED}) — the scope is broken, so "
        "this guard is passing without having looked"
    )
    roots = [r.name for r in _scanned_roots()]
    assert any(r.startswith("navig-") for r in roots), f"plugins are not in scope: {roots}"


def _rel(path: Path) -> str:
    return str(path.relative_to(REPO)).replace("\\", "/")


def test_every_sqlite_store_sets_a_busy_timeout():
    offenders = [_rel(p) for p in _python_files() if _rel(p) not in _ALLOWLIST and _is_offender(p)]
    assert not offenders, (
        "these files open a sqlite3 connection with NO busy timeout — a transient lock (a second "
        "process, a backup/AV agent) will raise 'database is locked' instantly instead of waiting, "
        "and on a read path degrade to a phantom-empty result (the #687 vault class). Add "
        "`conn.execute(\"PRAGMA busy_timeout=5000\")` after connect (the canonical default), or pass "
        "`sqlite3.connect(..., timeout=N)`:\n  " + "\n  ".join(offenders)
    )


def test_allowlist_entries_still_match():
    """Ratchet: every allowlisted file must STILL be an offender-ignoring-allowlist. When a site
    is fixed (or its connect removed), its entry must be deleted — a file-level allowlist would
    otherwise hide a NEW raw connect added to the same file."""
    stale = []
    for rel in _ALLOWLIST:
        path = REPO / rel
        if not path.exists():
            stale.append(f"{rel} (file gone)")
        elif not _is_offender(path):
            stale.append(f"{rel} (no longer matches — remove the allowlist entry)")
    assert not stale, "stale allowlist entries: " + ", ".join(stale)


def test_the_rule_catches_a_regression():
    """Guard the guard: a raw connect is flagged; a PRAGMA busy_timeout OR a timeout kwarg clears
    it; a file with no sqlite3.connect at all is never flagged."""
    import tempfile

    def offender(src: str) -> bool:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.py"
            p.write_text(src, encoding="utf-8")
            return _is_offender(p)

    assert offender("import sqlite3\nc = sqlite3.connect('x.db')\n"), "missed a raw connect"
    assert not offender(
        "import sqlite3\nc = sqlite3.connect('x.db')\nc.execute('PRAGMA busy_timeout=5000')\n"
    ), "flagged a connect that sets the pragma"
    assert not offender(
        "import sqlite3\nc = sqlite3.connect('x.db', timeout=5.0)\n"
    ), "flagged a connect that passes timeout="
    assert not offender("x = 1\n"), "flagged a file with no sqlite3.connect"


def test_the_nine_swept_stores_are_covered():
    """The stores fixed in this sweep must not regress to no-busy-timeout. This is a positive
    ratchet distinct from the negative offender scan: if any of these loses its busy timeout, the
    class is back even though the offender list might stay green via an unrelated allowlist edit."""
    swept = [
        "core/navig/memory/knowledge_graph.py",
        "core/navig/memory/links_db.py",
        "core/navig/memory/storage.py",
        "core/navig/memory/knowledge_base.py",
        "core/navig/identity/store.py",
        "core/navig/bot/stats_store.py",
        "core/navig/gateway/channels/telegram_formatter.py",
        "core/navig/agent/context/daily_log.py",
        "core/navig/agent/pattern_observer.py",
    ]
    regressed = [rel for rel in swept if _is_offender(REPO / rel)]
    assert not regressed, "swept stores lost their busy timeout: " + ", ".join(regressed)
