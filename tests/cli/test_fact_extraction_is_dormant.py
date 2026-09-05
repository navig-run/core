"""`register_fact_extraction` records nothing — asserted, not merely written down.

A "not wired" note is only useful while it is true, and it goes stale in the dangerous
direction: a reader who believes a live path is dormant reasons about the system incorrectly.
The sibling of this idea is `tests/quality/test_dormant_modules.py`, which pins three modules
documented as NOT WIRED.

This one is worth pinning for a sharper reason: the existing coverage actively *certifies it
green*. `tests/cli/test_cli_middleware.py` asserts only that an atexit handler was
**registered** — the "registered ≠ effective" shape — so nothing in 29,000 tests notices that
the handler does nothing.

Each test below fails if one of the four independent reasons stops holding. That is the point:
if someone wires this, the suite says so and the product/privacy decision gets made explicitly
rather than by accident. **Do not "fix" these by deleting them** — remove the entry, delete the
NOT WIRED block from the docstring, and answer the question in it.
"""
from __future__ import annotations

import inspect

import navig.cli.middleware as mw


def test_the_memory_manager_still_lacks_the_method_it_is_asked_for() -> None:
    """Reason 1: the worker probes `record_command` on the wrong object.

    It lives on `UserProfile`, not `MemoryManager`. If this starts passing, fact extraction
    may now be live — which is a decision, not a bugfix.
    """
    from navig.memory.manager import MemoryManager

    missing = [
        name for name in ("record_command", "fact_extractor", "store_facts")
        if hasattr(MemoryManager, name)
    ]
    assert not missing, (
        f"MemoryManager now has {missing} — register_fact_extraction may no longer be "
        "dormant. Decide deliberately whether every CLI command should be written into the "
        "user's profile, then update its NOT WIRED docstring."
    )


def test_the_method_it_wants_lives_somewhere_else() -> None:
    """The claim in the docstring is checkable, so check it."""
    from navig.memory.user_profile import UserProfile

    assert hasattr(UserProfile, "record_command"), (
        "record_command has moved; the NOT WIRED note names UserProfile as its home"
    )


def test_the_worker_is_still_a_daemon_thread_started_at_exit() -> None:
    """Reason 3: measured, such a thread only completes if its work fits in ~5 ms.

    Its first statement imports `navig.memory.manager`, which costs ~229 ms. Asserted on the
    source because the behaviour itself is interpreter shutdown — not something a test can
    observe from inside the process.
    """
    src = inspect.getsource(mw.register_fact_extraction)

    assert "atexit.register" in src, "no longer registered at exit — re-check the dormancy"
    assert "daemon=True" in src, (
        "the worker is no longer a daemon thread; if it is now joined, it may actually run"
    )


def test_the_docstring_still_carries_the_warning() -> None:
    """The note and the assertions have to travel together, or one rots without the other."""
    doc = inspect.getdoc(mw.register_fact_extraction) or ""

    assert "NOT WIRED" in doc, (
        "the NOT WIRED block was removed from register_fact_extraction's docstring while "
        "these tests still assert it is dormant — one of the two is now lying"
    )
