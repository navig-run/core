"""
navig flux — Mesh management CLI overlay
=========================================

Usage
-----
  navig flux peers                    List all known mesh peers
  navig flux scan                     Trigger LAN discovery
  navig flux target [node_id]         Set routing target
  navig flux clear                    Clear routing target
  navig flux add <url>                Manually add a peer by gateway URL
  navig flux install [--push]         Show / push one-liner install to a peer
  navig flux token                    Show the mesh_token
  navig flux status                   Overall mesh health summary
"""

from __future__ import annotations

import json
import platform
import socket
from typing import Optional

import typer

try:
    import httpx
    _HTTPX = True
except ImportError:
    _HTTPX = False

flux_app = typer.Typer(
    name="flux",
    help="Mesh topology management — peers, targets, LAN discovery, remote install.",
    invoke_without_command=True,
    no_args_is_help=True,
)

_GW = "http://127.0.0.1:8789"


# ─────────────────────────── helpers ─────────────────────────────────────────

def _daemon_offline_msg() -> str:
    return (
        "\n[OFFLINE]  NAVIG daemon is not running.\n"
        "\n"
        "  Start it:   navig service start\n"
        "  Status:     navig service status\n"
        "  Install:    https://github.com/navigHQ/navig\n"
        "\nOnce the daemon is running, retry: navig flux status\n"
    )


def _get(path: str) -> dict:
    if not _HTTPX:
        import urllib.request
        try:
            with urllib.request.urlopen(f"{_GW}{path}", timeout=5) as r:
                return json.loads(r.read())
        except OSError as e:
            typer.echo(_daemon_offline_msg(), err=True)
            raise SystemExit(1) from e
    try:
        r = httpx.get(f"{_GW}{path}", timeout=5)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        typer.echo(_daemon_offline_msg(), err=True)
        raise SystemExit(1)
    except Exception as e:
        typer.echo(f"[ERROR] Daemon error: {e}", err=True)
        raise SystemExit(1)


def _post(path: str, payload: dict) -> dict:
    if not _HTTPX:
        import urllib.request
        try:
            data = json.dumps(payload).encode()
            req = urllib.request.Request(f"{_GW}{path}", data=data, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.loads(r.read())
        except OSError as e:
            typer.echo(_daemon_offline_msg(), err=True)
            raise SystemExit(1) from e
    try:
        r = httpx.post(f"{_GW}{path}", json=payload, timeout=10)
        r.raise_for_status()
        return r.json()
    except httpx.ConnectError:
        typer.echo(_daemon_offline_msg(), err=True)
        raise SystemExit(1)
    except Exception as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise SystemExit(1)


def _lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _table(rows: list, headers: list) -> None:
    all_rows = [headers] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in all_rows if i < len(r)) for i in range(len(headers))]
    sep = "─" * (sum(widths) + 3 * len(widths) + 1)
    fmt = " | ".join(f"{{:<{w}}}" for w in widths)
    typer.echo(sep)
    typer.echo(" " + fmt.format(*headers))
    typer.echo(sep)
    for row in rows:
        padded = (row + [""] * len(headers))[: len(headers)]
        typer.echo(" " + fmt.format(*[str(c) for c in padded]))
    typer.echo(sep)


# ─────────────────────────── commands ────────────────────────────────────────

@flux_app.command("peers")
def peers(
    json_out: bool = typer.Option(False, "--json", help="Output as JSON"),
    plain:    bool = typer.Option(False, "--plain", help="Tab-separated (scripting)"),
) -> None:
    """List all known mesh peers."""
    data = _get("/mesh/peers")
    peer_list: list = data if isinstance(data, list) else data.get("peers", [])

    if not peer_list:
        typer.echo("No peers discovered yet. Try: navig flux scan")
        return

    if json_out:
        typer.echo(json.dumps(peer_list, indent=2))
        return

    if plain:
        for p in peer_list:
            typer.echo("\t".join([
                p.get("node_id", "")[:16],
                p.get("hostname", "-"),
                p.get("os", "-"),
                p.get("health", "-"),
                str(p.get("load_pct", "-")),
                p.get("gateway_url", "-"),
            ]))
        return

    rows = []
    for p in peer_list:
        target = " ←" if p.get("is_current_target") else ""
        rows.append([
            p.get("node_id", "")[:14] + target,
            p.get("hostname", "-"),
            p.get("os", "-"),
            p.get("health", "?"),
            f"{float(p.get('load_pct', 0)):.0f}%" if p.get("load_pct") is not None else "-",
            f"{float(p.get('rtt_ms', 0)):.0f}ms" if p.get("rtt_ms") is not None else "-",
            p.get("gateway_url", "-"),
        ])
    _table(rows, ["Node ID", "Hostname", "OS", "Health", "Load", "RTT", "Gateway"])
    typer.echo(f"\n  {len(peer_list)} peer(s)   use `navig flux target <id>` to route\n")


@flux_app.command("scan")
def scan(
    wait: float = typer.Option(2.0, "--wait", help="Seconds to wait for responses"),
) -> None:
    """Trigger LAN multicast discovery and wait for responses."""
    typer.echo("🔍 Scanning LAN for NAVIG nodes...")
    try:
        _post("/mesh/discovery/scan", {})
    except SystemExit:
        pass
    import time
    time.sleep(wait)
    peers()


@flux_app.command("target")
def target(
    node_id: Optional[str] = typer.Argument(None, help="Node ID or partial hostname"),
) -> None:
    """Set the active routing target. Interactive picker if no arg given."""
    data = _get("/mesh/peers")
    peer_list: list = data if isinstance(data, list) else data.get("peers", [])

    if not peer_list:
        typer.echo("No peers — run: navig flux scan")
        raise SystemExit(1)

    if node_id is None:
        typer.echo("Available peers:\n")
        for i, p in enumerate(peer_list):
            marker = "→" if p.get("is_current_target") else " "
            typer.echo(f"  [{i + 1}] {marker} {p['node_id'][:16]}  {p.get('hostname', '')}  {p.get('gateway_url', '')}")
        raw = typer.prompt("\nEnter number or node_id").strip()
        if raw.isdigit():
            idx = int(raw) - 1
            node_id = peer_list[idx]["node_id"] if 0 <= idx < len(peer_list) else raw
        else:
            node_id = raw

    match = next(
        (p for p in peer_list
         if p["node_id"].startswith(node_id) or p.get("hostname", "").lower() == node_id.lower()),
        None,
    )
    if match is None:
        typer.echo(f"⚠  No peer matching '{node_id}'", err=True)
        raise SystemExit(1)

    _post("/mesh/target", {"node_id": match["node_id"]})
    typer.echo(f"🎯 Target set to: {match['node_id'][:16]}  ({match.get('hostname', '')})")


@flux_app.command("clear")
def clear() -> None:
    """Clear the routing target — commands run locally."""
    try:
        if _HTTPX:
            httpx.delete(f"{_GW}/mesh/target", timeout=5)
        else:
            import urllib.request
            req = urllib.request.Request(f"{_GW}/mesh/target", method="DELETE")
            urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
    typer.echo("🔄 Routing target cleared — commands will run locally.")


@flux_app.command("add")
def add_node(
    url: str = typer.Argument(..., help="Gateway URL e.g. http://10.0.0.50:8789"),
) -> None:
    """Manually add a peer by its gateway URL."""
    result = _post("/mesh/ping", {"gateway_url": url})
    typer.echo(f"✅ Peer response:\n{json.dumps(result, indent=2)}")


@flux_app.command("install")
def install(
    gateway: str          = typer.Option("", "--gateway", help="Source gateway URL"),
    push:    bool         = typer.Option(False, "--push", help="Push install over SSH"),
    peer:    Optional[str] = typer.Argument(None, help="Target node_id / hostname (for --push)"),
) -> None:
    """Show one-liner install commands, or push the install to a peer (--push)."""
    src = gateway or f"http://{_lan_ip()}:8789"

    if push:
        if not peer:
            typer.echo("--push requires a target peer identifier", err=True)
            raise SystemExit(1)
        bash = f"curl -fsSL {src}/install/linux | bash"
        import subprocess
        result = subprocess.run(["navig", "run", f"--host={peer}", bash], capture_output=True, text=True)
        typer.echo(result.stdout)
        if result.returncode != 0:
            typer.echo(result.stderr, err=True)
        return

    typer.echo(f"\n  📦  NAVIG — Add a machine to this mesh\n")
    typer.echo(f"  Windows (PowerShell 5+):")
    typer.echo(f"    (iwr {src}/install/windows).Content | iex\n")
    typer.echo(f"  Linux / macOS (bash):")
    typer.echo(f"    curl -fsSL {src}/install/linux | bash\n")
    typer.echo(f"  Install page: {src}/install\n")
    try:
        cfg = _get("/install/config")
        typer.echo(f"  mesh_token: {cfg.get('mesh_token', '(none)')}\n")
    except SystemExit:
        pass


@flux_app.command("token")
def token(
    copy: bool = typer.Option(False, "--copy", help="Copy to clipboard"),
) -> None:
    """Show (and optionally copy) the mesh_token."""
    tok = ""
    try:
        cfg = _get("/install/config")
        tok = cfg.get("mesh_token", "")
    except SystemExit:
        try:
            from navig.config import load_config
            tok = load_config().get("gateway", {}).get("mesh_token", "")
        except Exception:
            pass

    if not tok:
        typer.echo("⚠  No mesh_token set. Run: navig service start", err=True)
        raise SystemExit(1)

    typer.echo(f"\n  mesh_token: {tok}\n")

    if copy:
        if platform.system() == "Windows":
            import subprocess
            subprocess.run(["clip"], input=tok.encode(), check=True)
            typer.echo("  ✓ Copied to clipboard.")
        elif platform.system() == "Darwin":
            import subprocess
            subprocess.run(["pbcopy"], input=tok.encode(), check=True)
            typer.echo("  ✓ Copied to clipboard.")
        else:
            typer.echo("  (--copy not supported on this OS)")


@flux_app.command("status")
def status(
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Overall mesh health summary."""
    data = _get("/mesh/peers")
    peer_list: list = data if isinstance(data, list) else data.get("peers", [])

    healthy     = sum(1 for p in peer_list if p.get("health") == "healthy")
    degraded    = sum(1 for p in peer_list if p.get("health") == "degraded")
    unreachable = len(peer_list) - healthy - degraded
    target_node = next((p for p in peer_list if p.get("is_current_target")), None)

    summary = {
        "total": len(peer_list), "healthy": healthy,
        "degraded": degraded, "unreachable": unreachable,
        "target": target_node.get("node_id") if target_node else None,
        "local_ip": _lan_ip(),
    }

    if json_out:
        typer.echo(json.dumps(summary, indent=2))
        return

    typer.echo(
        f"\n  Peers: {len(peer_list)} total  |  "
        f"✅ {healthy} healthy  ⚠️  {degraded} degraded  ❌ {unreachable} unreachable"
    )
    typer.echo(f"  Target: {target_node['node_id'][:16] if target_node else '(local)'}")
    typer.echo(f"  This machine: {_lan_ip()}\n")
