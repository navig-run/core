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

mode_route_app = typer.Typer(
    help="Hybrid routing tier slots (small / big / code)",
    invoke_without_command=True,
    no_args_is_help=False,
)
mode_app.add_typer(mode_route_app, name="route")


@mode_app.callback()
def mode_callback(ctx: typer.Context):
    """LLM Mode Router — run without subcommand to show modes."""
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            _show_modes()
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("mode", mode_app)


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
    mode: str = typer.Argument(..., help="Mode name or alias (e.g. coding, chat, research)"),
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
        console.print(f"[green]✓[/green] Mode [cyan]{canonical}[/cyan] updated and saved.")
    except Exception as e:
        console.print(f"[yellow]⚠[/yellow] Updated in memory but failed to persist: {e}")

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
        router.uncensored.model_dump() if hasattr(router.uncensored, "model_dump") else {}
    )

    cm.save_global_config(raw)


def _normalize_route_tier(tier: str) -> str:
    mapping = {
        "small": "small",
        "s": "small",
        "big": "big",
        "b": "big",
        "code": "coder_big",
        "coder": "coder_big",
        "coder_big": "coder_big",
        "c": "coder_big",
    }
    normalized = mapping.get((tier or "").strip().lower())
    if not normalized:
        raise ValueError("Tier must be one of: small, big, code")
    return normalized


def _persist_hybrid_route_slot(tier: str, provider: str | None, model: str | None) -> dict[str, str]:
    """Persist one hybrid routing slot under ai.routing.models.<tier>."""
    from navig.config import get_config_manager

    cfg_mgr = get_config_manager()
    global_cfg = dict(cfg_mgr.global_config or {})
    ai_cfg = dict(global_cfg.get("ai") or {})
    routing_cfg = dict(ai_cfg.get("routing") or {})
    models_cfg = dict(routing_cfg.get("models") or {})
    slot_cfg = dict(models_cfg.get(tier) or {})

    if provider:
        slot_cfg["provider"] = provider
    if model:
        slot_cfg["model"] = model

    if "defaults" not in slot_cfg or not isinstance(slot_cfg.get("defaults"), dict):
        slot_cfg["defaults"] = {}

    models_cfg[tier] = slot_cfg
    routing_cfg["enabled"] = True
    routing_cfg["mode"] = routing_cfg.get("mode") or "rules_then_fallback"
    routing_cfg["models"] = models_cfg
    ai_cfg["routing"] = routing_cfg
    cfg_mgr.update_global_config({"ai": ai_cfg})

    return {
        "tier": tier,
        "provider": str(slot_cfg.get("provider") or ""),
        "model": str(slot_cfg.get("model") or ""),
    }


@mode_route_app.callback()
def mode_route_callback(ctx: typer.Context):
    """Hybrid route slot controls (defaults to show)."""
    if ctx.invoked_subcommand is None:
        mode_route_show()
        raise typer.Exit()


@mode_route_app.command("show")
def mode_route_show(
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """Show hybrid routing slots (small/big/code)."""
    from navig.agent.ai_client import get_ai_client

    router = get_ai_client().model_router
    slots = {
        "small": {"provider": "", "model": ""},
        "big": {"provider": "", "model": ""},
        "coder_big": {"provider": "", "model": ""},
    }

    if router and getattr(router, "cfg", None):
        for tier in ("small", "big", "coder_big"):
            slot = router.cfg.slot_for_tier(tier)
            slots[tier] = {
                "provider": slot.provider or "",
                "model": slot.model or "",
            }

    if json_output:
        import json as _json

        typer.echo(_json.dumps(slots, indent=2, sort_keys=True))
        return

    from rich.console import Console
    from rich.table import Table

    console = Console()
    table = Table(title="Hybrid Routing Slots", border_style="dim")
    table.add_column("Tier", style="cyan", min_width=10)
    table.add_column("Provider", min_width=14)
    table.add_column("Model", min_width=24)
    table.add_row("⚡ Small", slots["small"]["provider"] or "—", slots["small"]["model"] or "—")
    table.add_row("🧠 Big", slots["big"]["provider"] or "—", slots["big"]["model"] or "—")
    table.add_row("💻 Code", slots["coder_big"]["provider"] or "—", slots["coder_big"]["model"] or "—")
    console.print(table)


@mode_route_app.command("set")
def mode_route_set(
    tier: str = typer.Argument(..., help="Tier: small | big | code"),
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Provider id (openai, xai, ollama, openrouter, ...)",
    ),
    model: str | None = typer.Option(None, "--model", "-m", help="Model id/name"),
):
    """Set provider/model for one hybrid routing tier slot."""
    if not provider and not model:
        typer.echo("Provide at least one of --provider or --model")
        raise typer.Exit(1)

    try:
        normalized_tier = _normalize_route_tier(tier)
    except ValueError as exc:
        typer.echo(str(exc))
        raise typer.Exit(1) from exc

    updated = _persist_hybrid_route_slot(normalized_tier, provider, model)

    from rich.console import Console

    console = Console()
    tier_label = {"small": "Small", "big": "Big", "coder_big": "Code"}[normalized_tier]
    console.print(
        f"[green]✓[/green] Updated [cyan]{tier_label}[/cyan] slot: "
        f"[bold]{updated['provider'] or '—'}:{updated['model'] or '—'}[/bold]"
    )
    console.print("[dim]Routing is enabled in config (ai.routing.enabled: true). Restart daemon if needed.[/dim]")


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
            status = "[green]✓ pulled[/green]" if m["available"] else "[red]✗ not pulled[/red]"
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
            status = "[green]✓ present[/green]" if m["api_key_present"] else "[red]✗ missing[/red]"
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
