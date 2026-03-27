# NAVIG AutoHotkey CLI Commands
"""
CLI commands for AutoHotkey v2 automation.

Commands:
- navig ahk install      : Detect or install AHKv2
- navig ahk status       : Show AHK status
- navig ahk run          : Execute AHK script file
- navig ahk exec         : Execute inline AHK code
- navig ahk click        : Click at coordinates
- navig ahk type         : Type text
- navig ahk open         : Open application/URL
- navig ahk windows      : List windows
- navig ahk automate     : AI-powered automation
"""

import sys
from pathlib import Path

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

# Create Typer app
ahk_app = typer.Typer(
    name="ahk",
    help="AutoHotkey v2 automation (Windows)",
    no_args_is_help=True,
)

# Lazy imports for heavy modules
_ahk_adapter = None


def _get_adapter():
    """Lazy import AHKAdapter."""
    global _ahk_adapter
    if _ahk_adapter is None:
        if sys.platform != "win32":
            ch.error("AutoHotkey is only available on Windows")
            return None

        try:
            from navig.adapters.automation.ahk import AHKAdapter

            _ahk_adapter = AHKAdapter()
        except ImportError as e:
            ch.error(f"Failed to load AHK adapter: {e}")
            return None
    return _ahk_adapter


# ==================== Installation & Status ====================


@ahk_app.command("install")
def ahk_install(
    portable: bool = typer.Option(
        False, "--portable", "-p", help="Install portable version"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Force reinstall/redetect"),
):
    """
    Detect or install AutoHotkey v2.

    Examples:
        navig ahk install
        navig ahk install --portable
    """
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    if sys.platform != "win32":
        ch.error("AutoHotkey is only available on Windows")
        raise typer.Exit(1)

    adapter = _get_adapter()
    if adapter is None:
        raise typer.Exit(1)

    # Check current status
    status = adapter.get_status()

    if status.detected and not force:
        ch.success("AutoHotkey v2 is already installed!")
        console.print(f"  Version: [cyan]{status.version}[/cyan]")
        console.print(f"  Path: [dim]{status.executable_path}[/dim]")
        console.print(f"  Detection: {status.detection_method}")
        return

    # Show installation instructions
    if portable:
        ch.info("Installing portable AHKv2 to ~/.navig/tools/ahk/")
        ch.warning("Portable installation not yet implemented")
        ch.info("Please download manually from: https://www.autohotkey.com/v2/")
    else:
        console.print(
            Panel(
                adapter.get_install_instructions(),
                title="AutoHotkey v2 Installation",
                border_style="cyan",
            )
        )


@ahk_app.command("status")
def ahk_status(
    refresh: bool = typer.Option(False, "--refresh", "-r", help="Refresh detection"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """
    Show AutoHotkey status and capabilities.

    Examples:
        navig ahk status
        navig ahk status --json
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()

    if sys.platform != "win32":
        ch.error("AutoHotkey is only available on Windows")
        if json_output:
            import json

            console.print(json.dumps({"available": False, "reason": "Not Windows"}))
        raise typer.Exit(1)

    adapter = _get_adapter()
    if adapter is None:
        raise typer.Exit(1)

    # Refresh if requested
    if refresh:
        ch.info("Refreshing AHK detection...")
        adapter.refresh_detection()

    status = adapter.get_status()

    if json_output:
        import json

        console.print(json.dumps(status.to_dict(), indent=2))
        return

    # Rich display
    console.print()

    if status.detected:
        table = Table(title="🔧 AutoHotkey v2 Status", show_header=False)
        table.add_column("Property", style="cyan")
        table.add_column("Value", style="white")

        table.add_row("Status", "[green]✓ Available[/green]")
        table.add_row("Version", status.version or "Unknown")
        table.add_row(
            "Executable",
            str(status.executable_path) if status.executable_path else "Unknown",
        )
        table.add_row("Detection Method", status.detection_method or "Unknown")

        console.print(table)

        # Show capabilities
        console.print("\n[cyan]Available Commands:[/cyan]")
        console.print("  navig ahk click <x> <y>          - Click at coordinates")
        console.print('  navig ahk type "<text>"          - Type text')
        console.print("  navig ahk open <app>             - Open application/URL")
        console.print("  navig ahk close <window>         - Close window")
        console.print("  navig ahk move <window> <x> <y>  - Move window")
        console.print("  navig ahk windows                - List all windows")
        console.print("  navig ahk run <script.ahk>       - Run AHK script")
        console.print('  navig ahk exec "<code>"          - Execute inline code')
    else:
        console.print(
            Panel(
                "[red]AutoHotkey v2 is not installed[/red]\n\n"
                "Run [cyan]navig ahk install[/cyan] for installation instructions.",
                title="🔧 AutoHotkey v2 Status",
                border_style="red",
            )
        )

    console.print()


@ahk_app.command("doctor")
def ahk_doctor():
    """
    Diagnose AutoHotkey integration issues.

    Example:
        navig ahk doctor
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()

    if sys.platform != "win32":
        ch.error("AutoHotkey is only available on Windows")
        raise typer.Exit(1)

    console.print("\n[cyan]🔍 AutoHotkey Diagnostics[/cyan]\n")

    checks = []

    # Check 1: Platform
    checks.append(("Platform", "Windows", "✓ Pass", "green"))

    # Check 2: Adapter import
    adapter = None
    try:
        from navig.adapters.automation.ahk import AHKAdapter

        checks.append(("Adapter Import", "Success", "✓ Pass", "green"))
        adapter = AHKAdapter()
    except Exception as e:
        checks.append(("Adapter Import", str(e)[:40], "✗ Fail", "red"))

    if adapter:
        # Check 3: Detection
        status = adapter.get_status()
        if status.detected:
            checks.append(
                ("AHKv2 Detection", status.detection_method, "✓ Pass", "green")
            )
            checks.append(
                ("AHKv2 Version", status.version or "Unknown", "✓ Pass", "green")
            )
            checks.append(
                (
                    "Executable Path",
                    (status.executable_path or "Unknown")[:40],
                    "✓ Pass",
                    "green",
                )
            )
        else:
            checks.append(("AHKv2 Detection", "Not found", "✗ Fail", "red"))

        # Check 4: Test execution
        if status.detected:
            test_result = adapter.execute('FileAppend("test", "*")\nExitApp 0')
            if test_result.success:
                checks.append(("Test Execution", "Success", "✓ Pass", "green"))
            else:
                checks.append(
                    ("Test Execution", test_result.stderr[:30], "✗ Fail", "red")
                )

        # Check 5: Directories
        script_dir = adapter._script_dir
        if script_dir.exists():
            checks.append(("Script Directory", str(script_dir)[:40], "✓ Pass", "green"))
        else:
            checks.append(("Script Directory", "Not created", "⚠ Warning", "yellow"))

    # Display results
    table = Table(title="Diagnostic Results")
    table.add_column("Check", style="cyan")
    table.add_column("Result")
    table.add_column("Status")

    for check, result, status_text, color in checks:
        table.add_row(check, result, f"[{color}]{status_text}[/{color}]")

    console.print(table)
    console.print()


# ==================== Direct Execution ====================


@ahk_app.command("run")
def ahk_run(
    script_path: str = typer.Argument(..., help="Path to .ahk script file"),
    args: str | None = typer.Option(
        None, "--args", "-a", help="Comma-separated arguments"
    ),
    timeout: float | None = typer.Option(
        None, "--timeout", "-t", help="Execution timeout in seconds"
    ),
):
    """
    Execute an AHK script file.

    Examples:
        navig ahk run my_script.ahk
        navig ahk run automation.ahk --args "arg1,arg2" --timeout 30
    """
    from rich.console import Console

    console = Console()

    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    path = Path(script_path)
    if not path.exists():
        ch.error(f"Script file not found: {script_path}")
        raise typer.Exit(1)

    ch.info(f"Running {path.name}...")

    args_list = args.split(",") if args else None

    result = adapter.execute_file(path, args=args_list, timeout=timeout)

    if result.success:
        ch.success(f"Script completed in {result.duration_seconds:.2f}s")
        if result.stdout:
            console.print(result.stdout)
    else:
        ch.error(f"Script failed: {result.status}")
        if result.stderr:
            console.print(f"[red]{result.stderr}[/red]")
        raise typer.Exit(1)


@ahk_app.command("exec")
def ahk_exec(
    code: str = typer.Argument(..., help="AHK code to execute"),
    timeout: float | None = typer.Option(
        None, "--timeout", "-t", help="Execution timeout"
    ),
):
    """
    Execute inline AHK code.

    Examples:
        navig ahk exec "MsgBox('Hello')"
        navig ahk exec "Click 100, 100"
    """
    from rich.console import Console

    console = Console()

    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.execute(code, timeout=timeout)

    if result.success:
        ch.success("Executed successfully")
        if result.stdout:
            console.print(result.stdout)
    else:
        ch.error(f"Execution failed: {result.status}")
        if result.stderr:
            console.print(f"[red]{result.stderr}[/red]")
        raise typer.Exit(1)


# ==================== Automation Primitives ====================


@ahk_app.command("click")
def ahk_click(
    x: int = typer.Argument(..., help="X coordinate"),
    y: int = typer.Argument(..., help="Y coordinate"),
    button: str = typer.Option(
        "left", "--button", "-b", help="Mouse button (left/right/middle)"
    ),
    clicks: int = typer.Option(1, "--clicks", "-c", help="Number of clicks"),
):
    """
    Click at screen coordinates.

    Examples:
        navig ahk click 100 200
        navig ahk click 500 300 --button right
        navig ahk click 100 100 --clicks 2
    """
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.click(x, y, button=button, clicks=clicks)

    if result.success:
        ch.success(f"Clicked at ({x}, {y})")
    else:
        ch.error(f"Click failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("type")
def ahk_type(
    text: str = typer.Argument(..., help="Text to type"),
    delay: int = typer.Option(0, "--delay", "-d", help="Delay between keystrokes (ms)"),
):
    """
    Type text using keyboard.

    Examples:
        navig ahk type "Hello World"
        navig ahk type "slow text" --delay 50
    """
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.type_text(text, delay=delay)

    if result.success:
        ch.success(f"Typed {len(text)} characters")
    else:
        ch.error(f"Type failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("send")
def ahk_send(
    keys: str = typer.Argument(..., help="Key sequence (e.g., '^c' for Ctrl+C)"),
):
    """
    Send key sequence.

    Key Modifiers:
        ^ = Ctrl
        ! = Alt
        + = Shift
        # = Win

    Examples:
        navig ahk send "^c"         # Ctrl+C
        navig ahk send "^v"         # Ctrl+V
        navig ahk send "#e"         # Win+E (Explorer)
        navig ahk send "{Enter}"    # Enter key
    """
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.send_keys(keys)

    if result.success:
        ch.success(f"Sent keys: {keys}")
    else:
        ch.error(f"Send failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("resize")
def ahk_resize(
    selector: str = typer.Argument(..., help="Window selector"),
    width: int = typer.Argument(..., help="New width"),
    height: int = typer.Argument(..., help="New height"),
):
    """Resize window."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.resize_window(selector, width, height)
    if result.success:
        ch.success(f"Resized {selector} to {width}x{height}")
    else:
        ch.error(f"Resize failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("mouse-move")
def ahk_mouse_move(
    x: int = typer.Argument(..., help="X coordinate"),
    y: int = typer.Argument(..., help="Y coordinate"),
    speed: int = typer.Option(2, "--speed", "-s", help="Speed (0-100)"),
):
    """Move mouse cursor."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.mouse_move(x, y, speed)
    if result.success:
        ch.success(f"Moved mouse to ({x}, {y})")
    else:
        ch.error(f"Move failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("read-text")
def ahk_read_text(
    selector: str = typer.Argument(..., help="Window selector"),
    control: str = typer.Option("", "--control", "-c", help="Control ID (ClassNN)"),
):
    """Read text from window or control."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    text = adapter.read_text(selector, control)
    if text:
        ch.console.print(text)
    else:
        ch.warning("No text found")


@ahk_app.command("open")
def ahk_open(
    target: str = typer.Argument(..., help="Application path, name, or URL"),
):
    """
    Open application or URL.

    Examples:
        navig ahk open notepad
        navig ahk open "C:/Program Files/App/app.exe"
        navig ahk open https://example.com
    """
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.open_app(target)

    if result.success:
        ch.success(f"Opened: {target}")
    else:
        ch.error(f"Open failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("close")
def ahk_close(
    selector: str = typer.Argument(
        ..., help="Window title, ahk_exe, ahk_class, or ahk_id"
    ),
):
    """
    Close window by selector.

    Examples:
        navig ahk close "Notepad"
        navig ahk close "ahk_exe notepad.exe"
        navig ahk close "ahk_class Notepad"
    """
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.close_window(selector)

    if result.success:
        ch.success(f"Closed window: {selector}")
    else:
        ch.error(f"Close failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("move")
def ahk_move(
    selector: str = typer.Argument(..., help="Window selector"),
    x: int = typer.Argument(..., help="New X position"),
    y: int = typer.Argument(..., help="New Y position"),
    width: int | None = typer.Option(None, "--width", "-w", help="New width"),
    height: int | None = typer.Option(None, "--height", "-h", help="New height"),
):
    """
    Move and optionally resize window.

    Examples:
        navig ahk move "Notepad" 100 100
        navig ahk move "Notepad" 0 0 --width 800 --height 600
    """
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.move_window(selector, x, y, width, height)

    if result.success:
        size_info = f" ({width}x{height})" if width and height else ""
        ch.success(f"Moved window to ({x}, {y}){size_info}")
    else:
        ch.error(f"Move failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("maximize")
def ahk_maximize(
    selector: str = typer.Argument(..., help="Window selector"),
):
    """Maximize window."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.maximize_window(selector)

    if result.success:
        ch.success(f"Maximized: {selector}")
    else:
        ch.error(f"Maximize failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("minimize")
def ahk_minimize(
    selector: str = typer.Argument(..., help="Window selector"),
):
    """Minimize window."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.minimize_window(selector)

    if result.success:
        ch.success(f"Minimized: {selector}")
    else:
        ch.error(f"Minimize failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("snap")
def ahk_snap(
    selector: str = typer.Argument(..., help="Window selector"),
    position: str = typer.Argument(
        ...,
        help="Position: left, right, top, bottom, top-left, top-right, bottom-left, bottom-right, center",
    ),
):
    """Snap window to screen edge/corner."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.snap_window(selector, position)

    if result.success:
        ch.success(f"Snapped {selector} to {position}")
    else:
        ch.error(f"Snap failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("pin")
def ahk_pin(
    selector: str = typer.Argument("", help="Window selector (default: active window)"),
):
    """Toggle Always-On-Top (Pin) status."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.toggle_always_on_top(selector)
    if result.success:
        ch.success(f"Toggled pin: {selector if selector else 'Active Window'}")
    else:
        ch.error(f"Pin failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("activate")
def ahk_activate(
    selector: str = typer.Argument(..., help="Window selector"),
):
    """Activate (focus) window."""
    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    result = adapter.activate_window(selector)

    if result.success:
        ch.success(f"Activated: {selector}")
    else:
        ch.error(f"Activate failed: {result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("windows")
def ahk_windows(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
    filter_text: str | None = typer.Option(
        None, "--filter", "-f", help="Filter by title"
    ),
):
    """
    List all visible windows.

    Examples:
        navig ahk windows
        navig ahk windows --filter Chrome
        navig ahk windows --json
    """
    from rich.console import Console
    from rich.table import Table

    console = Console()

    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    windows = adapter.get_all_windows()

    if not windows:
        ch.warning("No windows found")
        return

    # Apply filter if specified
    if filter_text:
        filter_lower = filter_text.lower()
        windows = [w for w in windows if filter_lower in w.title.lower()]

    if json_output:
        import json

        console.print(json.dumps([w.to_dict() for w in windows], indent=2))
        return

    # Rich table display
    table = Table(title=f"🪟 Windows ({len(windows)})")
    table.add_column("Title", style="cyan", max_width=40)
    table.add_column("Class", style="dim")
    table.add_column("PID", style="green")
    table.add_column("Position")
    table.add_column("Size")
    table.add_column("State")

    for w in windows:
        state = "📍 Normal"
        if hasattr(w, "is_minimized") and w.is_minimized:
            state = "⬇️ Min"
        elif hasattr(w, "is_maximized") and w.is_maximized:
            state = "⬆️ Max"

        table.add_row(
            w.title[:40] if len(w.title) > 40 else w.title,
            (
                (w.class_name[:15] if len(w.class_name) > 15 else w.class_name)
                if hasattr(w, "class_name")
                else ""
            ),
            str(w.pid) if hasattr(w, "pid") else "",
            f"{w.x}, {w.y}" if hasattr(w, "x") else "",
            f"{w.width}x{w.height}" if hasattr(w, "width") else "",
            state,
        )

    console.print()
    console.print(table)
    console.print()


# ==================== Clipboard ====================


@ahk_app.command("clipboard")
def ahk_clipboard(
    get: bool = typer.Option(False, "--get", "-g", help="Get clipboard content"),
    set_value: str | None = typer.Option(
        None, "--set", "-s", help="Set clipboard content"
    ),
):
    """
    Clipboard operations.

    Examples:
        navig ahk clipboard --get
        navig ahk clipboard --set "Hello World"
    """
    from rich.console import Console

    console = Console()

    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    if set_value:
        result = adapter.set_clipboard(set_value)
        if result.success:
            ch.success("Clipboard updated")
        else:
            ch.error(f"Failed: {result.stderr}")
            raise typer.Exit(1)
    else:
        # Default: show clipboard content
        content = adapter.get_clipboard()
        if content is not None:
            if get:
                console.print(content)
            else:
                ch.info("Clipboard content:")
                display = content[:500] if len(content) > 500 else content
                console.print(display)
                if len(content) > 500:
                    console.print(f"\n[dim]... ({len(content)} chars total)[/dim]")
        else:
            ch.warning("Clipboard is empty")


# ==================== AI-Powered Automation ====================


@ahk_app.command("automate")
def ahk_automate(
    goal: str = typer.Argument(..., help="Natural language goal description"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show script without executing"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip safety confirmation"),
):
    """
    AI-powered automation - generate and execute script for goal.

    Examples:
        navig ahk automate "tile all my windows"
        navig ahk automate "open notepad and type hello" --dry-run
        navig ahk automate "close all Chrome windows" --force
    """
    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    adapter = _get_adapter()
    if adapter is None or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    ch.info(f"Generating automation for: {goal}")

    # Gather context
    windows = adapter.get_all_windows()
    screen_size = adapter.get_screen_size()

    context = {
        "windows": [
            {
                "title": w.title,
                "class": getattr(w, "class_name", ""),
                "pid": getattr(w, "pid", 0),
            }
            for w in windows[:10]
        ],
        "screen_size": screen_size,
    }

    # Try to use AI to generate script
    try:
        from navig.adapters.automation.ahk_ai import AHKAIGenerator, GenerationContext

        generator = AHKAIGenerator()

        gen_context = GenerationContext(
            windows=[{"title": w.title} for w in windows[:10]],
            screen_width=screen_size[0] if screen_size else 1920,
            screen_height=screen_size[1] if screen_size else 1080,
        )

        ch.dim("Querying AI for script generation...")
        result = generator.generate(goal, gen_context)

        if not result.success:
            ch.error(f"Script generation failed: {result.error}")
            raise typer.Exit(1)

        script = result.script

        # Show the script
        console.print(Panel(script, title="Generated Script", border_style="cyan"))

        if dry_run:
            ch.info("Dry run - script not executed")
            return

        # Execute
        ch.info("Executing script...")
        exec_result = adapter.execute(script, force=force)

        if exec_result.success:
            ch.success(f"Automation completed in {exec_result.duration_seconds:.2f}s")
            if exec_result.stdout:
                console.print(exec_result.stdout)
        else:
            ch.error(f"Automation failed: {exec_result.status}")
            if exec_result.stderr:
                console.print(f"[red]{exec_result.stderr}[/red]")
            raise typer.Exit(1)

    except ImportError as _exc:
        ch.error("AI module not available")
        ch.info("The AI generation feature requires the AI assistant to be configured.")
        raise typer.Exit(1) from _exc
    except Exception as e:
        ch.error(f"Automation failed: {e}")
        raise typer.Exit(1) from e


# ==================== Evolution ====================


@ahk_app.command("evolve")
def ahk_evolve(
    goal: str = typer.Argument(..., help="Goal to evolve a script for"),
    dry_run: bool = typer.Option(
        False, "--dry-run", "-n", help="Show evolution without executing"
    ),
    retries: int = typer.Option(3, "--retries", "-r", help="Max evolution attempts"),
):
    """
    Auto-Evolve: Generate, test, and refine an AHK script.

    This runs a feedback loop:
    1. Generate script
    2. Execute
    3. If fail, analyze error and regenerate
    4. Save successful scripts to library

    Examples:
        navig ahk evolve "open notepad and type hello"
        navig ahk evolve "maximize all windows" --retries 5
    """
    from navig.adapters.automation.evolution.evolver import Evolver

    evolver = Evolver()
    evolver.max_retries = retries

    result = evolver.evolve(goal, dry_run=dry_run)

    if result.success:
        if result.script_id:
            ch.success(f"Script evolved and saved! ID: {result.script_id}")
        else:
            ch.success("Script evolved successfully (dry run)")
    else:
        ch.error("Failed to evolve a working script.")
        raise typer.Exit(1)


# ==================== Library ====================


library_app = typer.Typer(
    name="library",
    help="Manage evolved script library",
    no_args_is_help=True,
)
ahk_app.add_typer(library_app, name="library")


# ==================== Layouts ====================


layout_app = typer.Typer(
    name="layout",
    help="Manage window layouts",
    no_args_is_help=True,
)
ahk_app.add_typer(layout_app, name="layout")


@layout_app.command("save")
def layout_save(
    name: str = typer.Argument(..., help="Name of layout to save"),
):
    """Save current window positions and sizes."""
    from navig.core.window_manager import WindowManager

    adapter = _get_adapter()
    if not adapter:
        return

    wm = WindowManager(adapter)
    wm.save_layout(name)


@layout_app.command("restore")
def layout_restore(
    name: str = typer.Argument(..., help="Name of layout to restore"),
):
    """Restore a saved window layout."""
    from navig.core.window_manager import WindowManager

    adapter = _get_adapter()
    if not adapter:
        return

    wm = WindowManager(adapter)
    wm.restore_layout(name)


@layout_app.command("list")
def layout_list():
    """List saved layouts."""
    from rich.console import Console

    from navig.core.window_manager import WindowManager

    console = Console()
    adapter = _get_adapter()
    if not adapter:
        return

    wm = WindowManager(adapter)
    layouts = wm.list_layouts()
    if not layouts:
        ch.info("No layouts saved.")
        return

    console.print(f"[bold]Saved Layouts ({len(layouts)}):[/bold]")
    for l in layouts:
        console.print(f"  - {l}")


@library_app.command("list")
def library_list(
    limit: int = typer.Option(20, "--limit", "-l", help="Max scripts to list"),
):
    """List saved automation scripts."""
    from rich.console import Console
    from rich.table import Table

    from navig.adapters.automation.evolution.library import ScriptLibrary

    console = Console()
    library = ScriptLibrary()
    scripts = library.list_scripts()

    if not scripts:
        ch.info("Library is empty. Use 'navig ahk evolve' to add scripts.")
        return

    # Sort by last used (descending) -- fix sorting key
    scripts.sort(key=lambda x: x.last_used, reverse=True)
    scripts = scripts[:limit]

    table = Table(title=f"📚 Automation Library ({len(scripts)})")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Goal", style="white")
    table.add_column("Successes", justify="right", style="green")
    table.add_column("Last Used", style="dim")

    for s in scripts:
        goal_display = s.goal[:50] + "..." if len(s.goal) > 50 else s.goal
        last_used_display = (
            s.last_used[:16].replace("T", " ") if s.last_used else "Never"
        )

        table.add_row(s.id, goal_display, str(s.success_count), last_used_display)

    console.print(table)
    console.print()


@library_app.command("show")
def library_show(
    script_id: str = typer.Argument(..., help="Script ID or Goal substring"),
):
    """Show content of a saved script."""
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax

    from navig.adapters.automation.evolution.library import ScriptLibrary

    console = Console()
    library = ScriptLibrary()

    # Try exact ID match
    entry = library._index.get(script_id)

    if not entry:
        # Try finding by goal substring
        matches = [
            s for s in library.list_scripts() if script_id.lower() in s.goal.lower()
        ]
        if matches:
            entry = matches[0]
            if len(matches) > 1:
                ch.warning(f"Multiple matches found, showing first: {entry.goal}")

    if not entry:
        ch.error(f"Script not found: {script_id}")
        raise typer.Exit(1)

    console.print(f"\n[bold cyan]Goal:[/bold cyan] {entry.goal}")
    console.print(
        f"[dim]ID: {entry.id} | Successes: {entry.success_count} | Last Used: {entry.last_used}[/dim]\n"
    )

    syntax = Syntax(entry.script, "autohotkey", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title=f"{entry.id}.ahk", border_style="cyan"))
    console.print()


@ahk_app.command("dashboard")
def ahk_dashboard(
    refresh: float = typer.Option(
        1.0, "--refresh", "-r", help="Refresh rate in seconds"
    ),
):
    """
    Live window manager dashboard.
    Shows active windows, their positions, and status in real-time.
    Press Ctrl+C to exit.
    """
    import time
    from datetime import datetime

    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table

    from navig.core.window_manager import WindowManager

    console = Console()
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("AutoHotkey not available")
        raise typer.Exit(1)

    wm = WindowManager(adapter)

    def generate_view():
        table = Table(
            title=f"Windows Dashboard ({datetime.now().strftime('%H:%M:%S')})",
            expand=True,
            box=None,
        )
        table.add_column("PID", style="dim", width=8)
        table.add_column("Title", style="white bold")
        table.add_column("Process", style="cyan")
        table.add_column("Geometry", style="green")
        table.add_column("State", style="yellow")

        try:
            windows = wm.get_windows()
            # Sort: active apps first effectively (but we don't know active)
            # Sort by title for stability
            windows.sort(key=lambda w: w.title.lower())

            for w in windows:
                if not w.title or w.title == "Program Manager":
                    continue

                state = []
                if w.is_maximized:
                    state.append("MAX")
                elif w.is_minimized:
                    state.append("MIN")
                else:
                    state.append("NRM")

                geom = f"{w.x},{w.y} {w.width}x{w.height}"

                table.add_row(
                    str(w.pid), w.title[:50], w.process_name[:20], geom, ",".join(state)
                )
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        return Panel(table, title="NAVIG Window Manager", border_style="blue")

    console.print("[dim]Starting dashboard... Press Ctrl+C to stop.[/dim]")

    with Live(generate_view(), refresh_per_second=4, screen=True) as live:
        try:
            while True:
                live.update(generate_view())
                time.sleep(refresh)
        except KeyboardInterrupt:
            pass  # user interrupted; clean exit


@ahk_app.command("clipboard")
def ahk_clipboard(  # noqa: F811
    text: str | None = typer.Argument(
        None, help="Text to copy (if omitted, prints clipboard)"
    ),
):
    """Get or set clipboard content."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    if text is None:
        content = adapter.get_clipboard()
        print(content)
    else:
        res = adapter.set_clipboard(text)
        if res.success:
            ch.success("Copied to clipboard")
        else:
            ch.error(f"Failed to copy: {res.stderr}")
            raise typer.Exit(1)


@ahk_app.command("screenshot")
def ahk_screenshot(
    output: Path | None = typer.Option(None, "--output", "-o", help="Output file path"),
    region: str | None = typer.Option(None, "--region", help="Region x,y,w,h"),
):
    """Take a screenshot."""
    from navig.desktop.controller import DesktopController

    dc = DesktopController(config=None)

    reg_tuple = None
    if region:
        try:
            reg_tuple = tuple(map(int, region.split(",")))
        except ValueError as _exc:
            ch.error("Invalid region format. Use x,y,w,h")
            raise typer.Exit(1) from _exc

    try:
        path = dc.screenshot(region=reg_tuple, name=str(output) if output else None)
        ch.success(f"Screenshot saved to: {path}")
        print(path)
    except Exception as e:
        ch.error(f"Screenshot failed: {e}")
        raise typer.Exit(1) from e


@ahk_app.command("ocr")
def ahk_ocr(
    image: Path | None = typer.Option(
        None, "--image", help="Image file (default: screenshot)"
    ),
    region: str | None = typer.Option(None, "--region", help="Region x,y,w,h"),
):
    """Extract text from screen or image (requires pytesseract)."""
    try:
        import pytesseract
        from PIL import Image
    except ImportError as _exc:
        ch.error("OCR requires pytesseract and Pillow. pip install pytesseract Pillow")
        raise typer.Exit(1) from _exc

    img = None
    if image:
        try:
            img = Image.open(image)
        except Exception as e:
            ch.error(f"Failed to open image: {e}")
            raise typer.Exit(1) from e

        if region:
            try:
                x, y, w, h = map(int, region.split(","))
                img = img.crop((x, y, x + w, y + h))
            except ValueError as _exc:
                ch.error("Invalid region format. Use x,y,w,h")
                raise typer.Exit(1) from _exc
    else:
        # Screenshot
        from navig.desktop.controller import DesktopController

        dc = DesktopController()
        reg_tuple = None
        if region:
            try:
                reg_tuple = tuple(map(int, region.split(",")))
            except ValueError as _exc:
                ch.error("Invalid region format. Use x,y,w,h")
                raise typer.Exit(1) from _exc

        try:
            path = dc.screenshot(region=reg_tuple)
            img = Image.open(path)
        except Exception as e:
            ch.error(f"Screenshot failed: {e}")
            raise typer.Exit(1) from e

    try:
        text = pytesseract.image_to_string(img)
        print(text)
    except Exception as e:
        ch.error(f"OCR failed: {e}")
        raise typer.Exit(1) from e


@ahk_app.command("listen")
def ahk_listen(
    hotkey: str = typer.Argument(..., help="AHK Hotkey (e.g. ^!t)"),
    command: str = typer.Argument(..., help="Command to run"),
    start: bool = typer.Option(
        False, "--start", "-s", help="Start/Restart listener immediately"
    ),
):
    """
    Register a global hotkey to run a command.
    Appends to ~/.navig/scripts/listener.ahk.
    """
    script_dir = Path.home() / ".navig" / "scripts"
    script_dir.mkdir(parents=True, exist_ok=True)
    listener_path = script_dir / "listener.ahk"

    # Initialize header if new file or empty
    if not listener_path.exists() or listener_path.stat().st_size == 0:
        with open(listener_path, "w", encoding="utf-8") as f:
            f.write(
                "#Requires AutoHotkey v2.0\n#SingleInstance Force\nPersistent\n\n; NAVIG AutoHotkey Listener\n\n"
            )

    # Escape quotes for AHK v2 string
    safe_cmd = command.replace('"', '`"')
    entry = f'{hotkey}::Run "{safe_cmd}"\n'

    # Check for duplicate hotkey (simple check)
    if listener_path.exists():
        content = listener_path.read_text(encoding="utf-8")
        if f"{hotkey}::" in content:
            ch.warning(f"Hotkey {hotkey} already exists. Appending anyway.")

    with open(listener_path, "a", encoding="utf-8") as f:
        f.write(entry)

    ch.success(f"Registered hotkey: {hotkey} -> {command}")

    if start:
        ahk_listener_start()


@ahk_app.command("listener-start")
def ahk_listener_start():
    """Start or restart the persistent listener script."""
    script_dir = Path.home() / ".navig" / "scripts"
    listener_path = script_dir / "listener.ahk"

    if not listener_path.exists():
        ch.error(f"Listener script not found at {listener_path}")
        raise typer.Exit(1)

    adapter = _get_adapter()
    if not adapter:
        return

    # #SingleInstance Force in script handles replacement
    pid = adapter.run_detached(listener_path)
    if pid:
        ch.success(f"Listener started (PID: {pid})")
    else:
        ch.error("Failed to start listener")


@ahk_app.command("listener-edit")
def ahk_listener_edit():
    """Open listener script in default editor."""
    script_dir = Path.home() / ".navig" / "scripts"
    listener_path = script_dir / "listener.ahk"
    if not listener_path.exists():
        script_dir.mkdir(parents=True, exist_ok=True)
        listener_path.touch()

    import os

    if sys.platform == "win32":
        os.startfile(listener_path)
    else:
        import subprocess

        subprocess.call(("open", str(listener_path)))


# ==================== AI-Powered Automation ====================


@ahk_app.command("generate")
def ahk_generate(
    goal: str = typer.Argument(..., help="Goal in natural language"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show generated script without executing"
    ),
    save: bool = typer.Option(
        False, "--save", "-s", help="Save to library if successful"
    ),
):
    """
    Generate AHK script from natural language using AI.

    Examples:
        navig ahk generate "open notepad and type hello"
        navig ahk generate "minimize all windows" --dry-run
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.syntax import Syntax

    from navig.adapters.automation.ahk_ai import AHKAIGenerator, GenerationContext

    console = Console()
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    generator = AHKAIGenerator()
    if not generator.has_ai:
        ch.error("AI module not available. Check your API configuration.")
        raise typer.Exit(1)

    ch.info(f"🤖 Generating AHK script for: {goal}")

    # Get context
    windows = adapter.get_all_windows()
    screen_size = adapter.get_screen_size()

    context = GenerationContext(
        windows=[{"title": w.title, "pid": w.pid} for w in windows[:15]],
        screen_width=screen_size[0],
        screen_height=screen_size[1],
    )

    # Generate
    result = generator.generate(goal, context)

    if not result.success:
        ch.error(f"Generation failed: {result.error}")
        raise typer.Exit(1)

    # Display
    syntax = Syntax(result.script, "autohotkey", theme="monokai", line_numbers=True)
    console.print(Panel(syntax, title="🎯 Generated Script", border_style="green"))

    if dry_run:
        ch.info("Dry run - script not executed")
        return

    # Execute
    ch.info("Executing generated script...")
    exec_result = adapter.execute(result.script, timeout=10)

    if exec_result.success:
        ch.success("✓ Script executed successfully!")

        if save:
            from navig.adapters.automation.evolution.library import ScriptLibrary

            library = ScriptLibrary()
            script_id = library.save_script(goal, result.script)
            ch.success(f"💾 Saved to library as: {script_id}")
    else:
        ch.error(f"✗ Execution failed: {exec_result.stderr}")
        raise typer.Exit(1)


@ahk_app.command("evolve")
def ahk_evolve(  # noqa: F811
    goal: str = typer.Argument(..., help="Goal in natural language"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Show generated script without executing"
    ),
    max_retries: int = typer.Option(
        3, "--retries", "-r", help="Max evolution attempts"
    ),
):
    """
    Auto-generate, test, and evolve AHK scripts until they work.

    This command uses AI to iteratively improve scripts based on execution feedback.
    Scripts that succeed are automatically saved to the library.

    Examples:
        navig ahk evolve "open chrome and go to google.com"
        navig ahk evolve "arrange windows side by side" --retries 5
    """
    from rich.console import Console
    from rich.panel import Panel

    from navig.adapters.automation.evolution.evolver import Evolver

    console = Console()

    if not _get_adapter() or not _get_adapter().is_available():
        ch.error("AutoHotkey is not available")
        raise typer.Exit(1)

    ch.info(f"🧬 Evolving AHK script for: {goal}")
    console.print()

    evolver = Evolver()
    evolver.max_retries = max_retries

    result = evolver.evolve(goal, dry_run=dry_run)

    console.print()

    if result.success:
        ch.success(f"✓ Evolution successful after {result.attempts} attempt(s)!")

        if result.script_id:
            ch.success(f"💾 Saved to library as: {result.script_id}")

        if dry_run:
            from rich.syntax import Syntax

            syntax = Syntax(
                result.final_script, "autohotkey", theme="monokai", line_numbers=True
            )
            console.print(Panel(syntax, title="🎯 Final Script", border_style="green"))
    else:
        ch.error(f"✗ Evolution failed after {result.attempts} attempts")

        if result.final_script:
            ch.warning("Last attempted script:")
            from rich.syntax import Syntax

            syntax = Syntax(
                result.final_script, "autohotkey", theme="monokai", line_numbers=True
            )
            console.print(Panel(syntax, title="⚠️ Failed Script", border_style="red"))

        raise typer.Exit(1)


# ==================== Process Management ====================


@ahk_app.command("processes")
def ahk_processes(
    filter_name: str | None = typer.Option(
        None, "--filter", help="Filter by process name"
    ),
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
        processes = [
            p for p in processes if filter_name.lower() in p.get("name", "").lower()
        ]

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
        table.add_row(
            win.title[:50], win.class_name, str(win.pid), f"{win.width}x{win.height}"
        )

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
    level: int | None = typer.Argument(
        None, help="Volume level 0-100 (omit to show current)"
    ),
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


# ==================== Workflows ====================


workflow_app = typer.Typer(
    name="workflow",
    help="Manage automation workflows",
    no_args_is_help=True,
)
ahk_app.add_typer(workflow_app, name="workflow")


@workflow_app.command("run")
def workflow_run(
    name: str = typer.Argument(..., help="Name of workflow to run"),
    vars: list[str] = typer.Option(
        None, "--var", "-v", help="Variables in key=value format"
    ),
):
    """Run a cross-platform workflow."""
    from navig.core.automation_engine import WorkflowEngine

    engine = WorkflowEngine()
    workflow = engine.load_workflow(name)

    if not workflow:
        ch.error(f"Workflow not found: {name}")
        ch.info("Use 'navig ahk workflow list' to see available workflows.")
        raise typer.Exit(1)

    # Parse variables
    variables = {}
    if vars:
        for v in vars:
            try:
                key, val = v.split("=", 1)
                variables[key] = val
            except ValueError:
                ch.warning(f"Invalid variable format: {v}")

    try:
        engine.execute_workflow(workflow, variables)
        ch.success(f"Workflow '{name}' executed successfully.")
    except Exception as e:
        ch.error(f"Workflow execution failed: {e}")
        raise typer.Exit(1) from e


@workflow_app.command("list")
def workflow_list():
    """List available workflows."""
    from rich.console import Console
    from rich.table import Table

    from navig.core.automation_engine import WorkflowEngine

    console = Console()
    engine = WorkflowEngine()

    workflows_dir = engine._workflows_dir
    possible_paths = [workflows_dir, Path.home() / ".navig" / "workflows"]

    table = Table(title="📜 Automation Workflows")
    table.add_column("Name", style="cyan")
    table.add_column("Path", style="dim")

    found = False
    for p in possible_paths:
        if p.exists():
            for f in p.glob("*.yaml"):
                table.add_row(f.stem, str(f))
                found = True
            for f in p.glob("*.yml"):
                table.add_row(f.stem, str(f))
                found = True

    if found:
        console.print(table)
    else:
        ch.info("No workflows found.")
