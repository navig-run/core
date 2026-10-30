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
    # Two attribute-leak shapes neither monkeypatch tracks, and both must come back as they
    # were — a leak here is not cosmetic: it is the exact orphan
    # `test_no_module_imported_inside_outlives_the_no_plugins_world` reports
    # (`navig.commands.voice` sitting in sys.modules while `navig.commands` disowns it,
    # red and order-dependent under xdist):
    #   A) the entry exists but its parent attribute does not (a prior test popped it) —
    #      the block re-binds the attribute; restoring only the entry re-diverges the pair.
    #   B) the entry does not exist — the block BIRTHS entry and attribute together. The
    #      entry is dropped in the finally below, but nothing ever saw the attribute, so it
    #      dangles; the next cycle then registers that stale object as the value to restore
    #      and the end state is (entry restored, attribute stale).
    pre_existing: set[str] = set()
    absent_attrs: list[tuple[object, str]] = []
    # Snapshots reinstated by the finally below, so the pre-context state is back the
    # moment the `with` closes -- not only when the caller's monkeypatch finalises, which
    # left the originals missing from sys.modules for any code after the block.
    saved_modules: dict[str, object] = {}
    saved_attrs: list[tuple[object, str, object]] = []
    for mod in list(sys.modules):
        if not matches(mod, prefixes):
            continue
        pre_existing.add(mod)
        saved_modules[mod] = sys.modules[mod]
        parent_name, _, leaf = mod.rpartition(".")
        parent = sys.modules.get(parent_name)
        if parent is not None and not hasattr(parent, leaf):
            absent_attrs.append((parent, leaf))
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
            saved_attrs.append((parent, leaf, getattr(parent, leaf)))
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
            parent_name, _, leaf = mod.rpartition(".")
            parent = sys.modules.get(parent_name)
            obj = sys.modules[mod]
            del sys.modules[mod]
            # B) born inside this block → restore absence of the attribute too (monkeypatch
            # never saw it; only remove it if it still points at the object we just dropped).
            if mod not in pre_existing and parent is not None and getattr(parent, leaf, None) is obj:
                try:
                    delattr(parent, leaf)
                except AttributeError:
                    pass
        # A) attribute missing before the block → still missing after (see the header note).
        for parent, leaf in absent_attrs:
            if hasattr(parent, leaf):
                try:
                    delattr(parent, leaf)
                except AttributeError:
                    pass
        # Put the originals back now. The caller's monkeypatch still restores the same
        # objects at teardown, which is then a no-op.
        sys.modules.update(saved_modules)  # type: ignore[arg-type]
        for parent, leaf, value in saved_attrs:
            setattr(parent, leaf, value)
