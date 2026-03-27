"""NAVIG Kraken Dashboard — Rich Live TUI

A real-time operations dashboard for NAVIG Core. Features:
- Animated Kraken ASCII mascot
- Daemon / Telegram bot / Gateway / Tunnel status
- Host connectivity with SSH ping
- SSH connection pool stats
- Recent operations history
- Keyboard-driven navigation
- Deep sea tips & activity log
- Responsive layout for all terminal sizes
- Fast boot animation
"""

import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from rich.align import Align
from rich.console import Console
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

console = Console()

# ═══════════════════════════════════════════════════════════════
# Kraken Theming
# ═══════════════════════════════════════════════════════════════

KRAKEN_FRAMES = [
    "     ___\n"
    "  .-'   '-.\n"
    " /  ◉   ◉  \\\n"
    "|    ___    |\n"
    " \\  \\___/  /\n"
    "  '-._____.-'\n"
    " /|\\  |  /|\\\n"
    "/ | \\ | / | \\\n"
    "~  ~  ~  ~  ~",
    "     ___\n"
    "  .-'   '-.\n"
    " /  ◉   ◉  \\\n"
    "|    ___    |\n"
    " \\  \\___/  /\n"
    "  '-._____.-'\n"
    "  /| \\ / |\\\n"
    " / |  X  | \\\n"
    "~  | / \\ |  ~",
    "     ___\n"
    "  .-'   '-.\n"
    " /  ●   ●  \\\n"
    "|    ___    |\n"
    " \\  \\___/  /\n"
    "  '-._____.-'\n"
    " /|\\  |  /|\\\n"
    "/ | \\ | / | \\\n"
    "~  ~  ~  ~  ~",
    "     ___\n"
    "  .-'   '-.\n"
    " /  ◉   ◉  \\\n"
    "|    ___    |\n"
    " \\  \\___/  /\n"
    "  '-._____.-'\n"
    "\\|/  \\|/  \\|/\n"
    " ~    ~    ~",
]

KRAKEN_MINI = " ◉‿◉\n" " /||\\\n" " ~  ~"

NAVIG_BANNER = (
    "    ███╗   ██╗ █████╗ ██╗   ██╗██╗ ██████╗\n"
    "    ████╗  ██║██╔══██╗██║   ██║██║██╔════╝\n"
    "    ██╔██╗ ██║███████║██║   ██║██║██║  ███╗\n"
    "    ██║╚██╗██║██╔══██║╚██╗ ██╔╝██║██║   ██║\n"
    "    ██║ ╚████║██║  ██║ ╚████╔╝ ██║╚██████╔╝\n"
    "    ╚═╝  ╚═══╝╚═╝  ╚═╝  ╚═══╝  ╚═╝ ╚═════╝"
)

NAVIG_BANNER_MINI = " ╔═╗╔═╗╦  ╦╦╔═╗\n" " ║║║╠═╣╚╗╔╝║║ ╦\n" " ╝╚╝╩ ╩ ╚╝ ╩╚═╝"

TENTACLE_TIPS = [
    "The Kraken sees all ports, hears all logs.",
    "Tentacles deployed. The deep web trembles.",
    "Every tunnel is a tentacle. Every tentacle, alive.",
    "The abyss gazes back — through beautiful dashboards.",
    "Kraken formation: ACTIVE. All tentacles responding.",
    "Deep sea protocol engaged. Surface stable.",
    "Neural mesh synced. The Kraken thinks.",
    "From the deep — clarity, control, consciousness.",
    "Daemon watches. Gateway listens. Kraken commands.",
    "The ocean is vast. The Kraken is patient.",
]

# ═══════════════════════════════════════════════════════════════
# State File Paths
# ═══════════════════════════════════════════════════════════════

NAVIG_HOME = Path.home() / ".navig"
DAEMON_PID_FILE = NAVIG_HOME / "daemon" / "supervisor.pid"
DAEMON_STATE_FILE = NAVIG_HOME / "daemon" / "state.json"
TUNNELS_FILE = NAVIG_HOME / "cache" / "tunnels.json"
GATEWAY_PORT = 8789


# ═══════════════════════════════════════════════════════════════
# Operational Service Detection
# ═══════════════════════════════════════════════════════════════


def _check_port(port: int) -> bool:
    """Return True if a TCP port is listening on localhost."""
    import socket

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            return s.connect_ex(("127.0.0.1", port)) == 0
    except Exception:
        return False


def _check_pid_alive(pid: int) -> bool:
    """Check if a PID is alive."""
    try:
        if sys.platform == "win32":
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x100000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError):
        return False


def _detect_daemon_status() -> Dict[str, Any]:
    """Detect NAVIG daemon supervisor status."""
    info = {"status": "stopped", "pid": None, "children": []}
    if DAEMON_PID_FILE.exists():
        try:
            pid = int(DAEMON_PID_FILE.read_text().strip())
            if _check_pid_alive(pid):
                info["status"] = "running"
                info["pid"] = pid
            else:
                info["status"] = "dead"
                info["pid"] = pid
        except (ValueError, OSError):
            pass  # best-effort cleanup

    if DAEMON_STATE_FILE.exists():
        try:
            state = json.loads(DAEMON_STATE_FILE.read_text())
            info["children"] = state.get("children", [])
            info["started_at"] = state.get("started_at")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

    return info


def _detect_child_status(
    daemon_info: Dict[str, Any], child_name: str
) -> Dict[str, Any]:
    """Extract a specific child status from daemon state."""
    for child in daemon_info.get("children", []):
        if child.get("name") == child_name:
            alive = child.get("alive", False)
            pid = child.get("pid")
            # Verify PID is actually alive
            if alive and pid and not _check_pid_alive(pid):
                alive = False
            return {
                "status": "running" if alive else "stopped",
                "pid": pid,
                "restarts": child.get("restart_count", 0),
                "enabled": child.get("enabled", False),
                "exit_code": child.get("last_exit_code"),
            }
    return {"status": "unknown", "pid": None, "restarts": 0, "enabled": False}


def _detect_gateway_status() -> Dict[str, Any]:
    """Detect NAVIG Gateway status via port check."""
    if _check_port(GATEWAY_PORT):
        return {"status": "running", "port": GATEWAY_PORT}
    return {"status": "stopped", "port": GATEWAY_PORT}


def _detect_tunnels() -> List[Dict[str, Any]]:
    """Load active SSH tunnels from cache."""
    if not TUNNELS_FILE.exists():
        return []
    try:
        data = json.loads(TUNNELS_FILE.read_text())
        tunnels = []
        for name, info in data.items():
            pid = info.get("pid")
            alive = _check_pid_alive(pid) if pid else False
            tunnels.append(
                {
                    "server": name,
                    "pid": pid,
                    "local_port": info.get("local_port"),
                    "started_at": info.get("started_at"),
                    "alive": alive,
                }
            )
        return tunnels
    except Exception:
        return []


def _detect_pool_stats() -> Dict[str, Any]:
    """Get SSH connection pool stats (safe — no import error if pool unused)."""
    try:
        from navig.connection_pool import SSHConnectionPool

        pool = SSHConnectionPool.get_instance()
        return pool.stats
    except Exception:
        return {"active_connections": 0, "hits": 0, "misses": 0, "hit_rate": 0.0}


def _get_terminal_size() -> tuple:
    """Get terminal columns and rows."""
    sz = shutil.get_terminal_size((80, 24))
    return sz.columns, sz.lines


# ═══════════════════════════════════════════════════════════════
# Keyboard Input (cross-platform, non-blocking)
# ═══════════════════════════════════════════════════════════════


class KeyReader:
    """Non-blocking keyboard reader for the dashboard."""

    def __init__(self):
        self._keys: List[str] = []
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def get_key(self) -> Optional[str]:
        with self._lock:
            return self._keys.pop(0) if self._keys else None

    def _push(self, ch: str):
        with self._lock:
            self._keys.append(ch)

    def _read(self):
        if sys.platform == "win32":
            self._read_win()
        else:
            self._read_unix()

    def _read_win(self):
        import msvcrt

        while self._running:
            if msvcrt.kbhit():
                try:
                    self._push(msvcrt.getwch())
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
            else:
                time.sleep(0.05)

    def _read_unix(self):
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
                    try:
                        self._push(sys.stdin.read(1))
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ═══════════════════════════════════════════════════════════════
# Boot Sequence Animation (lightweight)
# ═══════════════════════════════════════════════════════════════

BOOT_STEPS = [
    ("🐙", "Waking the Kraken"),
    ("📡", "Scanning services"),
    ("🌊", "Surface protocol engaged"),
]

SPINNER_CHARS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def run_boot_sequence(fast: bool = False) -> None:
    """Run a lightweight Kraken boot animation."""
    if fast:
        console.print("[bold cyan]⚡ NAVIG Kraken[/bold cyan] — initializing…")
        time.sleep(0.2)
        return

    cols, rows = _get_terminal_size()
    wave = "".join("~" if (i % 3 != 0) else "≈" for i in range(cols))
    banner = NAVIG_BANNER if cols >= 60 else NAVIG_BANNER_MINI

    console.clear()
    console.print(f"[dim cyan]{wave}[/dim cyan]")
    console.print()

    for line in banner.splitlines():
        console.print(f"[bold cyan]{line}[/bold cyan]")
        time.sleep(0.02)

    console.print()
    console.print("[bold magenta]    ⚡ Release the Kraken ⚡[/bold magenta]")
    console.print(f"[dim cyan]{wave}[/dim cyan]")
    console.print()

    bar_w = min(20, cols - 30)
    if bar_w < 8:
        bar_w = 8
    for icon, label in BOOT_STEPS:
        for fi in range(bar_w + 3):
            filled = min(fi, bar_w)
            sp = SPINNER_CHARS[fi % len(SPINNER_CHARS)]
            bar = f"[cyan]{'█' * filled}[/cyan][dim]{'░' * (bar_w - filled)}[/dim]"
            console.print(f"\r  [yellow]{sp}[/yellow] {icon}  {label}  {bar}", end="")
            time.sleep(0.015)
        console.print(
            f"\r  [green]✓[/green] {icon}  {label}  [green]{'█' * bar_w}[/green]"
        )

    console.print()
    console.print("  [bold green]⚡ Kraken ready[/bold green]")
    console.print()
    time.sleep(0.3)


# ═══════════════════════════════════════════════════════════════
# Dashboard Panels
# ═══════════════════════════════════════════════════════════════


def create_layout(cols: int = 120, rows: int = 30) -> Layout:
    """Build responsive dashboard grid."""
    layout = Layout()
    layout.split(
        Layout(name="header", size=3),
        Layout(name="main", ratio=1),
        Layout(name="footer", size=3),
    )

    # Narrow terminal: stack vertically
    if cols < 90:
        layout["main"].split_column(
            Layout(name="services", ratio=3),
            Layout(name="tunnels", ratio=2),
            Layout(name="hosts", ratio=2),
            Layout(name="history", ratio=2),
        )
    # Wide terminal: two columns
    else:
        layout["main"].split_row(
            Layout(name="left", ratio=3),
            Layout(name="right", ratio=2),
        )
        layout["left"].split_column(
            Layout(name="services", ratio=3),
            Layout(name="tunnels", ratio=2),
        )
        # Show kraken panel only if tall enough
        if rows >= 28:
            layout["right"].split_column(
                Layout(name="hosts", ratio=2),
                Layout(name="kraken", size=14),
                Layout(name="tip", size=5),
                Layout(name="history", ratio=1),
            )
        else:
            layout["right"].split_column(
                Layout(name="hosts", ratio=2),
                Layout(name="tip", size=5),
                Layout(name="history", ratio=1),
            )
    return layout


def make_header(
    active_host: Optional[str],
    active_app: Optional[str],
    event_count: int,
    error_count: int,
    uptime_s: float,
    cols: int = 120,
) -> Panel:
    """Kraken-themed header bar."""
    now = datetime.now().strftime("%H:%M:%S")
    m = int(uptime_s // 60)
    up = f"{m}m" if m < 60 else f"{m // 60}h{m % 60}m"

    if cols >= 90:
        parts = ["[bold cyan]⚡ NAVIG KRAKEN[/bold cyan]"]
    else:
        parts = ["[bold cyan]⚡ NAVIG[/bold cyan]"]

    if active_host:
        parts.append(f"[green]●[/green] {active_host}")
    else:
        parts.append("[dim]○ no host[/dim]")
    if active_app:
        parts.append(f"[green]●[/green] {active_app}")
    err_c = f"[red]{error_count}[/red]" if error_count else "[green]0[/green]"
    parts.append(f"Err {err_c}")
    parts.append(f"[dim]{now}[/dim] [cyan]↑{up}[/cyan]")

    return Panel(" │ ".join(parts), border_style="cyan")


def make_services_panel(op_state: Dict[str, Any], cols: int = 120) -> Panel:
    """Core operational services status panel."""
    daemon = op_state.get("daemon", {})
    bot = op_state.get("telegram_bot", {})
    gateway = op_state.get("gateway", {})
    pool = op_state.get("pool", {})

    compact = cols < 90

    table = Table(
        show_header=True,
        header_style="bold cyan",
        expand=True,
        box=None,
        padding=(0, 1),
    )
    table.add_column("Service", min_width=10)
    table.add_column("Status", width=12, justify="center")
    if not compact:
        table.add_column("PID", width=7, justify="right")
        table.add_column("Port", width=6, justify="right")
        table.add_column("Info", style="dim")

    # Status renderer
    def _st(status: str) -> str:
        return {
            "running": "[green]● running[/green]",
            "stopped": "[white]○ stopped[/white]",
            "dead": "[red]✖ dead[/red]",
            "unknown": "[dim]? unknown[/dim]",
            "disabled": "[dim]- disabled[/dim]",
        }.get(status, f"[dim]{status}[/dim]")

    # 1. Daemon Supervisor
    d_pid = str(daemon.get("pid", "")) if daemon.get("pid") else "-"
    d_info = ""
    if daemon.get("started_at"):
        try:
            dt = datetime.fromisoformat(daemon["started_at"].replace("Z", "+00:00"))
            d_info = f"since {dt.strftime('%H:%M')}"
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
    if compact:
        table.add_row("🐙 Daemon", _st(daemon.get("status", "stopped")))
    else:
        table.add_row(
            "🐙 Daemon Supervisor",
            _st(daemon.get("status", "stopped")),
            d_pid,
            "-",
            d_info,
        )

    # 2. Telegram Bot
    b_status = bot.get("status", "unknown")
    if not bot.get("enabled") and b_status != "running":
        b_status = "disabled"
    b_pid = str(bot.get("pid", "")) if bot.get("pid") else "-"
    b_info = ""
    if bot.get("restarts", 0) > 0:
        b_info = f"restarts: {bot['restarts']}"
    if compact:
        table.add_row("📡 Telegram Bot", _st(b_status))
    else:
        table.add_row("📡 Telegram Bot", _st(b_status), b_pid, "-", b_info)

    # 3. Gateway
    gw_status = gateway.get("status", "stopped")
    gw_port = str(gateway.get("port", GATEWAY_PORT))
    if compact:
        table.add_row(f"🌐 Gateway :{gw_port}", _st(gw_status))
    else:
        table.add_row("🌐 Gateway", _st(gw_status), "-", gw_port, "aiohttp API server")

    # 4. Scheduler (daemon child)
    sched = op_state.get("scheduler", {})
    sc_status = sched.get("status", "unknown")
    if not sched.get("enabled") and sc_status != "running":
        sc_status = "disabled"
    sc_pid = str(sched.get("pid", "")) if sched.get("pid") else "-"
    if compact:
        table.add_row("⏱️  Scheduler", _st(sc_status))
    else:
        table.add_row("⏱️  Scheduler", _st(sc_status), sc_pid, "-", "")

    # 5. SSH Connection Pool
    active = pool.get("active_connections", 0)
    hits = pool.get("hits", 0)
    rate = pool.get("hit_rate", 0)
    pool_info = (
        f"active: {active}  hits: {hits}  rate: {rate:.0%}"
        if hits > 0
        else f"active: {active}"
    )
    p_status = "running" if active > 0 else "stopped"
    if compact:
        table.add_row(f"🔗 SSH Pool ({active})", _st(p_status))
    else:
        table.add_row("🔗 SSH Pool", _st(p_status), "-", "-", pool_info)

    return Panel(table, title="[bold]⚡ Core Services[/bold]", border_style="cyan")


def make_tunnels_panel(tunnels: List[Dict[str, Any]], cols: int = 120) -> Panel:
    """Active SSH tunnels panel."""
    compact = cols < 90

    if not tunnels:
        return Panel(
            "[dim]No active tunnels — use [cyan]navig tunnel start[/cyan] to open one[/dim]",
            title="[bold]🦑 SSH Tunnels[/bold]",
            border_style="magenta",
        )

    table = Table(
        show_header=True,
        header_style="bold magenta",
        expand=True,
        box=None,
        padding=(0, 1),
    )
    table.add_column("Server", min_width=8)
    table.add_column("Port", width=6, justify="right")
    table.add_column("Status", width=10, justify="center")
    if not compact:
        table.add_column("PID", width=7, justify="right")
        table.add_column("Since", width=8, style="dim")

    alive_count = sum(1 for t in tunnels if t.get("alive"))

    for t in tunnels:
        alive = t.get("alive", False)
        st = "[green]● alive[/green]" if alive else "[red]✖ dead[/red]"
        port = str(t.get("local_port", "?"))
        pid = str(t.get("pid", "")) if t.get("pid") else "-"
        since = ""
        if t.get("started_at"):
            try:
                dt = datetime.fromisoformat(t["started_at"].replace("Z", "+00:00"))
                since = dt.strftime("%H:%M")
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        if compact:
            table.add_row(t.get("server", "?")[:12], port, st)
        else:
            table.add_row(t.get("server", "?"), port, st, pid, since)

    title = f"[bold]🦑 SSH Tunnels ({alive_count}/{len(tunnels)})[/bold]"
    return Panel(table, title=title, border_style="magenta")


def make_hosts_panel(
    config_manager, hosts_status: Dict[str, Any], cols: int = 120
) -> Panel:
    """Remote host connectivity panel."""
    compact = cols < 90

    table = Table(
        show_header=True,
        header_style="bold cyan",
        expand=True,
        box=None,
    )
    table.add_column("Host", style="bold")
    if not compact:
        table.add_column("IP", style="dim")
    table.add_column("Status", justify="center")
    table.add_column("Latency", justify="right")

    try:
        hosts = config_manager.list_hosts()
        active = config_manager.get_active_host()
        limit = 4 if compact else 6
        for host_name in hosts[:limit]:
            try:
                hc = config_manager.load_host_config(host_name)
                ip = hc.get("host", hc.get("ip", "N/A"))
                si = hosts_status.get(host_name, {})
                status = si.get("status", "unknown")
                latency = si.get("latency")

                if status == "connected":
                    si_txt = "[green]● Online[/green]"
                    lat = f"[dim]{latency:.0f}ms[/dim]" if latency else ""
                elif status == "failed":
                    si_txt = "[red]✗ Down[/red]"
                    lat = ""
                else:
                    si_txt = "[yellow]○ …[/yellow]"
                    lat = ""

                nm = (
                    f"[bold green]{host_name}[/bold green]"
                    if host_name == active
                    else host_name
                )
                if compact:
                    table.add_row(nm, si_txt, lat)
                else:
                    table.add_row(nm, ip[:15] if ip else "", si_txt, lat)
            except Exception:
                if compact:
                    table.add_row(host_name, "[dim]err[/dim]", "")
                else:
                    table.add_row(host_name, "?", "[dim]err[/dim]", "")
    except Exception as e:
        if compact:
            table.add_row(f"[red]{e}[/red]", "", "")
        else:
            table.add_row(f"[red]{e}[/red]", "", "", "")

    return Panel(table, title="[bold]🌐 Remote Hosts[/bold]", border_style="cyan")


def make_kraken_panel(frame_idx: int, compact: bool = False) -> Panel:
    """Animated Kraken ASCII art."""
    if compact:
        frame = KRAKEN_MINI
    else:
        frame = KRAKEN_FRAMES[frame_idx % len(KRAKEN_FRAMES)]
    frame = frame.replace("◉", "[bold cyan]◉[/bold cyan]").replace(
        "●", "[bold magenta]●[/bold magenta]"
    )
    return Panel(
        Align.center(Text.from_markup(f"[magenta]{frame}[/magenta]")),
        title="[bold magenta]🐙 Kraken[/bold magenta]",
        border_style="magenta",
    )


def make_tip_panel(activity_log: List[str]) -> Panel:
    """Deep sea tips and activity feed."""
    tip = TENTACLE_TIPS[int(time.time() / 10) % len(TENTACLE_TIPS)]
    recent = activity_log[-3:] if activity_log else []
    lines = [f"[cyan]›[/cyan] {a}" for a in recent]
    lines.append(f"\n[bold magenta]{tip}[/bold magenta]")
    return Panel(
        "\n".join(lines),
        title="[bold blue]🌊 Deep Sea Transmissions[/bold blue]",
        border_style="blue",
    )


def make_history_panel() -> Panel:
    """Recent operations from operation recorder."""
    from navig.operation_recorder import get_operation_recorder

    table = Table(
        show_header=True,
        header_style="bold yellow",
        expand=True,
        box=None,
    )
    table.add_column("Time", style="dim", width=6)
    table.add_column("Command", style="bold")
    table.add_column("Host", style="cyan", width=8)
    table.add_column("", justify="center", width=3)

    try:
        recorder = get_operation_recorder()
        ops = recorder.get_last_n(6)
        if not ops:
            return Panel(
                "[dim]No operations yet — they appear as you use NAVIG[/dim]",
                title="[bold]📋 Recent Ops[/bold]",
                border_style="yellow",
            )
        for op in ops:
            try:
                ts = datetime.fromisoformat(op.timestamp.replace("Z", "+00:00"))
                t = ts.strftime("%H:%M")
            except Exception:
                t = "?"
            cmd = op.command[:20] + "…" if len(op.command) > 20 else op.command
            st_map = {
                "success": "[green]✓[/green]",
                "failed": "[red]✗[/red]",
                "running": "[yellow]●[/yellow]",
            }
            st = st_map.get(op.status, "[dim]○[/dim]")
            table.add_row(t, cmd, (op.host or "-")[:8], st)
    except Exception as e:
        return Panel(
            f"[red]{e}[/red]", title="[bold]📋 Recent Ops[/bold]", border_style="yellow"
        )

    return Panel(table, title="[bold]📋 Recent Ops[/bold]", border_style="yellow")


def make_footer(cols: int = 120) -> Panel:
    """Keyboard help bar."""
    if cols < 70:
        text = "[cyan]Q[/cyan] Quit │ [cyan]R[/cyan] Refresh │ [dim]Auto 5s[/dim]"
    else:
        text = (
            "[cyan]Q[/cyan] Quit  │  "
            "[cyan]R[/cyan] Refresh  │  "
            "[cyan]D[/cyan] Daemon  │  "
            "[cyan]G[/cyan] Gateway  │  "
            "[cyan]T[/cyan] Tunnels  │  "
            "[dim]Auto 5s[/dim]"
        )
    return Panel(text, border_style="dim")


# ═══════════════════════════════════════════════════════════════
# Dashboard State
# ═══════════════════════════════════════════════════════════════


class DashboardState:
    """All mutable dashboard state."""

    def __init__(self):
        self.hosts_status: Dict[str, Dict[str, Any]] = {}
        self.op_state: Dict[str, Any] = (
            {}
        )  # daemon, telegram_bot, gateway, scheduler, pool, tunnels
        self.last_hosts_check: float = 0
        self.last_service_check: float = 0
        self.host_check_interval: float = 30.0
        self.svc_check_interval: float = 5.0
        self.running: bool = True
        self.refresh_requested: bool = False
        self.kraken_frame: int = 0
        self.events: int = 0
        self.errors: int = 0
        self.started_at: float = time.time()
        self.activity_log: List[str] = []

    def log(self, msg: str):
        ts = datetime.now().strftime("%H:%M:%S")
        self.activity_log.append(f"[dim]{ts}[/dim] {msg}")
        if len(self.activity_log) > 50:
            self.activity_log = self.activity_log[-50:]
        self.events += 1


# ═══════════════════════════════════════════════════════════════
# Background Workers
# ═══════════════════════════════════════════════════════════════


def check_host_connectivity(
    host_config: Dict[str, Any], timeout: int = 5
) -> Dict[str, Any]:
    """Ping-based connectivity check."""
    try:
        host = host_config.get("host", host_config.get("ip"))
        if not host:
            return {"status": "failed", "latency": None}
        start = time.time()
        if platform.system().lower() == "windows":
            r = subprocess.run(
                ["ping", "-n", "1", "-w", str(timeout * 1000), host],
                capture_output=True,
                timeout=timeout + 1,
            )
        else:
            r = subprocess.run(
                ["ping", "-c", "1", "-W", str(timeout), host],
                capture_output=True,
                timeout=timeout + 1,
            )
        lat = (time.time() - start) * 1000
        return (
            {"status": "connected", "latency": lat}
            if r.returncode == 0
            else {"status": "failed", "latency": None}
        )
    except Exception:
        return {"status": "failed", "latency": None}


def _bg_update_hosts(config_manager, state: DashboardState):
    def _work():
        try:
            for h in config_manager.list_hosts()[:8]:
                try:
                    hc = config_manager.load_host_config(h)
                    state.hosts_status[h] = check_host_connectivity(hc)
                except Exception:
                    state.hosts_status[h] = {"status": "failed", "latency": None}
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        state.last_hosts_check = time.time()

    threading.Thread(target=_work, daemon=True).start()


def _bg_update_services(state: DashboardState):
    """Update all operational service states in background."""

    def _work():
        try:
            # Daemon + children
            daemon = _detect_daemon_status()
            state.op_state["daemon"] = daemon
            state.op_state["telegram_bot"] = _detect_child_status(
                daemon, "telegram-bot"
            )
            state.op_state["scheduler"] = _detect_child_status(daemon, "scheduler")

            # Gateway
            state.op_state["gateway"] = _detect_gateway_status()

            # Tunnels
            state.op_state["tunnels"] = _detect_tunnels()

            # SSH pool
            state.op_state["pool"] = _detect_pool_stats()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        state.last_service_check = time.time()

    threading.Thread(target=_work, daemon=True).start()


# ═══════════════════════════════════════════════════════════════
# Main Dashboard Entry Points
# ═══════════════════════════════════════════════════════════════


def run_dashboard(
    refresh_interval: int = 5,
    show_docker: bool = True,
    show_resources: bool = True,
    skip_boot: bool = False,
) -> None:
    """Run the live Kraken dashboard with keyboard control.

    Args:
        refresh_interval: Display refresh interval in seconds.
        show_docker: Reserved for future use.
        show_resources: Reserved for future use.
        skip_boot: Skip the boot animation.
    """
    from navig.config import get_config_manager

    if not sys.stdout.isatty():
        console.print("[red]Dashboard requires an interactive terminal.[/red]")
        console.print("[dim]Use 'navig status' for non-interactive output.[/dim]")
        return

    # Boot animation
    run_boot_sequence(fast=bool(skip_boot))

    config_manager = get_config_manager()
    state = DashboardState()
    keys = KeyReader()

    active_host = config_manager.get_active_host()
    active_app = None
    try:
        active_app = config_manager.get_active_app()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Initial background scans
    _bg_update_hosts(config_manager, state)
    _bg_update_services(state)
    state.log("[green]⚡[/green] Kraken awakened")

    def build() -> Layout:
        """Assemble responsive dashboard layout."""
        cols, rows = _get_terminal_size()
        layout = create_layout(cols, rows)
        up = time.time() - state.started_at

        layout["header"].update(
            make_header(active_host, active_app, state.events, state.errors, up, cols)
        )
        layout["services"].update(make_services_panel(state.op_state, cols))

        tunnels = state.op_state.get("tunnels", [])
        layout["tunnels"].update(make_tunnels_panel(tunnels, cols))

        # Wide layout has left/right split
        if cols >= 90:
            layout["hosts"].update(
                make_hosts_panel(config_manager, state.hosts_status, cols)
            )
            # Kraken only on tall terminals
            if rows >= 28:
                layout["kraken"].update(make_kraken_panel(state.kraken_frame))
            layout["tip"].update(make_tip_panel(state.activity_log))
            layout["history"].update(make_history_panel())
        else:
            # Narrow: vertical stack
            layout["hosts"].update(
                make_hosts_panel(config_manager, state.hosts_status, cols)
            )
            layout["history"].update(make_history_panel())

        layout["footer"].update(make_footer(cols))
        return layout

    def on_key(k: str) -> bool:
        """Process keypress. Return False to quit."""
        c = k.lower()
        if c == "q":
            return False
        if c == "r":
            state.refresh_requested = True
            _bg_update_hosts(config_manager, state)
            _bg_update_services(state)
            state.log("Manual refresh")
            return True
        if c == "d":
            # Quick daemon info
            d = state.op_state.get("daemon", {})
            st = d.get("status", "unknown")
            pid = d.get("pid", "-")
            state.log(f"Daemon: {st} (PID {pid})")
            return True
        if c == "g":
            # Quick gateway info
            gw = state.op_state.get("gateway", {})
            st = gw.get("status", "stopped")
            port = gw.get("port", GATEWAY_PORT)
            state.log(f"Gateway :{port} — {st}")
            if st == "running":
                try:
                    url = f"http://127.0.0.1:{port}"
                    if sys.platform == "win32":
                        os.startfile(url)
                    elif sys.platform == "darwin":
                        subprocess.Popen(["open", url])
                    else:
                        subprocess.Popen(["xdg-open", url])
                    state.log(f"[blue]🌐[/blue] Opened {url}")
                except Exception as exc:
                    state.log(f"[red]Browser error: {exc}[/red]")
            return True
        if c == "t":
            # Quick tunnel info
            tunnels = state.op_state.get("tunnels", [])
            alive = sum(1 for t in tunnels if t.get("alive"))
            state.log(f"Tunnels: {alive}/{len(tunnels)} active")
            return True
        return True

    keys.start()
    try:
        with Live(build(), refresh_per_second=2, console=console, screen=True) as live:
            last_draw = time.time()
            kraken_t = time.time()

            while state.running:
                now = time.time()

                # Drain key buffer
                while True:
                    ch = keys.get_key()
                    if ch is None:
                        break
                    if not on_key(ch):
                        state.running = False
                        break
                if not state.running:
                    break

                # Kraken frame every 2s
                if now - kraken_t >= 2.0:
                    state.kraken_frame += 1
                    kraken_t = now

                # Background checks
                if now - state.last_hosts_check > state.host_check_interval:
                    _bg_update_hosts(config_manager, state)
                if now - state.last_service_check > state.svc_check_interval:
                    _bg_update_services(state)

                # Redraw
                if now - last_draw >= refresh_interval or state.refresh_requested:
                    live.update(build())
                    last_draw = now
                    state.refresh_requested = False

                time.sleep(0.05)

    except KeyboardInterrupt:
        pass  # user interrupted; clean exit
    finally:
        keys.stop()
        console.print("\n[dim cyan]🐙 The Kraken retreats to the deep…[/dim cyan]\n")


def run_dashboard_simple() -> None:
    """Non-live snapshot for non-interactive environments."""
    from navig.config import get_config_manager

    config_manager = get_config_manager()
    state = DashboardState()

    active_host = config_manager.get_active_host()
    active_app = None
    try:
        active_app = config_manager.get_active_app()
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    cols, rows = _get_terminal_size()

    # Detect all operational services
    daemon = _detect_daemon_status()
    state.op_state["daemon"] = daemon
    state.op_state["telegram_bot"] = _detect_child_status(daemon, "telegram-bot")
    state.op_state["scheduler"] = _detect_child_status(daemon, "scheduler")
    state.op_state["gateway"] = _detect_gateway_status()
    state.op_state["tunnels"] = _detect_tunnels()
    state.op_state["pool"] = _detect_pool_stats()

    console.print(make_header(active_host, active_app, 0, 0, 0.0, cols))
    console.print()
    console.print(make_services_panel(state.op_state, cols))
    console.print()
    console.print(make_tunnels_panel(state.op_state.get("tunnels", []), cols))
    console.print()
    console.print(make_hosts_panel(config_manager, state.hosts_status, cols))
    console.print()
    console.print(make_history_panel())
