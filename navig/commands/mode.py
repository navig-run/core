"""
CLI commands for 'navig mode' — LLM mode management.

  navig mode show         — Display all 5 modes in a Rich table
  navig mode set <mode>   — Update a mode's configuration
  navig mode list         — Show available uncensored models + providers
  navig mode detect <text> — Test mode detection on input text
"""

from __future__ import annotations

import typer

mode_app = typer.Typer(
    help="LLM mode routing — view, configure, and test multi-mode AI routing",
    invoke_without_command=True,
    no_args_is_help=False,
)


@mode_app.callback()
def mode_callback(ctx: typer.Context):
    """LLM Mode Router — run without subcommand to show modes."""
    if ctx.invoked_subcommand is None:
        _show_modes()
        raise typer.Exit()


# ── navig mode show ──────────────────────────────────────


@mode_app.command("show")
def mode_show(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Display all LLM modes with their configuration."""
    if json_output:
        import json as _json

        from navig.llm_router import get_llm_router

        router = get_llm_router()
        typer.echo(_json.dumps(router.get_all_modes(), indent=2))
    else:
        _show_modes()


def _show_modes():
    """Render a Rich table of all LLM modes."""
    from rich.console import Console
    from rich.table import Table

    from navig.llm_router import CANONICAL_MODES, _has_api_key, get_llm_router

    console = Console()
    router = get_llm_router()

    table = Table(
        title="🧠 LLM Mode Router",
        title_style="bold cyan",
        show_header=True,
        header_style="bold",
        border_style="dim",
        pad_edge=True,
    )
    table.add_column("Mode", style="cyan bold", min_width=12)
    table.add_column("Provider", min_width=10)
    table.add_column("Model", min_width=20)
    table.add_column("Fallback", min_width=18)
    table.add_column("Temp", justify="center", min_width=5)
    table.add_column("MaxTok", justify="right", min_width=7)
    table.add_column("Uncensored", justify="center", min_width=10)
    table.add_column("Key?", justify="center", min_width=5)

    mode_emojis = {
        "small_talk": "💬",
        "big_tasks": "🧠",
        "coding": "💻",
        "summarize": "📝",
        "research": "🔬",
    }

    for mode_name in sorted(CANONICAL_MODES):
        cfg = router.modes.get_mode(mode_name)
        if cfg is None:
            continue

        emoji = mode_emojis.get(mode_name, "")
        has_key = _has_api_key(cfg.provider)
        key_icon = "[green]✓[/green]" if has_key else "[red]✗[/red]"
        uncensored = "[yellow]YES[/yellow]" if cfg.use_uncensored else "[dim]no[/dim]"
        fallback = cfg.fallback_model or "[dim]—[/dim]"

        table.add_row(
            f"{emoji} {mode_name}",
            cfg.provider,
            cfg.model,
            fallback,
            f"{cfg.temperature}",
            str(cfg.max_tokens),
            uncensored,
            key_icon,
        )

    console.print(table)
    console.print(
        "\n[dim]Tip: [cyan]navig mode set <mode> --provider X --model Y[/cyan] to change config[/dim]\n"
    )


# ── navig mode set ───────────────────────────────────────


@mode_app.command("set")
def mode_set(
    mode: str = typer.Argument(
        ..., help="Mode name or alias (e.g. coding, chat, research)"
    ),
    provider: str | None = typer.Option(
        None, "--provider", "-p", help="Provider (ollama, openai, groq, etc.)"
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model name/ID"),
    temperature: float | None = typer.Option(
        None, "--temperature", "--temp", "-t", help="Temperature (0.0–2.0)"
    ),
    max_tokens: int | None = typer.Option(None, "--max-tokens", help="Max tokens"),
    uncensored: bool | None = typer.Option(
        None, "--uncensored/--no-uncensored", help="Enable/disable uncensored routing"
    ),
):
    """Update a mode's provider, model, or parameters."""
    from rich.console import Console

    from navig.llm_router import get_llm_router

    console = Console()
    router = get_llm_router()

    canonical = router.resolve_mode(mode)
    ok = router.update_mode(
        canonical,
        provider=provider,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        use_uncensored=uncensored,
    )

    if not ok:
        console.print(f"[red]Unknown mode:[/red] {mode}")
        raise typer.Exit(1)

    # Persist to config
    try:
        _persist_mode_config(router)
        console.print(
            f"[green]✓[/green] Mode [cyan]{canonical}[/cyan] updated and saved."
        )
    except Exception as e:
        console.print(
            f"[yellow]⚠[/yellow] Updated in memory but failed to persist: {e}"
        )

    # Show resolved config
    resolved = router.get_config(canonical)
    console.print(f"  Provider: [bold]{resolved.provider}[/bold]")
    console.print(f"  Model:    [bold]{resolved.model}[/bold]")
    console.print(f"  Reason:   [dim]{resolved.resolution_reason}[/dim]")


def _persist_mode_config(router):
    """Save the current router config to config.yaml."""
    from navig.config import get_config_manager

    cm = get_config_manager()
    raw = cm.global_config

    # Store under llm_router.llm_modes
    if "llm_router" not in raw:
        raw["llm_router"] = {}
    raw["llm_router"]["llm_modes"] = router.get_all_modes()
    raw["llm_router"]["uncensored_overrides"] = (
        router.uncensored.model_dump()
        if hasattr(router.uncensored, "model_dump")
        else {}
    )

    cm.save_global_config(raw)


# ── navig mode list ──────────────────────────────────────


@mode_app.command("list")
def mode_list(
    uncensored_only: bool = typer.Option(
        False, "--uncensored-only", "-u", help="Show only uncensored models"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List available models per provider, with uncensored status."""
    from rich.console import Console
    from rich.table import Table

    from navig.llm_router import get_llm_router

    console = Console()
    router = get_llm_router()

    uncensored_info = router.list_uncensored_models()

    if json_output:
        import json as _json

        typer.echo(_json.dumps(uncensored_info, indent=2))
        return

    # Local uncensored models
    console.print("\n[bold cyan]🏠 Local Uncensored Models (Ollama)[/bold cyan]\n")

    if uncensored_info["local"]:
        table = Table(show_header=True, border_style="dim")
        table.add_column("Alias", style="cyan")
        table.add_column("Model")
        table.add_column("Installed", justify="center")

        for m in uncensored_info["local"]:
            status = (
                "[green]✓ pulled[/green]"
                if m["available"]
                else "[red]✗ not pulled[/red]"
            )
            table.add_row(m["alias"], m["model"], status)
        console.print(table)
    else:
        console.print("[dim]No local uncensored models configured.[/dim]")

    # API uncensored models
    console.print("\n[bold cyan]☁️  API Uncensored Models[/bold cyan]\n")

    if uncensored_info["api"]:
        table = Table(show_header=True, border_style="dim")
        table.add_column("Alias", style="cyan")
        table.add_column("Model")
        table.add_column("Provider")
        table.add_column("API Key", justify="center")

        for m in uncensored_info["api"]:
            status = (
                "[green]✓ present[/green]"
                if m["api_key_present"]
                else "[red]✗ missing[/red]"
            )
            table.add_row(m["alias"], m["model"], m["provider"], status)
        console.print(table)
    else:
        console.print("[dim]No API uncensored models configured.[/dim]")

    if not uncensored_only:
        console.print(
            "\n[dim]Tip: Pull local models with [cyan]ollama pull dolphin-llama3:8b[/cyan][/dim]\n"
        )


# ── navig mode detect ────────────────────────────────────


@mode_app.command("detect")
def mode_detect(
    text: str = typer.Argument(..., help="Text to classify"),
):
    """Test mode detection on a piece of text."""
    from rich.console import Console

    from navig.llm_router import get_llm_router

    console = Console()
    router = get_llm_router()

    mode = router.detect_mode(text)
    resolved = router.get_config(mode)

    console.print(f"[bold]Detected mode:[/bold] [cyan]{mode}[/cyan]")
    console.print(f"[bold]Would route to:[/bold] {resolved.provider}:{resolved.model}")
    console.print(f"[bold]Reason:[/bold] [dim]{resolved.resolution_reason}[/dim]")
    console.print(
        f"[bold]Uncensored:[/bold] {'[yellow]YES[/yellow]' if resolved.is_uncensored else '[dim]no[/dim]'}"
    )
