"""
Cross-platform automation CLI commands
"""

import sys

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

auto_app = typer.Typer(
    name="auto",
    help="Cross-platform desktop automation",
    no_args_is_help=True,
)


def _get_adapter():
    """Get automation adapter for current platform."""
    if sys.platform == "win32":
        from navig.adapters.automation.ahk import AHKAdapter

        return AHKAdapter()
    elif sys.platform == "linux":
        from navig.adapters.automation.linux import LinuxAdapter

        return LinuxAdapter()
    elif sys.platform == "darwin":
        from navig.adapters.automation.macos import MacOSAdapter

        return MacOSAdapter()
    return None


@auto_app.command("status")
def auto_status():
    """Check automation system status."""
    adapter = _get_adapter()

    if not adapter:
        ch.error(f"No adapter available for {sys.platform}")
        raise typer.Exit(1)

    if sys.platform == "linux":
        # The display protocol decides what is possible, so it is reported first and in
        # full: on Wayland, "xdotool is installed" is both true and useless, and the old
        # binary-presence check is exactly how `navig auto` came to look like it worked
        # while clicking into nothing.
        adapter.is_available()  # populate the capability probe once
        for label, ok, detail in adapter.diagnosis():
            ch.info(f"  {'✓' if ok else '✗'} {label}", detail)
        if adapter.is_available():
            if adapter.windows_supported():
                ch.success("Automation ready — input and window control")
            else:
                ch.success("Automation ready — input only, no window control")
        else:
            ch.warning(adapter.unavailable_reason())
        return

    if adapter.is_available():
        ch.success(f"Automation ready on {sys.platform}")

        if sys.platform == "darwin":
            ch.info("  AppleScript: ✓")
            ch.info(f"  cliclick: {'✓' if adapter._has_cliclick else '✗ (optional)'}")
    else:
        ch.error("Automation not available")

        if sys.platform == "darwin":
            ch.info("Optional: brew install cliclick")


@auto_app.command("click")
def auto_click(
    x: int = typer.Argument(..., help="X coordinate"),
    y: int = typer.Argument(..., help="Y coordinate"),
    button: str = typer.Option("left", "--button", "-b", help="Mouse button (left/right/middle)"),
):
    """Click at screen coordinates."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("Automation not available")
        raise typer.Exit(1)

    result = adapter.click(x, y, button)
    if result.success:
        ch.success(f"Clicked at ({x}, {y})")
    else:
        ch.error(f"Failed: {result.stderr}")
        raise typer.Exit(1)


@auto_app.command("type")
def auto_type(
    text: str = typer.Argument(..., help="Text to type"),
    delay: int = typer.Option(50, "--delay", "-d", help="Delay between keystrokes (ms)"),
):
    """Type text."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("Automation not available")
        raise typer.Exit(1)

    result = adapter.type_text(text, delay)
    if result.success:
        ch.success("Text typed")
    else:
        ch.error(f"Failed: {result.stderr}")
        raise typer.Exit(1)


@auto_app.command("open")
def auto_open(
    target: str = typer.Argument(..., help="Application name or path"),
):
    """Open application."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("Automation not available")
        raise typer.Exit(1)

    result = adapter.open_app(target)
    if result.success:
        ch.success(f"Opened: {target}")
    else:
        ch.error(f"Failed: {result.stderr}")
        raise typer.Exit(1)


@auto_app.command("windows")
def auto_windows():
    """List all windows."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("Automation not available")
        raise typer.Exit(1)

    windows = adapter.get_all_windows()

    from rich.table import Table

    table = Table(title=f"Windows ({len(windows)})")
    table.add_column("ID", style="dim")
    table.add_column("Title", style="cyan")
    table.add_column("Process", style="yellow")
    table.add_column("Position", style="green")
    table.add_column("Size", style="blue")

    for w in windows:
        table.add_row(
            str(w.id)[:8],
            w.title[:50],
            w.process_name[:20],
            f"{w.x},{w.y}",
            f"{w.width}x{w.height}",
        )

    ch.console.print(table)


@auto_app.command("snap")
def auto_snap(
    selector: str = typer.Argument(..., help="Window title or app name"),
    position: str = typer.Argument(..., help="Position: left, right, top, bottom"),
):
    """Snap window to screen position."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("Automation not available")
        raise typer.Exit(1)

    result = adapter.snap_window(selector, position)
    if result.success:
        ch.success(f"Snapped window to {position}")
    else:
        ch.error(f"Failed: {result.stderr}")
        raise typer.Exit(1)


@auto_app.command("clipboard")
def auto_clipboard(
    text: str | None = typer.Argument(None, help="Text to copy (if omitted, prints clipboard)"),
):
    """Get or set clipboard content."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("Automation not available")
        raise typer.Exit(1)

    if text is None:
        content = adapter.get_clipboard()
        print(content)
    else:
        result = adapter.set_clipboard(text)
        if result.success:
            ch.success("Copied to clipboard")
        else:
            ch.error(f"Failed: {result.stderr}")
            raise typer.Exit(1)


@auto_app.command("focus")
def auto_focus():
    """Get currently focused window."""
    adapter = _get_adapter()
    if not adapter or not adapter.is_available():
        ch.error("Automation not available")
        raise typer.Exit(1)

    window = adapter.get_focused_window()
    if window:
        ch.info("Focused window:")
        ch.console.print(f"  Title: {window.title}")
        ch.console.print(f"  Process: {window.process_name}")
        ch.console.print(f"  Position: ({window.x}, {window.y})")
        ch.console.print(f"  Size: {window.width}x{window.height}")
    else:
        ch.warning("No focused window")
