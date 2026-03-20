"""
NAVIG Mini — builtin plugin for controlling remote Mini agent daemons.

Registers:  navig mini status / run / deploy / logs / restart / list

Config (set via navig config set, or env vars):
    mini.url        http://DEVICE_IP:9191          default agent URL
    mini.secret     <hmac_secret>                  HMAC-SHA256 signing key
    mini.ssh_host   wd-cloud-nas                   NAVIG host alias for SSH ops
    mini.agents     [{"name":"nas","url":"..."}]   JSON list for multi-agent

Quick-start:
    navig config set mini.url    http://10.0.0.34:9191
    navig config set mini.secret ce3648def9fb81a42be35b50987a83f6b1b9c97c4bbdb07...
    navig mini status
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
import time
from typing import List, Optional, Tuple
from urllib.error import URLError
from urllib.request import Request, urlopen

import typer
from rich.console import Console
from rich.table import Table

# ── Plugin metadata (read by PluginManager) ────────────────────────────────────
name        = "mini"
version     = "1.1.0"
description = "Control remote NAVIG Mini agent daemons on low-power devices"

console = Console()


# ── Config helpers ──────────────────────────────────────────────────────────────
def _navig_get(key: str, default: str = "") -> str:
    """Read a key from the NAVIG config/vault."""
    try:
        r = subprocess.run(
            ["navig", "config", "get", key],
            capture_output=True, text=True, timeout=3
        )
        val = r.stdout.strip()
        if val and "not found" not in val.lower() and "error" not in val.lower():
            return val
    except Exception:
        pass
    return os.environ.get(key.replace(".", "_").upper(), default)


def _cfg() -> dict:
    return {
        "url":      _navig_get("mini.url",    os.environ.get("MINI_AGENT_URL",    "http://10.0.0.34:9191")),
        "secret":   _navig_get("mini.secret", os.environ.get("MINI_AGENT_SECRET", os.environ.get("AGENT_SECRET", ""))),
        "ssh_host": _navig_get("mini.ssh_host", os.environ.get("MINI_SSH_HOST",  "wd-cloud-nas")),
    }


# ── HTTP helpers ────────────────────────────────────────────────────────────────
def _get(url: str, path: str, timeout: int = 5) -> Optional[dict]:
    try:
        resp = urlopen(f"{url.rstrip('/')}{path}", timeout=timeout)
        return json.loads(resp.read())
    except URLError as exc:
        console.print(f"[red]✗ Agent unreachable:[/red] {exc}")
        raise typer.Exit(1) from exc


def _post(url: str, path: str, secret: str, payload: dict, timeout: int = 35) -> dict:
    body = json.dumps(payload).encode()
    sig  = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    req  = Request(
        f"{url.rstrip('/')}{path}",
        data=body,
        headers={"Content-Type": "application/json", "X-Agent-Sig": sig},
        method="POST",
    )
    try:
        return json.loads(urlopen(req, timeout=timeout).read())
    except URLError as exc:
        console.print(f"[red]✗ Agent POST error:[/red] {exc}")
        raise typer.Exit(1) from exc


def _ping(url: str, timeout: int = 3) -> Optional[dict]:
    """Non-raising ping — returns None on failure."""
    try:
        return json.loads(urlopen(f"{url.rstrip('/')}/ping", timeout=timeout).read())
    except Exception:
        return None


# ── Typer app ────────────────────────────────────────────────────────────────────
app = typer.Typer(
    name="mini",
    help="Control remote NAVIG Mini agent daemons (Raspberry Pi, NAS, VPS).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command("status")
def cmd_status(
    url:      Optional[str] = typer.Option(None, "--url",    "-u", help="Override agent URL"),
    json_out: bool          = typer.Option(False, "--json",        help="Output raw JSON"),
):
    """[bold]Show health and system stats[/bold] of the remote agent."""
    cfg  = _cfg()
    aurl = url or cfg["url"]
    s    = _get(aurl, "/stats")

    if json_out:
        console.print_json(json.dumps(s))
        return

    console.print(
        f"\n  [bold green]✓[/bold green]  NAVIG Mini "
        f"[cyan]v{s.get('version','?')}[/cyan] — [bold]{s.get('hostname','?')}[/bold]"
    )
    t = Table(show_header=False, box=None, padding=(0, 2))
    t.add_column(style="dim")
    t.add_column(style="cyan")
    t.add_row("URL",     aurl)
    t.add_row("Uptime",  f"{s.get('uptime_sec', '?')}s")
    t.add_row("RAM",     f"{s.get('ram_free_mb','?')} MB free / {s.get('ram_total_mb','?')} MB")
    t.add_row("Disk",    f"{s.get('disk_pct','?')} used — {s.get('disk_free','?')} free  [{s.get('disk_path','?')}]")
    t.add_row("Load",    str(s.get("load", "?")))
    t.add_row("Python",  str(s.get("python", "?")))
    console.print(t)
    console.print()


@app.command("run")
def cmd_run(
    cmd:     str           = typer.Argument(..., help="Command to execute (must be in agent allowlist)"),
    url:     Optional[str] = typer.Option(None, "--url",     "-u"),
    secret:  Optional[str] = typer.Option(None, "--secret",  "-s"),
    timeout: int           = typer.Option(30,   "--timeout", "-t", help="Timeout in seconds"),
    json_out:bool          = typer.Option(False, "--json"),
):
    """[bold]Execute an allowlisted command[/bold] on the remote agent (HMAC-signed)."""
    cfg  = _cfg()
    aurl = url or cfg["url"]
    asec = secret or cfg["secret"]

    if not asec:
        console.print("[red]✗ No HMAC secret.[/red]  Set: [cyan]navig config set mini.secret <secret>[/cyan]")
        raise typer.Exit(1)

    r = _post(aurl, "/run", asec, {"cmd": cmd, "timeout": timeout})

    if json_out:
        console.print_json(json.dumps(r))
        return

    if r.get("code", 0) != 0:
        console.print(f"[yellow]⚠ Exit code {r['code']}[/yellow]")
    if r.get("stdout"):
        console.print(r["stdout"].rstrip())
    if r.get("stderr"):
        console.print(f"[dim]{r['stderr'].rstrip()}[/dim]")


@app.command("deploy")
def cmd_deploy(
    host:       Optional[str] = typer.Option(None, "--host", "-H",
                                             help="NAVIG host name (default: mini.ssh_host)"),
    remote_dir: str           = typer.Option("~/navig-mini", "--dir", "-d",
                                             help="Remote install directory"),
    restart:    bool          = typer.Option(True,  help="Restart agent after deploy"),
    local_dir:  Optional[str] = typer.Option(None, "--local", "-l",
                                             help="Local navig-mini/ path (auto-detected if omitted)"),
):
    """[bold]Upload agent files[/bold] to a remote device via SSH and restart."""
    from pathlib import Path

    cfg      = _cfg()
    ssh_host = host or cfg["ssh_host"]

    # Locate local source files
    candidates: list[Path] = []
    if local_dir:
        candidates.append(Path(local_dir))
    candidates += [
        Path(os.environ.get("NAVIG_MINI_DIR", "__none__")),
        Path.home() / "navig-mini",
        Path("/opt/navig-mini"),
    ]
    source_dir = next((p for p in candidates if (p / "agent.py").exists()), None)

    if not source_dir:
        console.print("[red]✗ Cannot locate navig-mini/ source directory.[/red]")
        console.print("  Use [cyan]--local /path/to/navig-mini[/cyan] or set NAVIG_MINI_DIR")
        raise typer.Exit(1)

    console.print(f"\n  [cyan]Deploying to[/cyan] [bold]{ssh_host}[/bold]:{remote_dir}")
    console.print(f"  [dim]Source:[/dim] {source_dir}\n")

    for fname in ["agent.py", "monitor.py"]:
        src = source_dir / fname
        if not src.exists():
            console.print(f"  [yellow]⚠[/yellow] {fname} not found locally — skipping")
            continue
        console.print(f"  ↑  {fname}…", end="")
        r = subprocess.run(
            ["navig", "file", "add", str(src), f"{remote_dir}/{fname}", "--host", ssh_host],
            capture_output=True, text=True
        )
        if r.returncode == 0:
            console.print("  [green]✓[/green]")
        else:
            console.print(f"  [red]✗[/red]  {r.stderr.strip()}")

    if restart:
        console.print("  ↻  restarting agent…", end="")
        subprocess.run(
            ["navig", "run",
             f"pkill -f {remote_dir}/agent.py 2>/dev/null; sleep 1; "
             f"nohup python3 {remote_dir}/agent.py >> {remote_dir}/agent.log 2>&1 </dev/null &",
             "--host", ssh_host],
            capture_output=True, text=True
        )
        time.sleep(3)
        console.print("  [green]✓[/green]")

    console.print("\n  [bold green]Deploy complete![/bold green]")
    url_port = cfg["url"].rsplit(":", 1)[-1] if ":" in cfg["url"] else "9191"
    console.print(f"  Verify: [cyan]navig mini status --url http://DEVICE_IP:{url_port}[/cyan]\n")


@app.command("logs")
def cmd_logs(
    service:    str           = typer.Argument("agent", help="Log source: agent | monitor"),
    lines:      int           = typer.Option(50, "-n", "--lines"),
    host:       Optional[str] = typer.Option(None, "--host", "-H"),
    remote_dir: str           = typer.Option("~/navig-mini", "--dir", "-d"),
):
    """[bold]Tail agent or monitor logs[/bold] from the remote device via SSH."""
    cfg      = _cfg()
    ssh_host = host or cfg["ssh_host"]
    log_file = f"{remote_dir}/{service}.log"

    r = subprocess.run(
        ["navig", "run", f"tail -n {lines} {log_file}", "--host", ssh_host],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        console.print(r.stdout)
    else:
        console.print(f"[red]✗ Could not read {log_file}[/red]")
        console.print(f"  {r.stderr.strip()}")
        raise typer.Exit(1)


@app.command("restart")
def cmd_restart(
    service:    str           = typer.Argument("agent",
                                               help="What to restart: agent | monitor | <proc-name>"),
    remote_dir: str           = typer.Option("~/navig-mini", "--dir", "-d",
                                               help="Remote install directory"),
    host:       Optional[str] = typer.Option(None, "--host", "-H"),
    url:        Optional[str] = typer.Option(None, "--url", "-u"),
):
    """[bold]Restart the agent daemon[/bold] (or a watched service) remotely."""
    cfg      = _cfg()
    ssh_host = host or cfg["ssh_host"]
    aurl     = url or cfg["url"]

    if service == "agent":
        cmd = (
            f"pkill -f {remote_dir}/agent.py 2>/dev/null; sleep 1; "
            f"nohup python3 {remote_dir}/agent.py >> {remote_dir}/agent.log 2>&1 </dev/null &"
        )
        subprocess.run(["navig", "run", cmd, "--host", ssh_host], capture_output=True, text=True)
        time.sleep(3)
        p = _ping(aurl)
        if p:
            console.print(f"  [green]✓[/green] Agent restarted — uptime {p.get('uptime', 0)}s")
        else:
            console.print("  [yellow]⚠[/yellow] Agent may still be starting — check: [cyan]navig mini logs[/cyan]")

    elif service == "monitor":
        cmd = f"pkill -f {remote_dir}/monitor.py 2>/dev/null; echo done"
        r   = subprocess.run(["navig", "run", cmd, "--host", ssh_host], capture_output=True, text=True)
        console.print("  [green]✓[/green] Monitor stopped (cron will restart it within 5 min)")

    else:
        # Delegate to agent /run endpoint
        asec = cfg["secret"]
        if not asec:
            console.print("[red]✗ No HMAC secret — cannot use agent /run endpoint[/red]")
            raise typer.Exit(1)
        r = _post(aurl, "/run", asec, {"cmd": f"pkill -c {service}"}, timeout=10)
        console.print(f"  [green]✓[/green] Signal sent  (killed {r.get('code', '?')} {service} processes)")


@app.command("list")
def cmd_list(
    check:    bool = typer.Option(False, "--check", "-c", help="Probe each agent via HTTP"),
    json_out: bool = typer.Option(False, "--json"),
):
    """[bold]List all configured NAVIG Mini agents.[/bold]"""
    raw    = _navig_get("mini.agents", "[]")
    try:
        agents: list = json.loads(raw)
    except Exception:
        agents = []

    default_url  = _navig_get("mini.url", "")
    default_host = _navig_get("mini.ssh_host", "")

    if default_url and not any(a.get("url") == default_url for a in agents):
        agents.insert(0, {"name": "default", "url": default_url, "ssh_host": default_host})

    if not agents:
        console.print("  [dim]No agents configured.[/dim]")
        console.print("  Register one: [cyan]navig config set mini.url http://DEVICE:9191[/cyan]")
        return

    if json_out:
        console.print_json(json.dumps(agents))
        return

    t = Table(title="NAVIG Mini Agents", box=None, padding=(0, 2))
    t.add_column("Name",     style="bold")
    t.add_column("URL",      style="cyan")
    t.add_column("SSH Host", style="dim")
    if check:
        t.add_column("Status")

    for a in agents:
        row = [a.get("name", "?"), a.get("url", "?"), a.get("ssh_host", "")]
        if check:
            p = _ping(a.get("url", ""), timeout=2)
            row.append(f"[green]✓ up ({p.get('uptime',0)}s)[/green]" if p else "[red]✗ unreachable[/red]")
        t.add_row(*row)

    console.print(t)


# ── Plugin dependency check (called by PluginManager) ─────────────────────────
def check_dependencies() -> Tuple[bool, List[str]]:
    """All imports are stdlib — no missing deps possible."""
    missing: List[str] = []
    for mod in ("typer", "rich"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    return (len(missing) == 0, missing)
