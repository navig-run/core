"""Creative Mode — NAVIG's proactive capability intelligence.

An orchestrator package (never a parallel system): it *reads* the existing
registries (command manifest, ToolMeta, ``hub.collect_store``, modules, skills,
agent tools), *mines* the existing operation history, and *delivers* through the
existing notify router. It stores no capability truth of its own — the index
snapshot under ``data_dir()/creative/`` is a rebuildable derivative.

Law: **zero LLM imports in this package** (guard-tested by
``tests/creative/test_creative_no_llm_calls.py``). All inference happens in the
chat agent turn (``navig/agent/``); everything here is deterministic Python.

Heavy submodules are imported lazily by their callers so ``navig help`` / the
main.py fast path never pays for them.
"""

from __future__ import annotations

from typing import Any

__all__ = ["get_agent_mode", "is_creative", "status_payload"]


def get_agent_mode() -> str:
    """Canonical agent mode (``standard`` | ``creative``); ``standard`` on failure."""
    from navig.creative.mode import get_agent_mode as _get

    return _get()


def is_creative() -> bool:
    """True when the operator's agent mode is Creative (authoring/pro scan armed)."""
    from navig.creative.mode import is_creative as _is

    return _is()


def status_payload() -> dict[str, Any]:
    """One shared status shape for the CLI, the deck route and tests.

    Lives here (not in the CLI module) so the deck route cannot drift from
    what `navig creative status` reports — same numbers, one implementation.
    """
    import time

    from navig.creative import proposals as _proposals
    from navig.creative.index import status as _index_status
    from navig.creative.mode import get_agent_mode as _mode
    from navig.creative.policy import load_policy as _policy

    rows = _proposals.list_proposals()
    by_status: dict[str, int] = {}
    for row in rows:
        key = str(row.get("status", "?"))
        by_status[key] = by_status.get(key, 0) + 1
    last = _proposals.last_scan_at()
    return {
        "mode": _mode(),
        "index": _index_status(),
        "last_scan_age_s": (max(0.0, time.time() - float(last)) if last else None),
        "proposals": {"total": len(rows), "by_status": by_status},
        "publish": _policy(),
    }
