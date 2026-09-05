"""Hide modules from ``sys.modules`` for a test, and put back exactly what was there.

``monkeypatch.delitem(sys.modules, …)`` restores only what existed **when it ran**. A
module first imported *inside* the eviction is tracked by nothing: it outlives teardown
while its parent package is rolled back to the original object, which has no such
attribute. ``sys.modules`` is then holding a child its parent does not know about, and
``importlib.import_module`` hands that child straight back from the cache **without**
re-binding the parent.

The damage lands in a different file. Dotted ``monkeypatch.setattr`` resolves by attribute
traversal, so every later ``setattr("navig.gateway.routes.core.X", …)`` in the same xdist
worker raises ``AttributeError``. That is measured, not theoretical: it reddened both
WS-heartbeat tests in ``tests/gateway/test_gateway_core_routes.py`` and blocked four
pre-push gate runs while reading as a timing flake, because whether it bit depended only
on how xdist distributed the files (#1109).

:func:`evicted_modules` is a **context manager** because the purge is not optional — there
is no way to evict through this helper and forget it. ``tests/quality/
test_sys_modules_eviction.py`` keeps raw ``monkeypatch.delitem(sys.modules, …)`` out of
the suite so the two cannot drift apart again.
"""

from __future__ import annotations

import contextlib
import sys
from collections.abc import Iterator

import pytest


def matches(mod: str, prefixes: tuple[str, ...]) -> bool:
    """True when *mod* is one of *prefixes* or lives beneath one."""
    return any(mod == p or mod.startswith(p + ".") for p in prefixes)


@contextlib.contextmanager
def evicted_modules(
    monkeypatch: pytest.MonkeyPatch, prefixes: tuple[str, ...]
) -> Iterator[None]:
    """Make every module under *prefixes* look unimported for the duration of the block.

    Restores the original ``sys.modules`` entries and parent-package attributes on exit,
    and drops anything imported while inside.
    """
    for mod in list(sys.modules):
        if not matches(mod, prefixes):
            continue
        monkeypatch.delitem(sys.modules, mod, raising=False)
        # `import a.b` binds `b` as an ATTRIBUTE on package `a` as well, and delitem does
        # not track that. Re-importing inside rebinds it to a NEW module object which
        # survives teardown, so afterwards `sys.modules["a.b"]` and `a.b` are two
        # different modules holding two sets of globals — and `monkeypatch.setattr(
        # "a.b.x", …)` patches the stale duplicate while the code under test reads the
        # original, so the patch silently does nothing. Re-registering the attribute at
        # its CURRENT value makes monkeypatch restore it on teardown.
        #
        # It made tests/voice/test_voice_input.py's `test_no_backend_error` fail whenever
        # xdist put an evicting file first in the same worker: its monkeypatch missed, the
        # handler used the really-installed faster-whisper, and a UNIT test downloaded a
        # model from huggingface.co — inside a suite whose marker promises mocked deps.
        parent_name, _, leaf = mod.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and hasattr(parent, leaf):
            monkeypatch.setattr(parent, leaf, getattr(parent, leaf))
    try:
        yield
    finally:
        # Everything under *prefixes* is now something imported INSIDE the block: the
        # originals are held by monkeypatch, not by sys.modules. Dropping them leaves the
        # caller's monkeypatch to restore exactly the original set, with no orphan whose
        # parent package disowns it. This runs first because the `with` closes before the
        # caller's `monkeypatch` fixture finalises.
        for mod in [m for m in sys.modules if matches(m, prefixes)]:
            del sys.modules[mod]
