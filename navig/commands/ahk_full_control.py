"""
NAVIG AutoHotkey Full Windows Control Extension
================================================

This module extends the base AHK commands with advanced Windows control capabilities:
- Process management (list, start, kill)
- Multi-monitor support (detect, move windows between)
- Window state detection (minimized, maximized, visible, active)
- Transparency control
- System notifications
- Sound/volume control
- Advanced window searching

Add to navig/commands/ahk.py before the Workflows section (line ~1438)
"""

# ==================== Process Management ====================


@ahk_app.command("processes")
def ahk_processes(
    filter_name: Optional[str] = typer.Option(None, "--filter", help="Filter by process name"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all running processes."""
    import json

    from rich.console import Console
    from rich.table import Table

    adapter = _get_adapter()
    if not adapter:
        return

    processes = adapter.get_processes()

    if filter_name:
        processes = [p for p in processes if filter_name.lower() in p.get("name", "").lower()]

    if json_output:
        print(json.dumps(processes, indent=2))
        return

    console = Console()
    table = Table(title="Running Processes")
    table.add_column("PID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Memory (MB)", style="yellow")

    for proc in sorted(processes, key=lambda x: x.get("memory", 0), reverse=True)[:50]:
        mem_mb = proc.get("memory", 0) / (1024 * 1024)
        table.add_row(str(proc.get("pid", "")), proc.get("name", ""), f"{mem_mb:.1f}")

    console.print(table)


@ahk_app.command("kill")
def ahk_kill(
    identifier: str = typer.Argument(..., help="Process name or PID"),
    force: bool = typer.Option(False, "--force", help="Skip confirmation"),
):
    """Kill a process by name or PID."""
    adapter = _get_adapter()
    if not adapter:
        return

    if not force:
        ch.warning(f"About to kill process: {identifier}")
        import typer

        if not typer.confirm("Are you sure?"):
            ch.info("Cancelled")
            return

    result = adapter.kill_process(identifier)
    if result.success:
        ch.success(f"Killed process: {identifier}")
    else:
        ch.error(f"Failed to kill: {result.stderr}")


@ahk_app.command("start")
def ahk_start(
    executable: str = typer.Argument(..., help="Path to executable"),
    args: str = typer.Option("", "--args", help="Command line arguments"),
    wait: bool = typer.Option(False, "--wait", help="Wait for process to exit"),
):
    """Start a new process."""
    adapter = _get_adapter()
    if not adapter:
        return

    result = adapter.start_process(executable, args, wait)
    if result.success:
        ch.success(f"Started: {executable}")
    else:
        ch.error(f"Failed to start: {result.stderr}")


# ==================== Multi-Monitor Support ====================


@ahk_app.command("monitors")
def ahk_monitors(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List all connected monitors."""
    import json

    from rich.console import Console
    from rich.table import Table

    adapter = _get_adapter()
    if not adapter:
        return

    monitors = adapter.get_monitors()

    if json_output:
        print(json.dumps(monitors, indent=2))
        return

    console = Console()
    table = Table(title="Connected Monitors")
    table.add_column("#", style="cyan")
    table.add_column("Resolution", style="green")
    table.add_column("Position", style="yellow")
    table.add_column("Work Area", style="blue")
    table.add_column("Primary", style="magenta")

    for mon in monitors:
        resolution = f"{mon['width']}x{mon['height']}"
        position = f"({mon['left']}, {mon['top']})"
        work_area = f"{mon['work_width']}x{mon['work_height']}"
        primary = "✓" if mon["primary"] else ""

        table.add_row(str(mon["index"]), resolution, position, work_area, primary)

    console.print(table)


@ahk_app.command("move-to-monitor")
def ahk_move_to_monitor(
    window: str = typer.Argument(..., help="Window selector"),
    monitor: int = typer.Argument(..., help="Monitor index (1-based)"),
):
    """Move window to specific monitor."""
    adapter = _get_adapter()
    if not adapter:
        return

    result = adapter.move_window_to_monitor(window, monitor)
    if result.success:
        ch.success(f"Moved '{window}' to monitor {monitor}")
    else:
        ch.error(f"Failed: {result.stderr}")


# ==================== Window State & Effects ====================


@ahk_app.command("transparency")
def ahk_transparency(
    window: str = typer.Argument(..., help="Window selector"),
    opacity: int = typer.Argument(..., help="Opacity 0-255 (0=invisible, 255=opaque)"),
):
    """Set window transparency."""
    adapter = _get_adapter()
    if not adapter:
        return

    result = adapter.set_window_transparency(window, opacity)
    if result.success:
        ch.success(f"Set '{window}' opacity to {opacity}")
    else:
        ch.error(f"Failed: {result.stderr}")


@ahk_app.command("window-state")
def ahk_window_state(
    window: str = typer.Argument(..., help="Window selector"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Get detailed window state."""
    import json

    from rich.console import Console
    from rich.table import Table

    adapter = _get_adapter()
    if not adapter:
        return

    state = adapter.get_window_state(window)

    if json_output:
        print(json.dumps(state, indent=2))
        return

    console = Console()

    if not state.get("exists"):
        ch.error(f"Window not found: {window}")
        return

    table = Table(title=f"State: {window}")
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="green")

    for key, value in state.items():
        table.add_row(key, "✓ Yes" if value else "✗ No")

    console.print(table)


@ahk_app.command("active-window")
def ahk_active_window(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Get currently active window."""
    import json

    from rich.console import Console
    from rich.panel import Panel

    adapter = _get_adapter()
    if not adapter:
        return

    window = adapter.get_active_window()

    if not window:
        ch.error("No active window")
        return

    if json_output:
        print(json.dumps(window.to_dict(), indent=2))
        return

    console = Console()
    info = f"""Title: {window.title}
Class: {window.class_name}
PID: {window.pid}
Position: ({window.x}, {window.y})
Size: {window.width}x{window.height}
Handle: {window.id}"""

    console.print(Panel(info, title="Active Window", border_style="green"))


@ahk_app.command("find")
def ahk_find(
    title: str = typer.Option("", "--title", help="Title pattern"),
    class_name: str = typer.Option("", "--class", help="Class pattern"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Find windows matching criteria."""
    import json

    from rich.console import Console
    from rich.table import Table

    adapter = _get_adapter()
    if not adapter:
        return

    windows = adapter.find_windows(title, class_name)

    if json_output:
        print(json.dumps([w.to_dict() for w in windows], indent=2))
        return

    console = Console()

    if not windows:
        ch.warning("No windows found")
        return

    table = Table(title=f"Found {len(windows)} window(s)")
    table.add_column("Title", style="cyan")
    table.add_column("Class", style="green")
    table.add_column("PID", style="yellow")
    table.add_column("Size", style="blue")

    for win in windows:
        table.add_row(win.title[:50], win.class_name, str(win.pid), f"{win.width}x{win.height}")

    console.print(table)


# ==================== Notifications ====================


@ahk_app.command("notify")
def ahk_notify(
    message: str = typer.Argument(..., help="Notification message"),
    title: str = typer.Option("NAVIG", "--title", "-t", help="Notification title"),
    duration: int = typer.Option(3, "--duration", "-d", help="Duration in seconds"),
):
    """Show Windows notification."""
    adapter = _get_adapter()
    if not adapter:
        return

    result = adapter.show_notification(title, message, duration)
    if result.success:
        ch.success("Notification shown")
    else:
        ch.error(f"Failed: {result.stderr}")


# ==================== Sound Control ====================


@ahk_app.command("volume")
def ahk_volume(
    level: Optional[int] = typer.Argument(None, help="Volume level 0-100 (omit to show current)"),
):
    """Get or set system volume."""
    adapter = _get_adapter()
    if not adapter:
        return

    if level is None:
        # Get current volume
        vol = adapter.get_volume()
        ch.info(f"Current volume: {vol}%")
    else:
        # Set volume
        result = adapter.set_volume(level)
        if result.success:
            ch.success(f"Volume set to {level}%")
        else:
            ch.error(f"Failed: {result.stderr}")


@ahk_app.command("mute")
def ahk_mute(
    unmute: bool = typer.Option(False, "--unmute", help="Unmute instead"),
):
    """Mute or unmute system audio."""
    adapter = _get_adapter()
    if not adapter:
        return

    result = adapter.mute(not unmute)
    if result.success:
        status = "unmuted" if unmute else "muted"
        ch.success(f"System audio {status}")
    else:
        ch.error(f"Failed: {result.stderr}")


@ahk_app.command("is-muted")
def ahk_is_muted():
    """Check if system audio is muted."""
    adapter = _get_adapter()
    if not adapter:
        return

    muted = adapter.is_muted()
    if muted:
        ch.info("🔇 System is muted")
    else:
        ch.info("🔊 System is not muted")


# ==================== Usage Examples ====================
"""
Process Management:
-------------------
navig ahk processes                     # List all processes
navig ahk processes --filter chrome     # Filter by name
navig ahk kill notepad.exe              # Kill process
navig ahk start calc.exe                # Launch calculator

Multi-Monitor:
--------------
navig ahk monitors                      # List monitors
navig ahk move-to-monitor "Chrome" 2    # Move to second monitor

Window Effects:
---------------
navig ahk transparency "Notepad" 128    # 50% transparent
navig ahk window-state "Chrome"         # Check state
navig ahk active-window                 # Get current window
navig ahk find --title "Visual Studio"  # Find windows

Notifications:
--------------
navig ahk notify "Deploy complete!"
navig ahk notify "Error detected" --title "Alert" --duration 5

Sound:
------
navig ahk volume 50                     # Set to 50%
navig ahk volume                        # Show current
navig ahk mute                          # Mute
navig ahk mute --unmute                 # Unmute
navig ahk is-muted                      # Check status
"""
