"""
NAVIG Ask CLI Commands

Direct access to VS Code Copilot via the MCP Bridge from the command line.
Works over the SSH tunnel — requires an active Bridge connection.

Usage:
    navig ask ask "How do I configure nginx rate limiting?"
    navig ask explain /var/log/nginx/error.log --lines 50
    navig ask suggest --topic security
    navig ask status
"""

import asyncio

import typer

from navig.lazy_loader import lazy_import
from navig.providers.bridge_grid_reader import BRIDGE_DEFAULT_PORT

ch = lazy_import("navig.console_helper")

copilot_app = typer.Typer(
    name="ask",
    help="Chat with VS Code Copilot via the MCP Bridge",
    no_args_is_help=True,
)


# ── Helpers ─────────────────────────────────────────────────────────


def _run(coro):
    """Run an async function from sync Typer context."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


async def _get_bridge_provider():
    """Build a McpBridgeProvider from config."""
    from navig.agent.llm_providers import McpBridgeProvider

    url, token = _read_bridge_config()
    provider = McpBridgeProvider(base_url=url, api_key=token)

    if not await provider.is_available():
        await provider.close()
        return None
    return provider


def _read_bridge_config():
    """Read MCP Bridge URL and token from config / env."""
    import os

    url = os.getenv("NAVIG_BRIDGE_MCP_URL", "")
    token = os.getenv("NAVIG_BRIDGE_LLM_TOKEN", "")
    if not url:
        try:
            from navig.config import get_config_manager

            cfg = get_config_manager().global_config or {}
            bridge = cfg.get("bridge", {})
            url = bridge.get("mcp_url") or ""
            token = token or bridge.get("token", "")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
    return url or f"ws://127.0.0.1:{BRIDGE_DEFAULT_PORT}", token


async def _chat(messages: list, model: str = "") -> str:
    """Send messages through the Bridge and return the response."""
    provider = await _get_bridge_provider()
    if provider is None:
        ch.error("MCP Bridge is not reachable.")
        ch.info("  Check that:")
        ch.info("    1. VS Code is running with the Bridge extension active")
        ch.info("    2. The SSH tunnel is up (systemctl status bridge-tunnel)")
        ch.info(
            "    3. bridge.mcp_url / bridge.token in ~/.navig/config.yaml are correct"
        )
        raise typer.Exit(1)
    try:
        resp = await provider.chat(model=model, messages=messages, max_tokens=4096)
        return resp.content
    finally:
        await provider.close()


# ── Commands ────────────────────────────────────────────────────────


@copilot_app.command("ask")
def copilot_ask(
    question: str = typer.Argument(..., help="Natural language question"),
    model: str | None = typer.Option(None, "--model", "-m", help="Model override"),
    system: str | None = typer.Option(
        None, "--system", "-s", help="Custom system prompt"
    ),
):
    """
    Ask Copilot a question and get a response.

    Examples:
        navig ask ask "How do I set up a reverse proxy in nginx?"
        navig ask ask "Explain Python decorators" --model gpt-4o
    """
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": question})

    result = _run(_chat(messages, model=model or ""))
    ch.print_markdown(result)


@copilot_app.command("explain")
def copilot_explain(
    target: str = typer.Argument(
        ..., help="File path, error message, or code snippet to explain"
    ),
    lines: int = typer.Option(
        50, "--lines", "-n", help="Number of lines (for log files)"
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model override"),
    remote: bool = typer.Option(
        False, "--remote", "-r", help="Read file from remote host via NAVIG"
    ),
):
    """
    Explain a log file, error, or code snippet using Copilot.

    Examples:
        navig ask explain /var/log/nginx/error.log
        navig ask explain "ECONNREFUSED 127.0.0.1:3306"
        navig ask explain /var/log/syslog --lines 100 --remote
    """
    import os

    content = target  # Assume inline text by default

    # Check if it looks like a file path
    if "/" in target or "\\" in target:
        if remote:
            # Read from remote via NAVIG
            import subprocess

            try:
                result = subprocess.run(
                    ["navig", "file", "show", target, "--tail", "--lines", str(lines)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if result.returncode == 0 and result.stdout.strip():
                    content = f"File: {target} (last {lines} lines)\n```\n{result.stdout.strip()}\n```"
                else:
                    ch.warning(f"Could not read remote file: {result.stderr.strip()}")
                    content = target
            except Exception as e:
                ch.warning(f"Failed to read remote file: {e}")
                content = target
        elif os.path.isfile(target):
            # Read local file
            try:
                with open(target, errors="replace") as f:
                    file_lines = f.readlines()
                tail = file_lines[-lines:] if len(file_lines) > lines else file_lines
                content = (
                    f"File: {target} (last {lines} lines)\n```\n{''.join(tail)}```"
                )
            except Exception as e:
                ch.warning(f"Could not read file: {e}")
                content = target

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior systems engineer. Analyze the following content. "
                "Identify errors, warnings, and issues. Explain root causes clearly "
                "and suggest concrete fixes. Be concise but thorough."
            ),
        },
        {"role": "user", "content": content},
    ]

    result = _run(_chat(messages, model=model or ""))
    ch.print_markdown(result)


@copilot_app.command("suggest")
def copilot_suggest(
    topic: str = typer.Option(
        "general",
        "--topic",
        "-t",
        help="Focus area: security, performance, reliability, general",
    ),
    context: str | None = typer.Option(
        None, "--context", "-c", help="Additional context or file path"
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model override"),
):
    """
    Get optimization suggestions from Copilot.

    Examples:
        navig ask suggest --topic security
        navig ask suggest --topic performance --context "nginx with PHP-FPM"
    """
    prompt = (
        f"Provide actionable {topic} optimization suggestions. "
        f"Focus on practical, high-impact improvements. "
        f"Format as a numbered list with brief explanations."
    )
    if context:
        prompt += f"\n\nContext: {context}"

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior DevOps engineer reviewing infrastructure. "
                "Give specific, actionable suggestions — not generic advice."
            ),
        },
        {"role": "user", "content": prompt},
    ]

    result = _run(_chat(messages, model=model or ""))
    ch.print_markdown(result)


@copilot_app.command("review")
def copilot_review(
    file_path: str = typer.Argument(..., help="File path to review"),
    focus: str | None = typer.Option(
        None, "--focus", "-f", help="Focus area: bugs, security, perf, style"
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model override"),
):
    """
    Code review a file using Copilot.

    Examples:
        navig ask review ./app/Http/Controllers/AuthController.php
        navig ask review ./docker-compose.yml --focus security
    """
    import os

    if not os.path.isfile(file_path):
        ch.error(f"File not found: {file_path}")
        raise typer.Exit(1)

    try:
        with open(file_path, errors="replace") as f:
            code = f.read()
    except Exception as e:
        ch.error(f"Cannot read file: {e}")
        raise typer.Exit(1) from e

    # Truncate very large files
    max_chars = 15000
    if len(code) > max_chars:
        code = code[:max_chars] + f"\n... (truncated, {len(code)} total chars)"

    focus_text = f" Focus particularly on {focus}." if focus else ""

    messages = [
        {
            "role": "system",
            "content": (
                "You are a senior code reviewer. Review the following file for "
                f"bugs, security issues, performance problems, and style.{focus_text} "
                "Be specific — reference line numbers and provide fixes."
            ),
        },
        {
            "role": "user",
            "content": f"Review this file ({file_path}):\n\n```\n{code}\n```",
        },
    ]

    result = _run(_chat(messages, model=model or ""))
    ch.print_markdown(result)


# ── Session History sub-app ──────────────────────────────────────────────────
# Lazy-register to avoid import cost on unrelated commands.
def _register_sessions_subapp():
    try:
        from navig.commands.sessions import sessions_app

        copilot_app.add_typer(sessions_app, name="sessions")
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical


_register_sessions_subapp()


@copilot_app.command("status")
def copilot_status(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """
    Check navig-bridge MCP connectivity and status.

    Examples:
        navig ask status
        navig ask status --json
    """
    import time

    url, token = _read_bridge_config()

    async def _check():
        from navig.agent.llm_providers import McpBridgeProvider

        provider = McpBridgeProvider(base_url=url, api_key=token)
        t0 = time.monotonic()
        available = await provider.is_available()
        latency = int((time.monotonic() - t0) * 1000)

        info = {
            "available": available,
            "url": url,
            "latency_ms": latency,
            "auth": bool(token),
        }
        await provider.close()
        return info

    info = _run(_check())

    if json_output:
        import json

        print(json.dumps(info, indent=2))
    else:
        if info["available"]:
            ch.success("navig-bridge MCP is online")
            ch.info(f"  URL:     {info['url']}")
            ch.info(f"  Latency: {info['latency_ms']}ms")
            ch.info(f"  Auth:    {'configured' if info['auth'] else 'NONE'}")
        else:
            ch.error("navig-bridge MCP is OFFLINE")
            ch.info(f"  URL:  {info['url']}")
            ch.info(f"  Auth: {'configured' if info['auth'] else 'NONE'}")
            ch.info("")
            ch.info("  Troubleshooting:")
            ch.info("    1. Is VS Code running with navig-bridge extension?")
            ch.info("    2. Is the SSH tunnel active?")
            ch.info("       systemctl status bridge-tunnel")
            ch.info("    3. Check token in ~/.navig/config.yaml → bridge.token")
