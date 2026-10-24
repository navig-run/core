"""The ``/status`` session snapshot card — data collection + rendering.

The card used to be assembled inline in ``telegram_commands._handle_status``. It now
also surfaces the three initiatives the brain is running, each from its OWN artifact
(read-only, best-effort — the card never fails because one of them is missing):

* **Genesis** — this node's birth identity from ``~/.navig/genesis.json``
  (``navig.onboarding.genesis``): node id, name, birth date.
* **Brain unify** — the authoritative-brain lease cached from the Lighthouse
  ``/api/ownership`` endpoint (``~/.navig/brain/authority-cache.json``). One brain,
  portable ownership: the card says WHO the brain is, its epoch and how much of its
  TTL is left; a stale lease is called out instead of hidden.
* **self aware** — the operator-state inference
  (``navig.agent.proactive.user_state``) plus the last self-programming cycle from
  ``~/.navig/workspace/SELF-PROGRAMMING-RECEIPTS.md``.

And it stops conflating two different things: a **Space** can *be* a project checkout
(the operator's ``grabnite``), so the card names the git **Project** (name + branch)
separately from the Space. Before, a project-space read as if the space name were the
project.

Every collector is best-effort and returns an empty/None shape on failure — this runs
on the chat path, so a missing file must cost a line, never the card.
"""

from __future__ import annotations

import json
import platform
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Byte-identical prefix the regression test pins (Telegram refresh must not re-render
#: HTML). Changing it would break `tests/regression/test_telegram_refresh_keeps_formatting`.
CARD_HEADER = "🧭 <b>NAVIG Status</b> — <i>Session Snapshot</i>"

RECEIPTS_FILENAME = "SELF-PROGRAMMING-RECEIPTS.md"
_GIT_MARKERS = (".git",)


# ── tiny safe readers ────────────────────────────────────────────────────────


def _config_dir() -> Path:
    from navig.platform.paths import config_dir

    return Path(config_dir())


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _human_age(seconds: float | None) -> str:
    if seconds is None:
        return "—"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h {(s % 3600) // 60:02d}m"
    return f"{s // 86400}d {(s % 86400) // 3600:02d}h"


def _clip(value: Any, limit: int = 48) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# ── Genesis — the node's birth identity ─────────────────────────────────────


def genesis_identity() -> dict[str, Any]:
    """What ``navig space init`` wrote once: the immutable node identity."""
    data = _read_json(_config_dir() / "genesis.json")
    if not data:
        return {}
    return {
        "node_id": str(data.get("nodeId") or ""),
        "name": str(data.get("name") or ""),
        "born_at": str(data.get("bornAt") or ""),
        "engine": str(data.get("engineVersion") or ""),
        "url": str(data.get("qrTarget") or ""),
    }


def genesis_born_label(born_at: str) -> str:
    try:
        moment = datetime.fromisoformat(str(born_at).replace("Z", "+00:00"))
        return moment.strftime("%d %b %Y")
    except (TypeError, ValueError):
        return "—"


# ── Brain unify — the authoritative brain lease ─────────────────────────────


def brain_authority(host: str) -> dict[str, Any]:
    """The cached ``/api/ownership`` lease: who the brain is, epoch, TTL left.

    A cache is a snapshot, not a live lease. When this install is not the owner it may
    not have refreshed the cache for hours — the lease fields are then simply old, and
    calling that "stale" would blame the brain for our own stale copy. ``cache_fresh``
    therefore gates every freshness claim; an old snapshot is labelled with its age.
    """
    cache = _read_json(_config_dir() / "brain" / "authority-cache.json")
    record = cache.get("record") if isinstance(cache.get("record"), dict) else {}
    if not record:
        return {"known": False}
    try:
        heartbeat = float(record.get("heartbeat_at") or 0)
        ttl = float(record.get("ttl") or 0)
        fetched = float(cache.get("fetched_at") or 0)
    except (TypeError, ValueError):
        heartbeat, ttl, fetched = 0.0, 0.0, 0.0
    age = max(0.0, time.time() - heartbeat) if heartbeat else None
    left = max(0.0, ttl - age) if (ttl and age is not None) else None
    cache_age = max(0.0, time.time() - fetched) if fetched else None
    cache_fresh = cache_age is not None and cache_age <= (max(ttl, 60.0) if ttl else 300.0)
    owner_host = str(record.get("hostname") or "")
    return {
        "known": True,
        "hostname": owner_host,
        "role": str(record.get("role") or ""),
        "epoch": record.get("epoch"),
        "owner_id": str(record.get("owner_id") or ""),
        "age_s": age,
        "ttl": ttl,
        "lease_left_s": left,
        "is_local": bool(owner_host) and owner_host.lower() == (host or "").lower(),
        "cache_age_s": cache_age,
        "cache_fresh": cache_fresh,
        "stale": bool(cache_fresh and left is not None and left <= 0),
        "url": str(cache.get("url") or ""),
    }


# ── self aware — operator state + the last self-programming cycle ───────────


def operator_status() -> dict[str, Any]:
    try:
        from navig.agent.proactive.user_state import get_user_state_tracker

        tracker = get_user_state_tracker()
        state = tracker.get_operator_state()
        tod = tracker.get_time_of_day()
        last_seen = getattr(getattr(tracker, "stats", None), "last_seen", None)
        age = max(0.0, time.time() - float(last_seen)) if last_seen else None
        return {
            "state": getattr(state, "value", str(state)),
            "time_of_day": getattr(tod, "value", str(tod)),
            "last_seen_age_s": age,
        }
    except Exception:  # noqa: BLE001 — the self-aware layer is optional
        return {"state": "unknown", "time_of_day": "", "last_seen_age_s": None}


def self_programming() -> dict[str, Any]:
    """Last cycles from the receipts ledger the self-aware session appends to."""
    path = _config_dir() / "workspace" / RECEIPTS_FILENAME
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    cycles = re.findall(r"(?m)^##\s+Cycle\s+(\d+)\s+—\s+(.+?)\s*$", text)
    commits = re.findall(r"(?m)\*\*Commit\.\*\*\s+`?([0-9a-f]{7,40})`?", text)
    last = cycles[0] if cycles else None
    return {
        "path": str(path),
        "count": len(cycles),
        "last_cycle": ({"number": int(last[0]), "date": last[1].strip()} if last else None),
        "last_commit": commits[0] if commits else "",
    }


# ── Project vs Space ────────────────────────────────────────────────────────


def _git_dir_above(start: Path, *, max_depth: int = 8) -> Path | None:
    """The ``.git`` directory for *start* or any ancestor — worktrees included."""
    current = start
    for _ in range(max_depth + 1):
        marker = current / ".git"
        try:
            if marker.is_dir():
                return marker
            if marker.is_file():
                raw = marker.read_text(encoding="utf-8", errors="replace").strip()
                if raw.lower().startswith("gitdir:"):
                    target = raw.split(":", 1)[1].strip()
                    candidate = Path(target)
                    if not candidate.is_absolute():
                        candidate = (current / candidate).resolve()
                    return candidate if candidate.exists() else None
        except OSError:
            return None
        if current.parent == current:
            break
        current = current.parent
    return None


def project_of(path: Path | None) -> dict[str, Any]:
    """``{name, root, branch}`` when *path* sits inside a git checkout, else ``{}``."""
    if path is None:
        return {}
    try:
        resolved = Path(path).resolve()
    except OSError:
        return {}
    if not resolved.is_dir():
        return {}
    git_dir = _git_dir_above(resolved)
    if git_dir is None:
        return {}
    root = git_dir.parent
    branch = ""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
        if head.startswith("ref:"):
            branch = head.split("refs/heads/", 1)[-1].strip() or head
        elif head:
            branch = f"detached {head[:8]}"
    except OSError:
        pass
    return {"name": root.name, "root": str(root), "branch": branch}


def _project_candidate_roots(space_id: str) -> list[Path]:
    """Common checkout homes where an app project named after the space lives.

    A space is not always the checkout itself — on the brain, ``grabnite`` is a
    project under the dev tree while its space folder is a manifest. These are cheap
    stat probes, tried only when the space root is not itself a git checkout.
    """
    if not space_id:
        return []
    home = Path.home()
    names = (space_id, space_id.replace("-space", ""))
    homes = (
        home / "vibe",
        home / "projects",
        home / "apps",
        home / "code",
        home / "dev",
        home,
    )
    return [base / name for base in homes for name in names][:12]


def space_details(space_id: str) -> dict[str, Any]:
    """The active space's root + the project it happens to BE (or points at)."""
    root: str | None = None
    try:
        from navig.spaces.registry import canonical_root

        root = canonical_root(space_id)
    except Exception:  # noqa: BLE001 — a registry hiccup costs the path line only
        root = None
    project: dict[str, Any] = project_of(Path(root)) if root else {}
    if not project:
        for candidate in _project_candidate_roots(space_id):
            project = project_of(candidate)
            if project:
                break
    return {"id": space_id, "path": root, "project": project}


# ── counters (best-effort; every one may be missing) ────────────────────────


def work_counts(chat_id: int, user_id: int) -> dict[str, Any]:
    counts: dict[str, Any] = {"reminders": None, "tasks_due": None, "inbox_unread": None}
    try:
        from navig.store.runtime import get_runtime_store

        counts["reminders"] = len(get_runtime_store().get_user_reminders(user_id) or [])
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.store.board import get_board_store

        todos = get_board_store().list_todos()
        now = datetime.now(timezone.utc).isoformat()
        counts["tasks_due"] = sum(
            1
            for t in todos
            if str(t.get("due_at") or "").strip()
            and str(t.get("due_at")) <= now
            and not t.get("completed_at")
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.notify import feed

        counts["inbox_unread"] = int(feed.unread_count())
    except Exception:  # noqa: BLE001
        pass
    return counts


def system_stats() -> dict[str, Any]:
    stats: dict[str, Any] = {"version": "", "uptime_s": None, "cron_jobs": None}
    try:
        import navig as _navig

        stats["version"] = str(getattr(_navig, "__version__", "") or "")
    except Exception:  # noqa: BLE001
        pass
    try:
        from navig.daemon.supervisor import NavigDaemon

        state = NavigDaemon.read_state() or {}
        started = str(state.get("started_at") or "")
        if started:
            moment = datetime.fromisoformat(started.replace("Z", "+00:00"))
            stats["uptime_s"] = max(0.0, (datetime.now(timezone.utc) - moment).total_seconds())
    except Exception:  # noqa: BLE001
        pass
    if stats["uptime_s"] is None:
        # A running daemon that does not expose state.json (a build without the
        # supervisor state, or a just-restarted one): the pid file's age is the next
        # honest answer, and "—" is better than a number we did not measure.
        for name in ("supervisor.pid", "worker.pid"):
            try:
                mtime = (_config_dir() / name).stat().st_mtime
                stats["uptime_s"] = max(0.0, time.time() - mtime)
                break
            except OSError:
                continue
    try:
        jobs = _read_json(_config_dir() / "scheduler" / "cron_jobs.json")
        rows = jobs.get("jobs") if isinstance(jobs.get("jobs"), list) else jobs
        if isinstance(rows, list):
            stats["cron_jobs"] = sum(1 for j in rows if isinstance(j, dict) and j.get("enabled", True))
    except Exception:  # noqa: BLE001
        pass
    return stats


def readiness() -> dict[str, Any]:
    try:
        from navig.commands.init import get_init_status_payload

        payload = get_init_status_payload()
        ready = payload.get("readiness", {}) if isinstance(payload, dict) else {}
        issues = [i for i in (ready.get("issues") or []) if isinstance(i, dict)]
        return {
            "state": str(ready.get("state") or "unknown"),
            "score": int(ready.get("score") or 0),
            "issues": issues,
        }
    except Exception:  # noqa: BLE001
        return {"state": "unknown", "score": 0, "issues": []}


# ── collect + render ────────────────────────────────────────────────────────


def delivery_health() -> dict[str, Any]:
    """Telegram delivery: the mode the brain is in, unconfirmed messages, last send age.

    Incident 2026-10-22 had no card that answered "is delivery healthy?" — the mode
    churned every 10 minutes and nothing surfaced it. Everything here is local state.
    """
    info: dict[str, Any] = {"mode": "unknown", "transitions": 0, "pending": 0, "last_send_age_s": None}
    try:
        import json as _json

        mode_path = _config_dir() / "brain" / "telegram_mode.json"
        data = _json.loads(mode_path.read_text(encoding="utf-8", errors="replace"))
        if isinstance(data, dict):
            info["mode"] = str(data.get("mode") or "unknown")
            info["transitions"] = len(data.get("transitions") or [])
    except (OSError, ValueError):
        pass
    try:
        from navig.telegram import receipts  # noqa: PLC0415

        info["pending"] = receipts.pending_count()
    except Exception:  # noqa: BLE001
        pass
    try:
        sent = _config_dir() / "cache" / "telegram_sent.jsonl"
        info["last_send_age_s"] = max(0.0, time.time() - sent.stat().st_mtime)
    except OSError:
        pass
    return info


def collect(
    chat_id: int,
    user_id: int,
    *,
    space: str,
    tier: str,
    persona: str,
    host: str | None = None,
) -> dict[str, Any]:
    """Everything the card renders, gathered once. Never raises."""
    host_label = host or platform.node() or "this machine"
    space_info = space_details(space)
    return {
        "chat_id": chat_id,
        "user_id": user_id,
        "now": datetime.now(timezone.utc),
        "host": host_label,
        "space": space,
        "space_info": space_info,
        "project": space_info.get("project") or {},
        "genesis": genesis_identity(),
        "brain": brain_authority(host_label),
        "operator": operator_status(),
        "self_prog": self_programming(),
        "tier": tier or "auto",
        "persona": persona or "default",
        "work": work_counts(chat_id, user_id),
        "system": system_stats(),
        "readiness": readiness(),
        "delivery": delivery_health(),
    }


def button_label(key: str, fallback: str) -> str:
    """One localized button label.

    A Telegram button must speak the operator's language (this account runs in
    Russian) and the localization guard refuses English literals inside ``"text"``
    dicts in ``navig/telegram``. The fallback is only for a locale file that has not
    been written yet — it keeps the card usable, it is never the shipped English.
    """
    try:
        from navig.core import i18n

        text = i18n.t(key)
        return text if text and text != key else fallback
    except Exception:  # noqa: BLE001 — a locale lookup may never break the card
        return fallback


def keyboard(snapshot: dict[str, Any]) -> list[list[dict[str, str]]]:
    """Inline buttons: recovery first, then the session's own surfaces."""
    rows: list[list[dict[str, str]]] = []
    for issue in (snapshot.get("readiness", {}).get("issues") or [])[:2]:
        code = str(issue.get("code") or "").strip()
        if not code:
            continue
        summary = _clip(issue.get("summary") or "Run fix", 34)
        rows.append([{"text": f"🛠 {summary}", "callback_data": f"stfix:{code}"}])
    rows.extend(
        [
            [
                {
                    "text": button_label("status.button.refresh", "🔄 Refresh"),
                    "callback_data": "nav:open:status",
                },
                {
                    "text": button_label("status.button.briefing", "📋 Briefing"),
                    "callback_data": "nav:open:briefing",
                },
                {
                    "text": button_label("status.button.reminders", "⏰ Reminders"),
                    "callback_data": "slash:reminders",
                },
            ],
            [
                {
                    "text": button_label("status.button.spaces", "🗂 Spaces"),
                    "callback_data": "nav:cmd:/spaces",
                },
                {
                    "text": button_label("status.button.brain", "🧠 Brain"),
                    "callback_data": "nav:brain",
                },
                {
                    "text": button_label("status.button.self", "🧘 Self"),
                    "callback_data": "nav:self",
                },
            ],
            [
                {
                    "text": button_label("status.button.genesis", "🌱 Genesis"),
                    "callback_data": "nav:genesis",
                },
                {
                    "text": button_label("status.button.plans", "📋 Plans"),
                    "callback_data": "nav:cmd:/plans",
                },
                {
                    "text": button_label("status.button.home", "🏠 Home"),
                    "callback_data": "nav:home",
                },
            ],
        ]
    )
    return rows


def render(snapshot: dict[str, Any]) -> str:
    from navig.gateway.channels.telegram_html import html_escape

    esc = html_escape
    now_utc = snapshot["now"].strftime("%H:%M UTC, %d %b %Y")
    work = snapshot.get("work") or {}
    sysinfo = snapshot.get("system") or {}
    readiness_info = snapshot.get("readiness") or {}

    lines: list[str] = [CARD_HEADER, f"<i>{now_utc}</i>", "", "<b>📍 Context</b>"]
    lines.append(f"  Host     <code>{esc(snapshot['host'])}</code>")
    lines.append(f"  Space    <code>{esc(snapshot['space'])}</code>")
    project = snapshot.get("project") or {}
    if project:
        branch = f" · {esc(project.get('branch', ''))}" if project.get("branch") else ""
        lines.append(f"  Project  <code>{esc(project.get('name', '?'))}{branch}</code>")

    brain = snapshot.get("brain") or {}
    if brain.get("known"):
        if not brain.get("cache_fresh"):
            state = f"snapshot {_human_age(brain.get('cache_age_s'))} ago"
        elif brain.get("stale"):
            state = "⚠️ stale"
        else:
            state = f"lease {_human_age(brain.get('lease_left_s'))} left"
        lines += [
            "",
            "<b>🧠 Brain</b>",
            f"  Authority <code>{esc(brain.get('hostname') or '?')}</code>"
            f" · {esc(brain.get('role') or '?')} · epoch {esc(brain.get('epoch', '?'))}",
            f"  {state} · owner <code>{esc((brain.get('owner_id') or '')[:16])}</code>",
        ]

    genesis = snapshot.get("genesis") or {}
    if genesis:
        lines += [
            "",
            "<b>🌱 Genesis</b>",
            f"  Node     <code>{esc(genesis.get('node_id') or '?')}</code>"
            f" · {esc(genesis.get('name') or '?')}"
            f" · born {esc(genesis_born_label(genesis.get('born_at', '')))}",
        ]

    lines += [
        "",
        "<b>🤖 AI</b>",
        f"  Model    <code>{esc(snapshot['tier'])}</code>",
        f"  Persona  <code>{esc(snapshot['persona'])}</code>",
    ]

    operator = snapshot.get("operator") or {}
    if operator:
        last_seen = operator.get("last_seen_age_s")
        seen = f" · last seen {_human_age(last_seen)} ago" if last_seen is not None else ""
        lines += [
            "",
            "<b>🧘 Self</b>",
            f"  State    <code>{esc(operator.get('state') or 'unknown')}</code>"
            f" · {esc(operator.get('time_of_day') or '')}{seen}",
        ]
        prog = snapshot.get("self_prog") or {}
        if prog.get("count"):
            last = prog.get("last_cycle") or {}
            cycle = f"cycle {last.get('number', '?')} · {esc(last.get('date', ''))}"
            commit = prog.get("last_commit")
            lines.append(
                f"  Cycles   <code>{prog['count']}</code> self-programming · {cycle}"
                + (f" · <code>{esc(str(commit)[:8])}</code>" if commit else "")
            )

    lines += ["", "<b>📋 Work</b>"]
    counts = [
        ("Reminders", work.get("reminders")),
        ("Tasks due", work.get("tasks_due")),
        ("Unread", work.get("inbox_unread")),
    ]
    lines.append(
        "  "
        + " · ".join(
            f"{label} <code>{value if value is not None else '—'}</code>"
            for label, value in counts
        )
    )

    uptime = _human_age(sysinfo.get("uptime_s")) if sysinfo.get("uptime_s") is not None else "—"
    cron = sysinfo.get("cron_jobs")
    lines += [
        "",
        "<b>💻 System</b>",
        f"  Uptime <code>{uptime}</code> · v{esc(sysinfo.get('version') or '?')}"
        + (f" · cron <code>{cron}</code> jobs" if cron is not None else ""),
    ]

    score = max(0, min(100, int(readiness_info.get("score") or 0)))
    filled = max(0, min(10, round(score / 10)))
    bar = "█" * filled + "░" * (10 - filled)
    state = str(readiness_info.get("state") or "unknown")
    icon = "✅" if state == "ready" else ("⚠️" if score >= 50 else "🔴")
    lines += [
        "",
        f"{icon} <b>Setup</b>   <code>{bar}</code>  {score}%  <code>{esc(state)}</code>",
    ]
    delivery = snapshot.get("delivery") or {}
    if delivery:
        last = delivery.get("last_send_age_s")
        last_text = f"{_human_age(last)} ago" if last is not None else "—"
        lines += [
            "",
            f"📨 <b>Delivery</b> <code>{esc(delivery.get('mode') or 'unknown')}</code>"
            f" · <code>{delivery.get('pending', 0)}</code> unconfirmed"
            f" · last send {last_text}",
        ]
    issues = readiness_info.get("issues") or []
    if issues:
        lines.append("  <b>Pending fixes:</b>")
        for issue in issues[:2]:
            summary = esc(str(issue.get("summary") or "").strip())
            command = str(issue.get("command") or "").strip()
            if summary and command:
                lines.append(f"  ⚡ {summary} — <code>{esc(command)}</code>")
        remaining = len(issues) - 2
        if remaining > 0:
            lines.append(f"  + {remaining} more issue(s)")
    return "\n".join(lines)


# ── detail cards (the new buttons) ─────────────────────────────────────────


def render_brain(snapshot: dict[str, Any]) -> str:
    from navig.gateway.channels.telegram_html import html_escape

    esc = html_escape
    brain = snapshot.get("brain") or {}
    lines = ["🧠 <b>Brain</b> — <i>authoritative ownership</i>"]
    if not brain.get("known"):
        lines += [
            "",
            "No ownership lease cached yet. The brain claims it from the Lighthouse",
            "edge (`/api/ownership`) — once claimed, this card shows who owns it.",
        ]
    else:
        block = [
            "",
            f"  Authority <code>{esc(brain.get('hostname') or '?')}</code>"
            f" · role {esc(brain.get('role') or '?')}",
            f"  Epoch     <code>{esc(brain.get('epoch', '?'))}</code>",
            f"  Owner     <code>{esc(brain.get('owner_id') or '?')}</code>",
            f"  Heartbeat {_human_age(brain.get('age_s'))} ago (TTL {int(brain.get('ttl') or 0)}s)",
            "  Lease     "
            + (
                "⚠️ <b>stale</b>"
                if brain.get("stale")
                else f"{_human_age(brain.get('lease_left_s'))} left"
            ),
        ]
        if not brain.get("cache_fresh"):
            block.append(f"  Snapshot  {_human_age(brain.get('cache_age_s'))} ago")
        block += [
            f"  Source    {esc(brain.get('url') or '—')}",
            "",
            "<i>Exactly one brain may own this install. A stale lease means the owner",
            "stopped heartbeating; the next guarded start takes over. An old snapshot",
            "is this install's cache talking, not a claim about the live owner.</i>",
        ]
        lines += block
    lines += ["", f"<i>This host: {esc(snapshot['host'])}</i>"]
    return "\n".join(lines)


def render_genesis(snapshot: dict[str, Any]) -> str:
    from navig.gateway.channels.telegram_html import html_escape

    esc = html_escape
    genesis = snapshot.get("genesis") or {}
    lines = ["🌱 <b>Genesis</b> — <i>node identity</i>"]
    if not genesis:
        lines += ["", "No <code>genesis.json</code> on this install — run <code>navig space init</code>."]
        return "\n".join(lines)
    lines += [
        "",
        f"  Node     <code>{esc(genesis.get('node_id') or '?')}</code>",
        f"  Name     <code>{esc(genesis.get('name') or '?')}</code>",
        f"  Born     <code>{esc(genesis_born_label(genesis.get('born_at', '')))}</code>",
        f"  Engine   {esc(genesis.get('engine') or '—')}",
    ]
    if genesis.get("url"):
        lines.append(f"  Node URL {esc(genesis['url'])}")
    readiness_info = snapshot.get("readiness") or {}
    score = max(0, min(100, int(readiness_info.get("score") or 0)))
    filled = max(0, min(10, round(score / 10)))
    lines += [
        "",
        f"Setup <code>{'█' * filled}{'░' * (10 - filled)}</code> {score}% "
        f"<code>{esc(readiness_info.get('state') or 'unknown')}</code>",
    ]
    for issue in (readiness_info.get("issues") or [])[:5]:
        summary = esc(str(issue.get("summary") or "").strip())
        command = str(issue.get("command") or "").strip()
        if summary:
            lines.append(f"  ⚡ {summary}" + (f" — <code>{esc(command)}</code>" if command else ""))
    return "\n".join(lines)


def render_self(snapshot: dict[str, Any]) -> str:
    from navig.gateway.channels.telegram_html import html_escape

    esc = html_escape
    operator = snapshot.get("operator") or {}
    prog = snapshot.get("self_prog") or {}
    lines = ["🧘 <b>Self</b> — <i>state &amp; self-programming</i>", ""]
    lines += [
        f"  State    <code>{esc(operator.get('state') or 'unknown')}</code>",
        f"  Time     {esc(operator.get('time_of_day') or '—')}",
        f"  Last seen {_human_age(operator.get('last_seen_age_s'))} ago"
        if operator.get("last_seen_age_s") is not None
        else "  Last seen —",
    ]
    if prog.get("count"):
        last = prog.get("last_cycle") or {}
        lines += [
            "",
            f"  Cycles   <code>{prog['count']}</code> recorded",
            f"  Latest   cycle {last.get('number', '?')} · {esc(last.get('date', ''))}",
        ]
        if prog.get("last_commit"):
            lines.append(f"  Commit   <code>{esc(str(prog['last_commit'])[:12])}</code>")
        lines.append(f"  Ledger   {esc(prog.get('path') or '—')}")
    else:
        lines += [
            "",
            "No self-programming cycle recorded yet. The self-aware session writes one",
            f"entry per cycle to {esc(str(_config_dir() / 'workspace' / RECEIPTS_FILENAME))}.",
        ]
    return "\n".join(lines)


def render_detail(name: str, snapshot: dict[str, Any]) -> str:
    renderer = {"brain": render_brain, "genesis": render_genesis, "self": render_self}.get(name)
    return renderer(snapshot) if renderer else render(snapshot)
