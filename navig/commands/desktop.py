"""
navig desktop — Windows Desktop Automation CLI command group.

Provides UI Automation (UIA) and AutoHotkey (AHK) control via the
navig-desktop-agent Python sidecar.

Commands:
  navig desktop ping                   Health-check the desktop agent.
  navig desktop find                   Search UI element tree.
  navig desktop click <handle>         Click a UI element (requires --confirm).
  navig desktop set <handle> <value>   Set element value (requires --confirm).
  navig desktop tree                   Dump the UI element tree.
  navig desktop ahk <script|filepath>  Run an AHK script (requires --confirm).
  navig desktop crash-chrome           Crash all Chrome tabs+extensions, keep the browser (frees RAM).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import typer

from navig.platform.paths import resolve_user_path

desktop_app = typer.Typer(
    name="desktop",
    help="Windows Desktop Automation — UI control via UIA and AHK.",
    no_args_is_help=True,
)

# ─────────────────────────── OS guard ────────────────────────────────────────

if sys.platform != "win32":
    # Stub out every command with a graceful error on non-Windows.
    @desktop_app.callback(invoke_without_command=True)
    def _windows_only(ctx: typer.Context) -> None:
        if ctx.invoked_subcommand is None:
            typer.echo("error: navig desktop is Windows only", err=True)
            raise typer.Exit(1)

    # Refuse-on-invocation only: the registry keeps `navig desktop` out of the shipped
    # manifest, which is generated on Windows where this callback does not exist.
    _windows_only.__navig_platform_stub__ = True  # type: ignore[attr-defined]


# ─────────────────────────── Agent RPC client ─────────────────────────────────


class _AgentError(Exception):
    pass


class _DesktopClient:
    """Lightweight Python JSON-RPC-over-stdio client for agent.py.

    Mirrors the Go client.go pattern: spawns agent.py as a subprocess,
    sends newline-delimited JSON requests, reads newline-delimited JSON
    responses.
    """

    def __init__(self) -> None:
        # The agent backend is a source-tree host component (host/internal/desktop), not
        # Python package data — it is absent from every installed navig. Fail with that,
        # rather than Popen-ing a path that does not exist: python then starts, prints
        # "can't open file", and closes stdout, so the user saw the useless
        # "agent closed stdout unexpectedly" instead of being told what was wrong.
        agent_script = (
            Path(__file__).parent.parent.parent / "host" / "internal" / "desktop" / "agent.py"
        )
        if not agent_script.exists():
            raise _AgentError(
                f"desktop agent backend not found at {agent_script} — `navig desktop` needs "
                "the host component, which ships only with a source checkout."
            )

        python_exe = os.environ.get("NAVIG_PYTHON_PATH", sys.executable)

        self._proc = subprocess.Popen(
            [python_exe, str(agent_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            text=True,
            encoding="utf-8",
        )
        self._next_id = 0

    def _call(self, method: str, params: dict) -> object:
        self._next_id += 1
        req = {"id": self._next_id, "method": method, "params": params}
        line = json.dumps(req, separators=(",", ":")) + "\n"
        self._proc.stdin.write(line)  # type: ignore[union-attr]
        self._proc.stdin.flush()  # type: ignore[union-attr]

        resp_line = self._proc.stdout.readline()  # type: ignore[union-attr]
        if not resp_line:
            raise _AgentError("agent closed stdout unexpectedly")

        resp = json.loads(resp_line)
        if "error" in resp and resp["error"] is not None:
            raise _AgentError(resp["error"])
        return resp.get("result")

    def ping(self) -> dict:
        return self._call("ping", {})  # type: ignore[return-value]

    def find_element(
        self,
        name: str | None,
        class_name: str | None,
        control_type: str | None,
        depth: int,
    ) -> list:
        params: dict = {"depth": depth}
        if name is not None:
            params["name"] = name
        if class_name is not None:
            params["class_name"] = class_name
        if control_type is not None:
            params["control_type"] = control_type
        return self._call("find_element", params)  # type: ignore[return-value]

    def click(self, handle: int) -> dict:
        return self._call("click", {"handle": handle})  # type: ignore[return-value]

    def set_value(self, handle: int, value: str) -> dict:
        return self._call("set_value", {"handle": handle, "value": value})  # type: ignore[return-value]

    def get_window_tree(self, depth: int) -> dict:
        return self._call("get_window_tree", {"depth": depth})  # type: ignore[return-value]

    def ahk_run(self, script: str) -> dict:
        return self._call("ahk_run", {"script": script})  # type: ignore[return-value]

    def close(self) -> None:
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
            self._proc.terminate()
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def _get_client() -> _DesktopClient:
    """Spawn and return a desktop agent client.  Exits with error on failure."""
    try:
        return _DesktopClient()
    except Exception as exc:
        typer.echo(f"error: could not start desktop agent: {exc}", err=True)
        raise typer.Exit(1) from exc


def _emit(data: object, json_output: bool) -> None:
    """Print data as JSON or human-readable depending on flag."""
    if json_output:
        typer.echo(json.dumps(data, indent=2, default=str))
    else:
        if isinstance(data, dict):
            for k, v in data.items():
                typer.echo(f"  {k}: {v}")
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    parts = []
                    for k, v in item.items():
                        parts.append(f"{k}={v!r}")
                    typer.echo("  " + "  ".join(parts))
                else:
                    typer.echo(f"  {item}")
        else:
            typer.echo(str(data))


# ─────────────────────────── Commands ─────────────────────────────────────────


@desktop_app.command("ping")
def desktop_ping(
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Health-check the desktop agent."""
    if sys.platform != "win32":
        typer.echo("error: navig desktop is Windows only", err=True)
        raise typer.Exit(1)

    client = _get_client()
    try:
        result = client.ping()
        _emit(result, json_output)
    except _AgentError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


@desktop_app.command("find")
def desktop_find(
    name: str | None = typer.Option(None, "--name", "-n", help="Filter by element Name."),
    class_name: str | None = typer.Option(None, "--class", "-c", help="Filter by ClassName."),
    control_type: str | None = typer.Option(
        None, "--type", "-t", help="Filter by control type (e.g. Button, Edit)."
    ),
    depth: int = typer.Option(5, "--depth", "-d", help="Search depth."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Search the UI element tree for matching elements."""
    if sys.platform != "win32":
        typer.echo("error: navig desktop is Windows only", err=True)
        raise typer.Exit(1)

    client = _get_client()
    try:
        elements = client.find_element(name, class_name, control_type, depth)
        if json_output:
            typer.echo(json.dumps(elements, indent=2, default=str))
        else:
            if not elements:
                typer.echo("  No elements found.")
                return
            for elem in elements:
                handle = elem.get("handle", 0)
                elem_name = elem.get("name", "")
                ct = elem.get("control_type", "")
                rect = elem.get("rect", {})
                typer.echo(f"  [{handle}] {ct} — {elem_name!r}  rect={rect}")
    except _AgentError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


@desktop_app.command("click")
def desktop_click(
    handle: int = typer.Argument(..., help="Native window handle of the element to click."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Required flag for destructive operation."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Click a UI element by its native window handle."""
    if sys.platform != "win32":
        typer.echo("error: navig desktop is Windows only", err=True)
        raise typer.Exit(1)

    if not confirm:
        typer.echo("error: --confirm flag required for destructive operations", err=True)
        raise typer.Exit(1)

    client = _get_client()
    try:
        result = client.click(handle)
        _emit(result, json_output)
    except _AgentError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


@desktop_app.command("set")
def desktop_set(
    handle: int = typer.Argument(..., help="Native window handle of the element."),
    value: str = typer.Argument(..., help="Value to set on the element."),
    confirm: bool = typer.Option(
        False, "--confirm", help="Required flag for destructive operation."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Set the value of a UI element by its native window handle."""
    if sys.platform != "win32":
        typer.echo("error: navig desktop is Windows only", err=True)
        raise typer.Exit(1)

    if not confirm:
        typer.echo("error: --confirm flag required for destructive operations", err=True)
        raise typer.Exit(1)

    client = _get_client()
    try:
        result = client.set_value(handle, value)
        _emit(result, json_output)
    except _AgentError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


@desktop_app.command("crash-chrome")
def desktop_crash_chrome(
    confirm: bool = typer.Option(
        False, "--confirm", "-y", help="Skip the interactive confirmation."
    ),
    extensions: bool = typer.Option(
        True,
        "--extensions/--no-extensions",
        help="Also crash extension processes (default: yes).",
    ),
    only_extensions: bool = typer.Option(
        False, "--only-extensions", "-x", help="Crash ONLY extension processes; leave every tab alone."
    ),
    channel: list[str] = typer.Option(
        [], "--channel", "-c",
        help="Limit to a Chrome channel: stable, beta, dev, canary (repeatable). Default: all.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="List what would be crashed, per channel."),
    include_automation: bool = typer.Option(
        False, "--include-automation",
        help="Also crash automation browsers (test harnesses, navig cdp — anything launched with --user-data-dir).",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Crash all Chrome tabs + extensions to reclaim RAM — without closing the browser.

    Kills every Chrome *renderer* process (each open tab and each extension host is one
    renderer) while leaving the main browser process, GPU, and network service alive.
    The Chrome window stays open; crashed tabs show "Aw, Snap!" and reload on click
    (Ctrl+Shift+T restores them). Frees the memory the tabs/extensions were holding.

    Covers every channel at once — Chrome, Beta, Dev and Canary all run as chrome.exe
    (they differ only by install folder). --only-extensions spares the tabs; --channel
    narrows to some channels.

    \b
    Examples:
      navig desktop crash-chrome -y                    # tabs + extensions, all channels
      navig desktop crash-chrome --only-extensions -y  # extensions only, all channels
      navig desktop crash-chrome -x -c beta -c dev -y  # extensions in Beta + Dev only
      navig desktop crash-chrome -x --dry-run          # preview, per channel

    Cross-platform Chrome logic is reused from navig.commands.processes.
    """
    if sys.platform != "win32":
        typer.echo("error: navig desktop is Windows only", err=True)
        raise typer.Exit(1)

    from navig.commands.processes import (
        available_ram_bytes,
        chrome_channel,
        find_chrome_extensions,
        find_chrome_renderers,
        kill_processes,
    )

    wanted = {c.strip().lower() for c in channel if c.strip()}
    if only_extensions:
        procs = find_chrome_extensions(wanted or None, include_automation=include_automation)
        what = "extension process(es)"
    else:
        procs = [
            p for p in find_chrome_renderers(include_extensions=extensions, include_automation=include_automation)
            if not wanted or chrome_channel(p.get("cmdline") or []) in wanted
        ]
        what = f"renderer(s) (tabs{'+extensions' if extensions else ''})"
    if not procs:
        if json_output:
            typer.echo(json.dumps({"crashed": 0, "message": "nothing matched"}))
        else:
            typer.echo(f"  No Chrome {what} found{' in ' + ', '.join(sorted(wanted)) if wanted else ''}.")
        return

    per_channel: dict[str, int] = {}
    for p in procs:
        ch = p.get("channel") or chrome_channel(p.get("cmdline") or [])
        per_channel[ch] = per_channel.get(ch, 0) + 1
    breakdown = ", ".join(f"{ch} {n}" for ch, n in sorted(per_channel.items()))
    if dry_run:
        if json_output:
            typer.echo(json.dumps({"would_crash": len(procs), "per_channel": per_channel, "kind": what}))
        else:
            typer.echo(f"  Would crash {len(procs)} Chrome {what}: {breakdown}. Browsers stay open.")
        return

    if not confirm:
        typer.confirm(f"Crash {len(procs)} Chrome {what} ({breakdown})? The browsers stay open.", abort=True)

    ram_before = available_ram_bytes()
    report = kill_processes(procs, force=True, exclude_self=True)
    # The OS returns a killed process's pages a beat after it exits; read too
    # early and the delta under-reports.
    time.sleep(_RAM_SETTLE_SECS)
    ram_after = available_ram_bytes()
    # Count only what can have come BACK: extension hosts (background workers
    # restart on their own). Tabs never respawn, and in --only-extensions mode
    # they were never touched — counting all renderers reported the open tabs as
    # "came back" (2026-10-09: "160 renderer(s) came back" after a 53-extension crash).
    respawned = len(find_chrome_extensions(wanted or None, include_automation=include_automation))
    tabs_crashed = not only_extensions
    result = {
        "crashed": len(report.get("killed", [])),
        "failed": len(report.get("failed", [])),
        "skipped": len(report.get("skipped", [])),
        "extensions": extensions,
        "only_extensions": only_extensions,
        "per_channel": per_channel,
        # Measured, not estimated: available system RAM after minus before. Other
        # apps allocate meanwhile, so it is clamped at 0; None = could not read.
        "freed_bytes": _freed_bytes(ram_before, ram_after),
        "ram_available_before": ram_before,
        "ram_available_after": ram_after,
        "extensions_restarted": respawned,
        "note": "browser left running — crashed tabs reload when clicked" if tabs_crashed else "browser and tabs left open — only extensions crashed",
    }
    if json_output:
        typer.echo(json.dumps(result, indent=2, default=str))
    else:
        freed = result["freed_bytes"]
        freed_txt = f"Freed ~{format_bytes_short(freed)} — " if freed is not None else ""
        typer.echo(
            f"  {freed_txt}Crashed {result['crashed']} Chrome {what} [{breakdown}] "
            f"(failed {result['failed']})."
        )
        if respawned:
            typer.echo(f"  {respawned} extension(s) restarted on their own (background workers) — that is normal.")
        if tabs_crashed:
            typer.echo("  Browser still open — click a crashed tab to reload it.")
        else:
            typer.echo("  Browser and every tab left open — only extensions were crashed.")


_RAM_SETTLE_SECS = 0.75


def _freed_bytes(before: int | None, after: int | None) -> int | None:
    """Available-RAM delta across the kill; None when either read failed."""
    if before is None or after is None:
        return None
    return max(0, after - before)


def format_bytes_short(n: int) -> str:
    """1.4 GB · 640 MB — the one-number answer to "how much did that free?"."""
    gb = n / 1024**3
    if gb >= 1:
        return f"{gb:.1f} GB"
    return f"{round(n / 1024**2)} MB"


@desktop_app.command("tree")
def desktop_tree(
    depth: int = typer.Option(3, "--depth", "-d", help="Tree depth."),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Dump the UI element tree to the specified depth."""
    if sys.platform != "win32":
        typer.echo("error: navig desktop is Windows only", err=True)
        raise typer.Exit(1)

    client = _get_client()
    try:
        tree = client.get_window_tree(depth)
        if json_output:
            typer.echo(json.dumps(tree, indent=2, default=str))
        else:
            _print_tree(tree, indent=0)
    except _AgentError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()


def _print_tree(node: dict, indent: int) -> None:
    """Recursively pretty-print a window tree node."""
    prefix = "  " * indent
    handle = node.get("handle", 0)
    name = node.get("name", "")
    ct = node.get("control_type", "")
    typer.echo(f"{prefix}[{handle}] {ct} — {name!r}")
    for child in node.get("children", []):
        _print_tree(child, indent + 1)


@desktop_app.command("ahk")
def desktop_ahk(
    script_or_path: str = typer.Argument(
        ...,
        help=(
            "Inline AHK script string OR path to an existing .ahk file. "
            "If a file path is provided, its contents are read and executed."
        ),
    ),
    confirm: bool = typer.Option(
        False, "--confirm", help="Required flag for destructive operation."
    ),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Execute an AutoHotkey v2 script via AutoHotkey.exe."""
    if sys.platform != "win32":
        typer.echo("error: navig desktop is Windows only", err=True)
        raise typer.Exit(1)

    if not confirm:
        typer.echo("error: --confirm flag required for destructive operations", err=True)
        raise typer.Exit(1)

    # Resolve file vs inline script
    script = script_or_path
    candidate = resolve_user_path(script_or_path)
    if candidate.is_file():
        try:
            script = candidate.read_text(encoding="utf-8")
        except Exception as exc:
            typer.echo(f"error: could not read script file: {exc}", err=True)
            raise typer.Exit(1) from exc

    client = _get_client()
    try:
        result = client.ahk_run(script)
        if json_output:
            typer.echo(json.dumps(result, indent=2, default=str))
        else:
            if result.get("stdout"):
                typer.echo(result["stdout"].rstrip())
            if result.get("stderr"):
                typer.echo(result["stderr"].rstrip(), err=True)
            exit_code: int = result.get("exit_code", 0)
            if exit_code != 0:
                typer.echo(f"  exit_code: {exit_code}", err=True)
                raise typer.Exit(exit_code)
    except _AgentError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    finally:
        client.close()
