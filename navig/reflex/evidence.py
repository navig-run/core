"""Campaign evidence — prove a routed answer inside the campaign workspace.

``navig tasks campaigns checkpoint`` accepts only evidence paths that exist
**inside** the campaign workspace (``navig_tasks/campaign.py``'s rule), so this
writer does exactly one thing: drop ``answer-<token>.json`` in that directory and
return the path. The router never opens ``tasks.db`` — the campaign layer stays the
single writer, and the operator cites the file with the existing checkpoint verb.

No policy language, no receipt format, no second journal: just a file the existing
rule accepts.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from navig.reflex.answer_bridge import answer_as_dict
from navig.reflex.errors import ReflexError

#: Tokens are ``plan-<hex>``; keep the filename to that alphabet plus a fallback.
_SAFE_TOKEN = re.compile(r"[^a-z0-9_-]+")


def _safe_name(token: str) -> str:
    cleaned = _SAFE_TOKEN.sub("", str(token or "").strip().lower())
    return cleaned or "current"


def _answer_token(answer: Any) -> str:
    try:
        payload = answer_as_dict(answer)
    except Exception:  # noqa: BLE001 — a missing envelope still names a file
        return ""
    plan = payload.get("plan") or {}
    if isinstance(plan, dict):
        token = plan.get("commit_token") or plan.get("token")
        if token:
            return str(token)
    data = payload.get("data") or {}
    if isinstance(data, dict):
        for key in ("commit_token", "token"):
            if data.get(key):
                return str(data[key])
        plan = data.get("plan") or {}
        if isinstance(plan, dict) and plan.get("token"):
            return str(plan["token"])
    return ""


def write_evidence(answer: Any, workspace: str | Path, *, token: str = "") -> Path:
    """Write the Answer envelope inside *workspace* and return the created path."""
    root = Path(workspace).expanduser()
    if not root.is_absolute():
        raise ReflexError(
            f"campaign workspace must be an absolute path, got {str(workspace)!r}",
            "pass the campaign's own workspace directory",
        )
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    name = f"answer-{_safe_name(token or _answer_token(answer))}.json"
    target = (root / name).resolve()
    if target.parent != root:
        raise ReflexError(
            f"evidence path escaped the campaign workspace: {target}",
            "the campaign rule requires evidence inside the workspace",
        )
    payload = answer_as_dict(answer)
    payload.setdefault("evidence_written_at", _now_iso())
    target.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n",
        encoding="utf-8",
    )
    return target


def _now_iso() -> str:
    from navig.reflex.plans import utc_now_iso  # noqa: PLC0415

    return utc_now_iso()


__all__ = ["write_evidence"]
