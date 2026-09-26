"""
CLI commands for 'navig mode' — LLM mode management.

  navig mode show         — Display all 5 modes in a Rich table
  navig mode set <mode>   — Update a mode's configuration
  navig mode list         — Show available uncensored models + providers
  navig mode detect <text> — Test mode detection on input text
"""

from __future__ import annotations

import typer

from navig.console_helper import get_console

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

        from navig.llm.router import get_llm_router

        router = get_llm_router()
        typer.echo(_json.dumps(router.get_all_modes(), indent=2))
    else:
        _show_modes()


def _fallback_label(cfg) -> str:
    """How a mode's fallback is shown in the table.

    A fallback is only useful on a DIFFERENT provider — otherwise one outage
    takes out the primary and its safety net together. So the model name alone is
    ambiguous: "grok-3-mini" does not say xai. Show the provider whenever it
    differs; an empty ``fallback_provider`` means "same as the primary" and needs
    no prefix.
    """
    model = (getattr(cfg, "fallback_model", "") or "").strip()
    if not model:
        return "[dim]—[/dim]"
    provider = (getattr(cfg, "fallback_provider", "") or "").strip()
    if provider and provider != (getattr(cfg, "provider", "") or "").strip():
        return f"{provider}:{model}"
    return model


def _show_modes():
    """Render a Rich table of all LLM modes."""
    from rich.table import Table

    from navig.llm.router import CANONICAL_MODES, _has_api_key, get_llm_router

    console = get_console()
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
        fallback = _fallback_label(cfg)

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
    fallback_provider: str | None = typer.Option(
        None, "--fallback-provider", help="Provider to use when the primary fails"
    ),
    fallback_model: str | None = typer.Option(
        None, "--fallback-model", help="Model to use when the primary fails"
    ),
):
    """Update a mode's provider, model, or parameters.

    A mode's FALLBACK is what answers when the primary cannot. It had no CLI at
    all, so repointing one meant hand-editing `llm_router.llm_modes.<mode>.*` in
    config.yaml — and `navig mode doctor` now probes fallbacks, so it can show
    you a dead one it gave you no way to fix.
    """

    from navig.llm.router import CANONICAL_MODES, MODE_ALIASES, get_llm_router

    console = get_console()
    router = get_llm_router()

    # `resolve_mode` defaults ANY unrecognised hint to "big_tasks". That is the
    # right call when ROUTING a message — send an unknown intent to the capable
    # model — and destructive when choosing which config row to OVERWRITE:
    # `navig mode set codingg --model X` silently repointed the heaviest mode,
    # and the "Unknown mode" branch below could never fire because update_mode
    # never sees an unresolved name. Validate the WRITE, leave routing alone.
    typed = (mode or "").strip().lower()
    if typed not in CANONICAL_MODES and typed not in MODE_ALIASES:
        console.print(f"[red]Unknown mode:[/red] {mode}")
        console.print(f"[dim]Modes:   {', '.join(sorted(CANONICAL_MODES))}[/dim]")
        console.print(f"[dim]Aliases: {', '.join(sorted(MODE_ALIASES))}[/dim]")
        raise typer.Exit(1)

    canonical = router.resolve_mode(mode)
    try:
        ok = router.update_mode(
            canonical,
            provider=provider,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            use_uncensored=uncensored,
            fallback_provider=fallback_provider,
            fallback_model=fallback_model,
        )
    except ValueError as exc:
        # Out-of-range temp/max_tokens — reject instead of persisting a value that
        # would wipe the whole mode config on the next reload.
        console.print(f"[red]✗ {exc}[/red]")
        raise typer.Exit(1) from exc

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
    mode_cfg = router.modes.get_mode(canonical)
    fb_model = (getattr(mode_cfg, "fallback_model", "") or "") if mode_cfg else ""
    if fb_model:
        # An empty fallback_provider means "same provider as the primary" — show
        # what will actually be dialled, not the blank.
        fb_provider = (getattr(mode_cfg, "fallback_provider", "") or "") or resolved.provider
        console.print(f"  Fallback: [bold]{fb_provider}:{fb_model}[/bold]")
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

    # ConfigManager exposes _save_global_config (there is no public
    # save_global_config — calling the wrong name silently dropped every
    # `navig mode set` to memory-only, never persisting to config.yaml).
    cm._save_global_config(raw)


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

    from rich.table import Table

    console = get_console()
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


    console = get_console()
    tier_label = {"small": "Small", "big": "Big", "coder_big": "Code"}[normalized_tier]
    console.print(
        f"[green]✓[/green] Updated [cyan]{tier_label}[/cyan] slot: "
        f"[bold]{updated['provider'] or '—'}:{updated['model'] or '—'}[/bold]"
    )
    console.print("[dim]Routing is enabled in config (ai.routing.enabled: true). Restart daemon if needed.[/dim]")


# ── navig mode doctor ────────────────────────────────────


@mode_app.command("doctor")
def mode_doctor(
    mode: str = typer.Argument(None, help="Mode to probe (default: all modes)."),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON."),
    modes_only: bool = typer.Option(
        False, "--modes-only", help="Skip fallbacks and hybrid routing tiers."
    ),
):
    """Probe each mode's provider:model with a 1-token call — catches DEAD/EOL
    models (410), auth failures (401), and unreachable endpoints before they hit
    you in production. Exits non-zero if any mode needs FIXING (CI-friendly); a
    provider that is merely busy or rate-limited is reported, not failed.

    Also probes each mode's FALLBACK and the hybrid routing tiers, which nothing
    covered: a fallback is exercised only when its primary has already failed,
    and all three of one install's routing tiers pointed at models that answered
    410 GONE with no surface reporting it."""
    from navig.llm.liveness import probe_modes, probe_routes
    from navig.llm.router import get_llm_router

    console = get_console()
    router = get_llm_router()
    targets = [router.resolve_mode(mode)] if mode else None  # None → all modes

    _ICON = {
        "live": "[green]● live[/green]",
        "dead": "[red]✗ DEAD[/red]",
        "auth": "[yellow]⚠ auth[/yellow]",
        "nokey": "[yellow]○ no key[/yellow]",
        "unreachable": "[red]✗ unreachable[/red]",
        "transient": "[yellow]↻ busy[/yellow]",
        "slow": "[yellow]🐢 slow[/yellow]",
        "error": "[red]✗ error[/red]",
    }
    # A provider being briefly busy is not a broken config, so it must not fail
    # the command — `navig mode doctor` is documented as CI-friendly, and a red
    # build over someone else's rate limit teaches people to ignore it.
    # `slow` joins them: the model ANSWERED, it was just past the probe's cap
    # (an NVIDIA cold start is 40–107 s). Failing the command over that is the
    # same "red build over someone else's latency" this comment already rejects.
    _NOT_A_DEFECT = ("live", "transient", "slow")
    _ROUTE_LABEL = {"mode": "mode", "fallback": "└ fallback", "tier": "tier"}
    if not json_output:
        console.print("[dim]Probing each mode's model (1 token each)…[/dim]")
    # probe_modes resolves each mode's ACTUAL route (fast-chat override, default
    # provider, fallbacks) and runs a real 1-token call — the shared liveness path.
    if mode or modes_only:
        rows = [{"kind": "mode", "label": r["mode"], **r} for r in probe_modes(targets)]
    else:
        rows = probe_routes()

    if json_output:
        import json as _json

        typer.echo(_json.dumps(rows, indent=2))
    else:
        from rich.table import Table

        table = Table(title="🩺 LLM Route Liveness", border_style="dim", show_header=True,
                      header_style="bold")
        table.add_column("Route", style="dim", no_wrap=True)
        table.add_column("Name", style="cyan", no_wrap=True)
        table.add_column("Provider", no_wrap=True)
        table.add_column("Model", no_wrap=True)
        table.add_column("Status", no_wrap=True)
        table.add_column("Detail")
        for r in rows:
            table.add_row(_ROUTE_LABEL.get(r["kind"], r["kind"]), r["label"], r["provider"],
                          r["model"], _ICON.get(r["status"], r["status"]), r["detail"])
        console.print(table)
        # A dead FALLBACK is reported but does not fail the command: it breaks
        # nothing today, and a machine without ollama running would otherwise be
        # permanently red for a contingency it never uses. A dead primary or
        # routing tier IS live breakage and does fail.
        bad = [r for r in rows
               if r["status"] not in _NOT_A_DEFECT and r["kind"] != "fallback"]
        weak = [r for r in rows
                if r["status"] not in _NOT_A_DEFECT and r["kind"] == "fallback"]
        busy = [r["label"] for r in rows if r["status"] == "transient"]
        if weak:
            console.print(
                f"[yellow]{len(weak)} fallback(s) would not answer if their primary "
                f"failed: {', '.join(r['label'] for r in weak)}.[/yellow]"
            )
        if bad:
            dead = [r["label"] for r in rows
                    if r["status"] == "dead" and r["kind"] != "fallback"]
            hint = (f" Repoint with [cyan]navig mode set {dead[0]} --provider <p> --model <m>[/cyan]"
                    if dead else "")
            console.print(f"[yellow]{len(bad)}/{len(rows)} route(s) need fixing.[/yellow]{hint}")
        else:
            console.print(f"[green]All {len(rows)} routes usable.[/green]")
        if busy:
            console.print(
                f"[dim]{len(busy)} provider(s) busy or rate-limited after a retry "
                f"({', '.join(busy)}) — transient, re-run to confirm.[/dim]"
            )

    if any(r["status"] not in _NOT_A_DEFECT and r["kind"] != "fallback" for r in rows):
        raise typer.Exit(1)


# ── navig mode list ──────────────────────────────────────


@mode_app.command("list")
def mode_list(
    uncensored_only: bool = typer.Option(
        False, "--uncensored-only", "-u", help="Show only uncensored models"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """List available models per provider, with uncensored status."""
    from rich.table import Table

    from navig.llm.router import get_llm_router

    console = get_console()
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

    from navig.llm.router import get_llm_router

    console = get_console()
    router = get_llm_router()

    mode = router.detect_mode(text)
    resolved = router.get_config(mode)

    console.print(f"[bold]Detected mode:[/bold] [cyan]{mode}[/cyan]")
    console.print(f"[bold]Would route to:[/bold] {resolved.provider}:{resolved.model}")
    console.print(f"[bold]Reason:[/bold] [dim]{resolved.resolution_reason}[/dim]")
    console.print(
        f"[bold]Uncensored:[/bold] {'[yellow]YES[/yellow]' if resolved.is_uncensored else '[dim]no[/dim]'}"
    )
