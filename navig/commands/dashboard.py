"""NAVIG dashboard — a live operator console (Rich).

One screen that answers "is this machine healthy, and what has it been doing?":

- **Identity** — this install's sigil (the same one ``navig whoami`` draws), the
  active space and its plan progress.
- **Services** — daemon (identity-checked pid + heartbeat), gateway (HTTP
  ``/status``, not a port probe), bot, cron, reachability, default AI.
- **Safety** — ledger chain, pending approvals, host locks, in-flight operations,
  store wiring.
- **Hosts** — a TCP probe of each host's SSH port (ICMP is blocked on most
  servers and ``ping -W`` means different things on Linux and macOS).
- **Activity** — the operations ledger, with every ``OperationStatus`` mapped.

Every row reads the SAME function the matching CLI command reads, so the
dashboard can never disagree with ``navig service status`` / ``navig status`` /
``navig ledger verify`` / ``navig approve list``. Slow sources run on a single
background collector with per-source intervals; the screen redraws at 2 Hz from
the last snapshot and never blocks on the network.
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.align import Align
from rich.console import Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from navig.console_helper import get_console
from navig.platform.paths import config_dir as _config_dir

console = get_console()

# Terminal breakpoints. Below WIDE_COLS the identity column folds into the header;
# below STACK_COLS the four data panels stack instead of sitting in a 2×2 grid.
WIDE_COLS = 100
STACK_COLS = 72
#: Width of the identity column (the 9-cell sigil + its labelled rows).
IDENTITY_COLS = 30
#: Rows the full 9×9 sigil needs inside the identity panel (sigil 9 + name 2 +
#: space/stats 8 + borders 2). Shorter terminals get the 5×5 grid.
FULL_SIGIL_MIN_MAIN_ROWS = 24
MIN_PANEL_ROWS = 6

BRAND = "#2271D0"
LABEL = "dim #6B8CAE"


# ═══════════════════════════════════════════════════════════════
# State-file paths — resolved at CALL time so NAVIG_CONFIG_DIR is honoured
# ═══════════════════════════════════════════════════════════════


def _daemon_pid_file() -> Path:
    return _config_dir() / "daemon" / "supervisor.pid"


def _daemon_state_file() -> Path:
    return _config_dir() / "daemon" / "state.json"


def _tunnels_file() -> Path:
    return _config_dir() / "cache" / "tunnels.json"


# ═══════════════════════════════════════════════════════════════
# Small probes
# ═══════════════════════════════════════════════════════════════


def _check_port(port: int, host: str = "127.0.0.1", timeout: float = 0.3) -> bool:
    """Return True if a TCP port accepts a connection."""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def _check_pid_alive(pid: int) -> bool:
    """Is *pid* a live process (no identity check — callers use pid_from_pidfile)."""
    try:
        import psutil  # type: ignore[import-untyped]

        return bool(psutil.pid_exists(int(pid)))
    except ImportError:
        pass
    except (ValueError, TypeError):
        return False
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x100000, False, int(pid))
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def _probe_tcp(host: str, port: int, timeout: float = 1.5) -> dict[str, Any]:
    """Reachability of ``host:port`` with connect latency — the SSH-port check."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return {"status": "up", "latency_ms": (time.perf_counter() - start) * 1000}
    except (OSError, ValueError) as exc:
        return {"status": "down", "error": type(exc).__name__}


def _get_terminal_size() -> tuple[int, int]:
    sz = shutil.get_terminal_size((100, 30))
    return sz.columns, sz.lines


def _fmt_age(seconds: float | None) -> str:
    if seconds is None:
        return "?"
    s = int(max(seconds, 0))
    if s < 60:
        return f"{s}s"
    m = s // 60
    if m < 60:
        return f"{m}m"
    h, m = divmod(m, 60)
    if h < 48:
        return f"{h}h{m:02d}m"
    return f"{h // 24}d{h % 24}h"


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ═══════════════════════════════════════════════════════════════
# Collectors — each reads the canonical source for one row
# ═══════════════════════════════════════════════════════════════


def collect_identity() -> dict[str, Any]:
    """This install's entity. Never writes: a missing entity is derived, not saved."""
    from navig.identity.entity import derive_entity
    from navig.identity.sigil_store import get_seed_for_session, load_entity

    data = load_entity()
    saved = data is not None
    seed = data["seed"] if data else get_seed_for_session()
    entity = derive_entity(seed)
    try:
        from navig import __version__ as version
    except Exception:  # noqa: BLE001
        version = "?"
    return {"entity": entity, "saved": saved, "version": version}


def collect_daemon() -> dict[str, Any]:
    """Daemon + supervised children — the reads ``navig service status`` makes."""
    from navig.daemon.service_manager import _child_is_live
    from navig.daemon.supervisor import NavigDaemon

    pid = NavigDaemon.read_pid()  # identity-checked: a recycled pid reads as None
    state = NavigDaemon.read_state() or {}
    info: dict[str, Any] = {"running": pid is not None, "pid": pid, "children": {}}
    if not pid:
        return info
    started = _parse_iso(state.get("started_at"))
    if started:
        info["uptime_s"] = (datetime.now(timezone.utc) - started).total_seconds()
    interval = state.get("heartbeat_s")
    last = _parse_iso(NavigDaemon.last_seen_alive())
    if interval and last:
        age = (datetime.now(timezone.utc) - last).total_seconds()
        info["heartbeat_age_s"] = age
        info["heartbeat_stale"] = age > 3 * float(interval)
    for child in state.get("children", []) or []:
        name = child.get("name")
        if not name:
            continue
        info["children"][name] = {
            "pid": child.get("pid"),
            "live": _child_is_live(child.get("pid")),
            "enabled": child.get("enabled", True),
            "restarts": child.get("restart_count", 0),
        }
    return info


def collect_gateway() -> dict[str, Any]:
    """Gateway health from its own ``/status`` — a port that merely answers is not a gateway."""
    from navig.commands.status import get_gateway_status

    try:
        from navig.gateway_client import gateway_live_defaults

        port = gateway_live_defaults()[0]
    except Exception:  # noqa: BLE001
        port = None
    status = get_gateway_status()
    status["port"] = port
    return status


def collect_reach_and_ai() -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from navig.commands.service import reachability_summary

        out["reach"] = reachability_summary()
    except Exception as exc:  # noqa: BLE001
        out["reach_error"] = str(exc)
    try:
        from navig.providers.connect import resolve_default

        out["ai"] = resolve_default()
    except Exception as exc:  # noqa: BLE001
        out["ai_error"] = str(exc)
    return out


def collect_space() -> dict[str, Any]:
    from navig.commands.space import resolve_active_space

    name = resolve_active_space()
    out: dict[str, Any] = {"name": name}
    if not name:
        return out
    try:
        from navig.spaces.progress import collect_spaces_progress

        for row in collect_spaces_progress():
            if row.name == name:
                out.update(pct=row.completion_pct, goal=row.goal)
                break
    except Exception:  # noqa: BLE001 — progress is decoration; the name is the fact
        pass
    return out


_LEDGER_CACHE: dict[str, Any] = {}


def collect_safety(config_manager: Any) -> dict[str, Any]:
    """Ledger chain · approvals · host locks · in-flight ops — the ``doctor`` safety rows."""
    from navig.operation_recorder import get_operation_recorder

    out: dict[str, Any] = {}
    recorder = get_operation_recorder()

    # Ledger chain — hashing is O(file); re-verify only when the file changed.
    try:
        from navig.ledger_chain import verify_ledger

        path = Path(recorder.history_file)
        st = path.stat() if path.exists() else None
        key = (str(path), st.st_mtime_ns if st else 0, st.st_size if st else 0)
        if _LEDGER_CACHE.get("key") != key:
            v = verify_ledger(path)
            _LEDGER_CACHE.update(
                key=key,
                value={
                    "status": v.status,
                    "total": v.total,
                    "first_broken_line": v.first_broken_line,
                },
            )
        out["ledger"] = _LEDGER_CACHE["value"]
    except Exception as exc:  # noqa: BLE001
        out["ledger"] = {"status": "error", "error": str(exc)}

    try:
        from navig.approval.journal import list_pending

        out["approvals"] = len(list_pending() or {})
    except Exception:  # noqa: BLE001
        out["approvals"] = None

    locks: list[dict[str, Any]] = []
    try:
        from navig.core.host_lock import lock_state, read_lock

        for host in config_manager.list_hosts():
            ls = lock_state(read_lock(host))
            if ls.state in ("held", "stale"):
                locks.append(
                    {"host": host, "state": ls.state, "user": ls.user, "age_min": ls.age_minutes}
                )
    except Exception:  # noqa: BLE001
        pass
    out["locks"] = locks

    try:
        out["inflight"] = len(recorder.iter_inflight())
    except Exception:  # noqa: BLE001
        out["inflight"] = 0
    return out


def collect_store() -> dict[str, Any]:
    from navig.hub.aggregator import store_status

    s = store_status()
    return {
        "total": s.get("total", 0),
        "broken": len(s.get("broken", [])),
        "degraded": len(s.get("degraded", [])),
    }


def collect_hosts(config_manager: Any, limit: int = 8) -> dict[str, dict[str, Any]]:
    """Probe each host's SSH port in parallel; every probe carries its own timeout."""
    targets: list[tuple[str, str, int]] = []
    for name in config_manager.list_hosts()[:limit]:
        try:
            hc = config_manager.load_host_config(name) or {}
        except Exception:  # noqa: BLE001
            continue
        address = hc.get("host") or hc.get("hostname") or hc.get("ip")
        if address:
            targets.append((name, str(address), int(hc.get("port") or 22)))
    results: dict[str, dict[str, Any]] = {}
    if not targets:
        return results
    with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
        futures = {name: pool.submit(_probe_tcp, addr, port) for name, addr, port in targets}
        for (name, addr, port), fut in zip(targets, futures.values(), strict=True):
            res = fut.result()
            res.update(address=addr, port=port)
            results[name] = res
    return results


def collect_activity(limit: int = 8) -> list[dict[str, Any]]:
    from navig.operation_recorder import get_operation_recorder

    rows = []
    for op in get_operation_recorder().get_last_n(limit):
        status = getattr(op.status, "value", op.status)
        rows.append(
            {
                "ts": op.timestamp,
                "command": op.command,
                "host": op.host,
                "status": str(status),
                "duration_ms": op.duration_ms,
            }
        )
    return rows


def collect_doctor() -> dict[str, Any]:
    from navig.commands.doctor import collect_report

    report = collect_report(quiet=True)
    problems = [
        {"section": sec["name"], "label": c["label"], "warn": c["warn"], "detail": c["detail"]}
        for sec in report.get("sections", [])
        for c in sec.get("checks", [])
        if not c.get("ok")
    ]
    return {"summary": report.get("summary", {}), "problems": problems}


# ═══════════════════════════════════════════════════════════════
# Background collector — one thread, per-source intervals
# ═══════════════════════════════════════════════════════════════


class Collector:
    """Runs every source on its own interval in ONE worker thread.

    A snapshot is a plain dict; the renderer reads a copy, so a slow source
    (a host timing out, the store sweep) delays only its own row.
    """

    def __init__(self, sources: dict[str, tuple[float, Callable[[], Any]]]):
        self._sources = sources
        self._due: dict[str, float] = dict.fromkeys(sources, 0.0)
        self._data: dict[str, Any] = {}
        self._errors: dict[str, str] = {}
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # -- control -----------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="navig-dashboard", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def refresh(self, *names: str) -> None:
        for name in names or tuple(self._sources):
            if name in self._due:
                self._due[name] = 0.0
        self._wake.set()

    def run_once(self, names: list[str] | None = None) -> None:
        for name in names or list(self._sources):
            self._run(name)

    # -- data --------------------------------------------------------------
    def snapshot(self) -> tuple[dict[str, Any], dict[str, str]]:
        with self._lock:
            return dict(self._data), dict(self._errors)

    def _run(self, name: str) -> None:
        interval, fn = self._sources[name]
        try:
            value = fn()
            with self._lock:
                self._data[name] = value
                self._errors.pop(name, None)
        except Exception as exc:  # noqa: BLE001 — one broken source must not blank the screen
            with self._lock:
                self._errors[name] = f"{type(exc).__name__}: {exc}"[:160]
        self._due[name] = time.monotonic() + interval

    def _loop(self) -> None:
        while not self._stop.is_set():
            now = time.monotonic()
            for name, due in list(self._due.items()):
                if self._stop.is_set():
                    return
                if due <= now:
                    self._run(name)
            nxt = min(self._due.values()) - time.monotonic()
            self._wake.wait(timeout=max(0.1, min(nxt, 1.0)))
            self._wake.clear()


def build_sources(config_manager: Any, refresh_s: float = 5.0) -> dict[str, tuple[float, Callable]]:
    fast = max(1.0, float(refresh_s))
    return {
        "identity": (3600.0, collect_identity),
        "daemon": (min(fast, 3.0), collect_daemon),
        "activity": (min(fast, 3.0), collect_activity),
        "gateway": (fast, collect_gateway),
        "safety": (fast, lambda: collect_safety(config_manager)),
        "space": (15.0, collect_space),
        "hosts": (30.0, lambda: collect_hosts(config_manager)),
        "reach_ai": (60.0, collect_reach_and_ai),
        "store": (120.0, collect_store),
    }


# ═══════════════════════════════════════════════════════════════
# State (UI-side)
# ═══════════════════════════════════════════════════════════════


class DashboardState:
    """UI state that is not data: overlays, the activity feed, counters."""

    def __init__(self) -> None:
        self.hosts_status: dict[str, dict[str, Any]] = {}
        self.op_state: dict[str, Any] = {}
        self.running: bool = True
        self.refresh_requested: bool = False
        self.overlay: str | None = None  # None | "help" | "doctor"
        self.events: int = 0
        self.errors: int = 0
        self.started_at: float = time.time()
        self.activity_log: list[str] = []

    def log(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.activity_log.append(f"[dim]{ts}[/dim] {msg}")
        if len(self.activity_log) > 50:
            self.activity_log = self.activity_log[-50:]
        self.events += 1


# ═══════════════════════════════════════════════════════════════
# Rendering
# ═══════════════════════════════════════════════════════════════

OP_STATUS_GLYPH: dict[str, str] = {
    "success": "[green]✓[/green]",
    "failed": "[red]✗[/red]",
    "partial": "[yellow]◐[/yellow]",
    "cancelled": "[dim]⊘[/dim]",
    "pending": "[cyan]●[/cyan]",
    "interrupted": "[magenta]↯[/magenta]",
}


def _one_line(markup: str) -> Text:
    """A markup line that truncates with an ellipsis instead of wrapping."""
    text = Text.from_markup(markup, overflow="ellipsis")
    text.no_wrap = True
    return text


def _row(table: Table, label: str, value: str) -> None:
    table.add_row(f"[{LABEL}]{label}[/]", value)


def _kv_table() -> Table:
    t = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    t.add_column(no_wrap=True, width=10)
    t.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
    return t


def attention_items(data: dict[str, Any], errors: dict[str, str]) -> list[str]:
    """Everything that wants the operator — one list, so the header count is honest."""
    items: list[str] = []
    d = data.get("daemon") or {}
    if d.get("heartbeat_stale"):
        items.append("daemon heartbeat stale")
    safety = data.get("safety") or {}
    ledger = (safety.get("ledger") or {}).get("status")
    if ledger == "broken":
        items.append("ledger chain broken")
    if safety.get("approvals"):
        items.append(f"{safety['approvals']} approval(s) waiting")
    for lock in safety.get("locks") or []:
        items.append(f"{lock['host']} lock {lock['state']}")
    store = data.get("store") or {}
    if store.get("broken"):
        items.append(f"{store['broken']} store item(s) broken")
    for name, res in (data.get("hosts") or {}).items():
        if res.get("status") == "down":
            items.append(f"{name} unreachable")
    items.extend(f"{name}: read failed" for name in errors)
    return items


def make_header(
    data: dict[str, Any], errors: dict[str, str], cols: int, compact_identity: bool
) -> Panel:
    ident = data.get("identity") or {}
    entity = ident.get("entity")
    parts = [f"[bold {BRAND}]NAVIG[/]"]
    if entity is not None:
        from navig.identity.entity import PALETTES

        primary = PALETTES[entity.palette_key][1]
        parts.append(f"[bold {primary}]NODE-{entity.seed[:4].upper()}[/]")
    # Most important first: on a narrow terminal the line truncates from the right.
    attention = attention_items(data, errors)
    parts.append(
        f"[yellow]⚠ {len(attention)} need attention[/yellow]"
        if attention
        else "[green]✓ all clear[/green]"
    )
    space = (data.get("space") or {}).get("name")
    parts.append(f"[green]◆[/green] {space}" if space else "[dim]◇ no space[/dim]")
    if cols >= 90:
        if compact_identity and ident.get("version"):
            parts.append(f"[dim]v{ident['version']}[/dim]")
        parts.append(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]")
    sep = "  │  " if cols >= 90 else " │ "
    return Panel(_one_line(sep.join(parts)), border_style=BRAND)


def make_identity_panel(
    data: dict[str, Any], compact_sigil: bool, errors: dict[str, str] | None = None
) -> Panel:
    from navig.identity.renderer import sigil_text

    ident = data.get("identity") or {}
    entity = ident.get("entity")
    if entity is None:
        return Panel(
            Align.center("[dim]deriving identity…[/dim]", vertical="middle"), border_style=BRAND
        )
    from navig.identity.entity import PALETTES

    primary = PALETTES[entity.palette_key][1]
    node = "NODE-" + entity.seed[:4].upper()
    parts: list[Any] = [
        Text(""),
        Align.center(sigil_text(entity, compact=compact_sigil, indent="")),
        Text(""),
        Align.center(Text("  ".join(node), style=f"bold {primary}")),
        Align.center(
            Text(
                f"{entity.archetype.title()} · {entity.palette_key.replace('_', ' ')}", style="dim"
            )
        ),
        Text(""),
    ]
    from navig.identity.entity import generate_machine_name

    t = _kv_table()
    _row(t, "Machine", generate_machine_name(entity.seed))
    space = data.get("space") or {}
    if space.get("name"):
        _row(t, "Space", str(space["name"]))
        pct = space.get("pct")
        if isinstance(pct, (int, float)):
            filled = max(0, min(8, round(pct / 12.5)))
            _row(
                t,
                "Plan",
                f"[{primary}]{'━' * filled}[/][dim]{'─' * (8 - filled)}[/dim] {pct:.0f}%",
            )
    else:
        _row(t, "Space", "[dim]none[/dim]")
    _row(t, "Version", f"navig {ident.get('version', '?')}")
    if not ident.get("saved"):
        _row(t, "Identity", "[yellow]unsealed[/yellow]")
    parts.append(t)

    # What wants the operator — the header only counts it; here it is named.
    attention = attention_items(data, errors or {})
    parts.append(Text(""))
    if attention:
        parts.append(Text("Needs you", style=LABEL))
        for item in attention[:4]:
            parts.append(Text.from_markup(f"[yellow]⚠[/yellow] {item}", overflow="ellipsis"))
        if len(attention) > 4:
            parts.append(Text(f"  +{len(attention) - 4} more", style="dim"))
    else:
        parts.append(Text.from_markup("[green]✓ nothing needs you[/green]"))
    return Panel(Group(*parts), title=f"[bold {BRAND}]Identity[/]", border_style=BRAND)


def _status(ok: bool | None, good: str, bad: str, unknown: str = "[dim]? unknown[/dim]") -> str:
    if ok is None:
        return unknown
    return f"[green]● {good}[/green]" if ok else f"[white]○ {bad}[/white]"


def make_services_panel(data: dict[str, Any], errors: dict[str, str]) -> Panel:
    t = _kv_table()
    nxt: list[str] = []  # next steps, most important first — one is shown
    d = data.get("daemon")
    if d is None:
        failed = "daemon" in errors
        _row(t, "Daemon", "[red]✗ read failed[/red]" if failed else "[dim]checking…[/dim]")
    elif d.get("running"):
        extra = [f"pid {d['pid']}"]
        if d.get("uptime_s") is not None:
            extra.append(f"up {_fmt_age(d['uptime_s'])}")
        if d.get("heartbeat_stale"):
            _row(t, "Daemon", f"[yellow]● stale {_fmt_age(d.get('heartbeat_age_s'))}[/yellow]")
            nxt.append("navig service restart")
        else:
            _row(t, "Daemon", "[green]● running[/green] [dim]" + " · ".join(extra) + "[/dim]")
    else:
        _row(t, "Daemon", "[white]○ stopped[/white]")
        nxt.append("navig service start")

    g = data.get("gateway")
    if g is None:
        _row(t, "Gateway", "[dim]checking…[/dim]")
    elif g.get("running") and g.get("auth_error"):
        _row(t, "Gateway", "[yellow]● up · token rejected[/yellow]")
        nxt.append("navig config get gateway.auth.token")
    elif g.get("running"):
        extra = [f":{g.get('port')}"] if g.get("port") else []
        if g.get("uptime") is not None:
            extra.append(f"up {_fmt_age(g['uptime'])}")
        extra.append(f"{g.get('sessions', 0)} sess")
        _row(t, "Gateway", "[green]● healthy[/green] [dim]" + " · ".join(extra) + "[/dim]")
    else:
        _row(t, "Gateway", "[white]○ down[/white]")
        nxt.append("navig start")

    children = (d or {}).get("children", {})
    bot = children.get("telegram-bot")
    if bot is None:
        _row(t, "Bot", "[dim]— not supervised[/dim]")
    elif not bot.get("enabled"):
        _row(t, "Bot", "[dim]- disabled[/dim]")
    else:
        live = bot.get("live")
        restarts = f" [dim]↻{bot['restarts']}[/dim]" if bot.get("restarts") else ""
        _row(t, "Bot", _status(live, "running", "down", "[yellow]● unverified[/yellow]") + restarts)

    cron = (g or {}).get("cron") or {}
    if g and g.get("running") and cron:
        jobs = cron.get("jobs", cron.get("total", 0))
        enabled = cron.get("enabled_jobs", cron.get("enabled", jobs))
        label = f"{enabled}/{jobs} job(s) enabled" if jobs else "no jobs yet"
        _row(t, "Cron", f"[green]●[/green] {label}")
    else:
        _row(t, "Cron", "[dim]— needs the gateway[/dim]")

    ra = data.get("reach_ai") or {}
    reach = ra.get("reach") or {}
    if reach:
        _row(t, "Reach", f"{reach.get('mode', '?')} [dim]{reach.get('reach_url', '')}[/dim]")
    ai = ra.get("ai")
    if ai:
        model = ai.get("default_model") or ""
        health = ai.get("health_state", "")
        dot = "[green]●[/green]" if health in ("healthy", "ok", "") else "[yellow]●[/yellow]"
        _row(t, "AI", f"{dot} {ai.get('name', '?')} [dim]{model}[/dim]")
    elif "reach_ai" in data:
        _row(t, "AI", "[dim]○ none[/dim]")
        nxt.append("navig ai connect")

    body: Any = t
    if nxt:
        hint = _one_line(f"[dim]next →[/dim] [cyan]{nxt[0]}[/cyan]")
        body = Group(t, Text(""), hint)
    return Panel(body, title="[bold]Services[/bold]", border_style="cyan")


def make_safety_panel(data: dict[str, Any]) -> Panel:
    t = _kv_table()
    s = data.get("safety")
    if s is None:
        _row(t, "Ledger", "[dim]checking…[/dim]")
        return Panel(t, title="[bold]Safety[/bold]", border_style="green")
    led = s.get("ledger") or {}
    st = led.get("status")
    if st == "intact":
        _row(
            t,
            "Ledger",
            f"[green]✓ chain intact[/green]  [dim]{led.get('total', 0):,} entries[/dim]",
        )
    elif st == "broken":
        _row(
            t,
            "Ledger",
            f"[red]✗ broken at line {led.get('first_broken_line')}[/red]  [dim]→ navig ledger verify[/dim]",
        )
    elif st in ("empty", "missing"):
        _row(t, "Ledger", "[dim]○ no operations yet[/dim]")
    elif st == "legacy":
        _row(t, "Ledger", "[yellow]○ unchained (legacy entries)[/yellow]")
    else:
        _row(t, "Ledger", f"[dim]? {st or 'unknown'}[/dim]")

    n = s.get("approvals")
    if n is None:
        _row(t, "Approvals", "[dim]? unreadable[/dim]")
    elif n:
        _row(t, "Approvals", f"[yellow]⚠ {n} waiting[/yellow]  [dim]→ navig approve list[/dim]")
    else:
        _row(t, "Approvals", "[green]✓[/green] none waiting")

    locks = s.get("locks") or []
    if locks:
        first = locks[0]
        who = f" by {first['user']}" if first.get("user") else ""
        more = f" +{len(locks) - 1}" if len(locks) > 1 else ""
        _row(t, "Host locks", f"[yellow]⚠ {first['host']} {first['state']}{who}[/yellow]{more}")
    else:
        _row(t, "Host locks", "[green]✓[/green] none held")

    inflight = s.get("inflight") or 0
    _row(t, "In flight", f"[cyan]● {inflight} running[/cyan]" if inflight else "[dim]○ idle[/dim]")

    store = data.get("store")
    if store:
        extra = []
        if store.get("broken"):
            extra.append(f"[red]{store['broken']} broken[/red]")
        if store.get("degraded"):
            extra.append(f"[yellow]{store['degraded']} degraded[/yellow]")
        tail = "  " + " · ".join(extra) if extra else ""
        _row(t, "Store", f"{store.get('total', 0)} wired{tail}")
    return Panel(t, title="[bold]Safety[/bold]", border_style="green")


def make_hosts_panel(
    data: dict[str, Any], config_manager: Any = None, show_address: bool = True
) -> Panel:
    hosts = data.get("hosts")
    t = Table(box=None, show_header=True, header_style="bold cyan", padding=(0, 1), expand=True)
    t.add_column("Host", no_wrap=True, ratio=2, overflow="ellipsis")
    if show_address:
        t.add_column("SSH", no_wrap=True, ratio=2, overflow="ellipsis", style="dim")
    t.add_column("State", no_wrap=True, width=12)
    active = None
    try:
        active = config_manager.get_active_host() if config_manager else None
    except Exception:  # noqa: BLE001
        pass
    if hosts is None:
        return Panel("[dim]probing…[/dim]", title="[bold]Hosts[/bold]", border_style="cyan")
    if not hosts:
        return Panel(
            "[dim]No hosts yet — [cyan]navig host add[/cyan][/dim]",
            title="[bold]Hosts[/bold]",
            border_style="cyan",
        )
    up = 0
    for name, res in hosts.items():
        label = f"[bold green]{name}[/bold green]" if name == active else name
        if res.get("status") == "up":
            up += 1
            state = f"[green]● {res.get('latency_ms', 0):.0f} ms[/green]"
        else:
            state = "[red]✗ down[/red]"
        cells = (
            [label, f"{res.get('address')}:{res.get('port')}", state]
            if show_address
            else [label, state]
        )
        t.add_row(*cells)
    return Panel(
        t, title=f"[bold]Hosts[/bold] [dim]{up}/{len(hosts)} reachable[/dim]", border_style="cyan"
    )


def make_activity_panel(data: dict[str, Any]) -> Panel:
    ops = data.get("activity")
    if ops is None:
        return Panel(
            "[dim]reading ledger…[/dim]", title="[bold]Activity[/bold]", border_style="yellow"
        )
    if not ops:
        return Panel(
            "[dim]No operations yet — every navig action lands here, hash-chained.[/dim]",
            title="[bold]Activity[/bold]",
            border_style="yellow",
        )
    t = Table(box=None, show_header=False, padding=(0, 1), expand=True)
    t.add_column(width=5, style="dim", no_wrap=True)
    t.add_column(width=1, no_wrap=True)
    t.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    t.add_column(width=10, style="cyan", no_wrap=True, overflow="ellipsis")
    for op in ops:
        dt = _parse_iso(op.get("ts"))
        when = dt.astimezone().strftime("%H:%M") if dt else "?"
        glyph = OP_STATUS_GLYPH.get(op.get("status", ""), "[dim]○[/dim]")
        cmd = op.get("command", "")
        cmd = cmd[len("navig ") :] if cmd.startswith("navig ") else cmd  # every row is navig
        t.add_row(when, glyph, Text(cmd, overflow="ellipsis"), op.get("host") or "")
    return Panel(t, title="[bold]Activity[/bold] [dim]ledger[/dim]", border_style="yellow")


def make_footer(refresh_s: float, keys_enabled: bool = True) -> Panel:
    if not keys_enabled:
        text = f"[dim]Ctrl+C quit  │  data every {refresh_s:g}s[/dim]"
    else:
        text = (
            "[cyan]q[/cyan] quit  [cyan]r[/cyan] refresh  [cyan]d[/cyan] doctor  "
            "[cyan]g[/cyan] open deck  [cyan]?[/cyan] help  "
            f"[dim]│ data every {refresh_s:g}s[/dim]"
        )
    return Panel(_one_line(text), border_style="dim")


def make_overlay(kind: str, data: dict[str, Any], errors: dict[str, str]) -> Panel:
    if kind == "help":
        body = (
            "[bold]Keys[/bold]\n"
            "  [cyan]q[/cyan]  quit            [cyan]r[/cyan]  re-read every source now\n"
            "  [cyan]d[/cyan]  run navig doctor [cyan]g[/cyan]  open the deck in your browser\n"
            "  [cyan]?[/cyan]  this help        [dim]any key closes an overlay[/dim]\n\n"
            "[bold]Where each row comes from[/bold]\n"
            "  Daemon · Bot   navig service status   Gateway · Cron  navig status\n"
            "  Ledger         navig ledger verify    Approvals       navig approve list\n"
            "  Hosts          TCP to each host's SSH port (no ICMP)"
        )
        return Panel(body, title="[bold]Help[/bold]", border_style=BRAND, padding=(1, 2))
    doc = data.get("doctor")
    if doc is None:
        msg = errors.get("doctor") or "running navig doctor… (a few seconds)"
        return Panel(
            f"[dim]{msg}[/dim]", title="[bold]Doctor[/bold]", border_style=BRAND, padding=(1, 2)
        )
    s = doc.get("summary", {})
    lines = [
        f"[green]✓ {s.get('passed', 0)} passed[/green]   "
        f"[yellow]⚠ {s.get('warnings', 0)} warnings[/yellow]   "
        f"[red]✗ {s.get('failed', 0)} failed[/red]\n"
    ]
    for p in doc.get("problems", [])[:12]:
        mark = "[yellow]⚠[/yellow]" if p["warn"] else "[red]✗[/red]"
        lines.append(
            f"{mark} [bold]{p['section']}[/bold] › {p['label']}  [dim]{p['detail'][:70]}[/dim]"
        )
    if not doc.get("problems"):
        lines.append("[green]Everything checks out.[/green]")
    lines.append("\n[dim]Full report: navig doctor[/dim]")
    return Panel("\n".join(lines), title="[bold]Doctor[/bold]", border_style=BRAND, padding=(1, 2))


# ═══════════════════════════════════════════════════════════════
# Layout
# ═══════════════════════════════════════════════════════════════


def create_layout(cols: int = 120, rows: int = 30) -> Layout:
    """Responsive grid.

    * ``cols ≥ WIDE_COLS`` — identity column + a 2×2 grid of data panels.
    * ``STACK_COLS ≤ cols < WIDE_COLS`` — the 2×2 grid alone (identity folds into the header).
    * narrower — the four data panels stacked.
    """
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3),
    )
    grid_parent = layout["main"]
    if cols >= WIDE_COLS:
        layout["main"].split_row(
            Layout(name="identity", size=IDENTITY_COLS), Layout(name="body", ratio=1)
        )
        grid_parent = layout["body"]
    if cols >= STACK_COLS:
        grid_parent.split_column(
            Layout(name="top", ratio=1, minimum_size=MIN_PANEL_ROWS),
            Layout(name="bottom", ratio=1, minimum_size=MIN_PANEL_ROWS),
        )
        layout["top"].split_row(Layout(name="services", ratio=1), Layout(name="safety", ratio=1))
        layout["bottom"].split_row(Layout(name="hosts", ratio=1), Layout(name="activity", ratio=1))
    else:
        grid_parent.split_column(
            Layout(name="services", ratio=1, minimum_size=MIN_PANEL_ROWS),
            Layout(name="safety", ratio=1, minimum_size=MIN_PANEL_ROWS),
            Layout(name="hosts", ratio=1, minimum_size=MIN_PANEL_ROWS),
            Layout(name="activity", ratio=1, minimum_size=MIN_PANEL_ROWS),
        )
    return layout


def render(
    data: dict[str, Any],
    errors: dict[str, str],
    state: DashboardState,
    config_manager: Any,
    refresh_s: float,
    cols: int,
    rows: int,
    keys_enabled: bool = True,
) -> Layout:
    layout = create_layout(cols, rows)
    wide = cols >= WIDE_COLS
    layout["header"].update(make_header(data, errors, cols, compact_identity=not wide))
    if wide:
        layout["identity"].update(
            make_identity_panel(data, (rows - 6) < FULL_SIGIL_MIN_MAIN_ROWS, errors)
        )
    if state.overlay:
        # The grid is a SPLIT layout: updating its parent without unsplitting it
        # renders Rich's placeholder boxes ("Layout(name='services')") instead.
        target = layout["body" if wide else "main"]
        target.unsplit()
        target.update(make_overlay(state.overlay, data, errors))
    else:
        grid_w = cols - IDENTITY_COLS if wide else cols
        panel_w = grid_w // 2 if cols >= STACK_COLS else grid_w
        layout["services"].update(make_services_panel(data, errors))
        layout["safety"].update(make_safety_panel(data))
        layout["hosts"].update(make_hosts_panel(data, config_manager, show_address=panel_w >= 56))
        layout["activity"].update(make_activity_panel(data))
    layout["footer"].update(make_footer(refresh_s, keys_enabled))
    return layout


# ═══════════════════════════════════════════════════════════════
# Keyboard input (cross-platform, non-blocking)
# ═══════════════════════════════════════════════════════════════


class KeyReader:
    """Non-blocking keyboard reader. ``enabled`` is False when stdin is not a TTY."""

    def __init__(self) -> None:
        self._keys: list[str] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self.enabled = bool(getattr(sys.stdin, "isatty", lambda: False)())

    def start(self) -> None:
        if not self.enabled:
            return
        self._running = True
        self._thread = threading.Thread(target=self._read, name="navig-dashboard-keys", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread:
            # The POSIX reader restores the terminal mode in its own ``finally`` —
            # wait for it, or the shell is left in cbreak mode.
            self._thread.join(timeout=1.0)

    def get_key(self) -> str | None:
        with self._lock:
            return self._keys.pop(0) if self._keys else None

    def _push(self, ch: str) -> None:
        with self._lock:
            self._keys.append(ch)

    def _read(self) -> None:
        try:
            if sys.platform == "win32":
                self._read_win()
            else:
                self._read_unix()
        except Exception:  # noqa: BLE001 — keys are a convenience; Ctrl+C still works
            self.enabled = False

    def _read_win(self) -> None:
        import msvcrt

        while self._running:
            if msvcrt.kbhit():
                self._push(msvcrt.getwch())
            else:
                time.sleep(0.05)

    def _read_unix(self) -> None:
        import select
        import termios
        import tty

        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd, termios.TCSANOW)
            while self._running:
                r, _, _ = select.select([sys.stdin], [], [], 0.05)
                if r:
                    self._push(sys.stdin.read(1))
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ═══════════════════════════════════════════════════════════════
# Boot — the sigil assembles itself
# ═══════════════════════════════════════════════════════════════


def run_boot_sequence(entity: Any = None, fast: bool = False) -> None:
    """Reveal the install's sigil row by row, then name the node (~0.5 s)."""
    if fast or entity is None:
        return
    from navig.identity.entity import PALETTES
    from navig.identity.renderer import sigil_text

    primary = PALETTES[entity.palette_key][1]
    console.clear()
    console.print()
    for i in range(len(entity.sigil_matrix)):
        row = sigil_text(entity, reveal=i + 1)
        line = row.split("\n")[-1]
        console.print(Align.center(line))
        time.sleep(0.04)
    console.print()
    console.print(
        Align.center(Text("NODE-" + entity.seed[:4].upper() + "  online", style=f"bold {primary}"))
    )
    time.sleep(0.25)


# ═══════════════════════════════════════════════════════════════
# Entry points
# ═══════════════════════════════════════════════════════════════


def _open_deck(data: dict[str, Any], state: DashboardState) -> None:
    reach = (data.get("reach_ai") or {}).get("reach") or {}
    url = reach.get("deck_url") or reach.get("gateway_url")
    if not url:
        state.log("[yellow]No deck URL yet[/yellow]")
        return
    try:
        from navig.platform.opener import open_path

        open_path(url)
        state.log(f"Opened {url}")
    except Exception as exc:  # noqa: BLE001
        state.log(f"[red]Could not open {url}: {exc}[/red]")


def run_dashboard(refresh_interval: int = 5, skip_boot: bool = False, **_legacy: Any) -> None:
    """Run the live dashboard. Falls back to a snapshot when stdout is not a terminal."""
    from navig.config import get_config_manager

    if not sys.stdout.isatty():
        run_dashboard_simple()
        return

    config_manager = get_config_manager()
    refresh_s = float(max(1, refresh_interval))
    sources = build_sources(config_manager, refresh_s)
    collector = Collector(sources)
    collector.run_once(["identity"])
    data, _ = collector.snapshot()
    run_boot_sequence((data.get("identity") or {}).get("entity"), fast=skip_boot)

    # The doctor is on demand only — never on a timer.
    doctor_sources = {"doctor": (10**9, collect_doctor)}
    doctor = Collector(doctor_sources)

    state = DashboardState()
    keys = KeyReader()

    def on_key(k: str) -> bool:
        c = k.lower()
        if state.overlay and c != "q":
            state.overlay = None
            return True
        if c == "q":
            return False
        if c == "r":
            collector.refresh()
            state.log("Manual refresh")
        elif c == "d":
            state.overlay = "doctor"
            doctor.refresh()
            if doctor._thread is None:
                doctor.start()
        elif c == "g":
            _open_deck(collector.snapshot()[0], state)
        elif c == "?":
            state.overlay = "help"
        return True

    collector.start()
    keys.start()
    try:
        with Live(console=console, screen=True, auto_refresh=False) as live:
            while state.running:
                while (ch := keys.get_key()) is not None:
                    if not on_key(ch):
                        state.running = False
                        break
                if not state.running:
                    break
                data, errors = collector.snapshot()
                if state.overlay == "doctor":
                    ddata, derr = doctor.snapshot()
                    data = {**data, **ddata}
                    errors = {**errors, **derr}
                state.op_state = data
                state.hosts_status = data.get("hosts") or {}
                state.errors = len(errors)
                cols, rows = _get_terminal_size()
                live.update(
                    render(
                        data, errors, state, config_manager, refresh_s, cols, rows, keys.enabled
                    ),
                    refresh=True,
                )
                time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        keys.stop()
        collector.stop()
        doctor.stop()


def run_dashboard_simple() -> None:
    """One synchronous pass, printed once — for pipes, CI logs and ``--no-live``."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    collector = Collector(build_sources(config_manager))
    collector.run_once()
    data, errors = collector.snapshot()
    cols, _rows = _get_terminal_size()
    console.print(make_header(data, errors, cols, compact_identity=True))
    grid = Table.grid(expand=True, padding=(0, 1))
    grid.add_column(ratio=1)
    grid.add_column(ratio=1)
    if cols >= STACK_COLS:
        grid.add_row(make_services_panel(data, errors), make_safety_panel(data))
        grid.add_row(make_hosts_panel(data, config_manager), make_activity_panel(data))
        console.print(grid)
    else:
        for panel in (
            make_services_panel(data, errors),
            make_safety_panel(data),
            make_hosts_panel(data, config_manager),
            make_activity_panel(data),
        ):
            console.print(panel)
    attention = attention_items(data, errors)
    if attention:
        console.print(
            f"[yellow]⚠ {len(attention)} need attention:[/yellow] " + " · ".join(attention[:5])
        )
    else:
        console.print("[green]✓ all clear[/green] [dim]· live view: navig dashboard[/dim]")


__all__ = [
    "Collector",
    "DashboardState",
    "KeyReader",
    "attention_items",
    "build_sources",
    "create_layout",
    "render",
    "run_boot_sequence",
    "run_dashboard",
    "run_dashboard_simple",
]
