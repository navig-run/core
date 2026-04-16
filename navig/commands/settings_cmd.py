"""
navig.commands.settings_cmd — View and edit layered NAVIG settings.

Surface:
    navig settings                        # display all settings grouped by namespace
    navig settings <key>                  # show single key + source layer
    navig settings <key> <value>          # write key to --layer (default: global)
    navig settings --layer project        # restrict display to project layer values
    navig settings --reset                # remove a key override (resets to default)

Layers (lowest → highest priority):
    defaults → global → layer → project → local
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from navig.console_helper import get_console
from navig.core.yaml_io import atomic_write_text as _atomic_write_text

if TYPE_CHECKING:
    from navig.settings.resolver import SettingsResolver

# ── Group labels ─────────────────────────────────────────────────────────────

_GROUPS = {
    "navig.ai": "AI Provider",
    "navig.daemon": "Daemon",
    "navig.inbox": "Inbox",
    "navig.mesh": "Mesh",
    "navig.memory": "Memory",
    "navig.safety": "Safety",
    "navig.vault": "Vault",
    "navig.ui": "Interface",
    "navig.telemetry": "Telemetry",
    "navig": "General",  # catch-all
}

_LAYER_COLORS = {
    "defaults": "dim white",
    "global": "#22d3ee",
    "project": "#a78bfa",
    "local": "#f59e0b",
}

# Keys whose values should be masked in display
_SENSITIVE_KEYS = {"navig.ai.api_key", "navig.vault.passphrase", "navig.vault.token"}


# ─────────────────────────────────────────────────────────────────────────────


def _badge(layer_name: str) -> str:
    color = _LAYER_COLORS.get(layer_name.split(":")[0], "dim")
    return f"[{color}]{layer_name}[/{color}]"


def _mask(key: str, value: object) -> str:
    if key in _SENSITIVE_KEYS and isinstance(value, str) and len(value) > 4:
        return value[:4] + "•" * min(len(value) - 4, 12)
    return str(value)


def _group_for(key: str) -> str:
    for prefix in _GROUPS:
        if key.startswith(prefix + ".") or key == prefix:
            return prefix
    return "navig"


def _source_layer_for(key: str, resolver: SettingsResolver) -> str:  # type: ignore[name-defined]
    """Return the highest-priority layer that has a value for *key*."""
    # Walk sources from highest to lowest priority
    for name, path, exists in reversed(resolver.all_sources()):
        if not exists:
            continue
        try:
            import json

            from navig.settings.resolver import _flatten

            raw = json.loads(path.read_text(encoding="utf-8"))
            flat = _flatten(raw)
            if key in flat:
                return name
        except Exception:  # noqa: BLE001
            pass
    return "defaults"


# ── Main entry point ──────────────────────────────────────────────────────────


def run_settings(
    key: str | None = None,
    value: str | None = None,
    layer: str = "global",
    reset: bool = False,
    show_sources: bool = False,
) -> None:
    """
    View or edit layered NAVIG settings.

    Args:
        key:          Dot-separated setting key (e.g. navig.ai.provider).
        value:        New value to write. Triggers write mode when provided.
        layer:        Target layer for writes: "global", "project", or "local".
        reset:        Remove the key from the target layer (restore default).
        show_sources: Include file paths for each layer in the header.
    """
    try:

        from rich.panel import Panel
        from rich.table import Table
        from rich.text import Text  # noqa: F401

        RICH = True
    except ImportError:
        RICH = False

    from navig.settings.resolver import DEFAULTS, SettingsResolver

    resolver = SettingsResolver()

    # ── WRITE mode ───────────────────────────────────────────────────────────
    if value is not None:
        _coerced = _coerce(value, DEFAULTS.get(key, value))  # type: ignore[arg-type]
        resolver.set(key, _coerced, layer=layer)  # type: ignore[arg-type]
        if RICH:
            console = get_console()
            console.print(
                f"[bold green]✓[/bold green] Set [cyan]{key}[/cyan] = "
                f"[white]{_mask(key, _coerced)}[/white]  "
                f"({_badge(layer)})"
            )
        else:
            print(f"Set {key} = {_coerced}  ({layer})")
        return

    # ── RESET mode ───────────────────────────────────────────────────────────
    if reset:
        if key is None:
            _err("--reset requires a key", RICH)
            return
        _reset_key(key, layer, resolver)
        if RICH:
            console = get_console()
            console.print(
                f"[bold yellow]↺[/bold yellow] Reset [cyan]{key}[/cyan] in layer ({_badge(layer)})"
            )
        else:
            print(f"Reset {key} in layer {layer}")
        return

    settings = resolver.resolve()

    # ── SINGLE KEY mode ──────────────────────────────────────────────────────
    if key is not None:
        if key not in settings and key not in DEFAULTS:
            _err(f"Unknown key: {key!r}. Run 'navig settings' to see all keys.", RICH)
            return
        current = settings.get(key, DEFAULTS.get(key))
        src = _source_layer_for(key, resolver)
        default = DEFAULTS.get(key, "—")
        if RICH:
            console = get_console()
            tbl = Table(show_header=False, box=None, padding=(0, 1))
            tbl.add_column("label", style="dim")
            tbl.add_column("val")
            tbl.add_row("Key", f"[cyan]{key}[/cyan]")
            tbl.add_row("Value", f"[white]{_mask(key, current)}[/white]")
            tbl.add_row("Source", _badge(src))
            tbl.add_row("Default", f"[dim]{_mask(key, default)}[/dim]")
            console.print(Panel(tbl, title="[bold]Setting[/bold]", border_style="#2271D0"))
        else:
            print(f"{key} = {current}  [{src}]  (default: {default})")
        return

    # ── LIST ALL mode ────────────────────────────────────────────────────────
    if RICH:
        _display_rich(settings, resolver, show_sources, DEFAULTS)
    else:
        _display_plain(settings, DEFAULTS)


# ── Display helpers ───────────────────────────────────────────────────────────


def _display_rich(
    settings: dict,
    resolver: SettingsResolver,  # type: ignore[name-defined]
    show_sources: bool,
    defaults: dict,
) -> None:
    from rich.panel import Panel
    from rich.rule import Rule
    from rich.table import Table

    console = get_console()

    # Header: layer source map
    if show_sources:
        src_tbl = Table(show_header=True, box=None, padding=(0, 1))
        src_tbl.add_column("Layer", style="dim")
        src_tbl.add_column("Path")
        src_tbl.add_column("Status")
        for name, path, exists in resolver.all_sources():
            status = "[green]●[/green] found" if exists else "[dim]○ missing[/dim]"
            src_tbl.add_row(
                f"[{_LAYER_COLORS.get(name, 'white')}]{name}[/]",
                f"[dim]{path}[/dim]",
                status,
            )
        console.print(Panel(src_tbl, title="[bold]Settings Layers[/bold]", border_style="#2271D0"))

    # Group keys
    grouped: dict[str, list[tuple[str, object, str]]] = {}
    for key, val in sorted(settings.items()):
        group = _group_for(key)
        grouped.setdefault(group, [])
        src = _source_layer_for(key, resolver) if val != defaults.get(key) else "defaults"
        grouped[group].append((key, val, src))

    for prefix in _GROUPS:
        rows = grouped.get(prefix)
        if not rows:
            continue
        label = _GROUPS[prefix]
        tbl = Table(show_header=True, box=None, padding=(0, 1), expand=True)
        tbl.add_column("Key", style="cyan", no_wrap=True)
        tbl.add_column("Value", style="white")
        tbl.add_column("Layer", style="dim", no_wrap=True)
        for k, v, src in rows:
            display_key = k[len("navig.") :] if k.startswith("navig.") else k
            color = _LAYER_COLORS.get(src.split(":")[0], "dim white")
            tbl.add_row(display_key, _mask(k, v), f"[{color}]{src}[/]")
        console.print(Rule(f"[bold]{label}[/bold]", style="#334155"))
        console.print(tbl)

    console.print()
    console.print(
        "[dim]  navig settings <key>            — inspect a key\n"
        "  navig settings <key> <value>     — write to --layer (default: global)\n"
        "  navig settings <key> --reset     — remove override\n"
        "  navig settings --show-sources    — show layer file paths[/dim]"
    )


def _display_plain(settings: dict, defaults: dict) -> None:
    for key in sorted(settings):
        val = settings[key]
        changed = val != defaults.get(key)
        marker = "*" if changed else " "
        print(f"{marker} {key} = {_mask(key, val)}")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _coerce(raw: str, reference: object) -> object:
    """Coerce a CLI string value to match the reference type."""
    if isinstance(reference, bool):
        return raw.lower() in {"1", "true", "yes", "on"}
    if isinstance(reference, int):
        try:
            return int(raw)
        except ValueError:
            return raw
    if isinstance(reference, float):
        try:
            return float(raw)
        except ValueError:
            return raw
    if isinstance(reference, list):
        import json

        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001
            return [x.strip() for x in raw.split(",") if x.strip()]
    return raw


def _reset_key(key: str, layer: str, resolver: SettingsResolver) -> None:  # type: ignore[name-defined]
    """Remove *key* from the specified layer file."""
    import json

    from navig.settings.resolver import _flatten, _unflatten

    path = resolver._layer_path(layer)  # noqa: SLF001
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        flat = _flatten(raw)
        flat.pop(key, None)
        nested = _unflatten(flat)
        _atomic_write_text(path, json.dumps(nested, indent=2, ensure_ascii=False))
        resolver._cache = None  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


def _err(msg: str, rich_available: bool) -> None:
    if rich_available:

        get_console().print(f"[red]Error:[/red] {msg}")
    else:
        print(f"Error: {msg}")
