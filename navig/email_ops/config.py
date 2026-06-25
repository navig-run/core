"""Email-ops config — filter rules + briefing schedules, persisted as JSON at
``~/.navig/email/config.json``. Best-effort; a missing/corrupt file yields the
empty defaults so the service never crashes."""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from navig.platform import paths

logger = logging.getLogger("navig.email_ops")


def _config_path() -> Path:
    return paths.data_dir().parent / "email" / "config.json"  # ~/.navig/email/config.json


_DEFAULT: dict[str, Any] = {
    "monitor_enabled": True,
    "rules": [],       # [{id,name,from,subject_contains,subject_exact,body_words[],channels[],enabled}]
    "briefings": [],   # [{id,name,query|label,cadence,hour,weekday,day,channels[],focus,enabled}]
    "state": {"seen_ids": [], "last_brief": {}},
}


def load_config() -> dict[str, Any]:
    p = _config_path()
    if not p.exists():
        return json.loads(json.dumps(_DEFAULT))
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # merge defaults for forward-compat
        out = json.loads(json.dumps(_DEFAULT))
        out.update({k: data.get(k, out[k]) for k in out})
        out["state"] = {**_DEFAULT["state"], **(data.get("state") or {})}
        return out
    except Exception:
        logger.debug("email config load failed; using defaults", exc_info=True)
        return json.loads(json.dumps(_DEFAULT))


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # normalise ids on rules/briefings
    for coll in ("rules", "briefings"):
        for item in cfg.get(coll, []) or []:
            if not item.get("id"):
                item["id"] = uuid.uuid4().hex[:8]
    p.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return cfg


def get_state() -> dict[str, Any]:
    return load_config().get("state") or {"seen_ids": [], "last_brief": {}}


def update_state(**changes: Any) -> None:
    cfg = load_config()
    cfg.setdefault("state", {})
    cfg["state"].update(changes)
    save_config(cfg)
