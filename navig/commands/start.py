from __future__ import annotations

import typer

from navig.commands.space import space_switch


app = typer.Typer(
    name="start",
    help="Start work in a space and show immediate next actions.",
    invoke_without_command=True,
    no_args_is_help=False,
)


@app.callback()
def start_space(
    ctx: typer.Context,
    space: str | None = typer.Argument(None, help="Space to activate"),
) -> None:
    """Activate a space and print kickoff next actions."""
    if ctx.invoked_subcommand is not None:
        return

    if not space:
        print(ctx.get_help())
        raise typer.Exit(1)

    space_switch(space)


# ── Quick launcher (navig start) ─────────────────────────────────────────────

def run_quick_start(
    bot: bool = True,
    gateway: bool = True,
    port: int | None = None,
    background: bool = True,
) -> None:
    """Start NAVIG services (gateway + bot) with sensible defaults."""
    import os
    import subprocess
    import sys

    from navig import console_helper as ch

    if bot:
        from navig.messaging.secrets import resolve_telegram_bot_token

        telegram_token = resolve_telegram_bot_token()
        if not telegram_token:
            ch.error("TELEGRAM_BOT_TOKEN not set!")
            ch.info("  Get token from @BotFather on Telegram")
            ch.info("  Add to .env file: TELEGRAM_BOT_TOKEN=your-token")
            raise typer.Exit(1)

    if gateway and port is None:
        from navig.commands.gateway import _load_gateway_cli_defaults

        port, _host = _load_gateway_cli_defaults()

    if gateway and bot:
        ch.info("Starting NAVIG (Gateway + Telegram Bot)...")
        cmd = [
            sys.executable,
            "-m",
            "navig.daemon.telegram_worker",
            "--port",
            str(port),
        ]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
            ch.info(f"  Gateway: http://localhost:{port}")
            ch.info("  Status: navig bot status")
            ch.info("  Stop: navig bot stop")
        else:
            os.execv(sys.executable, cmd)

    elif bot:
        ch.info("Starting NAVIG Telegram Bot (standalone)...")
        ch.warning("⚠️  Conversations reset on restart")
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if background:
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                subprocess.Popen(
                    cmd,
                    start_new_session=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            ch.success("Started in background")
        else:
            os.execv(sys.executable, cmd)

    elif gateway:
        from navig.commands.gateway import gateway_start

        ch.info(f"Starting NAVIG Gateway on port {port}...")
        gateway_start(port=port, host="0.0.0.0", background=background)
