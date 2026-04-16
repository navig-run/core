"""
NAVIG Onboarding Command — Animated, keyboard-first CLI setup wizard.

Provides two modes:
  • TUI mode  (textual installed): boot sequence → system checks → 5-step wizard
  • Fallback mode (rich only):     existing quickstart / manual flow unchanged

Install TUI mode:
    pip install "navig[tui]"         # or:  pip install textual>=0.50.0

Run:
    navig onboard                    # auto-selects best mode
    python -m navig.commands.onboard
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import platform
import shutil
import socket
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from navig.platform.paths import config_dir
from navig.workspace_ownership import (
    USER_WORKSPACE_DIR,
    detect_project_workspace_duplicates,
    summarize_duplicates,
)

if TYPE_CHECKING:
    from rich.console import Console as RichConsole

    ConsoleType = RichConsole | None
else:
    ConsoleType = Any

# ---------------------------------------------------------------------------
# Rich availability guard (always needed — fallback mode depends on it)
# ---------------------------------------------------------------------------
try:
    from rich import print as rprint  # noqa: F401
    from rich.console import Console
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    from rich.table import Table

    RICH_AVAILABLE = True
except ImportError:
    Console = None  # type: ignore[misc, assignment]
    RICH_AVAILABLE = False

# ---------------------------------------------------------------------------
# Textual availability guard — ALL Textual code lives inside if _TEXTUAL_AVAILABLE
# ---------------------------------------------------------------------------
try:
    import textual  # noqa: F401 — version probe
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Container, Horizontal, Vertical
    from textual.css.query import NoMatches
    from textual.reactive import reactive
    from textual.screen import ModalScreen, Screen
    from textual.widgets import (
        Button,
        Checkbox,
        ContentSwitcher,
        Input,
        Label,
        RadioButton,
        RadioSet,
        RichLog,
        Select,
        Static,
        Switch,
    )
    from textual.worker import WorkerCancelled

    _TEXTUAL_AVAILABLE = True
except ImportError:
    _TEXTUAL_AVAILABLE = False

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
DEFAULT_NAVIG_DIR = config_dir()
DEFAULT_WORKSPACE_DIR = USER_WORKSPACE_DIR
DEFAULT_CONFIG_FILE = DEFAULT_NAVIG_DIR / "navig.json"

# ---------------------------------------------------------------------------
# Provider registry — single source of truth for all wizard flows
# ---------------------------------------------------------------------------


@dataclass
class ProviderDef:
    """Describes a single AI provider — used across all wizard paths."""

    id: str
    label: str
    type: Literal["cloud", "local", "other"]
    env_vars: list[str]  # env var names that signal “already configured”
    port: int | None  # TCP port to probe (local providers only)
    local_path: str | None  # home-relative dir that signals install (e.g. ".jan")
    api_key_url: str  # link to get an API key (empty for local/other)
    start_cmd: str  # one-line start command when local service is off
    note: str  # short display note shown inline


#: All supported providers in preferred display order.
PROVIDER_REGISTRY: list[ProviderDef] = [
    # ── Cloud providers ────────────────────────────────────────────────────────
    ProviderDef(
        "openrouter",
        "OpenRouter",
        "cloud",
        ["OPENROUTER_API_KEY"],
        None,
        None,
        "https://openrouter.ai/keys",
        "",
        "recommended · access 200+ models",
    ),
    ProviderDef(
        "openai",
        "OpenAI",
        "cloud",
        ["OPENAI_API_KEY"],
        None,
        None,
        "https://platform.openai.com/api-keys",
        "",
        "GPT-4o and o-series",
    ),
    ProviderDef(
        "anthropic",
        "Anthropic",
        "cloud",
        ["ANTHROPIC_API_KEY"],
        None,
        None,
        "https://console.anthropic.com/settings/keys",
        "",
        "Claude 3.5 Sonnet / Haiku",
    ),
    ProviderDef(
        "gemini",
        "Google Gemini",
        "cloud",
        ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        None,
        None,
        "https://aistudio.google.com/app/apikey",
        "",
        "Gemini 2.0 Flash / Pro",
    ),
    ProviderDef(
        "mistral",
        "Mistral AI",
        "cloud",
        ["MISTRAL_API_KEY"],
        None,
        None,
        "https://console.mistral.ai/api-keys",
        "",
        "",
    ),
    ProviderDef(
        "cohere",
        "Cohere",
        "cloud",
        ["COHERE_API_KEY"],
        None,
        None,
        "https://dashboard.cohere.com/api-keys",
        "",
        "",
    ),
    ProviderDef(
        "groq",
        "Groq",
        "cloud",
        ["GROQ_API_KEY"],
        None,
        None,
        "https://console.groq.com/keys",
        "",
        "fastest inference",
    ),
    ProviderDef(
        "nvidia",
        "NVIDIA NIM",
        "cloud",
        ["NVIDIA_API_KEY"],
        None,
        None,
        "https://build.nvidia.com/",
        "",
        "GPU-optimised models",
    ),
    ProviderDef(
        "together",
        "Together AI",
        "cloud",
        ["TOGETHER_API_KEY"],
        None,
        None,
        "https://api.together.xyz/settings/api-keys",
        "",
        "",
    ),
    ProviderDef(
        "fireworks",
        "Fireworks AI",
        "cloud",
        ["FIREWORKS_API_KEY"],
        None,
        None,
        "https://fireworks.ai/account/api-keys",
        "",
        "",
    ),
    ProviderDef(
        "perplexity",
        "Perplexity AI",
        "cloud",
        ["PERPLEXITY_API_KEY"],
        None,
        None,
        "https://www.perplexity.ai/settings/api",
        "",
        "",
    ),
    ProviderDef(
        "xai",
        "xAI (Grok)",
        "cloud",
        ["XAI_API_KEY"],
        None,
        None,
        "https://console.x.ai/",
        "",
        "",
    ),
    ProviderDef(
        "deepseek",
        "DeepSeek",
        "cloud",
        ["DEEPSEEK_API_KEY"],
        None,
        None,
        "https://platform.deepseek.com/api_keys",
        "",
        "",
    ),
    ProviderDef(
        "azure",
        "Azure OpenAI",
        "cloud",
        ["AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT"],
        None,
        None,
        "https://portal.azure.com/",
        "",
        "",
    ),
    ProviderDef(
        "bedrock",
        "AWS Bedrock",
        "cloud",
        ["AWS_ACCESS_KEY_ID", "AWS_REGION"],
        None,
        None,
        "https://aws.amazon.com/bedrock/",
        "",
        "",
    ),
    # ── Local providers ──────────────────────────────────────────────────
    ProviderDef(
        "ollama",
        "Ollama",
        "local",
        [],
        11434,
        None,
        "https://ollama.com/download",
        "ollama serve",
        "",
    ),
    ProviderDef(
        "lmstudio",
        "LM Studio",
        "local",
        [],
        1234,
        ".lmstudio",
        "https://lmstudio.ai/",
        "",
        "",
    ),
    ProviderDef(
        "jan",
        "Jan",
        "local",
        [],
        1337,
        ".jan",
        "https://jan.ai/",
        "jan",
        "Start Jan from the desktop app or via the 'jan' command.",
    ),
    ProviderDef(
        "localai",
        "LocalAI",
        "local",
        ["LOCAL_AI_URL"],
        8080,
        None,
        "https://localai.io/",
        "",
        "",
    ),
    # ── Other ────────────────────────────────────────────────────────────
    ProviderDef("none", "Skip for now", "other", [], None, None, "", "", ""),
]


# ---------------------------------------------------------------------------
# NavigConfig dataclass  (single source of truth across all wizard steps)
# ---------------------------------------------------------------------------


@dataclass
class NavigConfig:
    """Mutable config object shared by reference across all wizard steps."""

    # Step 1 — Identity
    profile_name: str = "operator"
    workspace_root: str = str(DEFAULT_WORKSPACE_DIR)
    theme: str = "dark"

    # Step 2 — Provider
    ai_provider: str = "openrouter"
    api_key: str = ""
    ai_provider_env_var: str = ""  # env var name resolved at runtime (never a raw secret)

    # Step 3 — Runtime
    local_runtime_enabled: bool = False
    local_runtime_host: str = "http://localhost:11434"

    # Step 4 — Packs
    capability_packs: list[str] = field(default_factory=list)

    # Step 5 — Shell & hooks
    shell_integration: bool = True
    auto_update: bool = True
    git_hooks: bool = False
    telemetry: bool = False


def _store_in_vault(
    provider: str,
    key_name: str,
    secret_value: str,
    credential_type: str = "api_key",
    console: ConsoleType = None,
) -> str | None:
    """
    Store a secret in the vault and return its credential ID.

    Returns the cred_id string on success, None on failure.
    Failure is non-fatal — caller decides whether to warn the user.
    """
    if not secret_value or not secret_value.strip():
        return None
    try:
        from navig.vault import get_vault  # lazy import — keep startup fast

        vault = get_vault()
        cred_id = vault.add(
            provider=provider,
            credential_type=credential_type,
            data={key_name: secret_value},
            profile_id="default",
            label=f"{provider} (onboarding)",
        )
        return cred_id
    except Exception as exc:  # vault init can fail on locked-down systems
        if console:
            exc_text = str(exc).replace("[", "\\[")  # escape Rich markup in exception messages
            provider_safe = provider.replace(
                "[", "\\["
            )  # provider may be user-typed in manual flow
            console.print(
                f"[yellow]⚠ Could not store {provider_safe} secret in vault ({exc_text}). "
                "Key not saved.[/yellow]"
            )
        return None


def get_console() -> ConsoleType:
    """Get rich console or fall back to basic print."""
    try:
        from navig.console_helper import get_console as _ch_get_console

        return _ch_get_console()
    except Exception:
        pass
    if RICH_AVAILABLE and Console:
        return Console()
    return None


def print_banner(console: ConsoleType) -> None:
    """Print the onboarding banner."""
    banner = """
╔═══════════════════════════════════════════════════════════════════╗
║   Welcome to NAVIG - Your AI-Powered Operations Assistant         ║
╚═══════════════════════════════════════════════════════════════════╝
"""
    if console:
        console.print(banner, style="bold #2c8bb7")
    else:
        print(banner)


# ---------------------------------------------------------------------------
# New utility functions (environment detection + real system checks)
# ---------------------------------------------------------------------------


def detect_environment() -> dict[str, str]:
    """Return a snapshot of the local operator environment."""
    shell = os.environ.get("SHELL") or os.environ.get("COMSPEC") or "unknown"
    return {
        "hostname": socket.gethostname(),
        "shell": shell,
        "os": platform.system(),
        "python": platform.python_version(),
        "mode": "local",
        "status": "unbound",
    }


def check_python_version() -> bool:
    """Python >= 3.10 required."""
    return sys.version_info >= (3, 10)


def check_git_installed() -> bool:
    """True if git is on PATH."""
    return shutil.which("git") is not None


def check_network() -> bool:
    """DNS resolution probe."""
    try:
        socket.gethostbyname("example.com")
        return True
    except OSError:
        return False


def check_disk_space(min_mb: int = 100) -> bool:
    """At least min_mb of free disk space in home dir."""
    try:
        usage = shutil.disk_usage(Path.home())
        return (usage.free / 1024 / 1024) >= min_mb
    except OSError:
        return False


def check_config_dir_writable() -> bool:
    """~/.navig is writable (create-on-demand)."""
    try:
        navig_dir = config_dir()
        navig_dir.mkdir(parents=True, exist_ok=True)
        probe = navig_dir / ".write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check_ollama_reachable(host: str = "http://localhost:11434") -> bool:
    """True if Ollama HTTP endpoint responds within 2 s."""
    try:
        import httpx  # already a core dep

        resp = httpx.get(host, timeout=2.0)
        return resp.status_code < 500
    except Exception:
        return False



# ---------------------------------------------------------------------------
# Provider detection — runs all probes concurrently, finishes within timeout_ms
# ---------------------------------------------------------------------------


def detect_providers(timeout_ms: int = 500) -> dict[str, bool]:
    """
    Probe every entry in PROVIDER_REGISTRY and return ``{provider_id: detected}``.

    * Cloud providers: env-var check (in-process, zero I/O).
    * Local providers: TCP connect to known port (concurrent, 300 ms per probe).
    * Local path providers: ``Path.home() / local_path`` existence check.
    All TCP probes run concurrently and are bounded by *timeout_ms* wall-clock time.
    """
    result: dict[str, bool] = {}
    deadline = time.monotonic() + timeout_ms / 1000.0

    # ── In-process checks (instant) ──────────────────────────────────────────
    for p in PROVIDER_REGISTRY:
        if p.type == "cloud":
            result[p.id] = any(os.environ.get(v, "").strip() for v in p.env_vars)
        elif p.type == "other":
            result[p.id] = False
        elif p.local_path:
            # Path check; port probe (if present) may override below
            result[p.id] = (Path.home() / p.local_path).exists()
        else:
            result[p.id] = False  # port-only — resolved in the TCP block

    # Special case: LocalAI env var counts as detected even without a port probe
    if os.environ.get("LOCAL_AI_URL", "").strip():
        result["localai"] = True

    # ── Concurrent TCP port probes ────────────────────────────────────────────
    port_providers = [p for p in PROVIDER_REGISTRY if p.type == "local" and p.port]

    def _tcp_probe(pdef: ProviderDef) -> tuple[str, bool]:
        if pdef.port is None:
            return (pdef.id, False)
        try:
            with socket.create_connection(("127.0.0.1", pdef.port), timeout=0.3):
                return (pdef.id, True)
        except OSError:
            return (pdef.id, False)

    if port_providers:
        remaining = max(0.05, deadline - time.monotonic())
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(port_providers)) as executor:
            futures = {executor.submit(_tcp_probe, p): p for p in port_providers}
            try:
                for fut in concurrent.futures.as_completed(futures, timeout=remaining):
                    try:
                        pid, detected = fut.result()
                        # Port takes priority over path-existence for local providers
                        if detected:
                            result[pid] = True
                        elif not result.get(pid):
                            result[pid] = False
                    except Exception:
                        pass  # timed out or errored → keep existing value
            except concurrent.futures.TimeoutError:
                pass  # hard deadline hit — remaining probes treated as undetected

    return result


# ---------------------------------------------------------------------------
# Provider menu renderer — categorised, sorted, with status indicators
# ---------------------------------------------------------------------------


def render_provider_menu(
    detected: dict[str, bool],
    console: ConsoleType,
) -> tuple[list[ProviderDef], int]:
    """
    Print a categorised, sorted provider selection menu to *console*.

    Within each category, detected providers sort first (then alphabetically).

    Returns:
        ordered_providers: flat list in display order (position + 1 = 1-based number)
        default_idx: 1-based default selection (first detected, else OpenRouter)
    """
    if not console:
        return (PROVIDER_REGISTRY, 3)

    def _sort_key(p: ProviderDef) -> tuple[int, str]:
        return (0 if detected.get(p.id) else 1, p.label.lower())

    cloud = sorted([p for p in PROVIDER_REGISTRY if p.type == "cloud"], key=_sort_key)
    local = sorted([p for p in PROVIDER_REGISTRY if p.type == "local"], key=_sort_key)
    other = [p for p in PROVIDER_REGISTRY if p.type == "other"]
    ordered: list[ProviderDef] = cloud + local + other

    console.print("\n  [dim]✓ = configured   ◉ = local service running   · = not detected[/dim]\n")

    def _print_group(header: str, group: list[ProviderDef], start: int) -> int:
        bar = "─" * max(0, 52 - len(header))
        console.print(f"  [dim]── {header} {bar}[/dim]")
        num = start
        for p in group:
            is_det = detected.get(p.id, False)
            if p.type == "local":
                symbol = "[bold cyan]◉[/bold cyan]" if is_det else "[dim]·[/dim]"
            elif p.type == "cloud":
                symbol = "[bold green]✓[/bold green]" if is_det else "[dim]·[/dim]"
            else:
                symbol = " "

            note_parts: list[str] = []
            if is_det:
                if p.env_vars:
                    note_parts.append(f"[green]{p.env_vars[0]} found[/green]")
                elif p.port:
                    note_parts.append(f"[cyan]running on :{p.port}[/cyan]")
            elif p.note:
                note_parts.append(f"[dim]{p.note}[/dim]")

            note_str = "  " + note_parts[0] if note_parts else ""
            console.print(f"  {num:>2}.  {symbol}  {p.label}{note_str}")
            num += 1
        console.print()
        return num

    num = 1
    num = _print_group("Cloud Providers", cloud, num)
    num = _print_group("Local Providers", local, num)
    _print_group("Other", other, num)

    # Default = first detected provider, fallback to OpenRouter
    default_idx = next(
        (i + 1 for i, p in enumerate(ordered) if detected.get(p.id)),
        next(
            (i + 1 for i, p in enumerate(ordered) if p.id == "openrouter"),
            3,  # static fallback if OpenRouter somehow not in registry
        ),
    )
    return (ordered, default_idx)


# ---------------------------------------------------------------------------
# Post-selection handler — shared by quickstart and manual flows
# ---------------------------------------------------------------------------


def _handle_provider_selection(
    provider: ProviderDef,
    detected: dict[str, bool],
    console: ConsoleType,
) -> tuple[str, str]:
    """
    Drive the post-selection UX for a chosen provider.

    Returns ``(provider_id, env_var_name)`` where *env_var_name* is the env var
    that holds the API key at runtime, or ``""`` for local / skip providers.
    Raw secret values are always vaulted — never returned or stored in config.
    """
    if not console:
        return (provider.id, provider.env_vars[0] if provider.env_vars else "")

    is_detected = detected.get(provider.id, False)

    # ── Skip ─────────────────────────────────────────────────────────────────
    if provider.type == "other":
        console.print(
            "[dim]Skipping AI provider setup. Run 'navig init' again to configure later.[/dim]"
        )
        return ("none", "")

    # ── Local provider ────────────────────────────────────────────────────────
    if provider.type == "local":
        if is_detected:
            port_info = f" on :{provider.port}" if provider.port else ""
            console.print(
                f"[green]✓ {provider.label} running{port_info} — no API key needed.[/green]"
            )
        else:
            console.print(f"\n[yellow]⚠ {provider.label} is not currently running.[/yellow]")
            if provider.api_key_url:
                console.print(
                    f"  Download: [link={provider.api_key_url}]{provider.api_key_url}[/link]"
                )
            if provider.start_cmd:
                console.print(f"  Start with: [bold]{provider.start_cmd}[/bold]")
            console.print()
            try:
                input("  Press Enter when ready (or Ctrl+C to cancel)... ")
            except KeyboardInterrupt:
                raise SystemExit("Setup cancelled. Re-run navig init to try again.") from None

            # Re-probe with generous 2 s timeout
            re_detected = detect_providers(timeout_ms=2000)
            if re_detected.get(provider.id):
                port_info = f" on :{provider.port}" if provider.port else ""
                console.print(f"[green]✓ {provider.label} is now running{port_info}.[/green]")
            else:
                console.print(f"[yellow]⚠ Still cannot reach {provider.label}.[/yellow]")
                try:
                    choice = Prompt.ask(
                        "  [c]ontinue anyway or [e]xit?",
                        choices=["c", "e"],
                        default="c",
                    )
                    if choice == "e":
                        raise SystemExit("Setup cancelled. Re-run navig init to try again.")
                except KeyboardInterrupt:
                    raise SystemExit("Setup cancelled. Re-run navig init to try again.") from None
        return (provider.id, "")

    # ── Cloud provider ────────────────────────────────────────────────────────
    primary_env = provider.env_vars[0] if provider.env_vars else ""

    if is_detected:
        console.print(f"[green]✓ {provider.label} — key found in {primary_env}.[/green]")
        return (provider.id, primary_env)

    # Key not found — prompt user
    if provider.api_key_url:
        console.print(f"  [dim]Get one at: {provider.api_key_url}[/dim]")

    def _validate_key(key: str) -> bool:
        stripped = key.strip()
        return bool(
            stripped and len(stripped) >= 10 and " " not in stripped and "\t" not in stripped
        )

    api_key = Prompt.ask(f"  Enter your {provider.label} API key", password=True, default="")
    if not _validate_key(api_key):
        console.print("[yellow]⚠ Invalid key format — must be ≥ 10 chars with no spaces.[/yellow]")
        api_key = Prompt.ask(
            f"  Enter your {provider.label} API key (retry)", password=True, default=""
        )
        if not _validate_key(api_key):
            raise SystemExit("Setup cancelled. Re-run navig init to try again.")

    vault_id = _store_in_vault(provider.id, primary_env, api_key.strip(), "api_key", console)
    if vault_id:
        console.print(f"[green]✓ {provider.label} API key saved to vault.[/green]")
    else:
        console.print(
            f"[yellow]⚠ Could not vault key. "
            f"Set {primary_env} in your environment before running navig.[/yellow]"
        )

    return (provider.id, primary_env)


def build_config_dict(cfg: NavigConfig) -> dict[str, Any]:
    """Convert NavigConfig -> JSON-serialisable dict matching navig.json schema."""
    return {
        "meta": {
            "version": "1.0.0",
            "created": datetime.now().isoformat(),
            "onboarding_flow": "tui-wizard",
            "profile_name": cfg.profile_name,
        },
        "agents": {
            "defaults": {
                "workspace": cfg.workspace_root,
                "model": cfg.ai_provider,
                "ai_provider_env_var": cfg.ai_provider_env_var,
                "typing_mode": "instant",
            }
        },
        "auth": {"profiles": {}},
        "channels": {},
        "runtime": {
            "local_enabled": cfg.local_runtime_enabled,
            "local_host": cfg.local_runtime_host if cfg.local_runtime_enabled else "",
        },
        "capabilities": cfg.capability_packs,
        "shell": {
            "integration": cfg.shell_integration,
            "auto_update": cfg.auto_update,
            "git_hooks": cfg.git_hooks,
            "telemetry": cfg.telemetry,
        },
    }


# ---------------------------------------------------------------------------
# Terminal capability probe
# ---------------------------------------------------------------------------


def _terminal_supports_tui() -> bool:
    """True when stdout is an interactive terminal wide enough for TUI."""
    if not sys.stdout.isatty():
        return False
    try:
        cols = os.get_terminal_size().columns
        return cols >= 80
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Shared Textual widgets + all TUI screens (only defined when Textual available)
# ---------------------------------------------------------------------------

if _TEXTUAL_AVAILABLE:

    class BrandHero(Static):
        """Animated NAVIG logo widget."""

        DEFAULT_CSS = """
        BrandHero {
            color: #22d3ee;
            text-style: bold;
            padding: 0 2;
        }
        """

        def __init__(self, **kwargs: Any) -> None:
            super().__init__("", **kwargs)
            self._content = ""

        def render(self) -> str:  # type: ignore[override]
            return self._content

        def set_text(self, text: str) -> None:
            self._content = text
            self.refresh()

    class StepIndicator(Static):
        """Renders  ● ● ○ ○ ○  step dots."""

        current_step: reactive[int] = reactive(0)
        total_steps: reactive[int] = reactive(5)

        def render(self) -> str:  # type: ignore[override]
            dots: list[str] = []
            for i in range(self.total_steps):
                if i < self.current_step:
                    dots.append("[bold #10b981]●[/bold #10b981]")
                elif i == self.current_step:
                    dots.append("[bold #22d3ee]●[/bold #22d3ee]")
                else:
                    dots.append("[#334155]○[/#334155]")
            step_num = self.current_step + 1
            labels = ["Identity", "Provider", "Runtime", "Packs", "Shell"]
            label = labels[min(self.current_step, len(labels) - 1)]
            return f"  {' '.join(dots)}   Step {step_num} / {self.total_steps} — {label}"

    class SummaryPanel(Static):
        """Live config summary panel."""

        DEFAULT_CSS = """
        SummaryPanel {
            border: round #22d3ee;
            background: #111827;
            padding: 1 2;
            width: 36;
            height: 100%;
            color: #94a3b8;
        }
        """

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__("", **kwargs)
            self._cfg = cfg
            self._status = "unbound"

        def refresh_from(self, cfg: NavigConfig) -> None:
            self._cfg = cfg
            self.refresh()

        def set_status(self, status: str) -> None:
            self._status = status
            self.refresh()

        def render(self) -> str:  # type: ignore[override]
            cfg = self._cfg
            packs = ", ".join(cfg.capability_packs) if cfg.capability_packs else "—"
            status_color = "#10b981" if self._status == "active" else "#f59e0b"
            lines = [
                "[bold #22d3ee]── Config Preview ──[/bold #22d3ee]",
                "",
                f"[dim]Operator :[/dim]  {cfg.profile_name or '—'}",
                f"[dim]Provider :[/dim]  {cfg.ai_provider}",
                f"[dim]Runtime  :[/dim]  {'local' if cfg.local_runtime_enabled else 'cloud'}",
                f"[dim]Packs    :[/dim]  {packs}",
                f"[dim]Shell    :[/dim]  {'✔' if cfg.shell_integration else '—'}",
                f"[dim]Hooks    :[/dim]  {'✔' if cfg.git_hooks else '—'}",
                f"[dim]Telemetry:[/dim]  {'✔' if cfg.telemetry else '—'}",
                "",
                f"[dim]Status   :[/dim]  [{status_color}]{self._status}[/{status_color}]",
            ]
            return "\n".join(lines)

    class CheckRow(Static):
        """One system-check row: icon + label + optional fix hint."""

        DEFAULT_CSS = """
        CheckRow {
            height: auto;
            padding: 0 1;
        }
        """

        def __init__(self, label: str, **kwargs: Any) -> None:
            super().__init__("", **kwargs)
            self._label = label
            self._state = "pending"
            self._hint = ""

        def set_pending(self) -> None:
            self._state = "pending"
            self._hint = ""
            self._refresh_render()

        def set_pass(self) -> None:
            self._state = "pass"
            self._hint = ""
            self._refresh_render()

        def set_fail(self, hint: str = "") -> None:
            self._state = "fail"
            self._hint = hint
            self._refresh_render()

        def _refresh_render(self) -> None:
            icon_map = {
                "pending": "[yellow]…[/yellow]",
                "pass": "[bold #10b981]✔[/bold #10b981]",
                "fail": "[bold red]✖[/bold red]",
            }
            icon = icon_map.get(self._state, "?")
            text = f"  {icon}  {self._label}"
            if self._hint and self._state == "fail":
                text += f"\n     [dim #64748b]↳ {self._hint}[/dim #64748b]"
            self.update(text)

    # -----------------------------------------------------------------------
    # ConfirmModal
    # -----------------------------------------------------------------------

    class ConfirmModal(ModalScreen):  # type: ignore[type-arg]
        """Ask user to confirm overwriting existing config."""

        DEFAULT_CSS = """
        ConfirmModal {
            align: center middle;
        }
        ConfirmModal > Container {
            width: 60;
            height: 12;
            border: round #22d3ee;
            background: #111827;
            padding: 1 2;
        }
        ConfirmModal Button {
            margin: 1 1;
        }
        """

        def compose(self) -> ComposeResult:
            with Container():
                yield Label(
                    "[bold yellow]⚠ Config already exists[/bold yellow]\n\n"
                    "Overwrite [cyan]~/.navig/navig.json[/cyan]?",
                    id="confirm-msg",
                )
                with Horizontal():
                    yield Button("Overwrite", variant="warning", id="btn-yes")
                    yield Button("Cancel", variant="default", id="btn-no")

        @on(Button.Pressed, "#btn-yes")
        def _yes(self) -> None:
            self.dismiss(True)

        @on(Button.Pressed, "#btn-no")
        def _no(self) -> None:
            self.dismiss(False)

    # -----------------------------------------------------------------------
    # BootScreen
    # -----------------------------------------------------------------------

    class BootScreen(Screen):  # type: ignore[type-arg]
        """Staged reveal: character-by-character logo -> capability bullets -> identity block."""

        BINDINGS = [Binding("enter", "skip", "Skip")]

        DEFAULT_CSS = """
        BootScreen {
            background: #0f172a;
            align: center middle;
        }
        #boot-log {
            width: 72;
            height: 28;
            border: round #22d3ee;
            background: #111827;
            padding: 0 1;
        }
        #boot-skip {
            color: #334155;
            text-align: center;
        }
        """

        def compose(self) -> ComposeResult:
            with Vertical():
                yield RichLog(id="boot-log", markup=True, highlight=False, wrap=False)
                yield Label("  press [bold]Enter[/bold] to skip", id="boot-skip")

        def on_mount(self) -> None:
            self._run_boot_sequence()

        @work(exclusive=True)
        async def _run_boot_sequence(self) -> None:
            log: RichLog = self.query_one("#boot-log", RichLog)
            try:
                # Phase 1: character-by-character logo
                logo_full = "NAVIG"
                for i in range(1, len(logo_full) + 1):
                    log.write(f"[bold #22d3ee]{logo_full[:i]}[/bold #22d3ee]")
                    await asyncio.sleep(0.18)

                await asyncio.sleep(0.25)
                log.write("")
                log.write("[dim]Autonomous Operations Assistant[/dim]")
                log.write("[#334155]────────────────────────────────[/#334155]")
                await asyncio.sleep(0.2)

                # Phase 2: capability bullets
                bullets = [
                    "Initializing operator shell",
                    "Loading runtime modules",
                    "Checking local environment",
                    "Preparing onboarding flow",
                ]
                for bullet in bullets:
                    log.write(f"[#64748b]•[/#64748b] {bullet}")
                    await asyncio.sleep(0.35)

                await asyncio.sleep(0.3)
                log.write("")

                # Phase 3: identity block
                env = detect_environment()
                log.write(
                    "[dim #64748b]NAVIG mesh detected:[/dim #64748b]  [#22d3ee]0 nodes[/#22d3ee]"
                )
                log.write(
                    "[dim #64748b]Operator identity:[/dim #64748b]   [yellow]not registered[/yellow]"
                )
                log.write("")
                log.write(f"[dim]Machine :[/dim]  [#22d3ee]{env['hostname']}[/#22d3ee]")
                log.write(f"[dim]Shell   :[/dim]  {env['shell']}")
                log.write(f"[dim]OS      :[/dim]  {env['os']} / Python {env['python']}")
                log.write("[dim]Mode    :[/dim]  local")
                log.write("[dim]Status  :[/dim]  [yellow]unbound[/yellow]")

                await asyncio.sleep(0.8)
                self.app.push_screen(WelcomeScreen())

            except WorkerCancelled:
                pass  # textual worker cancelled; expected during unmount
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Boot sequence error: {exc}", severity="warning")
                self.app.push_screen(WelcomeScreen())

        def action_skip(self) -> None:
            self.app.push_screen(WelcomeScreen())

    # -----------------------------------------------------------------------
    # WelcomeScreen
    # -----------------------------------------------------------------------

    class WelcomeScreen(Screen):  # type: ignore[type-arg]
        """Brief description + mode selection."""

        DEFAULT_CSS = """
        WelcomeScreen {
            background: #0f172a;
            align: center middle;
        }
        #welcome-panel {
            width: 70;
            border: round #22d3ee;
            background: #111827;
            padding: 1 3;
        }
        #welcome-title {
            color: #22d3ee;
            text-style: bold;
        }
        .welcome-bullet {
            color: #94a3b8;
        }
        #welcome-btns {
            margin-top: 1;
            align: center middle;
        }
        #welcome-btns Button {
            margin: 0 1;
        }
        """

        def compose(self) -> ComposeResult:
            with Vertical(id="welcome-panel"):
                yield Label("NAVIG — Setup Wizard", id="welcome-title")
                yield Label("")
                yield Label(
                    "  ▸ Connect to any server via SSH with a single command",
                    classes="welcome-bullet",
                )
                yield Label(
                    "  ▸ Run remote commands, transfer files, and manage databases",
                    classes="welcome-bullet",
                )
                yield Label(
                    "  ▸ Automate workflows with flows and skills",
                    classes="welcome-bullet",
                )
                yield Label(
                    "  ▸ Converse with an AI assistant that knows your infrastructure",
                    classes="welcome-bullet",
                )
                yield Label("")
                yield Label(
                    "  [dim]Press [bold]Ctrl+C[/bold] at any time to cancel[/dim]",
                    markup=True,
                )
                yield Label("")
                with Horizontal(id="welcome-btns"):
                    yield Button("Advanced Setup  →", variant="primary", id="btn-advanced")
                    yield Button("Quickstart", variant="default", id="btn-quickstart")

        @on(Button.Pressed, "#btn-advanced")
        def _go_advanced(self) -> None:
            self.app.push_screen(SystemChecksScreen())

        @on(Button.Pressed, "#btn-quickstart")
        def _go_quickstart(self) -> None:
            self.app.run_worker(self._do_quickstart(), exclusive=True)

        async def _do_quickstart(self) -> None:
            import functools

            console = get_console()
            cfg_dict = await asyncio.get_running_loop().run_in_executor(
                None, functools.partial(run_quickstart, console)
            )
            try:
                save_config(cfg_dict, DEFAULT_CONFIG_FILE)
                create_workspace_templates(Path(DEFAULT_WORKSPACE_DIR))
                sync_to_env(cfg_dict)
            except OSError as exc:
                self.notify(f"Config save failed: {exc}", severity="error")
                return
            self.app.exit()

    # -----------------------------------------------------------------------
    # SystemChecksScreen
    # -----------------------------------------------------------------------

    class SystemChecksScreen(Screen):  # type: ignore[type-arg]
        """7 real system checks with staggered reveal and inline fix hints."""

        DEFAULT_CSS = """
        SystemChecksScreen {
            background: #0f172a;
            align: center middle;
        }
        #checks-panel {
            width: 70;
            border: round #22d3ee;
            background: #111827;
            padding: 1 2;
        }
        #checks-title {
            color: #22d3ee;
            text-style: bold;
            margin-bottom: 1;
        }
        #checks-footer {
            margin-top: 1;
            align: center middle;
        }
        #checks-footer Button {
            margin: 0 1;
        }
        """

        _CHECK_DEFS: list[tuple] = [
            (
                "Python runtime >= 3.10",
                check_python_version,
                True,
                "Upgrade Python: https://python.org/downloads",
            ),
            (
                "Git installed",
                check_git_installed,
                False,
                "Install git: https://git-scm.com",
            ),
            (
                "Network / DNS",
                check_network,
                False,
                "Check your network connection or firewall",
            ),
            (
                "Disk space (100 MB free)",
                check_disk_space,
                True,
                "Free up disk space before continuing",
            ),
            (
                "Config directory writable (~/.navig)",
                check_config_dir_writable,
                True,
                "Check permissions on your home directory",
            ),
        ]

        def __init__(self, cfg: NavigConfig | None = None, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg or NavigConfig()
            self._critical_failed = False

        def compose(self) -> ComposeResult:
            with Vertical(id="checks-panel"):
                yield Label("System Checks", id="checks-title")
                for idx, (label, *_) in enumerate(self._CHECK_DEFS):
                    import re as _re

                    _safe_id = _re.sub(r"[^a-zA-Z0-9_-]", "", label[:24].replace(" ", "_"))
                    row = CheckRow(label, id=f"check-{_safe_id or str(idx)}")
                    yield row
                yield Label("")
                with Horizontal(id="checks-footer"):
                    yield Button(
                        "Continue  →",
                        variant="primary",
                        id="btn-continue",
                        disabled=True,
                    )
                    yield Button("← Back", variant="default", id="btn-back")

        def on_mount(self) -> None:
            self._run_checks()

        @work(exclusive=True)
        async def _run_checks(self) -> None:
            try:
                rows = list(self.query(CheckRow))
                for (_label, fn, is_critical, hint), row in zip(self._CHECK_DEFS, rows):
                    row.set_pending()
                    await asyncio.sleep(0.05)
                    try:
                        result = fn()
                    except Exception:
                        result = False
                    if result:
                        row.set_pass()
                    else:
                        row.set_fail(hint)
                        if is_critical:
                            self._critical_failed = True

                if self._cfg.local_runtime_enabled:
                    await asyncio.sleep(0.25)
                    try:
                        ok = check_ollama_reachable(self._cfg.local_runtime_host)
                        self.notify(
                            "Ollama: " + ("reachable ✔" if ok else "not reachable"),
                            severity="information" if ok else "warning",
                        )
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical

                btn: Button = self.query_one("#btn-continue", Button)
                btn.disabled = self._critical_failed
            except WorkerCancelled:
                pass  # textual worker cancelled; expected during unmount
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Check runner error: {exc}", severity="warning")
                btn = self.query_one("#btn-continue", Button)
                btn.disabled = False

        @on(Button.Pressed, "#btn-continue")
        def _continue(self) -> None:
            self.app.push_screen(WizardScreen(self._cfg))

        @on(Button.Pressed, "#btn-back")
        def _back(self) -> None:
            self.app.pop_screen()

    # -----------------------------------------------------------------------
    # Wizard step widgets
    # -----------------------------------------------------------------------

    class _WizardStepBase(Vertical):
        """Base class for all step widgets (handles .visible CSS fade-in)."""

        DEFAULT_CSS = """
        _WizardStepBase {
            opacity: 0%;
        }
        _WizardStepBase.visible {
            opacity: 100%;
        }
        """

        def on_mount(self) -> None:
            self.call_after_refresh(lambda: self.add_class("visible"))

    class Step1IdentityWidget(_WizardStepBase):
        DEFAULT_CSS = (
            _WizardStepBase.DEFAULT_CSS
            + """
        Step1IdentityWidget { height: auto; padding: 1 2; }
        Step1IdentityWidget Label { color: #94a3b8; margin-bottom: 0; }
        Step1IdentityWidget Input { margin-bottom: 1; }
        """
        )

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg

        def compose(self) -> ComposeResult:
            yield Label("Operator name (display)")
            yield Input(
                value=self._cfg.profile_name,
                id="inp-profile-name",
                placeholder="operator",
            )
            yield Label("Workspace root")
            yield Input(
                value=self._cfg.workspace_root,
                id="inp-workspace",
                placeholder=str(DEFAULT_WORKSPACE_DIR),
            )
            yield Label("Theme")
            yield Select(
                [("Dark", "dark"), ("Light", "light"), ("System", "system")],
                value=self._cfg.theme,
                id="sel-theme",
            )

        @on(Input.Changed, "#inp-profile-name")
        def _name_changed(self, event: Input.Changed) -> None:
            self._cfg.profile_name = event.value
            self._notify_parent()

        @on(Input.Changed, "#inp-workspace")
        def _ws_changed(self, event: Input.Changed) -> None:
            self._cfg.workspace_root = event.value
            self._notify_parent()

        @on(Select.Changed, "#sel-theme")
        def _theme_changed(self, event: Select.Changed) -> None:
            if event.value and event.value is not Select.BLANK:
                self._cfg.theme = str(event.value)
            self._notify_parent()

        def _notify_parent(self) -> None:
            try:
                self.app.query_one(SummaryPanel).refresh_from(self._cfg)
            except NoMatches:
                pass  # widget not present; skip

    class Step2ProviderWidget(_WizardStepBase):
        DEFAULT_CSS = (
            _WizardStepBase.DEFAULT_CSS
            + """
        Step2ProviderWidget { height: auto; padding: 1 2; }
        Step2ProviderWidget Label { color: #94a3b8; margin-bottom: 0; }
        Step2ProviderWidget RadioSet { margin-bottom: 1; }
        Step2ProviderWidget Input { margin-bottom: 1; }
        """
        )

        _PROVIDERS = ["openrouter", "openai", "anthropic", "groq", "ollama", "none"]
        # _PROVIDERS kept for reference; _ordered_providers drives rendering at runtime.
        _ordered_providers: list[ProviderDef] = []  # populated in __init__
        _detected: dict[str, bool] = {}  # populated in __init__

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg
            self._detected = detect_providers()

            def _sort_key(p: ProviderDef) -> tuple[int, int, str]:
                type_order = {"cloud": 0, "local": 1, "other": 2}
                return (
                    type_order.get(p.type, 9),
                    0 if self._detected.get(p.id) else 1,
                    p.label.lower(),
                )

            self._ordered_providers = sorted(PROVIDER_REGISTRY, key=_sort_key)

        def compose(self) -> ComposeResult:
            yield Label("AI Provider  [dim](✓ configured · ◉ running)[/dim]", markup=True)
            with RadioSet(id="radio-provider"):
                for p in self._ordered_providers:
                    is_det = self._detected.get(p.id, False)
                    if p.type == "local":
                        badge = " ◉" if is_det else ""
                    elif p.type == "cloud":
                        badge = " ✓" if is_det else ""
                    else:
                        badge = ""
                    display = f"{p.label}{badge}"
                    yield RadioButton(display, value=(p.id == self._cfg.ai_provider))
            yield Label("API Key  [dim](stored in vault — not echoed)[/dim]", markup=True)
            yield Input(
                value="",
                placeholder="sk-… or leave blank",
                password=True,
                id="inp-api-key",
            )

        @on(RadioSet.Changed, "#radio-provider")
        def _provider_changed(self, event: RadioSet.Changed) -> None:
            if event.pressed is not None:
                label_plain: str = event.pressed.label.plain  # type: ignore[union-attr]
                # Map displayed label back to provider id via _ordered_providers
                matched = next(
                    (p for p in self._ordered_providers if label_plain.startswith(p.label)),
                    None,
                )
                self._cfg.ai_provider = matched.id if matched else label_plain.strip()
                local_ids = {p.id for p in PROVIDER_REGISTRY if p.type == "local"} | {"none"}
                inp: Input = self.query_one("#inp-api-key", Input)
                inp.display = self._cfg.ai_provider not in local_ids
                self._notify_parent()

        @on(Input.Changed, "#inp-api-key")
        def _key_changed(self, event: Input.Changed) -> None:
            self._cfg.api_key = event.value
            try:
                self.app.query_one(SummaryPanel).refresh_from(self._cfg)
            except NoMatches:
                pass  # widget not present; skip

        def _notify_parent(self) -> None:
            try:
                self.app.query_one(SummaryPanel).refresh_from(self._cfg)
            except NoMatches:
                pass  # widget not present; skip

    class Step3RuntimeWidget(_WizardStepBase):
        DEFAULT_CSS = (
            _WizardStepBase.DEFAULT_CSS
            + """
        Step3RuntimeWidget { height: auto; padding: 1 2; }
        Step3RuntimeWidget Label { color: #94a3b8; }
        Step3RuntimeWidget Input { margin-top: 1; }
        """
        )

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg

        def compose(self) -> ComposeResult:
            yield Label("Local runtime (Ollama / custom LLM server)")
            yield Switch(value=self._cfg.local_runtime_enabled, id="sw-runtime")
            yield Input(
                value=self._cfg.local_runtime_host,
                placeholder="http://localhost:11434",
                id="inp-runtime-host",
                disabled=not self._cfg.local_runtime_enabled,
            )

        @on(Switch.Changed, "#sw-runtime")
        def _toggle(self, event: Switch.Changed) -> None:
            self._cfg.local_runtime_enabled = event.value
            inp: Input = self.query_one("#inp-runtime-host", Input)
            inp.disabled = not event.value
            try:
                self.app.query_one(SummaryPanel).refresh_from(self._cfg)
            except NoMatches:
                pass  # widget not present; skip

        @on(Input.Changed, "#inp-runtime-host")
        def _host_changed(self, event: Input.Changed) -> None:
            self._cfg.local_runtime_host = event.value

    class Step4PacksWidget(_WizardStepBase):
        DEFAULT_CSS = (
            _WizardStepBase.DEFAULT_CSS
            + """
        Step4PacksWidget { height: auto; padding: 1 2; }
        Step4PacksWidget Label { color: #94a3b8; margin-bottom: 1; }
        """
        )

        _PACKS = [("DevOps", "devops"), ("SysOps", "sysops"), ("LifeOps", "lifeops")]

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg

        def compose(self) -> ComposeResult:
            yield Label("Capability packs to activate")
            for display, key in self._PACKS:
                yield Checkbox(display, value=(key in self._cfg.capability_packs), id=f"cb-{key}")

        @on(Checkbox.Changed)
        def _pack_toggled(self, event: Checkbox.Changed) -> None:
            key = event.checkbox.id.replace("cb-", "") if event.checkbox.id else ""
            if event.value:
                if key not in self._cfg.capability_packs:
                    self._cfg.capability_packs.append(key)
            else:
                self._cfg.capability_packs = [p for p in self._cfg.capability_packs if p != key]
            try:
                self.app.query_one(SummaryPanel).refresh_from(self._cfg)
            except NoMatches:
                pass  # widget not present; skip

    class Step5ShellWidget(_WizardStepBase):
        DEFAULT_CSS = (
            _WizardStepBase.DEFAULT_CSS
            + """
        Step5ShellWidget { height: auto; padding: 1 2; }
        Step5ShellWidget .sw-label { color: #94a3b8; }
        Step5ShellWidget .sw-desc  { color: #334155; margin-bottom: 1; }
        """
        )

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg

        def compose(self) -> ComposeResult:
            items = [
                (
                    "sw-shell",
                    "Shell integration",
                    "Adds `navig` to $PATH and sets up completions",
                    "shell_integration",
                ),
                (
                    "sw-update",
                    "Auto-update",
                    "Automatically install patch updates",
                    "auto_update",
                ),
                (
                    "sw-git",
                    "Git hooks",
                    "Run pre-commit safety checks via navig",
                    "git_hooks",
                ),
                (
                    "sw-telemetry",
                    "Telemetry",
                    "Send anonymous usage stats to improve NAVIG",
                    "telemetry",
                ),
            ]
            for sw_id, title, desc, attr in items:
                yield Label(title, classes="sw-label")
                yield Label(f"  {desc}", classes="sw-desc")
                yield Switch(value=getattr(self._cfg, attr), id=sw_id)

        @on(Switch.Changed)
        def _sw_changed(self, event: Switch.Changed) -> None:
            mapping = {
                "sw-shell": "shell_integration",
                "sw-update": "auto_update",
                "sw-git": "git_hooks",
                "sw-telemetry": "telemetry",
            }
            attr = mapping.get(event.switch.id or "")
            if attr:
                setattr(self._cfg, attr, event.value)
            try:
                self.app.query_one(SummaryPanel).refresh_from(self._cfg)
            except NoMatches:
                pass  # widget not present; skip

    # -----------------------------------------------------------------------
    # WizardScreen (5-step controller)
    # -----------------------------------------------------------------------

    class WizardScreen(Screen):  # type: ignore[type-arg]
        """5-step wizard with live SummaryPanel and step progress indicator."""

        DEFAULT_CSS = """
        WizardScreen {
            background: #0f172a;
        }
        #wizard-header {
            height: 3;
            background: #111827;
            border-bottom: solid #1e293b;
            padding: 1 2;
        }
        #wizard-body {
            height: 1fr;
        }
        #wizard-steps {
            width: 1fr;
            padding: 1 2;
        }
        #wizard-summary {
            width: 38;
            padding: 1 1;
        }
        #wizard-footer {
            height: 5;
            background: #111827;
            border-top: solid #1e293b;
            align: right middle;
            padding: 0 2;
        }
        #wizard-footer Button {
            margin: 0 1;
        }
        """

        def __init__(self, cfg: NavigConfig | None = None, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg or NavigConfig()
            self._step = 0
            self._step_ids = ["step-1", "step-2", "step-3", "step-4", "step-5"]

        def compose(self) -> ComposeResult:
            with Horizontal(id="wizard-header"):
                yield StepIndicator(id="step-indicator")
            with Horizontal(id="wizard-body"):
                with ContentSwitcher(initial="step-1", id="wizard-steps"):
                    yield Step1IdentityWidget(self._cfg, id="step-1")
                    yield Step2ProviderWidget(self._cfg, id="step-2")
                    yield Step3RuntimeWidget(self._cfg, id="step-3")
                    yield Step4PacksWidget(self._cfg, id="step-4")
                    yield Step5ShellWidget(self._cfg, id="step-5")
                with Vertical(id="wizard-summary"):
                    yield SummaryPanel(self._cfg, id="summary-panel")
            with Horizontal(id="wizard-footer"):
                yield Button("← Back", id="btn-back", variant="default")
                yield Button("Next  →", id="btn-next", variant="primary")

        def on_mount(self) -> None:
            self._sync_nav_buttons()

        def _sync_nav_buttons(self) -> None:
            ind: StepIndicator = self.query_one("#step-indicator", StepIndicator)
            ind.current_step = self._step
            btn_next: Button = self.query_one("#btn-next", Button)
            btn_next.label = "Finish  ✔" if self._step == 4 else "Next  →"  # type: ignore[assignment]
            btn_back: Button = self.query_one("#btn-back", Button)
            btn_back.disabled = self._step == 0

        @on(Button.Pressed, "#btn-next")
        def _next(self) -> None:
            if not self._validate_current_step():
                return
            if self._step < 4:
                self._step += 1
                sw: ContentSwitcher = self.query_one("#wizard-steps", ContentSwitcher)
                sw.current = self._step_ids[self._step]
                self._sync_nav_buttons()
            else:
                self.app.push_screen(ReviewScreen(self._cfg))

        @on(Button.Pressed, "#btn-back")
        def _back(self) -> None:
            if self._step > 0:
                self._step -= 1
                sw: ContentSwitcher = self.query_one("#wizard-steps", ContentSwitcher)
                sw.current = self._step_ids[self._step]
                self._sync_nav_buttons()
            else:
                self.app.pop_screen()

        def _validate_current_step(self) -> bool:
            if self._step == 0:
                if not self._cfg.profile_name.strip():
                    self.notify("Operator name cannot be empty.", severity="warning")
                    return False
            if self._step == 1:
                if self._cfg.ai_provider not in ("ollama", "none") and not self._cfg.api_key:
                    self.notify(
                        "No API key entered. You can add one later via `navig ai providers`.",
                        severity="warning",
                        timeout=4,
                    )
            return True

    # -----------------------------------------------------------------------
    # ReviewScreen
    # -----------------------------------------------------------------------

    class ReviewScreen(Screen):  # type: ignore[type-arg]
        """Show SummaryPanel with Back / Confirm buttons."""

        DEFAULT_CSS = """
        ReviewScreen {
            background: #0f172a;
            align: center middle;
        }
        #review-panel {
            width: 70;
            border: round #22d3ee;
            background: #111827;
            padding: 1 3;
        }
        #review-title {
            color: #22d3ee;
            text-style: bold;
            margin-bottom: 1;
        }
        #review-btns {
            margin-top: 1;
            align: center middle;
        }
        #review-btns Button {
            margin: 0 1;
        }
        """

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg

        def compose(self) -> ComposeResult:
            with Vertical(id="review-panel"):
                yield Label("Review Configuration", id="review-title")
                yield SummaryPanel(self._cfg, id="review-summary")
                yield Label("")
                with Horizontal(id="review-btns"):
                    yield Button("Confirm & Install  ✔", variant="primary", id="btn-confirm")
                    yield Button("← Edit", variant="default", id="btn-back")

        def on_mount(self) -> None:
            if DEFAULT_CONFIG_FILE.exists():
                self.app.push_screen(ConfirmModal(), self._handle_confirm_modal)

        def _handle_confirm_modal(self, result: bool) -> None:
            if not result:
                self.app.pop_screen()

        @on(Button.Pressed, "#btn-confirm")
        def _confirm(self) -> None:
            self.app.push_screen(FinalScreen(self._cfg))

        @on(Button.Pressed, "#btn-back")
        def _back(self) -> None:
            self.app.pop_screen()

    # -----------------------------------------------------------------------
    # FinalScreen
    # -----------------------------------------------------------------------

    class FinalScreen(Screen):  # type: ignore[type-arg]
        """Mission-ready summary + animated operator registration moment."""

        DEFAULT_CSS = """
        FinalScreen {
            background: #0f172a;
            align: center middle;
        }
        #final-outer {
            width: 70;
        }
        #final-panel {
            border: round #22d3ee;
            background: #111827;
            padding: 1 3;
            height: auto;
        }
        #final-title {
            color: #22d3ee;
            text-style: bold;
        }
        #final-log {
            height: 12;
            margin-top: 1;
        }
        #final-footer {
            margin-top: 1;
            align: center middle;
        }
        """

        def __init__(self, cfg: NavigConfig, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._cfg = cfg

        def compose(self) -> ComposeResult:
            env = detect_environment()
            packs_str = "  ".join(f"{p.capitalize()} ✔" for p in self._cfg.capability_packs) or "—"
            summary_text = (
                f"[bold #22d3ee]NAVIG — Setup Complete[/bold #22d3ee]\n"
                f"\n"
                f"[dim]Operator [/dim] : {self._cfg.profile_name}\n"
                f"[dim]Machine  [/dim] : {env['hostname']}\n"
                f"[dim]Config   [/dim] : [cyan]~/.navig/navig.json[/cyan]\n"
                f"[dim]Provider [/dim] : {self._cfg.ai_provider}\n"
                f"[dim]Packs    [/dim] : {packs_str}\n"
                f"[dim]Status   [/dim] : [yellow]unbound[/yellow]"
            )
            with Vertical(id="final-outer"), Vertical(id="final-panel"):
                yield Static(summary_text, id="final-summary", markup=True)
                yield RichLog(id="final-log", markup=True, highlight=False, wrap=False)
                with Horizontal(id="final-footer"):
                    yield Button("Exit", variant="primary", id="btn-exit")
                    yield Button(
                        "Retry write",
                        variant="warning",
                        id="btn-retry",
                        display=False,
                    )

        def on_mount(self) -> None:
            self._run_registration()

        @work(exclusive=True)
        async def _run_registration(self) -> None:
            log: RichLog = self.query_one("#final-log", RichLog)
            retry_btn: Button = self.query_one("#btn-retry", Button)
            try:
                cfg_dict = build_config_dict(self._cfg)

                # Vault-store API key if provided
                if self._cfg.api_key:
                    vault_id = _store_in_vault(
                        self._cfg.ai_provider,
                        "api_key",
                        self._cfg.api_key,
                        "api_key",
                    )
                    if vault_id:
                        cfg_dict["auth"]["profiles"][self._cfg.ai_provider] = {
                            "type": "api-key",
                            "vault_id": vault_id,
                        }

                log.write("[dim]Writing configuration…[/dim]")
                await asyncio.sleep(0.15)
                try:
                    save_config(cfg_dict, DEFAULT_CONFIG_FILE)
                except OSError as exc:
                    self.notify(f"Config write failed: {exc}", severity="error")
                    retry_btn.display = True
                    return

                log.write("[bold #10b981]✔[/bold #10b981] Config sealed")

                await asyncio.sleep(0.1)
                create_workspace_templates(Path(self._cfg.workspace_root))
                log.write("[bold #10b981]✔[/bold #10b981] Workspace initialized")

                await asyncio.sleep(0.1)
                sync_to_env(cfg_dict)
                log.write("[bold #10b981]✔[/bold #10b981] Runtime linked")

                # json round-trip verify
                raw = DEFAULT_CONFIG_FILE.read_text(encoding="utf-8")
                json.loads(raw)

                # Animate status → active
                await asyncio.sleep(0.5)
                summary_widget: Static = self.query_one("#final-summary", Static)
                env = detect_environment()
                packs_str = (
                    "  ".join(f"{p.capitalize()} ✔" for p in self._cfg.capability_packs) or "—"
                )
                summary_widget.update(
                    f"[bold #22d3ee]NAVIG — Setup Complete[/bold #22d3ee]\n"
                    f"\n"
                    f"[dim]Operator [/dim] : {self._cfg.profile_name}\n"
                    f"[dim]Machine  [/dim] : {env['hostname']}\n"
                    f"[dim]Config   [/dim] : [cyan]~/.navig/navig.json[/cyan]\n"
                    f"[dim]Provider [/dim] : {self._cfg.ai_provider}\n"
                    f"[dim]Packs    [/dim] : {packs_str}\n"
                    f"[dim]Status   [/dim] : [bold #10b981]active[/bold #10b981]"
                )

                await asyncio.sleep(0.3)
                log.write(
                    f"\n[bold #10b981]✔ Operator registered[/bold #10b981]\n"
                    f"[bold #22d3ee]Welcome, {self._cfg.profile_name}.[/bold #22d3ee]\n"
                    f"[dim]NAVIG is ready.[/dim]\n\n"
                    f"[dim]Suggested next commands:[/dim]\n"
                    f"  [cyan]navig doctor[/cyan]\n"
                    f"  [cyan]navig status[/cyan]\n"
                    f"  [cyan]navig config show[/cyan]"
                )

            except WorkerCancelled:
                pass  # textual worker cancelled; expected during unmount
            except Exception as exc:  # noqa: BLE001
                self.notify(f"Registration error: {exc}", severity="error")
                retry_btn.display = True

        @on(Button.Pressed, "#btn-exit")
        def _exit(self) -> None:
            self.app.exit()

        @on(Button.Pressed, "#btn-retry")
        def _retry(self) -> None:
            retry_btn: Button = self.query_one("#btn-retry", Button)
            retry_btn.display = False
            self._run_registration()

    # -----------------------------------------------------------------------
    # NavigOnboardingApp
    # -----------------------------------------------------------------------

    class NavigOnboardingApp(App):  # type: ignore[type-arg]
        """Production-grade animated onboarding TUI."""

        TITLE = "NAVIG Setup"
        SUB_TITLE = "Autonomous Operations Assistant"
        BINDINGS = [Binding("ctrl+c", "quit", "Quit", priority=True)]

        CSS = """
        Screen { background: #0f172a; }

        .muted { color: #64748b; }
        .step-dot-active  { color: #22d3ee; }
        .step-dot-done    { color: #10b981; }
        .step-dot-pending { color: #334155; }

        .brand-panel {
            border: round #22d3ee;
            background: #111827;
            padding: 1 2;
        }

        Step1IdentityWidget { opacity: 0%; }
        Step1IdentityWidget.visible { opacity: 100%; }
        Step2ProviderWidget { opacity: 0%; }
        Step2ProviderWidget.visible { opacity: 100%; }
        Step3RuntimeWidget { opacity: 0%; }
        Step3RuntimeWidget.visible { opacity: 100%; }
        Step4PacksWidget { opacity: 0%; }
        Step4PacksWidget.visible { opacity: 100%; }
        Step5ShellWidget { opacity: 0%; }
        Step5ShellWidget.visible { opacity: 100%; }

        Button.-primary {
            background: #22d3ee;
            color: #0f172a;
        }
        Button.-primary:hover { background: #38bdf8; }
        Input { border: tall #1e293b; background: #1e293b; }
        Input:focus { border: tall #22d3ee; }
        RadioSet { background: #111827; border: round #1e293b; padding: 0 1; }
        Checkbox { background: #111827; }
        Switch.-on .switch--slider { background: #22d3ee; }
        Select { background: #1e293b; }
        """

        def on_mount(self) -> None:
            self.push_screen(BootScreen())


# ---------------------------------------------------------------------------
# _run_onboard_rich — fallback rich/prompt flow (all original logic preserved)
# ---------------------------------------------------------------------------


def _run_onboard_rich(flow: str = "auto", non_interactive: bool = False) -> None:
    """Original rich/prompt onboarding flow — used when Textual unavailable."""
    console = get_console()
    if not console:
        print("Rich library not installed. Run: pip install rich")
        return

    print_banner(console)
    console.print("[dim]────────────────────────────────────────────────────────────[/dim]")
    console.print(
        "[#2c8bb7]▸[/#2c8bb7] [bold]Connect[/bold] to any server via SSH with a single command"
    )
    console.print(
        "[#2c8bb7]▸[/#2c8bb7] [bold]Run[/bold] remote commands, transfer files, and manage databases"
    )
    console.print(
        "[#2c8bb7]▸[/#2c8bb7] [bold]Automate[/bold] workflows and repeat tasks with flows and skills"
    )
    console.print(
        "[#2c8bb7]▸[/#2c8bb7] [bold]Converse[/bold] with an AI assistant that knows your infrastructure"
    )
    console.print(
        "[#2c8bb7]▸[/#2c8bb7] [bold]Monitor[/bold] servers, logs, and health from one terminal"
    )
    console.print("[dim]────────────────────────────────────────────────────────────[/dim]")
    console.print(
        "[dim]Press Ctrl+C at any time to cancel · Run with --skip to exit immediately[/dim]\n"
    )

    try:
        if DEFAULT_CONFIG_FILE.exists():
            console.print(
                f"[yellow]⚠ Configuration already exists at {DEFAULT_CONFIG_FILE}[/yellow]"
            )
            if not Confirm.ask("Overwrite existing configuration?", default=False):
                console.print("[dim]Onboarding cancelled.[/dim]")
                return

        if non_interactive:
            console.print("[dim]Running in non-interactive mode with defaults...[/dim]")
            config: dict[str, Any] = {
                "meta": {
                    "version": "1.0.0",
                    "created": datetime.now().isoformat(),
                    "onboarding_flow": "non-interactive",
                },
                "agents": {
                    "defaults": {
                        "workspace": str(DEFAULT_WORKSPACE_DIR),
                        "model": "openrouter",
                        "typing_mode": "instant",
                    }
                },
                "auth": {"profiles": {}},
                "channels": {},
            }
        else:
            if flow == "auto":
                console.print("How would you like to set up NAVIG?")
                console.print(
                    "  [bold green]1. Quickstart[/bold green] - Get started quickly with sensible defaults"
                )
                console.print(
                    "  [bold blue]2. Advanced[/bold blue] - Full configuration with all options"
                )
                choice = Prompt.ask("Select setup mode", choices=["1", "2"], default="1")
                flow = "quickstart" if choice == "1" else "manual"

            if flow == "quickstart":
                config = run_quickstart(console)
            else:
                config = run_manual(console, non_interactive=non_interactive)

        console.print("\n[bold]Saving configuration...[/bold]")
        save_config(config, DEFAULT_CONFIG_FILE, console)

        workspace_path = Path(config["agents"]["defaults"]["workspace"])
        console.print("\n[bold]Creating workspace templates...[/bold]")
        create_workspace_templates(workspace_path, console)

        console.print("\n[bold]Syncing to environment...[/bold]")
        sync_to_env(config, console)

        try:
            from navig.commands.init import _prompt_local_discovery

            _prompt_local_discovery(config_dir())
        except Exception:  # noqa: BLE001
            pass

        console.print()
        console.rule("[bold #2c8bb7]✅ Onboarding Complete![/bold #2c8bb7]", style="#2c8bb7")

        summary = Table(title="Configuration Summary", show_header=False, border_style="#2c8bb7")
        summary.add_column("Setting", style="#2c8bb7")
        summary.add_column("Value", style="white")
        summary.add_row("Config File", str(DEFAULT_CONFIG_FILE))
        summary.add_row("Workspace", config["agents"]["defaults"]["workspace"])
        summary.add_row("AI Provider", config["agents"]["defaults"]["model"])
        summary.add_row("Typing Mode", config["agents"]["defaults"].get("typing_mode", "instant"))
        telegram_cfg = config.get("channels", {}).get("telegram", {})
        if telegram_cfg.get("enabled"):
            summary.add_row("Telegram", "Enabled ✓")
        console.print(summary)

        console.print("\n[bold]Next Steps:[/bold]")
        console.print("  1. Add a host:        [#2c8bb7]navig host add myserver[/#2c8bb7]")
        console.print(
            "  2. Configure AI:      [#2c8bb7]navig ai providers --add openrouter[/#2c8bb7]"
        )
        console.print("  3. Inspect setup:     [#2c8bb7]navig init --status[/#2c8bb7]")
        console.print()

        cheat = Table(
            title="Quick Reference",
            border_style="#2c8bb7",
            show_lines=False,
            expand=False,
        )
        cheat.add_column("Command", style="#2c8bb7", no_wrap=True, width=22)
        cheat.add_column("What it does", style="white")
        cheat.add_column("Example", style="dim", no_wrap=True)
        cheat.add_row("navig host add", "Connect a new server", "navig host add prod")
        cheat.add_row("navig run", "Run a remote command", "navig run 'df -h'")
        cheat.add_row("navig file", "Transfer files", "navig file upload ./app /srv")
        cheat.add_row("navig db", "Database operations", "navig db query 'SELECT 1'")
        cheat.add_row("navig flow", "Automate a workflow", "navig flow run deploy")
        cheat.add_row("navig init --status", "Show readiness", "navig init --status")
        cheat.add_row("navig doctor", "Self-diagnostics", "navig doctor")
        console.print(cheat)

        console.print()
        console.rule("[dim]Tips[/dim]", style="dim")
        console.print(
            "[dim]▸ Run [/dim][#2c8bb7]navig <command> --help[/#2c8bb7][dim] for full options on any command.[/dim]"
        )
        console.print(
            "[dim]▸ Use [/dim][#2c8bb7]navig help <topic>[/#2c8bb7][dim] to explore command groups quickly.[/dim]"
        )
        console.print(
            "[dim]▸ Run [/dim][#2c8bb7]navig doctor[/#2c8bb7][dim] if anything looks wrong.[/dim]"
        )
        console.print(
            "[dim]▸ Settings live in [/dim][#2c8bb7]~/.navig/navig.json[/#2c8bb7][dim] — edit anytime.[/dim]"
        )
        console.print("[dim]▸ Press Ctrl+C during any prompt to cancel without saving.[/dim]\n")

    except KeyboardInterrupt:
        console.print("\n[yellow]Onboarding cancelled.[/yellow]")
    except Exception as exc:  # noqa: BLE001
        err_msg = str(exc) or type(exc).__name__
        console.print(f"\n[red]✗ Onboarding failed:[/red] {err_msg}")
        console.print("[dim]Re-run with --debug or check logs for details.[/dim]")


def run_quickstart(console: ConsoleType) -> dict[str, Any]:
    """
    Run the quickstart onboarding flow.

    Minimal prompts, auto-generates sensible defaults.
    """
    if not console:
        return {}
    console.print("\n[bold green]⚡ Quickstart Setup[/bold green]")
    console.print("We'll set up NAVIG with sensible defaults in just a few steps.\n")

    config = {
        "meta": {
            "version": "1.0.0",
            "created": datetime.now().isoformat(),
            "onboarding_flow": "quickstart",
        },
        "agents": {
            "defaults": {
                "workspace": str(DEFAULT_WORKSPACE_DIR),
                "model": "openrouter",
                "typing_mode": "instant",
            }
        },
        "auth": {"profiles": {}},
        "channels": {},
    }

    # Step 1: AI Provider
    console.print("[#2c8bb7]\u25b8[/#2c8bb7] [#2c8bb7]\\[1/3][/#2c8bb7] [bold]AI Provider[/bold]")
    console.print("Which AI provider do you want to use?\n")

    detected = detect_providers()
    ordered_providers, default_idx = render_provider_menu(detected, console)
    total = len(ordered_providers)

    raw_choice = Prompt.ask(
        f"Select provider [1-{total}]",
        default=str(default_idx),
    )
    try:
        choice_idx = int(raw_choice.strip()) - 1
        if not (0 <= choice_idx < total):
            raise ValueError
    except ValueError:
        console.print(f"[yellow]Invalid choice — using default ({default_idx}).[/yellow]")
        choice_idx = default_idx - 1

    selected_provider = ordered_providers[choice_idx]
    provider_id, provider_env_var = _handle_provider_selection(selected_provider, detected, console)
    config["agents"]["defaults"]["model"] = provider_id
    config["agents"]["defaults"]["ai_provider_env_var"] = provider_env_var

    if provider_id not in ("none", "", "ollama", "lmstudio", "jan", "localai"):
        # Cloud provider — profile entry references vault, never raw key
        config["auth"]["profiles"][provider_id] = {"type": "api-key"}

    # Step 2: Telegram Bot (optional)
    console.print(
        "\n[#2c8bb7]\u25b8[/#2c8bb7] [#2c8bb7]\\[2/3][/#2c8bb7] [bold]Telegram Bot (optional)[/bold]"
    )
    setup_telegram = Confirm.ask("Do you want to set up a Telegram bot?", default=False)

    if setup_telegram:
        console.print("\n[dim]Get a bot token from @BotFather on Telegram[/dim]")
        bot_token = Prompt.ask("Telegram bot token", password=True, default="")

        if bot_token:
            console.print("\n[dim]Get your user ID from @userinfobot on Telegram[/dim]")
            user_id = Prompt.ask("Your Telegram user ID", default="")

            tg_cfg: dict[str, Any] = {
                "enabled": True,
                "allowed_users": [int(user_id)] if user_id.isdigit() else [],
            }
            vault_id = _store_in_vault("telegram", "bot_token", bot_token, "token", console)
            if vault_id:
                tg_cfg["bot_token_vault_id"] = vault_id
                console.print("[green]✓ Telegram token saved to vault[/green]")
            else:
                console.print("[yellow]⚠ Telegram token could not be vaulted — skipped[/yellow]")
            config["channels"]["telegram"] = tg_cfg

    # Step 3: Workspace
    console.print("\n[#2c8bb7]\u25b8[/#2c8bb7] [#2c8bb7]\\[3/3][/#2c8bb7] [bold]Workspace[/bold]")
    console.print(f"Default workspace: [#2c8bb7]{DEFAULT_WORKSPACE_DIR}[/#2c8bb7]")

    use_default = Confirm.ask("Use default workspace location?", default=True)
    if not use_default:
        requested_workspace = Prompt.ask("Enter workspace path", default=str(DEFAULT_WORKSPACE_DIR))
        console.print(
            "[yellow]Personal/state workspace files are always managed at "
            f"{DEFAULT_WORKSPACE_DIR}[/yellow]"
        )
        console.print(
            f"[dim]Requested path '{requested_workspace}' is treated as project context only.[/dim]"
        )

    return config


def run_manual(console: ConsoleType, non_interactive: bool = False) -> dict[str, Any]:
    """
    Run the manual/advanced onboarding flow.

    Full configuration prompts for power users.
    """
    console.print("\n[bold #2c8bb7]🔧 Advanced Setup[/bold #2c8bb7]")
    console.print("Let's configure NAVIG with all available options.\n")

    config = {
        "meta": {
            "version": "1.0.0",
            "created": datetime.now().isoformat(),
            "onboarding_flow": "manual",
        },
        "agents": {
            "defaults": {
                "workspace": str(DEFAULT_WORKSPACE_DIR),
                "model": "openrouter",
                "typing_mode": "instant",
                "typing_interval": 4.0,
                "max_history": 20,
            }
        },
        "auth": {"profiles": {}},
        "channels": {},
        "commands": {
            "confirm_destructive": True,
            "timeout_seconds": 60,
        },
    }

    # Section 1: Workspace Configuration
    if not non_interactive:
        console.print("[dim][1/5] Press Enter to continue, or Ctrl+C to cancel...[/dim]")
        input()
    console.print(Panel("[bold]Section 1: Workspace Configuration[/bold]", border_style="#2c8bb7"))

    requested_workspace = Prompt.ask("Workspace directory", default=str(DEFAULT_WORKSPACE_DIR))
    if requested_workspace != str(DEFAULT_WORKSPACE_DIR):
        console.print(
            "[yellow]Personal/state workspace files are always managed at "
            f"{DEFAULT_WORKSPACE_DIR}[/yellow]"
        )
        console.print(
            f"[dim]Requested path '{requested_workspace}' is treated as project context only.[/dim]"
        )

    # Section 2: AI Provider Configuration
    if not non_interactive:
        console.print("[dim][2/5] Press Enter to continue, or Ctrl+C to cancel...[/dim]")
        input()
    console.print(
        Panel("[bold]Section 2: AI Provider Configuration[/bold]", border_style="#2c8bb7")
    )

    detected = detect_providers()
    ordered_providers, default_idx = render_provider_menu(detected, console)
    total = len(ordered_providers)

    raw_choice = Prompt.ask(
        f"Primary AI provider [1-{total}]",
        default=str(default_idx),
    )
    try:
        choice_idx = int(raw_choice.strip()) - 1
        if not (0 <= choice_idx < total):
            raise ValueError
    except ValueError:
        console.print(f"[yellow]Invalid choice — using default ({default_idx}).[/yellow]")
        choice_idx = default_idx - 1

    selected_provider = ordered_providers[choice_idx]
    primary_provider_id, primary_env_var = _handle_provider_selection(
        selected_provider, detected, console
    )
    config["agents"]["defaults"]["model"] = primary_provider_id
    config["agents"]["defaults"]["ai_provider_env_var"] = primary_env_var

    if primary_provider_id not in ("none", "", "ollama", "lmstudio", "jan", "localai"):
        config["auth"]["profiles"][primary_provider_id] = {"type": "api-key"}

    # Optionally configure additional cloud providers
    configure_more = Confirm.ask("Configure additional providers?", default=False)

    while configure_more:
        _, extra_default = render_provider_menu(detected, console)
        extra_raw = Prompt.ask(
            f"  Additional provider [1-{total}]",
            default=str(extra_default),
        )
        try:
            extra_idx = int(extra_raw.strip()) - 1
            if not (0 <= extra_idx < total):
                raise ValueError
        except ValueError:
            extra_idx = extra_default - 1

        extra_pdef = ordered_providers[extra_idx]
        extra_id, _ = _handle_provider_selection(extra_pdef, detected, console)
        if extra_id and extra_id not in (
            "none",
            "",
            "ollama",
            "lmstudio",
            "jan",
            "localai",
        ):
            config["auth"]["profiles"][extra_id] = {"type": "api-key"}

        configure_more = Confirm.ask("Configure another provider?", default=False)

    # Section 3: Agent Settings
    if not non_interactive:
        console.print("[dim][3/5] Press Enter to continue, or Ctrl+C to cancel...[/dim]")
        input()
    console.print(Panel("[bold]Section 3: Agent Settings[/bold]", border_style="#2c8bb7"))

    typing_mode = Prompt.ask(
        "Typing indicator mode",
        choices=["instant", "message", "never"],
        default="instant",
    )
    config["agents"]["defaults"]["typing_mode"] = typing_mode

    typing_interval = Prompt.ask("Typing indicator refresh interval (seconds)", default="4.0")
    try:
        config["agents"]["defaults"]["typing_interval"] = float(typing_interval)
    except ValueError:
        config["agents"]["defaults"]["typing_interval"] = 4.0

    max_history = Prompt.ask("Max conversation history messages", default="20")
    try:
        config["agents"]["defaults"]["max_history"] = int(max_history)
    except ValueError:
        config["agents"]["defaults"]["max_history"] = 20

    # Section 4: Channel Configuration
    if not non_interactive:
        console.print("[dim][4/5] Press Enter to continue, or Ctrl+C to cancel...[/dim]")
        input()
    console.print(Panel("[bold]Section 4: Channel Configuration[/bold]", border_style="#2c8bb7"))

    # Telegram
    setup_telegram = Confirm.ask("Configure Telegram bot?", default=False)
    if setup_telegram:
        bot_token = Prompt.ask("Telegram bot token", password=True)
        user_ids = Prompt.ask("Allowed user IDs (comma-separated)", default="")

        tg_cfg_manual: dict[str, Any] = {
            "enabled": True,
            "allowed_users": [
                int(uid.strip()) for uid in user_ids.split(",") if uid.strip().isdigit()
            ],
            "typing_mode": typing_mode,
        }
        vault_id = _store_in_vault("telegram", "bot_token", bot_token, "token", console)
        if vault_id:
            tg_cfg_manual["bot_token_vault_id"] = vault_id
            console.print("[green]✓ Telegram token saved to vault[/green]")
        else:
            console.print("[yellow]⚠ Telegram token could not be vaulted — skipped[/yellow]")
        config["channels"]["telegram"] = tg_cfg_manual

    # Discord (placeholder)
    setup_discord = Confirm.ask("Configure Discord bot?", default=False)
    if setup_discord:
        console.print("[yellow]Discord support coming soon![/yellow]")
        config["channels"]["discord"] = {"enabled": False}

    # Section 5: Command Settings
    if not non_interactive:
        console.print("[dim][5/5] Press Enter to continue, or Ctrl+C to cancel...[/dim]")
        input()
    console.print(Panel("[bold]Section 5: Command Settings[/bold]", border_style="#2c8bb7"))

    confirm_destructive = Confirm.ask(
        "Confirm destructive operations (delete, restart, etc.)?", default=True
    )
    config["commands"]["confirm_destructive"] = confirm_destructive

    timeout = Prompt.ask("Command timeout (seconds)", default="60")
    try:
        config["commands"]["timeout_seconds"] = int(timeout)
    except ValueError:
        config["commands"]["timeout_seconds"] = 60

    return config


def create_workspace_templates(workspace_path: Path, console: ConsoleType = None) -> None:
    """Create workspace template files (Agent-style bootstrap files)."""
    templates = {
        "AGENTS.md": """---
summary: Operating instructions and memory for the NAVIG agent
read_when: Every session start
status: active
---

# NAVIG Agent Instructions

You are NAVIG, an AI-powered server management assistant. Your role is to help users manage their remote servers through natural language.

## Core Responsibilities

1. **Understand Intent**: Parse natural language queries and identify the user's goal
2. **Execute Commands**: Translate requests into NAVIG CLI commands
3. **Provide Context**: Explain what you're doing and why
4. **Stay Safe**: Warn about destructive operations and confirm before executing

## Available Skills

- **Server Management**: Check disk space, memory, CPU usage
- **Docker Operations**: List, start, stop, restart containers
- **Database Queries**: Run SQL queries, list databases/tables, backups
- **HestiaCP Management**: User management, domain configuration

## Communication Style

- Be concise but informative
- Use emoji sparingly for visual cues
- Format output clearly (tables, lists)
- Proactively suggest related actions

## Memory

Store important context here as you learn about the user's environment:

- Servers: (discovered during conversations)
- Common tasks: (patterns you notice)
- User preferences: (learned over time)
""",
        "BOOTSTRAP.md": """---
summary: First-run setup ritual (deleted after completion)
read_when: First session only
status: active
---

# Welcome to NAVIG! 🚀

This is your first time using NAVIG. Let me help you get started.

## Quick Setup Checklist

1. [ ] Verify AI provider is configured (`navig ai providers`)
2. [ ] Add at least one host (`navig host add`)
3. [ ] Test connection (`navig host test`)
4. [ ] Try a simple query ("Show disk space")

## First Steps

1. **Add a Host**:
   ```bash
   navig host add myserver --host example.com --user admin
   ```

2. **Set Active Host**:
   ```bash
   navig host use myserver
   ```

3. **Test Connection**:
   ```bash
   navig host test
   ```

4. **Ask Me Anything**:
   Just type naturally: "How much disk space is left?"

---
*This file will be removed after your first successful interaction.*
""",
        "IDENTITY.md": """---
summary: Agent name, personality, and visual identity
read_when: Every session start
status: active
---

# Agent Identity

**Name**: NAVIG
**Full Name**: Navigation AI Virtual Infrastructure Guardian
**Alias**: The Kraken
**Emoji**: 🦑
**Avatar**: A kraken/squid symbol - relentless guardian of the deep

## Personality Traits

- **Vigilant**: Watches relentlessly, catching crumbs before they compound
- **Decisive**: Acts swiftly, no hedging or fluff
- **Protective**: Guards infrastructure and personal workflows
- **Ruthless**: Against recurring failures ("crumbs")
- **Direct**: Short sentences, impact over process

## Voice

- First person ("Done. State preserved.")
- Active voice, no hedging ("Failed" not "Unfortunately...")
- Technical when needed, plain language otherwise
- Lead with impact, not process
""",
        "SOUL.md": """---
summary: Persona, boundaries, and ethical guidelines
read_when: Every session start
status: active
---

# Agent Soul

## Core Values

1. **Safety First**: Never execute destructive commands without confirmation
2. **Transparency**: Always explain what you're doing
3. **Privacy**: Don't log or transmit sensitive data unnecessarily
4. **Reliability**: Admit when you're uncertain

## Boundaries

### I Will:
- Execute read-only commands without confirmation
- Run safe management operations (status checks, listings)
- Help troubleshoot and diagnose issues
- Provide explanations and documentation

### I Will Ask First Before:
- Deleting files or data
- Restarting services
- Modifying configurations
- Running commands on production systems

### I Will Never:
- Execute arbitrary code without review
- Bypass security measures
- Store credentials insecurely
- Operate outside my authorized scope

## Ethical Guidelines

- Respect user privacy
- Be honest about capabilities and limitations
- Don't pretend to have access you don't have
- Recommend security best practices
""",
        "TOOLS.md": """---
summary: Available tools and usage conventions
read_when: When executing commands
status: active
---

# Available Tools

## NAVIG CLI Commands

### Host Management
- `navig host list` - List all configured hosts
- `navig host add <name>` - Add a new host
- `navig host use <name>` - Set active host
- `navig host test [name]` - Test connection
- `navig host show [name]` - Show host details

### Remote Execution
- `navig run "<command>"` - Run command on active host
- `navig run "<command>" --host <name>` - Run on specific host

### Docker
- `navig docker ps` - List containers
- `navig docker logs <container>` - Show logs
- `navig docker restart <container>` - Restart container

### Database
- `navig db list` - List databases
- `navig db tables <database>` - List tables
- `navig db query "<sql>"` - Run SQL query

### HestiaCP
- `navig hestia users` - List users
- `navig hestia domains <user>` - List domains
- `navig hestia backup <user>` - Create backup

## Tool Conventions

1. Always use `--plain` flag when parsing output programmatically
2. Prefer read-only commands first to gather information
3. Chain commands logically (switch host → run command → parse output)
4. Handle errors gracefully and inform the user
""",
        "USER.md": """---
summary: User profile and preferences
read_when: Every session start
status: active
---

# User Profile

## Identity

- **Name**: (Your name here)
- **Preferred Address**: (How you'd like to be called)
- **Timezone**: (Your timezone)

## Preferences

### Communication
- **Verbosity**: normal (options: brief, normal, detailed)
- **Emoji Usage**: moderate
- **Technical Level**: intermediate

### Operations
- **Confirm Destructive**: yes
- **Auto-backup**: yes (before changes)
- **Default Host**: (your primary server)

## Notes

Add any personal notes or preferences here:

- Favorite commands
- Common workflows
- Server naming conventions
""",
        "HEARTBEAT.md": """---
summary: Background task instructions
read_when: For scheduled/automated tasks
status: draft
---

# Heartbeat Tasks

Background and scheduled task instructions for the agent.

## Monitoring Tasks

### Health Checks
- Check disk space on critical servers (warn at 80%)
- Monitor container status
- Verify backup completion

### Reporting
- Daily summary of server health
- Weekly resource usage trends
- Alert on anomalies

## Task Configuration

```yaml
tasks:
  disk_check:
    interval: 1h
    command: navig run "df -h"
    alert_threshold: 80%

  container_health:
    interval: 5m
    command: navig docker ps
    alert_on: exited, unhealthy
```

## Notes

Heartbeat tasks are optional and require additional setup.
See documentation for enabling automated monitoring.
""",
    }

    requested_workspace = workspace_path.expanduser()
    canonical_workspace = USER_WORKSPACE_DIR
    canonical_workspace.mkdir(parents=True, exist_ok=True)

    if requested_workspace.resolve() != canonical_workspace.resolve():
        if console:
            console.print(
                "[yellow]Workspace ownership policy:[/yellow] personal/state files are created in "
                f"[cyan]{canonical_workspace}[/cyan] only."
            )
            console.print(
                f"[dim]Requested path {requested_workspace} will not receive personal file copies.[/dim]"
            )

    try:
        duplicates = detect_project_workspace_duplicates(project_root=Path.cwd())
    except Exception:
        duplicates = None
    if duplicates and console:
        summary = summarize_duplicates(duplicates)
        console.print(
            "[yellow]Detected project-level workspace duplicates.[/yellow] "
            "Using user-level files as source of truth."
        )
        console.print(
            "[dim]"
            f"conflicts={summary.get('duplicate_conflict', 0)}, "
            f"identical={summary.get('duplicate_identical', 0)}, "
            f"project_only={summary.get('project_only_legacy', 0)}"
            "[/dim]"
        )

    for filename, content in templates.items():
        file_path = canonical_workspace / filename
        if not file_path.exists():
            try:
                file_path.write_text(content, encoding="utf-8")
                if console:
                    console.print(f"  [green]✓[/green] Created {filename}")
            except OSError as exc:
                if console:
                    console.print(f"  [yellow]⚠[/yellow] Could not create {filename}: {exc}")

    if console:
        console.print(f"\n[green]Workspace initialized at:[/green] {canonical_workspace}")


def save_config(config: dict[str, Any], config_path: Path, console: ConsoleType = None) -> None:
    """Save configuration to JSON file."""
    config_path.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = config_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    tmp_path.replace(config_path)

    if console:
        console.print(f"[green]✓ Configuration saved to:[/green] {config_path}")


def sync_to_env(config: dict[str, Any], console: ConsoleType = None) -> None:
    """Sync configuration to .env file for the Telegram bot."""
    # Find .env in current directory or NAVIG project root
    env_paths = [
        Path.cwd() / ".env",
        Path(__file__).parent.parent.parent / ".env",
    ]

    env_path = None
    for p in env_paths:
        if p.exists():
            env_path = p
            break

    if not env_path:
        env_path = env_paths[0]

    env_lines = []
    line_ending = "\n"

    # Read existing .env if present
    if env_path.exists():
        raw = env_path.read_text(encoding="utf-8")
        line_ending = "\r\n" if "\r\n" in raw else "\n"
        env_lines = raw.splitlines()

    # Update/add values from config
    telegram_config = config.get("channels", {}).get("telegram", {})

    updates = {
        "NAVIG_AI_MODEL": config.get("agents", {}).get("defaults", {}).get("model", "openrouter"),
        "TYPING_MODE": config.get("agents", {}).get("defaults", {}).get("typing_mode", "instant"),
    }

    if telegram_config:
        from navig.messaging.secrets import resolve_telegram_bot_token

        resolved_token = resolve_telegram_bot_token(config)
        if resolved_token:
            updates["TELEGRAM_BOT_TOKEN"] = resolved_token
        elif telegram_config.get("bot_token"):  # legacy / manual config
            updates["TELEGRAM_BOT_TOKEN"] = telegram_config["bot_token"]
        if telegram_config.get("allowed_users"):
            updates["ALLOWED_TELEGRAM_USERS"] = ",".join(
                str(u) for u in telegram_config["allowed_users"]
            )

    # Merge updates into env_lines
    for key, value in updates.items():
        found = False
        for i, line in enumerate(env_lines):
            if line.startswith(f"{key}="):
                env_lines[i] = f"{key}={value}"
                found = True
                break
        if not found:
            env_lines.append(f"{key}={value}")

    _tmp_path: Path | None = None
    try:
        _fd, _tmp = tempfile.mkstemp(dir=env_path.parent, suffix=".tmp")
        _tmp_path = Path(_tmp)
        with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
            _fh.write(line_ending.join(env_lines) + line_ending)
        os.replace(_tmp_path, env_path)
        _tmp_path = None
    finally:
        if _tmp_path is not None:
            _tmp_path.unlink(missing_ok=True)

    if console:
        console.print(f"[green]✓ Environment synced to:[/green] {env_path}")


def _auto_install_textual() -> bool:
    """
    Install textual into the current environment if it is missing.
    Tries uv (fast, ~400 ms) first, then falls back to pip.
    Returns True if textual is importable after the attempt.
    """
    import importlib
    import shutil
    import subprocess

    console = get_console()
    if console:
        console.print(
            "\n[bold cyan]▶[/bold cyan] Installing [cyan]textual[/cyan] for the animated TUI "
            "[dim](one-time setup)[/dim]…"
        )

    pkg = "textual>=0.50.0"

    # --- try uv first (ships with navig dev workflow, much faster than pip) ---
    uv_bin = shutil.which("uv")
    if uv_bin:
        try:
            result = subprocess.run(
                [uv_bin, "pip", "install", "--python", sys.executable, pkg, "-q"],
                capture_output=True,
                timeout=60,
            )
            if result.returncode == 0:
                try:
                    importlib.import_module("textual")
                    if console:
                        console.print(
                            "[bold green]✓[/bold green] textual installed — launching TUI…\n"
                        )
                    return True
                except ImportError:
                    pass  # optional dependency not installed; feature disabled
        except (OSError, subprocess.TimeoutExpired):
            pass  # optional tool absent or timed out

    # --- fall back to pip ---
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                pkg,
                "-q",
                "--disable-pip-version-check",
            ],
            capture_output=True,
            timeout=90,
        )
        if result.returncode != 0:
            err = result.stderr.decode(errors="replace").strip()
            if console and err:
                console.print(f"[dim yellow]textual install warning: {err[:200]}[/dim yellow]")
            return False
    except (OSError, subprocess.TimeoutExpired):
        if console:
            console.print("[dim yellow]TUI install timed out — using text mode[/dim yellow]")
        return False

    try:
        importlib.import_module("textual")
        if console:
            console.print("[bold green]✓[/bold green] textual installed — launching TUI…\n")
        return True
    except ImportError:
        return False


def run_onboard(flow: str = "auto", non_interactive: bool = False, skip: bool = False) -> None:
    """
    Run the NAVIG onboarding wizard.

    Automatically selects TUI mode (Textual) when available and running in
    an interactive terminal wide enough (>=80 cols). If Textual is not
    installed but the terminal supports it, installs it automatically then
    launches the TUI. Falls back to the rich/prompt flow only when running
    non-interactively or in a narrow/piped terminal.

    Args:
        flow: "quickstart" | "manual" | "auto"
        non_interactive: Skip all prompts and use defaults (forces fallback mode)
        skip: Print banner and exit immediately
    """
    global _TEXTUAL_AVAILABLE  # noqa: PLW0603
    console = get_console()

    if skip:
        if console:
            print_banner(console)
            console.print("[dim]Onboarding skipped. Run 'navig onboard' to configure.[/dim]")
        else:
            print("Onboarding skipped. Run 'navig onboard' to configure.")
        return

    tui_capable = not non_interactive and _terminal_supports_tui()

    # Auto-install textual when terminal supports TUI but dep is missing
    if tui_capable and not _TEXTUAL_AVAILABLE:
        installed = _auto_install_textual()
        if installed:
            # Re-run the module-level Textual import after successful install
            try:
                from textual.app import App, ComposeResult  # noqa: F401

                _TEXTUAL_AVAILABLE = True
            except ImportError:
                _TEXTUAL_AVAILABLE = False

    use_tui = _TEXTUAL_AVAILABLE and tui_capable

    if use_tui:
        if "NavigOnboardingApp" in globals():
            globals()["NavigOnboardingApp"]().run()
        else:
            # Textual was just auto-installed — reload the module so all
            # widget/screen classes get defined, then run the app.
            try:
                import importlib

                import navig.commands.onboard as _self

                importlib.reload(_self)
                _self.NavigOnboardingApp().run()
            except Exception:
                _run_onboard_rich(flow=flow, non_interactive=non_interactive)
    else:
        _run_onboard_rich(flow=flow, non_interactive=non_interactive)


# ---------------------------------------------------------------------------
# Direct execution
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    _flow = sys.argv[1] if len(sys.argv) > 1 else "auto"
    run_onboard(flow=_flow)
