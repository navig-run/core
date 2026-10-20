"""Keep two replaced xdist workers from aborting the whole test run.

The gate runs ``-n N --dist loadgroup --timeout=30``. On Windows pytest-timeout can only use
its thread method, which ``os._exit()``s the worker holding a test past the ceiling. xdist
then starts a replacement — and when TWO workers die close together, its LoadScope
scheduler (``loadgroup`` subclasses it) crashes the controller:

* dead workers are never removed from ``registered_collections``, so
  ``collection_is_completed`` is already true when the FIRST replacement registers;
* ``schedule()`` / ``remove_node()`` then ``_reschedule()`` every node, including the second
  replacement, which has been added but has not finished collecting;
* ``_assign_work_unit`` reads ``registered_collections[node]`` → ``KeyError`` →
  ``INTERNALERROR``, and every test not yet run is silently never run.

Measured 2026-10-10: two full runs stopped at 8 % (3,206 of 34,358) and 49 % (16,752), each
after two back-to-back worker kills. pytest-xdist 3.8.0 is the latest release.

The guard skips a node that has not registered its collection. Nothing is lost: that node's
own ``worker_collectionfinish`` calls ``schedule()``, which serves it then. The killed test
is still reported as failed — the guard changes how much of the suite runs after it, never
whether it counts.
"""

from __future__ import annotations

_MARK = "_navig_reschedule_guard"


def install() -> bool:
    """Patch ``LoadScopeScheduling._reschedule`` once; return whether the guard is active."""
    try:
        from xdist.scheduler.loadscope import LoadScopeScheduling  # noqa: PLC0415
    except ImportError:  # xdist not installed: nothing to guard
        return False

    original = LoadScopeScheduling._reschedule
    if getattr(original, _MARK, False):
        return True

    def _reschedule(self, node) -> None:  # noqa: ANN001 — xdist's own signature
        if node not in self.registered_collections:
            return  # a replacement still collecting; its collectionfinish schedules it
        original(self, node)

    setattr(_reschedule, _MARK, True)
    LoadScopeScheduling._reschedule = _reschedule  # type: ignore[method-assign]
    return True
