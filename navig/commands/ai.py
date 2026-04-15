"""AI Assistant Commands — Typer sub-app + helpers.

Extracted from ``navig/cli/__init__.py`` during CLI decomposition.
"""

from __future__ import annotations

import locale
import logging
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch
from navig.cli._callbacks import show_subcommand_help
from navig.console_helper import get_console
from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_config():
    """Return the global ConfigManager singleton."""
    from navig.config import get_config_manager
    return get_config_manager()


def _format_openrouter_missing_key_error(config_manager, server_name: str) -> str:
    """Build host-aware guidance for missing OpenRouter key in ask flow."""
    ai_cfg = (config_manager.global_config or {}).get("ai") or {}
    source_checks = [
        f"OPENROUTER_API_KEY={'set' if os.getenv('OPENROUTER_API_KEY') else 'missing'}",
        f"ai.api_key={'set' if ai_cfg.get('api_key') else 'missing'}",
        (
            "openrouter_api_key="
            f"{'set' if (config_manager.global_config or {}).get('openrouter_api_key') else 'missing'}"
        ),
    ]
    return (
        f"OpenRouter API key not configured for active host '{server_name}'. "
        f"Checked sources: {', '.join(source_checks)}. "
        "Set one with: navig config set ai.api_key <key>"
    )


def _decode_command_output(raw: bytes | str) -> str:
    """Decode subprocess output robustly across Windows locale/codepage variants."""
    if isinstance(raw, str):
        return raw
    if not raw:
        return ""

    candidates = ["utf-8"]
    preferred = locale.getpreferredencoding(False)
    if preferred:
        candidates.append(preferred)
    candidates.extend(["cp1252", "latin-1"])

    for encoding in candidates:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue

    return raw.decode("utf-8", errors="replace")


def _host_exists(config_manager: Any, host_name: str) -> bool:
    """Best-effort host existence check with backward-compatible fallback."""
    if not host_name:
        return False

    checker = getattr(config_manager, "host_exists", None)
    if callable(checker):
        try:
            return bool(checker(host_name))
        except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("host_exists check failed for %s: %s", host_name, exc)

    return True


def ask_ai(question: str, model: str | None, options: dict[str, Any]):
    """Ask AI about server, get context-aware answers."""
    from navig.ai import AIAssistant
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    options = options or {}
    config_manager = get_config_manager()
    ai = AIAssistant(config_manager)

    server_name = (
        options.get("app")
        or options.get("host")
        or config_manager.get_active_server()
    )
    synthetic_local_context = False
    if not server_name:
        compat_active = str((config_manager.global_config or {}).get("active_host") or "").strip()
        if compat_active and _host_exists(config_manager, compat_active):
            server_name = compat_active

    if not server_name:
        # Keep `ask` usable even without a configured host/server.
        # This enables generic AI usage and preserves exit-code behavior for
        # downstream provider/config exceptions raised by AIAssistant.ask().
        synthetic_local_context = True
        server_name = "local"

    if not synthetic_local_context and not _host_exists(config_manager, server_name):
        ch.error(
            f"Active host '{server_name}' not found",
            "Use 'navig host list' and 'navig host use <name>'",
        )
        raise typer.Exit(2)

    # Gather context
    ch.dim("The Schema's engines are analyzing...\n")

    if synthetic_local_context:
        server_config = {
            "name": "local",
            "type": "local",
            "host": "localhost",
            "is_local": True,
        }
        ch.warning(
            "No active server configured.",
            "Continuing with local AI-only context.",
        )
    else:
        try:
            server_config = config_manager.load_server_config(server_name)
        except FileNotFoundError as e:
            ch.error(
                f"Host configuration for '{server_name}' not found",
                "Use 'navig host list' and 'navig host use <name>'",
            )
            raise typer.Exit(2) from e

    remote_ops = RemoteOperations(config_manager)

    # Always inject client platform so the AI gives OS-correct commands.
    root_directory = Path.home().anchor or os.path.abspath(os.sep)
    context: dict = {
        "server": server_config,
        "directory": root_directory,
        "client_os": f"{platform.system()} {platform.release()}",
        "client_arch": platform.machine(),
    }

    # Gather running processes (optional, can fail gracefully)
    is_local_host = (
        bool(server_config.get("is_local"))
        or str(server_config.get("type", "")).lower() == "local"
        or server_config.get("host", "") in ("localhost", "127.0.0.1", "::1")
    )
    try:
        if os.name == "nt" and is_local_host:
            # Windows local: tasklist filtered for common services — no SSH needed.
            # Do NOT pass text=True here — we decode manually so that non-UTF-8
            # bytes in tasklist output (e.g. OEM code page cp850/cp1252 on
            # French/European Windows) don't crash the subprocess readerthread
            # with UnicodeDecodeError.
            _r = subprocess.run(
                ["tasklist", "/FO", "CSV", "/NH"],
                capture_output=True, text=False, timeout=10,
            )
            if _r.returncode == 0:
                tasklist_text = _decode_command_output(_r.stdout)
                _relevant = [
                    ln for ln in tasklist_text.splitlines()
                    if any(k in ln.lower() for k in ("python", "nginx", "mysql", "node", "php", "apache"))
                ]
                context["processes"] = _relevant[:20] or ["(no web services detected)"]
        elif os.name != "nt":
            result = remote_ops.execute_command(
                "ps aux | grep -E 'nginx|php|mysql' | grep -v grep", server_config
            )
            if result.returncode == 0:
                context["processes"] = result.stdout.strip().split("\n")
        # Windows + remote host: skip probe (requires SSH client)
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("Failed to gather process context: %s", e)
        # Continue without process info — not critical
    except Exception as e:
        logger.debug("Unexpected error gathering process context: %s", e)

    # Get AI response
    try:
        effort = (options or {}).get("effort")
        response = ai.ask(question, context, model_override=model, effort=effort)

        # Render as markdown using console_helper
        ch.print_markdown(response)

    except ValueError as e:
        message = str(e)
        if "OpenRouter API key not configured" in message:
            ch.error(_format_openrouter_missing_key_error(config_manager, server_name))
        else:
            ch.error(message)
        raise typer.Exit(2) from e
    except Exception as e:
        ch.error(f"AI communication failed: {e}")
        raise typer.Exit(1) from e


# ============================================================================
# AI SUB-APP
# ============================================================================

ai_app = typer.Typer(
    help="AI-powered assistance for diagnostics, optimization, and knowledge",
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)


@ai_app.callback()
def ai_callback(ctx: typer.Context):
    """AI Assistant — ask anything, or run a subcommand."""
    if ctx.invoked_subcommand is not None:
        return

    query_parts = [a for a in (ctx.args or []) if not a.startswith("-")]
    if query_parts:
        # Hybrid routing: classify intent and dispatch
        from navig.commands.ai_router import (
            CONFIDENCE_THRESHOLD,
            classify_intent,
            top_two_intents,
        )

        text = " ".join(query_parts)
        best = classify_intent(text)

        if best.confidence >= CONFIDENCE_THRESHOLD:
            _dispatch_hybrid(ctx, best.subcommand, text)
        else:
            top2 = top_two_intents(text)
            ch.warning("Ambiguous input — top interpretations:")
            for i, r in enumerate(top2, 1):
                ch.dim(f"  {i}. navig ai {r.subcommand}  (confidence {r.confidence:.0%})")
            ch.dim("Run one of the above directly, or navig ai ask \"<question>\" to ask freely.")
        raise typer.Exit()

    show_subcommand_help("ai", ctx)
    raise typer.Exit()


def _dispatch_hybrid(ctx: typer.Context, subcommand: str, text: str) -> None:
    """Route hybrid natural-language input to the matching subcommand."""
    ch.dim(f"→ routing to: navig ai {subcommand}")
    if subcommand == "ask":
        ask_ai(text, None, ctx.obj or {})
    elif subcommand == "diagnose":
        from navig.commands.assistant import analyze_cmd
        analyze_cmd(ctx.obj or {})
    elif subcommand == "suggest":
        ask_ai(
            "Analyze the current server configuration and suggest optimizations.",
            None, ctx.obj or {},
        )
    elif subcommand == "show":
        from navig.commands.assistant import status_cmd
        status_cmd(ctx.obj or {})
    elif subcommand == "run":
        from navig.commands.assistant import analyze_cmd
        analyze_cmd(ctx.obj or {})
    elif subcommand == "explain":
        ask_ai(f"Explain: {text}", None, ctx.obj or {})
    else:
        ask_ai(text, None, ctx.obj or {})


@ai_app.command("ask")
def ai_ask(
    ctx: typer.Context,
    question: str = typer.Argument(..., help="Natural language question"),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        help="Override default AI model",
    ),
):
    """[DEPRECATED: Use 'navig ask'] Ask AI about server/configuration."""
    from navig.deprecation import deprecation_warning as _dw
    _dw("navig ai ask <question>", "navig ask <question>")
    ask_ai(question, model, ctx.obj)


@ai_app.command("explain")
def ai_explain(
    ctx: typer.Context,
    log_file: str = typer.Argument(..., help="Log file or command to explain"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of tail lines to read (for files)"),
):
    """[DEPRECATED: Use 'navig ask'] Explain a log file, error output, or shell command."""
    from navig.deprecation import deprecation_warning as _dw
    _dw("navig ai explain <file>", "navig ask 'explain <file>'")

    # Try to read as a file; fall back to treating as a command/concept string
    from pathlib import Path
    file_path = Path(log_file)
    if file_path.exists() and file_path.is_file():
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            tail = "\n".join(content.splitlines()[-lines:])
            question = (
                f"Analyze and explain the following log content from '{log_file}'.\n"
                f"Identify errors, warnings, or issues and suggest concrete solutions.\n\n"
                f"Log content:\n{tail}"
            )
        except OSError as exc:
            ch.error(f"Cannot read '{log_file}': {exc}")
            raise typer.Exit(1) from exc
    else:
        # Treat as a shell command or concept
        question = (
            f"Explain the following shell command or concept in plain language.\n"
            f"Cover what it does, any risks, and when to use it.\n\n"
            f"  {log_file}"
        )

    ask_ai(question, None, ctx.obj)


@ai_app.command("diagnose")
def ai_diagnose(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ask'] AI-powered issue diagnosis based on system state."""
    from navig.deprecation import deprecation_warning as _dw
    _dw("navig ai diagnose", "navig ask 'diagnose my server'")
    from navig.commands.assistant import analyze_cmd

    analyze_cmd(ctx.obj)


@ai_app.command("suggest")
def ai_suggest(ctx: typer.Context):
    """[DEPRECATED: Use 'navig ask'] Get AI-powered optimization suggestions."""
    from navig.deprecation import deprecation_warning as _dw
    _dw("navig ai suggest", "navig ask 'suggest optimisations for my server'")
    question = "Analyze the current server configuration and suggest optimizations for performance, security, and reliability."
    ask_ai(question, None, ctx.obj)


@ai_app.command("show")
def ai_show(
    ctx: typer.Context,
    status: bool = typer.Option(False, "--status", "-s", help="Show assistant status"),
    context: bool = typer.Option(False, "--context", "-c", help="Show AI context summary"),
    clipboard: bool = typer.Option(False, "--clipboard", help="Copy context to clipboard"),
    file: str | None = typer.Option(None, "--file", help="Save context to file"),
):
    """Show AI assistant information (canonical command)."""
    if status:
        from navig.commands.assistant import status_cmd

        status_cmd(ctx.obj)
    elif context:
        from navig.commands.assistant import context_cmd

        context_cmd(ctx.obj, clipboard, file)
    else:
        from navig.commands.assistant import status_cmd

        status_cmd(ctx.obj)


@ai_app.command("run")
def ai_run(
    ctx: typer.Context,
    analyze: bool = typer.Option(False, "--analyze", "-a", help="Run system analysis"),
    reset: bool = typer.Option(False, "--reset", "-r", help="Reset learning data"),
):
    """Run AI operations (canonical command)."""
    if analyze:
        from navig.commands.assistant import analyze_cmd

        analyze_cmd(ctx.obj)
    elif reset:
        from navig.commands.assistant import reset_cmd

        reset_cmd(ctx.obj)
    else:
        from navig.commands.assistant import analyze_cmd

        analyze_cmd(ctx.obj)


@ai_app.command("edit")
def ai_edit(ctx: typer.Context):
    """Configure AI assistant settings (interactive wizard)."""
    from navig.commands.assistant import config_cmd

    config_cmd(ctx.obj)


@ai_app.command("models")
def ai_models(
    ctx: typer.Context,
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="Filter by provider (e.g., openai, airllm)"
    ),
):
    """List available AI models from all providers.

    Examples:
        navig ai models
        navig ai models --provider airllm
        navig ai models --provider openai
    """
    from rich.table import Table

    console = get_console()

    try:
        from navig.providers import BUILTIN_PROVIDERS

        console.print("[bold cyan]Available AI Models[/bold cyan]")
        console.print()

        for pname, pconfig in BUILTIN_PROVIDERS.items():
            # Filter by provider if specified
            if provider and pname.lower() != provider.lower():
                continue

            if not pconfig.models:
                if pname == "ollama":
                    console.print(
                        f"[bold]{pname}[/bold] [dim](models discovered dynamically)[/dim]"
                    )
                elif pname == "airllm":
                    console.print(
                        f"[bold]{pname}[/bold] [dim](local inference - 70B+ models on limited VRAM)[/dim]"
                    )
                    console.print("  Suggested models (any HuggingFace model ID works):")
                    console.print("  • meta-llama/Llama-3.3-70B-Instruct")
                    console.print("  • Qwen/Qwen2.5-72B-Instruct")
                    console.print("  • deepseek-ai/deepseek-coder-33b-instruct")
                    console.print()
                continue

            console.print(f"[bold]{pname}[/bold]")

            table = Table(box=None, show_header=True, padding=(0, 2))
            table.add_column("Model ID", style="cyan")
            table.add_column("Name")
            table.add_column("Context", justify="right")
            table.add_column("Max Tokens", justify="right")

            for model in pconfig.models:
                ctx_str = (
                    f"{model.context_window // 1000}K"
                    if model.context_window >= 1000
                    else str(model.context_window)
                )
                table.add_row(
                    model.id,
                    model.name,
                    ctx_str,
                    str(model.max_tokens),
                )

            console.print(table)
            console.print()

        if provider and provider.lower() not in [p.lower() for p in BUILTIN_PROVIDERS.keys()]:
            console.print(f"[yellow]Unknown provider: {provider}[/yellow]")
            console.print(f"[dim]Available: {', '.join(BUILTIN_PROVIDERS.keys())}[/dim]")

    except ImportError:
        console.print("[yellow]Provider system not available.[/yellow]")


@ai_app.command("providers")
def ai_providers(
    ctx: typer.Context,
    add: str | None = typer.Option(
        None, "--add", "-a", help="Add API key for provider (e.g., openai, anthropic)"
    ),
    remove: str | None = typer.Option(None, "--remove", "-r", help="Remove API key for provider"),
    test: str | None = typer.Option(None, "--test", "-t", help="Test provider connection"),
):
    """Manage AI providers and API keys."""
    from rich.table import Table

    console = get_console()

    try:
        from navig.providers import BUILTIN_PROVIDERS, AuthProfileManager

        auth = AuthProfileManager()

        if add:
            # Add API key for provider
            import getpass

            provider_name = add.lower()
            if provider_name not in BUILTIN_PROVIDERS:
                console.print(
                    f"[yellow]⚠ Unknown provider '{provider_name}'. Known: {', '.join(BUILTIN_PROVIDERS.keys())}[/yellow]"
                )

            api_key = getpass.getpass(f"Enter API key for {provider_name}: ")
            if api_key:
                auth.add_api_key(
                    provider=provider_name, api_key=api_key, profile_id=f"{provider_name}-default"
                )
                auth.save()
                console.print(f"[green]✓ API key saved for {provider_name}[/green]")
            else:
                console.print("[yellow]No key entered, cancelled[/yellow]")
            return

        if remove:
            # Remove API key for provider
            provider_name = remove.lower()
            profile_id = f"{provider_name}-default"
            if auth.remove_profile(profile_id):
                auth.save()
                console.print(f"[green]✓ Removed API key for {provider_name}[/green]")
            else:
                console.print(f"[yellow]No API key found for {provider_name}[/yellow]")
            return

        if test:
            # Test provider connection
            provider_name = test.lower()
            api_key, source = auth.resolve_auth(provider_name)
            if not api_key:
                console.print(f"[red]✗ No API key configured for {provider_name}[/red]")
                console.print(f"  Add one with: navig ai providers --add {provider_name}")
                return

            console.print(f"[dim]Testing {provider_name} (key from: {source})...[/dim]")

            # Quick test - try to list models or make a tiny request
            import asyncio

            from navig.providers import BUILTIN_PROVIDERS, create_client

            config = BUILTIN_PROVIDERS.get(provider_name)
            if not config:
                console.print(f"[red]✗ Unknown provider: {provider_name}[/red]")
                return

            try:
                client = create_client(config, api_key=api_key, timeout=10)
                # Make a minimal request to test auth
                from navig.providers import CompletionRequest, Message

                async def test_request():
                    request = CompletionRequest(
                        messages=[Message(role="user", content="Hi")],
                        model=config.models[0].id if config.models else "gpt-4o-mini",
                        max_tokens=5,
                    )
                    try:
                        await client.complete(request)
                        return True, None
                    except Exception as e:
                        return False, str(e)
                    finally:
                        await client.close()

                success, error = asyncio.run(test_request())
                if success:
                    console.print(f"[green]✓ {provider_name} is working![/green]")
                else:
                    console.print(f"[red]✗ {provider_name} error: {error}[/red]")
            except Exception as e:
                console.print(f"[red]✗ Test failed: {e}[/red]")
            return

        # List providers and their status
        console.print("[bold cyan]AI Providers[/bold cyan]")
        console.print()

        table = Table(box=None, show_header=True, padding=(0, 2))
        table.add_column("Provider", style="cyan")
        table.add_column("API Key", style="green")
        table.add_column("Source")
        table.add_column("Models", style="dim")

        for name, config in BUILTIN_PROVIDERS.items():
            api_key, source = auth.resolve_auth(name)
            key_status = "✓ configured" if api_key else "✗ not set"
            key_style = "green" if api_key else "red"

            model_count = len(config.models)
            models_str = f"{model_count} models" if model_count else "dynamic"

            table.add_row(
                name,
                f"[{key_style}]{key_status}[/{key_style}]",
                source or "-",
                models_str,
            )

        console.print(table)
        console.print()
        console.print("[dim]Add a key: navig ai providers --add <provider>[/dim]")
        console.print("[dim]Test connection: navig ai providers --test <provider>[/dim]")
        console.print("[dim]Configure AirLLM: navig ai airllm --configure[/dim]")
        console.print("[dim]OAuth login: navig ai login openai-codex[/dim]")

    except ImportError as exc:
        console.print(
            "[yellow]Provider system not available. Install httpx: pip install httpx[/yellow]"
        )
        raise typer.Exit(1) from exc


@ai_app.command("airllm")
def ai_airllm(
    ctx: typer.Context,
    configure: bool = typer.Option(False, "--configure", "-c", help="Configure AirLLM settings"),
    model_path: str | None = typer.Option(
        None, "--model-path", "-p", help="HuggingFace model ID or local path"
    ),
    max_vram: float | None = typer.Option(None, "--max-vram", help="Maximum VRAM in GB"),
    compression: str | None = typer.Option(
        None, "--compression", help="Compression mode: 4bit, 8bit, or none"
    ),
    test: bool = typer.Option(False, "--test", "-t", help="Test AirLLM with a sample prompt"),
    status: bool = typer.Option(
        False, "--status", "-s", help="Show AirLLM status and configuration"
    ),
):
    """Configure and manage AirLLM local inference provider.

    AirLLM enables running 70B+ models on limited VRAM (4-8GB) through
    layer-wise inference and model sharding.

    Examples:
        navig ai airllm --status
        navig ai airllm --configure --model-path meta-llama/Llama-3.3-70B-Instruct
        navig ai airllm --configure --compression 4bit --max-vram 8
        navig ai airllm --test
    """
    from rich.panel import Panel
    from rich.table import Table

    console = get_console()

    # Check if AirLLM is installed
    try:
        from navig.providers import get_airllm_vram_recommendations, is_airllm_available
        from navig.providers.airllm import AirLLMConfig
    except ImportError as _exc:
        console.print("[red]✗ Provider system not available.[/red]")
        raise typer.Exit(1) from _exc

    airllm_available = is_airllm_available()

    if status or (not configure and not test):
        # Show AirLLM status
        console.print("[bold cyan]AirLLM Local Inference Provider[/bold cyan]")
        console.print()

        # Installation status
        if airllm_available:
            console.print("[green]✓ AirLLM is installed[/green]")
        else:
            console.print("[yellow]✗ AirLLM is not installed[/yellow]")
            console.print("  Install with: [cyan]pip install airllm[/cyan]")
            console.print()

        # Current configuration
        console.print()
        console.print("[bold]Current Configuration:[/bold]")

        config = AirLLMConfig.from_env()
        config_table = Table(box=None, show_header=False, padding=(0, 2))
        config_table.add_column("Setting", style="dim")
        config_table.add_column("Value")

        config_table.add_row("Model Path", config.model_path or "[dim]not set[/dim]")
        config_table.add_row("Max VRAM", f"{config.max_vram_gb} GB")
        config_table.add_row("Compression", config.compression or "none")
        config_table.add_row("Device", config.device)
        config_table.add_row("Layer Shards Path", config.layer_shards_path or "[dim]default[/dim]")
        config_table.add_row("Prefetching", "enabled" if config.prefetching else "disabled")

        console.print(config_table)

        # VRAM recommendations
        console.print()
        console.print("[bold]VRAM Recommendations:[/bold]")
        recommendations = get_airllm_vram_recommendations()
        for model_size, rec in recommendations.items():
            console.print(f"  • {model_size}: {rec}")

        # Environment variables
        console.print()
        console.print("[bold]Environment Variables:[/bold]")
        console.print("  AIRLLM_MODEL_PATH     - HuggingFace model ID or local path")
        console.print("  AIRLLM_MAX_VRAM_GB    - Maximum VRAM to use")
        console.print("  AIRLLM_COMPRESSION    - '4bit', '8bit', or empty for none")
        console.print("  AIRLLM_DEVICE         - 'cuda', 'cpu', or 'mps' (macOS)")
        console.print("  HF_TOKEN              - HuggingFace token for gated models")

        # Suggested models
        console.print()
        console.print("[bold]Suggested Models:[/bold]")
        console.print("  • meta-llama/Llama-3.3-70B-Instruct")
        console.print("  • Qwen/Qwen2.5-72B-Instruct")
        console.print("  • deepseek-ai/deepseek-coder-33b-instruct")
        console.print("  • mistralai/Mixtral-8x7B-Instruct-v0.1")

        return

    if configure:
        # Configure AirLLM settings

        config_updates = {}

        if model_path is not None:
            config_updates["AIRLLM_MODEL_PATH"] = model_path
            console.print(f"[green]✓ Model path: {model_path}[/green]")

        if max_vram is not None:
            config_updates["AIRLLM_MAX_VRAM_GB"] = str(max_vram)
            console.print(f"[green]✓ Max VRAM: {max_vram} GB[/green]")

        if compression is not None:
            if compression.lower() == "none":
                compression = ""
            config_updates["AIRLLM_COMPRESSION"] = compression
            console.print(f"[green]✓ Compression: {compression or 'disabled'}[/green]")

        if config_updates:
            # Save to config file
            try:
                config_manager = _get_config()
                # Build update dict with proper key names
                updates = {}
                for key, value in config_updates.items():
                    config_key = f"airllm_{key.lower().replace('airllm_', '')}"
                    updates[config_key] = value
                config_manager.update_global_config(updates)
                console.print()
                console.print("[green]Configuration saved to ~/.navig/config.yaml[/green]")
            except Exception as e:
                console.print(f"[yellow]⚠ Could not save to config file: {e}[/yellow]")

            # Also show env var export commands
            console.print()
            console.print("[dim]Or set environment variables:[/dim]")
            for key, value in config_updates.items():
                console.print(f'  export {key}="{value}"')
        else:
            console.print("[yellow]No configuration options specified.[/yellow]")
            console.print("Use --model-path, --max-vram, or --compression")

        return

    if test:
        # Test AirLLM with a sample prompt
        if not airllm_available:
            console.print("[red]✗ AirLLM is not installed.[/red]")
            console.print("  Install with: [cyan]pip install airllm[/cyan]")
            raise typer.Exit(1)

        config = AirLLMConfig.from_env()
        if not config.model_path:
            console.print("[red]✗ No model configured.[/red]")
            console.print("  Set AIRLLM_MODEL_PATH or use --configure --model-path")
            raise typer.Exit(1)

        console.print(f"[dim]Testing AirLLM with model: {config.model_path}[/dim]")
        console.print(
            "[dim]This may take a while on first run (downloading/sharding model)...[/dim]"
        )
        console.print()

        import asyncio

        from navig.providers import CompletionRequest, Message, create_airllm_client

        async def run_test():
            try:
                client = create_airllm_client(config)
                request = CompletionRequest(
                    messages=[
                        Message(role="user", content="What is 2 + 2? Answer briefly."),
                    ],
                    model=config.model_path,
                    max_tokens=50,
                )

                response = await client.complete(request)
                await client.close()
                return response
            except Exception as e:
                return str(e)

        with console.status("[bold green]Running inference..."):
            result = asyncio.run(run_test())

        if hasattr(result, "content"):
            console.print("[green]✓ AirLLM is working![/green]")
            console.print()
            console.print(Panel(result.content or "[no response]", title="Response"))
            if result.usage:
                console.print(
                    f"[dim]Tokens: {result.usage.get('prompt_tokens', 0)} prompt, {result.usage.get('completion_tokens', 0)} completion[/dim]"
                )
        else:
            console.print(f"[red]✗ Test failed: {result}[/red]")
            raise typer.Exit(1)


@ai_app.command("login")
def ai_login(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help="OAuth provider (e.g., openai-codex)"),
    headless: bool = typer.Option(False, "--headless", help="Headless mode (no browser auto-open)"),
):
    """Login to an AI provider using OAuth (e.g., OpenAI Codex)."""

    console = get_console()

    try:
        from navig.providers import (
            OAUTH_PROVIDERS,
            AuthProfileManager,
            run_oauth_flow_headless,
            run_oauth_flow_interactive,
        )

        # Check if any OAuth providers are configured
        if not OAUTH_PROVIDERS:
            console.print("[red]✗ OAuth authentication is not currently available.[/red]")
            console.print()
            console.print("[yellow]Why?[/yellow]")
            console.print("OAuth requires provider-specific client registration.")
            console.print("OpenAI's OAuth is only available to enterprise partners.")
            console.print()
            console.print("[cyan]Use API key authentication instead:[/cyan]")
            console.print("  navig vault add openai sk-... --type api-key")
            console.print("  navig vault add anthropic sk-ant-... --type api-key")
            console.print()
            console.print("[dim]See: docs/development/oauth-limitations.md[/dim]")
            raise typer.Exit(1)

        provider_lower = provider.lower()
        if provider_lower not in OAUTH_PROVIDERS:
            console.print(f"[red]✗ Unknown OAuth provider: {provider}[/red]")
            console.print(f"[dim]Available: {', '.join(OAUTH_PROVIDERS.keys()) or 'none'}[/dim]")
            raise typer.Exit(1)

        oauth_config = OAUTH_PROVIDERS[provider_lower]
        console.print(f"[bold cyan]OAuth Login: {oauth_config.name}[/bold cyan]")
        console.print()

        if headless:
            # Headless mode
            console.print(
                "[yellow]Headless mode: Copy the URL below and open it in a browser.[/yellow]"
            )
            console.print()

            def on_auth_url(url: str):
                console.print("[bold]Authorization URL:[/bold]")
                console.print(url)
                console.print()

            def get_callback_input() -> str:
                console.print("[bold]After signing in, paste the redirect URL here:[/bold]")
                return input("> ")

            result = run_oauth_flow_headless(
                provider_lower,
                on_auth_url=on_auth_url,
                get_callback_input=get_callback_input,
            )
        else:
            # Interactive mode
            def on_progress(msg: str):
                console.print(f"[dim]{msg}[/dim]")

            result = run_oauth_flow_interactive(
                provider_lower,
                on_progress=on_progress,
            )

        if result.success and result.credentials:
            # Save credentials
            auth = AuthProfileManager()
            profile_id = auth.add_oauth_credentials(
                provider=provider_lower,
                access_token=result.credentials.access,
                refresh_token=result.credentials.refresh,
                expires_at=result.credentials.expires,
                client_id=result.credentials.client_id,
                account_id=result.credentials.account_id,
                email=result.credentials.email,
            )

            console.print()
            console.print(f"[green]✓ Successfully logged in to {oauth_config.name}![/green]")
            console.print(f"[dim]Profile saved: {profile_id}[/dim]")

            if result.credentials.account_id:
                console.print(f"[dim]Account ID: {result.credentials.account_id}[/dim]")

            console.print()
            console.print("[dim]You can now use this provider with:[/dim]")
            console.print(f"  navig ai ask 'your question' --model {provider_lower}:gpt-4o")
        else:
            console.print(f"[red]✗ OAuth failed: {result.error}[/red]")
            raise typer.Exit(1)

    except ImportError as e:
        console.print(f"[yellow]OAuth not available: {e}[/yellow]")
        console.print("[dim]Install httpx: pip install httpx[/dim]")
        raise typer.Exit(1) from e


@ai_app.command("logout")
def ai_logout(
    ctx: typer.Context,
    provider: str = typer.Argument(..., help="Provider to logout from"),
):
    """Remove OAuth credentials for a provider."""

    console = get_console()

    try:
        from navig.providers import AuthProfileManager

        auth = AuthProfileManager()
        provider_lower = provider.lower()

        # Find and remove all profiles for this provider
        removed = []
        for profile_id in list(auth.store.profiles.keys()):
            cred = auth.store.profiles[profile_id]
            if cred.provider == provider_lower:
                del auth.store.profiles[profile_id]
                removed.append(profile_id)

        if removed:
            auth.save()
            console.print(f"[green]✓ Logged out from {provider}[/green]")
            for pid in removed:
                console.print(f"[dim]  Removed: {pid}[/dim]")
        else:
            console.print("Already logged out.")

    except ImportError as exc:
        console.print("[yellow]Provider system not available.[/yellow]")
        raise typer.Exit(1) from exc


# ============================================================================
# AI MEMORY SUB-COMMANDS (navig ai memory ...)
# ============================================================================

ai_memory_app = typer.Typer(
    help="Manage AI memory - what NAVIG knows about you",
    invoke_without_command=True,
    no_args_is_help=False,
)
ai_app.add_typer(ai_memory_app, name="memory")


@ai_memory_app.callback()
def memory_callback(ctx: typer.Context):
    """AI Memory - what NAVIG knows about you."""
    if ctx.invoked_subcommand is None:
        # Default: show memory
        _memory_show()


def _memory_show():
    """Display current user profile."""

    console = get_console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()
        console.print(profile.to_human_readable())
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error loading profile: {e}[/red]")


@ai_memory_app.command("show")
def memory_show():
    """Display what NAVIG knows about you."""
    _memory_show()


@ai_memory_app.command("edit")
def memory_edit():
    """Open user profile in your default editor."""
    import os


    console = get_console()

    profile_path = config_dir() / "memory" / "user_profile.json"

    if not profile_path.exists():
        # Create empty profile first
        try:
            from navig.memory.user_profile import get_profile

            profile = get_profile()
            profile.save()
            console.print(f"[green]Created new profile at: {profile_path}[/green]")
        except Exception as e:
            console.print(f"[red]Error creating profile: {e}[/red]")
            raise typer.Exit(1) from e

    # Get editor from environment
    editor = os.environ.get(
        "EDITOR", os.environ.get("VISUAL", "notepad" if os.name == "nt" else "nano")
    )

    console.print(f"[dim]Opening {profile_path} in {editor}...[/dim]")

    import subprocess

    try:
        subprocess.run([editor, str(profile_path)], check=True)
        console.print("[green]Profile updated. Changes will be loaded on next agent start.[/green]")
    except subprocess.CalledProcessError:
        console.print(f"[red]Failed to open editor: {editor}[/red]")
    except FileNotFoundError:
        console.print(f"[red]Editor not found: {editor}[/red]")
        console.print(f"[dim]Profile is at: {profile_path}[/dim]")


@ai_memory_app.command("add")
def memory_add(
    note: str = typer.Argument(..., help="Note to add to memory"),
    category: str = typer.Option("user_note", "--category", "-c", help="Note category"),
):
    """Add a note to NAVIG's memory about you."""

    console = get_console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()
        note_obj = profile.add_note(note, category=category, source="user")
        profile.save()
        console.print(f"[green]✓ Added note:[/green] {note[:60]}...")
        console.print(f"[dim]Category: {category} | Time: {note_obj.timestamp[:19]}[/dim]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error adding note: {e}[/red]")


@ai_memory_app.command("search")
def memory_search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
):
    """Search NAVIG's memory about you."""

    console = get_console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()
        results = profile.search_memory(query, limit=limit)

        if results:
            console.print(f"[bold]Found {len(results)} result(s) for '{query}':[/bold]\n")
            for i, result in enumerate(results, 1):
                console.print(f"  {i}. {result}")
        else:
            console.print(f"[yellow]No results found for '{query}'[/yellow]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error searching: {e}[/red]")


@ai_memory_app.command("clear")
def memory_clear(
    confirm: bool = typer.Option(False, "--confirm", help="Confirm clearing all memory"),
):
    """Clear all memory (requires --confirm)."""

    console = get_console()
    if not confirm:
        console.print("[yellow]⚠️  This will delete all stored user profile data.[/yellow]")
        console.print("[dim]Run with --confirm to proceed.[/dim]")
        raise typer.Exit(1)

    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()

        if profile.clear(confirm=True):
            console.print("[green]✓ Memory cleared. Backup created.[/green]")
        else:
            console.print("[red]Failed to clear memory.[/red]")
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")


@ai_memory_app.command("set")
def memory_set(
    field: str = typer.Argument(
        ..., help="Field to set (e.g., identity.name, technical_context.stack)"
    ),
    value: str = typer.Argument(..., help="Value to set"),
):
    """Set a specific profile field."""

    console = get_console()
    try:
        from navig.memory.user_profile import get_profile

        profile = get_profile()

        # Handle list fields (comma-separated)
        if field in [
            "technical_context.stack",
            "technical_context.managed_hosts",
            "technical_context.primary_projects",
            "work_patterns.active_hours",
            "work_patterns.common_tasks",
            "goals",
            "preferences.confirmation_required_for",
        ]:
            value = [v.strip() for v in value.split(",")]

        updated = profile.update({field: value})

        if updated:
            console.print(f"[green]✓ Updated {field} = {value}[/green]")
        else:
            console.print(f"[red]Failed to update {field}. Check field name.[/red]")
            console.print(
                "[dim]Valid fields: identity.name, identity.timezone, identity.role, "
                "technical_context.stack, goals, preferences.communication_style[/dim]"
            )
    except ImportError:
        console.print("[yellow]Memory system not available.[/yellow]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
