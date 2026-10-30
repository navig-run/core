"""Durable Webflow run checkpoints — Phase E of Webflows.

A headless run writes a checkpoint after every step, so an interrupted run (a crash, a
killed session, a machine reboot) can be resumed instead of re-doing its side-effecting
steps::

    <config_dir>/webflows/<flow_id>/runs/<run_id>.json    schema navig.webflow-run/1

**Sanitized by construction:** the writer re-serializes a WHITELIST of fields (ids, step
verdicts, statuses, a URL with secrets stripped). Parameter values — a secret or not — are
never accepted, so no path can store one; error texts are not stored either (the runner
already scrubs secrets, but a checkpoint is evidence for a resume, not a log).

**Failed read never becomes a destructive write** (the repo's sharp edge): loading a
corrupt checkpoint returns ``None`` and a subsequent :func:`write` for the same run refuses
to clobber it — the run id is then reported as unusable instead of silently resetting
another run's state. A *new* run id creates its file.

Resume eligibility is the caller's decision; this module only records what happened, in
enough detail (``next_index``, ``last_url``, per-step verdicts) to continue safely.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from navig.webflows.schema import WebflowError, origin_of

logger = logging.getLogger(__name__)

SCHEMA = "navig.webflow-run/1"
_RUN_ID = re.compile(r"^wfrun_[a-z0-9]{4,24}$")
_SECRET_QUERY = re.compile(
    r"^(code|auth|key|apikey|api_key|token|access_token|id_token|sig|signature|hash|hmac|salt|otp|pin|"
    r"password|pwd|sid|session|nonce|jwt|bearer|secret|ssn|cvv)$",
    re.I,
)
#: The ONLY fields ever written — everything else a caller carries is dropped.
_STEP_KEYS = ("id", "action", "ok", "ms", "category")
STATUS = ("running", "interrupted", "needs_user", "verified", "completed", "failed", "stopped")


def new_run_id(now: _dt.datetime | None = None) -> str:
    t = (now or _dt.datetime.now(_dt.timezone.utc)).strftime("%y%m%d%H%M%S")
    tail = os.urandom(3).hex()
    return f"wfrun_{t}{tail}"


def sanitize_url(url: str) -> str:
    """origin + path + query without secret-looking params, no fragment (token landings)."""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    keep = "&".join(kv for kv in parts.query.split("&") if kv and not _SECRET_QUERY.match(kv.split("=", 1)[0]))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, keep, ""))


def _path(flow_dir: Path, run_id: str) -> Path:
    if not _RUN_ID.match(str(run_id)):
        raise WebflowError([f"{run_id!r} is not a webflow run id"])
    return flow_dir / "runs" / f"{run_id}.json"


def load(flow_dir: Path, run_id: str) -> dict[str, Any] | None:
    """The checkpoint, or ``None`` when it does not exist, is unreadable, or is not ours."""
    p = _path(flow_dir, run_id)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        logger.warning("webflow run checkpoint %s unreadable: %s", p.name, type(exc).__name__)
        return None
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA or raw.get("run_id") != run_id:
        logger.warning("webflow run checkpoint %s is not a %s document", p.name, SCHEMA)
        return None
    return raw


def write(flow_dir: Path, cp: dict[str, Any]) -> dict[str, Any]:
    """Persist a checkpoint atomically; returns the stored (whitelisted) document.

    Refuses to overwrite an existing file whose current contents cannot be read — the one
    case where a write would destroy state the reader failed to parse.
    """
    run_id = str(cp.get("run_id") or "")
    status = str(cp.get("status") or "running")
    if not _RUN_ID.match(run_id):
        raise WebflowError([f"{run_id!r} is not a webflow run id"])
    if status not in STATUS:
        raise WebflowError([f"unknown run status {status!r}"])
    flow_id = str(cp.get("flow_id") or "")
    if not flow_id:
        raise WebflowError(["a checkpoint needs its flow id"])
    next_index = cp.get("next_index", 0)
    if isinstance(next_index, bool) or not isinstance(next_index, int) or next_index < 0:
        raise WebflowError(["next_index must be a non-negative integer"])

    p = _path(flow_dir, run_id)
    if p.exists() and load(flow_dir, run_id) is None:
        raise WebflowError([f"run {run_id} exists but cannot be read — refusing to overwrite it"])

    steps = []
    for s in (cp.get("steps") or [])[:400]:
        if not isinstance(s, dict):
            continue
        item: dict[str, Any] = {}
        for k in _STEP_KEYS:
            if k not in s:
                continue
            v = s[k]
            if k in ("id", "action", "category"):
                if isinstance(v, str):
                    item[k] = v[:120]  # strings only, bounded
            elif k == "ok":
                item[k] = bool(v)
            elif k == "ms" and isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 86_400_000:
                item[k] = v
        steps.append(item)
    # Microseconds matter: two runs of the same second must still sort newest-first.
    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    doc = {
        "schema": SCHEMA,
        "run_id": run_id,
        "flow_id": flow_id,
        "revision": cp.get("revision") if isinstance(cp.get("revision"), int) and not isinstance(cp.get("revision"), bool) else None,
        "next_index": next_index,
        "status": status,
        "started_at": _stamp(cp.get("started_at")) or now,
        "updated_at": now,
        "last_url": sanitize_url(str(cp.get("last_url") or "")),
        "steps": steps,
    }
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(doc, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass
    return doc


def _stamp(value: Any) -> str | None:
    """A caller-supplied ISO timestamp, kept only when it looks like one."""
    if isinstance(value, str) and 20 <= len(value) <= 40 and value[:4].isdigit():
        return value
    return None


def list_runs(flow_dir: Path) -> list[dict[str, Any]]:
    """Newest-first summaries of every readable checkpoint of one flow."""
    runs = flow_dir / "runs"
    if not runs.is_dir():
        return []
    out = []
    for f in runs.glob("wfrun_*.json"):
        cp = load(flow_dir, f.stem)
        if cp:
            out.append({k: cp.get(k) for k in ("run_id", "flow_id", "revision", "next_index", "status", "started_at", "updated_at")})
    out.sort(key=lambda c: c.get("updated_at") or "", reverse=True)
    return out


def resume_plan(cp: dict[str, Any], doc: dict[str, Any]) -> tuple[int, str] | None:
    """``(next_index, url)`` where a resume may continue — or ``None`` when it must not.

    Refuses when the checkpoint does not belong to this flow/revision, is terminal, or its
    recorded page is outside the flow's origins (a tampered or stale file must not send the
    browser anywhere): the caller then either starts fresh or fails closed.
    """
    if cp.get("flow_id") != doc.get("id") or cp.get("status") not in ("running", "interrupted", "needs_user"):
        return None
    idx = cp.get("next_index", 0)
    if isinstance(idx, bool) or not isinstance(idx, int) or not 0 <= idx <= len(doc.get("steps", [])):
        return None
    # Sanitize here too: a caller may hand over a raw URL; the file's is already clean.
    url = sanitize_url(str(cp.get("last_url") or ""))
    target = url or doc["start_url"]
    if origin_of(target) not in doc.get("origins", []):
        return None
    return idx, target


__all__ = ["SCHEMA", "STATUS", "load", "list_runs", "new_run_id", "resume_plan", "sanitize_url", "write"]
