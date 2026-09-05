"""Importing the proactive engine must not construct it.

``engine.py`` ended with ``_engine = ProactiveEngine()`` at module scope, so importing the
module did disk I/O: ``__init__`` builds a ``TriggerManager``, which mkdirs
``<config_dir>/triggers``, and reaching ``config_dir()`` builds the ConfigManager, which
mkdirs the config root and its subdirs.

Two costs. Every consumer of this module paid that work whether or not it ever asked for the
engine. And at **import** it runs before any test isolation can apply, so it landed in the
operator's real ``~/.navig`` -- measured at 33 of the 37 remaining mkdirs there during a full
suite run, after the browser/desktop dirs were fixed (#1143).

Asserted in a **subprocess**: this module is imported by much of the suite, so checking
``_engine is None`` in-process would only prove that something else got there first. A
source-level check would not do either -- the question is what the import *does*, not what
it looks like.
"""
from __future__ import annotations

import subprocess
import sys

_PROBE = """
import os
import sys
import tempfile
from pathlib import Path

# A FRESH config dir, so "was anything created?" is a real question. Pointing this at the
# operator's home would make the check depend on their filesystem: TriggerManager only
# mkdirs when the directory is absent, so once ~/.navig/triggers exists the call never
# happens again and the assertion can no longer fail. That is how this test was vacuous in
# its first version -- it passed against the eager singleton it exists to catch.
_cfg = tempfile.mkdtemp(prefix="lazyprobe-")
os.environ["NAVIG_CONFIG_DIR"] = _cfg
os.environ["NAVIG_SKIP_ONBOARDING"] = "1"

created = []
_real_mkdir = Path.mkdir


def _probe(self, *a, **k):
    created.append(str(self))
    return _real_mkdir(self, *a, **k)


Path.mkdir = _probe
import navig.agent.proactive.engine as engine

Path.mkdir = _real_mkdir
print("CONSTRUCTED", engine._engine is not None)
print("TRIGGER_MKDIRS", sum(1 for m in created if m.lower().endswith("triggers")))

# init_providers() imports optional integrations (google.auth et al) and raises without
# them. That is orthogonal to laziness and predates this change, so stub it out: the
# question here is whether the accessor builds exactly one engine, not whether Google
# Calendar is installed.
engine.ProactiveEngine.init_providers = lambda self: None
print("ACCESSOR_WORKS", engine.get_proactive_engine() is not None)
print("CACHED", engine.get_proactive_engine() is engine.get_proactive_engine())
"""


def _run_probe() -> dict[str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=180
    )
    assert proc.returncode == 0, f"probe failed:\n{proc.stdout}\n{proc.stderr}"
    out = {}
    for line in proc.stdout.splitlines():
        key, _, value = line.partition(" ")
        if value:
            out[key] = value
    return out


def test_importing_the_module_does_not_construct_the_engine() -> None:
    result = _run_probe()

    assert result.get("CONSTRUCTED") == "False", (
        "importing navig.agent.proactive.engine constructed the engine. That builds a "
        "TriggerManager and a ConfigManager, so a plain import does disk I/O -- and at "
        "import it runs before any test isolation, landing in the operator's real ~/.navig."
    )


def test_importing_the_module_creates_no_trigger_directory() -> None:
    result = _run_probe()

    assert result.get("TRIGGER_MKDIRS") == "0", (
        f"import created {result.get('TRIGGER_MKDIRS')} triggers director(ies). A plain "
        "import must not touch the filesystem -- at import this runs before any test "
        "isolation, so on a real run it lands in the operator's ~/.navig."
    )


def test_the_accessor_still_returns_one_cached_engine() -> None:
    """The laziness must not have cost the singleton property."""
    result = _run_probe()

    assert result.get("ACCESSOR_WORKS") == "True", "get_proactive_engine() returned nothing"
    assert result.get("CACHED") == "True", (
        "get_proactive_engine() built a new engine per call -- it is a process-wide singleton"
    )
