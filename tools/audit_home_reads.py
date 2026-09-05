"""Report which tests read the operator's REAL ``~/.navig``.

The suite isolates config with ``NAVIG_CONFIG_DIR`` (see ``tests/conftest.py``), but
isolation is a property of *state*, not of intent. A test that reads the operator's live
config, vault or gateway state answers differently on their machine than on anyone else's,
and it fails in the full suite while passing alone — the classic read of a flake.

This is an **opt-in diagnostic**, not a gate: it wraps ``open`` process-wide, which is too
invasive to run on every push, and the remaining reads are import-time ones it cannot fix.

Run it::

    cd core
    PYTHONPATH=tools py -3.13 -m pytest tests -q -p audit_home_reads -n auto

Write the raw records to a file as well::

    HOME_AUDIT_LOG=audit.tsv PYTHONPATH=tools py -3.13 -m pytest ... -p audit_home_reads

**Reading the result.** Reads attributed to ``<import/collect>`` happen before any test and
before the session's isolation fixture can run; they are reads only, the per-test singleton
reset in ``conftest`` keeps them from leaking into tests, and they are the expected floor.
Anything attributed to a **test nodeid** is the finding: that test saw the operator's real
machine.

**What the number counts.** One line per distinct ``(test, file)`` pair, deduplicated
in the controller — not raw syscalls. Sixteen xdist workers each importing
``theme.py`` and reading ``terminal.json`` is 16 syscalls but **one** actionable fact,
and a raw count would move with ``-n`` so no one could check it against a baseline.

Measured baseline (2026-08-26, full suite, ``-n auto``): **3 reads, all
``<import/collect>``** — ``config.yaml``, ``.config_cache.pkl``, ``terminal.json``,
one each, and **zero** inside tests.

It has already paid for itself twice. The first run recorded 106 reads across 32 tests and
led to:

* ``GatewayConfig.storage_dir`` defaulting to a literal ``"~/.navig"`` instead of
  ``paths.config_dir()`` — a production split brain, and 22 of those reads (#1121);
* three ``teardown_method``s that popped ``NAVIG_CONFIG_DIR`` instead of restoring it,
  leaving every later test in that xdist worker reading the real home — which was also the
  upstream cause of an image-generation test that failed only in the full suite (#1125).

⚠ A detector that loads nothing reports a clean zero and looks exactly like success. The
first run of this probe measured **nothing** because ``PYTHONPATH`` was set without ``-p``.
The summary below always prints the number of reads it *observed*, so a silent no-op is
visible rather than mistaken for a pass.
"""

from __future__ import annotations

import builtins
import os
from collections import Counter
from pathlib import Path

_REAL_HOME = str(Path.home() / ".navig").lower()
_IMPORT_PHASE = "<import/collect>"

_current = {"nodeid": _IMPORT_PHASE}
_seen: set[tuple[str, str]] = set()
# Records are appended to a SHARED FILE, not kept in memory. Under `-n auto` every read
# happens in a worker PROCESS while pytest_terminal_summary runs in the controller, so an
# in-memory list reports a confident 0 -- the exact failure this tool exists to expose.
# The controller seeds HOME_AUDIT_LOG before workers spawn, so they inherit it.
_log = {"path": ""}

_real_open = builtins.open
_real_read_text = Path.read_text
_real_read_bytes = Path.read_bytes


def _note(path: object) -> None:
    try:
        resolved = str(path).lower()
    except Exception:  # noqa: BLE001 — a diagnostic must never break the run
        return
    if not resolved.startswith(_REAL_HOME):
        return
    tail = resolved[len(_REAL_HOME):]
    tail = tail[1:] if tail[:1] in ("\\", "/") else tail
    tail = tail or "<root>"
    key = (_current["nodeid"], tail)
    if key in _seen:  # one line per (test, file); a chatty reader must not drown the report
        return
    _seen.add(key)
    path = _log["path"]
    if not path:
        return
    try:
        with _real_open(path, "a", encoding="utf-8") as fh:  # _real_open: never recurse
            fh.write(f"{key[0]}\t{key[1]}\n")
    except OSError:
        pass


def _open(file, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    _note(file)
    return _real_open(file, *args, **kwargs)


def _read_text(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    _note(self)
    return _real_read_text(self, *args, **kwargs)


def _read_bytes(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
    _note(self)
    return _real_read_bytes(self, *args, **kwargs)


builtins.open = _open
Path.read_text = _read_text
Path.read_bytes = _read_bytes


def pytest_configure(config) -> None:  # noqa: ANN001
    """Seed the shared record file. Workers inherit it through the environment."""
    import tempfile

    path = os.environ.get("HOME_AUDIT_LOG", "")
    if not path:
        path = str(Path(tempfile.gettempdir()) / "navig-home-read-audit.tsv")
        os.environ["HOME_AUDIT_LOG"] = path
    _log["path"] = path
    if not hasattr(config, "workerinput"):  # controller: start a fresh record
        try:
            Path(path).write_text("", encoding="utf-8")
        except OSError:
            pass


def pytest_runtest_setup(item) -> None:  # noqa: ANN001
    _current["nodeid"] = item.nodeid


def pytest_runtest_call(item) -> None:  # noqa: ANN001
    _current["nodeid"] = item.nodeid


def pytest_terminal_summary(terminalreporter, exitstatus, config) -> None:  # noqa: ANN001, ARG001
    """Print the finding, and always print the observed count so a no-op is visible.

    The controller aggregates; workers stay quiet. Records come from the shared file
    because under `-n auto` the reads happen in worker processes and this hook does not.
    """
    if hasattr(config, "workerinput"):
        return

    records: set[tuple[str, str]] = set()
    path = _log["path"]
    if path:
        try:
            with _real_open(path, encoding="utf-8") as fh:
                for line in fh:
                    nodeid, _, name = line.rstrip("\n").partition("\t")
                    if name:
                        records.add((nodeid, name))
        except OSError:
            pass

    in_test = sorted(r for r in records if r[0] != _IMPORT_PHASE)
    at_import = sorted(r for r in records if r[0] == _IMPORT_PHASE)

    write = terminalreporter.write_line
    write("")
    write(f"real-home audit: {len(records)} read(s) observed under {_REAL_HOME}")
    write(
        f"  {len(at_import)} at import/collect (expected floor)"
        f" | {len(in_test)} inside tests"
    )

    if at_import:
        write("  import/collect, by file:")
        for name, count in sorted(Counter(f for _, f in at_import).items()):
            write(f"    {count:4}  {name}")

    if in_test:
        write("  TESTS READING THE OPERATOR'S REAL HOME -- these are the finding:")
        for nodeid, name in in_test:
            write(f"    {nodeid}  ->  {name}")
    else:
        write("  no test read the operator's real home")

    if path:
        write(f"  records: {path}")
