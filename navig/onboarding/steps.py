"""
Step registry — two-phase onboarding.

Phase 1 — bootstrap (non-interactive, idempotent, safe in CI/automation):
  workspace-init · workspace-templates · config-file · configure-ssh · verify-network

Phase 2 — configuration (interactive, TTY-gated, auto-skipped when no TTY):
  ai-provider · vault-init · first-host · telegram-bot · skills-activation

Design rules:
- Every step is a plain OnboardingStep dataclass with callable run/verify.
- No global state, no side effects outside the declared output dict.
- Steps that write files declare the path in output.
- Step IDs are stable contracts — renaming is a breaking change.
- Phase 2 steps call _tty_check() first; non-TTY returns status=skipped.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import sys
from pathlib import Path
from typing import Any

from .engine import EngineConfig, OnboardingStep, StepResult
from .genesis import GenesisData

_WEB_SEARCH_PROVIDER_CATALOG: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "perplexity",
        "Perplexity Search  (structured results, domain/language/freshness filters)",
        ("PERPLEXITY_API_KEY", "PPLX_API_KEY"),
    ),
    (
        "brave",
        "Brave Search       (structured results, region-specific)",
        ("BRAVE_API_KEY",),
    ),
    (
        "gemini",
        "Gemini (Google)    (Google Search grounding, AI-synthesized)",
        ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ),
    (
        "grok",
        "Grok (xAI)         (xAI web-grounded responses)",
        ("XAI_API_KEY", "GROK_KEY"),
    ),
    (
        "kimi",
        "Kimi (Moonshot)    (Moonshot web search)",
        ("KIMI_API_KEY", "MOONSHOT_API_KEY"),
    ),
    (
        "tavily",
        "Tavily Search      (RAG-optimized, LLM-native search API)",
        ("TAVILY_API_KEY",),
    ),
)


def build_step_registry(
    config: EngineConfig,
    genesis: GenesisData,
) -> list[OnboardingStep]:
    """
    Ordered list of 10 onboarding steps for this run.

    Phase 1 — bootstrap (always runs, non-interactive):
      workspace-init · workspace-templates · config-file · configure-ssh · verify-network

    Phase 2 — configuration (interactive, skipped when no TTY):
      ai-provider · vault-init · first-host · telegram-bot · skills-activation
    """
    navig_dir = config.navig_dir
    steps = [
        # ── Phase 1: bootstrap ────────────────────────────────────────────
        _step_workspace_init(navig_dir),
        _step_terminal_setup(navig_dir),
        _step_workspace_templates(navig_dir),
        _step_config_file(navig_dir, genesis, config.reset),
        _step_configure_ssh(navig_dir),
        _step_verify_network(),
        _step_sigil_genesis(navig_dir, genesis),
        _step_core_navig(navig_dir),
        # ── Phase 2: interactive configuration ───────────────────────────
        _step_ai_provider(navig_dir),
        _step_vault_init(navig_dir),
        _step_web_search_provider(navig_dir),
        _step_voice_provider(navig_dir),
        _step_first_host(navig_dir),
        _step_matrix(navig_dir),
        _step_telegram_bot(navig_dir),
        _step_email(navig_dir),
        _step_social_networks(navig_dir),
        _step_runtime_secrets(navig_dir),
        _step_skills_activation(navig_dir),
    ]
    # Build title lookup for the review step summary display.
    step_titles = {s.id: s.title for s in steps}
    steps.append(_step_review(navig_dir, step_titles))
    return steps


# ── TTY helper ────────────────────────────────────────────────────────────


def _tty_check() -> StepResult | None:
    """Return a skipped StepResult if stdin is not a TTY, else None."""
    if not sys.stdin.isatty():
        return StepResult(
            status="skipped",
            output={"reason": "non-interactive environment"},
        )
    return None


def _detected_sources() -> dict[str, list[str]]:
    """Compatibility shim for tests that monkeypatch detected-source state."""
    return {}


def _prompt_masked(text: str, default: str = "") -> str:
    """Prompt for input while echoing '*' for each typed character."""
    import typer

    if os.getenv("PYTEST_CURRENT_TEST"):
        return typer.prompt(text, default=default)

    if os.environ.get("PYTEST_CURRENT_TEST"):
        return default

    prompt = f"{text} [{default}]: " if default else f"{text} []: "
    try:
        sys.stdout.write(prompt)
        sys.stdout.flush()

        if os.name == "nt":
            import msvcrt

            chars: list[str] = []
            while True:
                ch = msvcrt.getwch()
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    value = "".join(chars)
                    return value if value else default
                if ch == "\x03":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    raise KeyboardInterrupt
                if ch in ("\b", "\x7f"):
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue
                if ch in ("\x00", "\xe0"):
                    _ = msvcrt.getwch()
                    continue
                chars.append(ch)
                sys.stdout.write("*")
                sys.stdout.flush()

        import termios
        import tty

        chars = []
        fd = sys.stdin.fileno()
        old_settings = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            while True:
                ch = sys.stdin.read(1)
                if ch in ("\r", "\n"):
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    value = "".join(chars)
                    return value if value else default
                if ch == "\x03":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    raise KeyboardInterrupt
                if ch == "\x04":
                    sys.stdout.write("\n")
                    sys.stdout.flush()
                    raise EOFError
                if ch in ("\x7f", "\b"):
                    if chars:
                        chars.pop()
                        sys.stdout.write("\b \b")
                        sys.stdout.flush()
                    continue
                chars.append(ch)
                sys.stdout.write("*")
                sys.stdout.flush()
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
    except KeyboardInterrupt:
        raise
    except Exception:
        return typer.prompt(text, hide_input=True, default=default)


# ── Individual step factories ──────────────────────────────────────────────


def _step_terminal_setup(navig_dir: Path) -> OnboardingStep:
    """Detect Nerd Font availability; offer install on first run.

    Phase 1, bootstrap — runs non-interactively when no TTY; silently
    records the result to ``~/.navig/terminal.json`` so ``nf_icon()``
    and NAVIG_NERD_FONT checks on subsequent runs skip re-probing.

    If a TTY is present and no font is found, the user is offered a
    one-line install prompt (Windows: runs Install-NerdFont.ps1 via pwsh;
    macOS/Linux: prints the brew one-liner and skips).
    """
    terminal_json = navig_dir / "terminal.json"

    def run() -> StepResult:
        from navig import console_helper as ch
        from navig.ui._capabilities import (
            probe_nerd_font,
            try_install_nerd_font,
            write_terminal_json,
        )

        # Active probe
        nerd_font_found = probe_nerd_font()
        if nerd_font_found:
            write_terminal_json(navig_dir, nerd_font=True)
            return StepResult(
                status="completed",
                output={"nerd_font": True, "action": "detected"},
            )

        # No font — skip silently when no TTY (CI, pipes, SSH without allocation)
        if not sys.stdout.isatty():
            write_terminal_json(navig_dir, nerd_font=False)
            return StepResult(
                status="skipped",
                output={"nerd_font": False, "reason": "no-tty"},
            )

        # Interactive offer
        ch.dim("  Nerd Font glyphs extend NAVIG icons (Powerline separators, provider icons).")
        try:
            reply = input("  Install JetBrainsMono Nerd Font now? [Y/n] ").strip().lower()
        except (EOFError, OSError):
            write_terminal_json(navig_dir, nerd_font=False)
            return StepResult(
                status="skipped",
                output={"nerd_font": False, "reason": "no-input"},
            )

        if reply in ("", "y", "yes"):
            installed = try_install_nerd_font()
            write_terminal_json(navig_dir, nerd_font=installed)
            if installed:
                ch.success("  JetBrainsMono Nerd Font installed.")
                ch.dim("  Restart your terminal and VS Code to activate.")
                return StepResult(
                    status="completed",
                    output={"nerd_font": True, "action": "installed"},
                )
            # Auto-install unavailable (macOS/Linux or no pwsh)
            if sys.platform == "win32":
                ch.dim("  Automatic install failed. Run manually:")
                ch.dim("    pwsh scripts/Install-NerdFont.ps1")
            elif sys.platform == "darwin":
                ch.dim("  Run to install:")
                ch.dim("    brew install --cask font-jetbrains-mono-nerd-font")
            else:
                ch.dim("  Install via your distro or download from https://www.nerdfonts.com")
            return StepResult(
                status="skipped",
                output={"nerd_font": False, "reason": "install-unavailable"},
            )

        # User declined
        write_terminal_json(navig_dir, nerd_font=False)
        ch.dim("  Skipped. Icons will use Unicode/ASCII fallbacks.")
        ch.dim("  Set NAVIG_NERD_FONT=1 or re-run 'navig init' to enable later.")
        return StepResult(
            status="skipped",
            output={"nerd_font": False, "reason": "declined"},
        )

    def verify() -> bool:
        return terminal_json.exists()

    return OnboardingStep(
        id="terminal-setup",
        title="Detect terminal icon capabilities",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="bootstrap",
    )


def _step_workspace_init(navig_dir: Path) -> OnboardingStep:
    workspace = navig_dir / "workspace"

    def run() -> StepResult:
        workspace.mkdir(parents=True, exist_ok=True)
        # Fire anonymous one-time install telemetry — never blocks, never raises
        try:
            from navig.onboarding.telemetry import ping_install_if_first_time

            ping_install_if_first_time()
        except Exception:  # noqa: BLE001
            pass
        return StepResult(
            status="completed",
            output={"workspacePath": str(workspace)},
        )

    def verify() -> bool:
        return workspace.is_dir()

    return OnboardingStep(
        id="workspace-init",
        title="Initialise workspace directory",
        run=run,
        verify=verify,
        on_failure="abort",
        independent=True,
        phase="bootstrap",
    )


def _step_workspace_templates(navig_dir: Path) -> OnboardingStep:
    """Write SOUL.md, IDENTITY.md, AGENTS.md etc to the user workspace."""
    workspace = navig_dir / "workspace"
    # marker = presence of SOUL.md
    marker = navig_dir / "workspace" / "SOUL.md"

    def run() -> StepResult:
        try:
            from navig.workspace import create_workspace_templates

            create_workspace_templates(workspace)
            return StepResult(
                status="completed",
                output={"workspaceDir": str(workspace)},
            )
        except Exception as exc:  # noqa: BLE001
            return StepResult(
                status="skipped",
                output={"reason": str(exc)},
            )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="workspace-templates",
        title="Write workspace identity files",
        run=run,
        verify=verify,
        on_failure="skip",
        phase="bootstrap",
    )


def _step_config_file(
    navig_dir: Path,
    genesis: GenesisData,
    reset: bool = False,
) -> OnboardingStep:
    config_path = navig_dir / "config.yaml"
    config_content = (
        f"# NAVIG configuration\n"
        f"# Node: {genesis.nodeId}\n"
        f"# Generated: {genesis.bornAt}\n\n"
        f"node:\n"
        f"  id: {genesis.nodeId}\n"
        f"  name: {genesis.name}\n"
        f"  workspace: {navig_dir / 'workspace'}\n\n"
        f"# AI provider and routing are configured interactively during `navig init`.\n"
        f"# To change providers run `navig init --reconfigure` or edit this file.\n"
        f"# Full reference: config/config.example.yaml (shipped with the package)\n"
    )

    def run() -> StepResult:
        if config_path.exists() and not reset:
            # Config is already in place — report as completed, not skipped
            return StepResult(
                status="completed",
                output={"configPath": str(config_path)},
            )
        # First run or reset: write fresh config with current node id/name
        config_path.write_text(config_content, encoding="utf-8")
        return StepResult(
            status="completed",
            output={"configPath": str(config_path)},
        )

    def verify() -> bool:
        # On reset: always re-run so fresh config is written with current name/id
        return config_path.exists() and not reset

    return OnboardingStep(
        id="config-file",
        title="Write base configuration",
        run=run,
        verify=verify,
        on_failure="abort",
        phase="bootstrap",
    )


def _step_ai_provider(navig_dir: Path) -> OnboardingStep:
    """Interactive — choose AI provider and store API key in vault."""
    marker = navig_dir / ".ai_provider_configured"

    def _local_default_url(provider_id: str) -> str:
        defaults = {
            "llamacpp": "http://127.0.0.1:8080",
        }
        url = defaults.get(provider_id, "")
        try:
            from navig.providers.registry import get_provider

            manifest = get_provider(provider_id)
            probe = str(getattr(manifest, "local_probe", "") or "").strip()
            if probe and "://" not in probe:
                return f"http://{probe}"
            if probe:
                return probe
        except Exception:  # noqa: BLE001
            pass
        return url

    def _persist_provider_url(provider_id: str, base_url: str) -> bool:
        if not base_url:
            return False
        try:
            import yaml  # type: ignore[import]

            config_path = navig_dir / "config.yaml"
            cfg: dict[str, Any] = {}
            if config_path.exists():
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("llm_router", {}).setdefault("provider_base_urls", {})[provider_id] = (
                base_url
            )
            config_path.write_text(
                yaml.dump(cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def _has_vault_key(provider_id: str) -> bool:
        """Return True when a provider API key already exists in vault."""
        try:
            vault = _get_vault_for_onboarding()
            if vault is None:
                return False
            value = vault.get(f"{provider_id}/api_key")
            return bool(value)
        except Exception:  # noqa: BLE001
            return False

    def _get_vault_for_onboarding():
        """Return vault handle with legacy core_v2 fallback compatibility."""
        try:
            from navig.vault.core import get_vault

            vault = get_vault()
            if vault is not None:
                return vault
        except Exception:  # noqa: BLE001
            pass

        try:
            from navig.vault.core_v2 import get_vault_v2

            return get_vault_v2()
        except Exception:  # noqa: BLE001
            return None

    def _load_providers():
        """Load provider list dynamically from the registry at runtime."""
        try:
            from navig.providers.registry import list_enabled_providers

            providers = list_enabled_providers()
            # Sort: cloud first, then proxy, then local; within tier alphabetically
            return providers
        except Exception:
            # Hard fallback — never block onboarding if registry can't be imported
            from types import SimpleNamespace

            return [
                SimpleNamespace(
                    id="openrouter",
                    display_name="OpenRouter",
                    requires_key=True,
                    tier="cloud",
                ),
                SimpleNamespace(
                    id="openai", display_name="OpenAI", requires_key=True, tier="cloud"
                ),
                SimpleNamespace(
                    id="anthropic",
                    display_name="Anthropic",
                    requires_key=True,
                    tier="cloud",
                ),
                SimpleNamespace(
                    id="ollama",
                    display_name="Ollama (local)",
                    requires_key=False,
                    tier="local",
                ),
            ]

    # Common env var map shared by both helpers below
    _PROVIDER_ENV_VARS: dict[str, list[str]] = {
        "openai": ["OPENAI_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
        "groq": ["GROQ_API_KEY"],
        "google": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "nvidia": ["NVIDIA_API_KEY", "NIM_API_KEY"],
        "xai": ["XAI_API_KEY", "GROK_KEY"],
        "github_models": ["GITHUB_TOKEN", "GH_TOKEN"],
        "mistral": ["MISTRAL_API_KEY"],
    }

    def _detect_provider_key(provider_id: str) -> tuple[str, str]:
        """Return *(env_var_name, key_value)* for *provider_id*, or ('', '')."""
        try:
            from navig.providers.source_scan import provider_env_key  # noqa: F401

            manifest = get_provider(provider_id)
            if manifest:
                for var in manifest.env_vars:
                    val = os.environ.get(var, "").strip()
                    if val:
                        return var, val
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        for var in _PROVIDER_ENV_VARS.get(provider_id, []):
            val = os.environ.get(var, "").strip()
            if val:
                return var, val
        return "", ""

    def _detected_sources(provider_id: str) -> list[str]:
        try:
            from navig.providers.source_scan import detect_provider_sources

            return detect_provider_sources(provider_id, navig_dir=navig_dir)
        except Exception:  # noqa: BLE001
            return []

    def _fast_path_key(provider_id: str) -> str:
        """Return an env-var API key for *provider_id* if one is set, else ''."""
        _, val = _detect_provider_key(provider_id)
        return val

    def _env_var_for_provider(provider_id: str) -> str:
        """Return the name of the first detected env var for *provider_id*, or ''."""
        var, _ = _detect_provider_key(provider_id)
        return var

    def run() -> StepResult:
        # Environment-variable fast path (works in CI / non-TTY)
        provider = os.environ.get("NAVIG_LLM_PROVIDER", "").strip()
        if provider:
            api_key = _fast_path_key(provider)
            if api_key or provider in ("ollama", "llamacpp", "airllm", "mcp_bridge"):
                marker.write_text(provider, encoding="utf-8")
                return StepResult(
                    status="completed",
                    output={"provider": provider, "keySource": "environment"},
                )

        # No TTY — skip gracefully
        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        from navig import console_helper as ch

        providers = _load_providers()
        existing_vault = {p.id: _has_vault_key(p.id) for p in providers}

        configured = [p for p in providers if existing_vault.get(p.id, False)]
        if configured:
            configured_list = ", ".join(p.display_name for p in configured)
            sys.stdout.write(f"\n  Existing vault credentials detected: {configured_list}\n")

        source_by_provider: dict[str, list[str]] = {
            p.id: _detected_sources(p.id) for p in providers
        }

        # ── Scan env vars for recognized provider keys (upfront detection) ──
        env_detected: dict[str, str] = {}  # provider_id → env_var_name
        for p in providers:
            if not getattr(p, "requires_key", True):
                continue
            var_name = _env_var_for_provider(p.id)
            if var_name:
                env_detected[p.id] = var_name

        if env_detected:
            ch.info("API key(s) detected in environment:")
            for pid_det, var_name in env_detected.items():
                label_det = next((p.display_name for p in providers if p.id == pid_det), pid_det)
                ch.dim(f"    · {var_name}  →  {label_det}")

        default_idx = 1
        for i, p in enumerate(providers, start=1):
            if existing_vault.get(p.id, False):
                default_idx = i
                break

        try:
            current_provider = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        except (OSError, UnicodeError):
            current_provider = ""

        # Default: current provider > first env-detected > first-sourced > index 1
        if current_provider:
            default_choice = "s"
        else:
            first_env = next(
                (str(i + 1) for i, p in enumerate(providers) if p.id in env_detected),
                None,
            )
            first_sourced = next(
                (str(i + 1) for i, p in enumerate(providers) if source_by_provider.get(p.id)),
                None,
            )
            default_choice = first_env or first_sourced or "1"

        ch.info("Choose your AI provider:")
        for i, p in enumerate(providers, start=1):
            local_tag = "  (local, no key needed)" if not getattr(p, "requires_key", True) else ""
            sources = source_by_provider.get(p.id, [])
            ready_tag = f"  (already configured: {'/'.join(sources)})" if sources else ""
            env_tag = "  [✓ env var]" if p.id in env_detected else ""
            active_tag = "  ← current" if p.id == current_provider else ""
            ch.dim(f"    [{i}] {p.display_name}{local_tag}{env_tag}{ready_tag}{active_tag}")
        skip_hint = " (keep existing)" if current_provider else " for now"
        ch.dim(f"    [s] Skip{skip_hint}")

        try:
            choice_raw = typer.prompt("  Provider", default=default_choice)
            if choice_raw.strip().lower() in ("s", "skip", ""):
                return StepResult(status="skipped", output={"reason": "skipped by user"})
            idx = int(choice_raw.strip()) - 1
            if idx < 0 or idx >= len(providers):
                raise ValueError("out of range")
        except KeyboardInterrupt:
            raise
        except (ValueError, EOFError):
            return StepResult(
                status="skipped",
                output={"reason": "invalid selection or interrupted"},
            )

        chosen = providers[idx]
        pid = chosen.id
        label = chosen.display_name
        requires_key = getattr(chosen, "requires_key", True)
        existing_sources = source_by_provider.get(pid, [])
        configured_base_url = ""
        key_source = "interactive"
        keep_existing_key = False

        env_detected_key = _fast_path_key(pid)
        has_env_source = bool(env_detected_key)
        has_vault_source = "vault" in existing_sources

        if requires_key and has_env_source and not has_vault_source:
            try:
                import_env_choice = (
                    typer.prompt(
                        "  Environment key detected. Import to vault now? [Y/n]",
                        default="y",
                    )
                    .strip()
                    .lower()
                )
            except KeyboardInterrupt:
                raise
            except (EOFError, OSError):
                import_env_choice = "y"
            if import_env_choice in ("", "y", "yes"):
                try:
                    vault = _get_vault_for_onboarding()
                    if vault is not None and env_detected_key:
                        vault.put(
                            f"{pid}/api_key",
                            json.dumps({"value": env_detected_key}).encode(),
                        )
                        if "vault" not in existing_sources:
                            existing_sources.append("vault")
                except Exception:  # noqa: BLE001
                    pass

        if not requires_key:
            # Local provider — no key needed
            api_key = "local"
            key_source = "local"
            if pid == "llamacpp":
                default_url = _local_default_url(pid) or "http://127.0.0.1:8080"
                try:
                    configured_base_url = typer.prompt(
                        "  llama.cpp URL",
                        default=default_url,
                    ).strip()
                except KeyboardInterrupt:
                    raise
                except (EOFError, OSError):
                    configured_base_url = default_url
                if not configured_base_url:
                    configured_base_url = default_url
        elif pid in env_detected:
            # Key available from environment — use it automatically
            api_key = _fast_path_key(pid)
            key_source = "existing:" + "/".join(existing_sources or ["env"])
            ch.dim(f"  ✓ Using {label} key from {env_detected[pid]}.")
        else:
            try:
                prompt_text = f"  {label} API key"
                if existing_sources:
                    prompt_text += " (Enter to keep existing)"
                api_key = _prompt_masked(prompt_text, default="").strip()
            except KeyboardInterrupt:
                raise
            except (EOFError, OSError):
                return StepResult(status="skipped", output={"reason": "interrupted"})
            if not api_key:
                if existing_sources:
                    keep_existing_key = True
                    key_source = "existing:" + "/".join(existing_sources)
                else:
                    return StepResult(status="skipped", output={"reason": "skipped by user"})

        # Persist in vault if available, else fall back to marker file
        try:
            vault = _get_vault_for_onboarding()
            if vault is not None and not keep_existing_key:
                vault.put(
                    f"{pid}/api_key",
                    json.dumps({"value": api_key}).encode(),
                )
        except Exception:  # noqa: BLE001
            pass

        # ── Optional fallback provider ────────────────────────────────────
        fallback_pid = ""
        try:
            ch.info(
                "Configure a fallback provider? "
                "(used when primary is unavailable — press Enter to skip)"
            )
            fallback_providers = [p for p in providers if p.id != pid]
            for i, p in enumerate(fallback_providers, start=1):
                local_tag = "" if getattr(p, "requires_key", True) else "  (local)"
                ch.dim(f"    [{i}] {p.display_name}{local_tag}")
            ch.dim("    [s] Skip")
            fb_raw = typer.prompt("  Fallback provider", default="s").strip().lower()
            if fb_raw not in ("s", "skip", ""):
                try:
                    fb_idx = int(fb_raw) - 1
                    if 0 <= fb_idx < len(fallback_providers):
                        chosen_fb = fallback_providers[fb_idx]
                        fallback_pid = chosen_fb.id
                        if getattr(chosen_fb, "requires_key", True):
                            fb_key = _prompt_masked(
                                f"  {chosen_fb.display_name} API key (fallback)",
                                default="",
                            ).strip()
                            if fb_key:
                                try:
                                    vfb = _get_vault_for_onboarding()
                                    if vfb is not None:
                                        vfb.put(
                                            f"{fallback_pid}/api_key",
                                            json.dumps({"value": fb_key}).encode(),
                                        )
                                except Exception:  # noqa: BLE001
                                    pass
                except (ValueError, IndexError):
                    fallback_pid = ""
        except KeyboardInterrupt:
            raise
        except (EOFError, OSError):
            fallback_pid = ""

        # Write routing block to config.yaml when a fallback was chosen
        if fallback_pid:
            try:
                import yaml  # type: ignore[import]

                config_path = navig_dir / "config.yaml"
                cfg: dict = {}
                if config_path.exists():
                    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                cfg.setdefault("routing", {})["enabled"] = True
                modes = cfg["routing"].setdefault("llm_modes", {})
                for tier in ("small_talk", "big_tasks", "coding", "summarize", "research"):
                    modes.setdefault(tier, {})["fallback_provider"] = fallback_pid
                config_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass  # best-effort; never block onboarding

        verification: dict[str, Any] = {}
        try:
            run_verification = typer.confirm("  Test provider connection now?", default=False)
        except KeyboardInterrupt:
            raise
        except Exception:  # noqa: BLE001
            run_verification = False

        if run_verification:
            try:
                from navig.providers.registry import get_provider
                from navig.providers.verifier import verify_provider

                manifest = get_provider(pid)
                if manifest is not None:
                    verified = verify_provider(manifest)
                    verification = {
                        "ok": bool(verified.ok),
                        "issues": list(verified.issues),
                        "key_detected": bool(verified.key_detected),
                        "local_probe_ok": verified.local_probe_ok,
                    }
            except Exception:  # noqa: BLE001
                verification = {"ok": False, "issues": ["verification failed"]}

        base_url_saved = False
        if configured_base_url:
            base_url_saved = _persist_provider_url(pid, configured_base_url)

        marker.write_text(pid, encoding="utf-8")
        out: dict = {"provider": pid, "keySource": key_source}
        if configured_base_url:
            out["base_url"] = configured_base_url
            out["baseUrlSaved"] = "config" if base_url_saved else "none"
        if verification:
            out["verification"] = verification
        if fallback_pid:
            out["fallback_provider"] = fallback_pid
        return StepResult(status="completed", output=out)

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="ai-provider",
        title="Configure AI provider",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="recommended",
    )


def _step_configure_ssh(navig_dir: Path) -> OnboardingStep:
    ssh_dir = Path.home() / ".ssh"
    key_path = ssh_dir / "navig_ed25519"

    def run() -> StepResult:
        if key_path.exists():
            return StepResult(
                status="completed",
                output={"keyPath": str(key_path), "keyType": "ed25519"},
            )
        if not shutil.which("ssh-keygen"):
            return StepResult(
                status="skipped",
                output={"reason": "ssh-keygen not found in PATH"},
            )
        ssh_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        import subprocess

        try:
            subprocess.run(
                [
                    "ssh-keygen",
                    "-t",
                    "ed25519",
                    "-N",
                    "",
                    "-f",
                    str(key_path),
                    "-C",
                    f"navig-key@{socket.gethostname()}",
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except subprocess.TimeoutExpired:
            return StepResult(
                status="skipped",
                output={"reason": "ssh-keygen timed out after 15s"},
            )
        except subprocess.CalledProcessError as exc:
            return StepResult(
                status="skipped",
                output={"reason": f"ssh-keygen failed: {exc.returncode}"},
            )
        return StepResult(
            status="completed",
            output={"keyPath": str(key_path), "keyType": "ed25519"},
        )

    def verify() -> bool:
        return key_path.exists()

    return OnboardingStep(
        id="configure-ssh",
        title="Configure SSH key",
        run=run,
        verify=verify,
        on_failure="skip",
        phase="bootstrap",
    )


def _step_verify_network() -> OnboardingStep:
    """Live smoke test — always runs (no idempotency marker)."""

    def run() -> StepResult:
        try:
            old_timeout = socket.getdefaulttimeout()
            socket.setdefaulttimeout(3)
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.connect(("8.8.8.8", 53))
                s.close()
            finally:
                socket.setdefaulttimeout(old_timeout)
            return StepResult(
                status="completed",
                output={"networkReachable": "true"},
            )
        except OSError:
            return StepResult(
                status="skipped",
                output={"networkReachable": "false", "note": "offline mode"},
            )

    def verify() -> bool:
        return False  # Always run — this is a live check, not a one-time action

    return OnboardingStep(
        id="verify-network",
        title="Verify network connectivity",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="bootstrap",
    )


# ── Phase 2: interactive configuration steps ───────────────────────────────


def _step_vault_init(navig_dir: Path) -> OnboardingStep:
    """Initialise the NAVIG credential vault with a master passphrase."""
    vault_db = navig_dir / "vault" / "vault.db"

    def run() -> StepResult:
        if vault_db.exists():
            return StepResult(
                status="completed",
                output={"vaultPath": str(vault_db), "note": "already initialised"},
            )

        tty = _tty_check()
        if tty is not None:
            return tty

        try:
            pw = _prompt_masked("  Vault passphrase", default="").strip()
            if not pw:
                return StepResult(status="skipped", output={"reason": "empty passphrase"})
            confirm = _prompt_masked("  Confirm passphrase", default="").strip()
        except KeyboardInterrupt:
            raise
        except EOFError:
            return StepResult(status="skipped", output={"reason": "interrupted"})

        if pw != confirm:
            return StepResult(status="skipped", output={"reason": "passphrases did not match"})

        try:
            from navig.vault.core import Vault

            vault_dir = navig_dir / "vault"
            vault_dir.mkdir(parents=True, exist_ok=True)
            Vault(vault_dir).unlock(passphrase=pw.encode())
            return StepResult(
                status="completed",
                output={"vaultPath": str(vault_db)},
            )
        except Exception as exc:  # noqa: BLE001
            return StepResult(status="skipped", output={"reason": str(exc)})

    def verify() -> bool:
        return vault_db.exists()

    return OnboardingStep(
        id="vault-init",
        title="Initialise credential vault",
        run=run,
        verify=verify,
        on_failure="skip",
        phase="configuration",
        tier="recommended",
    )


# ── Voice providers catalog ────────────────────────────────────────────────
_VOICE_PROVIDER_CATALOG: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    (
        "deepgram",
        "Deepgram    (STT + TTS — streaming & batched, low latency)",
        "STT+TTS",
        ("DEEPGRAM_API_KEY", "DEEPGRAM_KEY"),
    ),
    (
        "elevenlabs",
        "ElevenLabs  (TTS — ultra-realistic voices, 30+ languages)",
        "TTS",
        ("ELEVENLABS_API_KEY", "XI_API_KEY"),
    ),
)


def _step_voice_provider(navig_dir: Path) -> OnboardingStep:
    """Configure voice API keys (Deepgram STT/TTS, ElevenLabs TTS)."""
    marker = navig_dir / ".voice_provider_configured"

    def _detect_env_key(env_vars: tuple[str, ...]) -> str:
        for env_var in env_vars:
            val = os.environ.get(env_var, "").strip()
            if val:
                return val
        return ""

    def _store_key(provider_id: str, api_key: str) -> str:
        label = f"{provider_id}/api_key"
        try:
            from navig.vault.core import get_vault  # type: ignore[import]

            vault = get_vault()
            if vault is not None:
                vault.put(label, json.dumps({"value": api_key}).encode())
                return "vault"
        except Exception:  # noqa: BLE001
            pass
        return "skipped"

    def _validate_key(provider_id: str, api_key: str) -> bool:
        try:
            from navig.vault.types import Credential, CredentialType  # type: ignore[import]
            from navig.vault.validators import get_validator  # type: ignore[import]

            cred = Credential(
                provider=provider_id,
                credential_type=CredentialType.API_KEY,
                data={"api_key": api_key},
            )
            result = get_validator(provider_id).validate(cred)
            return result.success
        except Exception:  # noqa: BLE001
            return False

    def run() -> StepResult:  # noqa: C901
        if marker.exists():
            return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        from navig import console_helper as ch

        # Fast-path: detect existing env keys and offer import.
        auto_imported: list[str] = []
        for prov_id, _desc, _role, env_vars in _VOICE_PROVIDER_CATALOG:
            val = _detect_env_key(env_vars)
            if val:
                try:
                    if typer.confirm(f"  Import {prov_id} key from environment?", default=True):
                        source = _store_key(prov_id, val)
                        if source == "vault":
                            auto_imported.append(prov_id)
                except (KeyboardInterrupt, EOFError):
                    break

        if auto_imported:
            marker.write_text(",".join(auto_imported), encoding="utf-8")
            return StepResult(
                status="completed",
                output={"imported": auto_imported, "source": "environment"},
            )

        # Interactive picker.
        try:
            answer = (
                typer.prompt(
                    "  Set up a voice provider? (deepgram/elevenlabs/skip)",
                    default="skip",
                )
                .strip()
                .lower()
            )
        except (KeyboardInterrupt, EOFError):
            return StepResult(status="skipped", output={"reason": "interrupted"})

        if answer in ("", "skip", "s", "no", "n"):
            return StepResult(status="skipped", output={"reason": "user chose to skip"})

        catalog_ids = [p[0] for p in _VOICE_PROVIDER_CATALOG]
        provider_id = answer if answer in catalog_ids else ""
        if not provider_id:
            ch.warning(f"  Unknown provider '{answer}'. Valid choices: {', '.join(catalog_ids)}")
            return StepResult(status="skipped", output={"reason": "unknown provider"})

        try:
            api_key = _prompt_masked(f"  {provider_id} API key", default="").strip()
        except (KeyboardInterrupt, EOFError):
            return StepResult(status="skipped", output={"reason": "interrupted"})

        if not api_key:
            return StepResult(status="skipped", output={"reason": "no key entered"})

        # Live validation.
        ch.info("  Validating key...")
        valid = _validate_key(provider_id, api_key)
        if not valid:
            ch.warning("  Key validation failed — storing anyway (check connectivity).")

        source = _store_key(provider_id, api_key)
        marker.write_text(provider_id, encoding="utf-8")
        return StepResult(
            status="completed",
            output={"provider": provider_id, "keySource": source, "validated": valid},
        )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="voice-provider",
        title="Set up voice provider (STT/TTS)",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="optional",
    )


def _step_first_host(navig_dir: Path) -> OnboardingStep:
    """Prompt user to connect a first remote server if none exist yet."""
    hosts_dir = navig_dir / "hosts"

    def run() -> StepResult:
        existing = list(hosts_dir.glob("*.yaml")) if hosts_dir.exists() else []
        if existing:
            return StepResult(
                status="completed",
                output={"hostsFound": len(existing)},
            )
        # No TTY — leave for later
        tty = _tty_check()
        if tty is not None:
            return tty
        # Interactive: user can add a host now or skip
        import typer

        try:
            answer = (
                typer.prompt(
                    "  Add a remote host now? (y/N)",
                    default="n",
                )
                .strip()
                .lower()
            )
        except KeyboardInterrupt:
            raise
        except EOFError:
            answer = "n"

        if answer == "y":
            import subprocess

            _env = {**os.environ, "NAVIG_SKIP_ONBOARDING": "1"}
            subprocess.run(
                [sys.executable, "-m", "navig", "host", "add"],
                check=False,
                env=_env,
            )
            existing = list(hosts_dir.glob("*.yaml")) if hosts_dir.exists() else []
            if existing:
                return StepResult(status="completed", output={"hostsFound": len(existing)})

        return StepResult(
            status="skipped",
            output={"note": "no host configured — run 'navig host add' later"},
        )

    def verify() -> bool:
        return False  # Always check at runtime

    return OnboardingStep(
        id="first-host",
        title="Connect first remote server",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="recommended",
    )


def _step_web_search_provider(navig_dir: Path) -> OnboardingStep:
    """Configure web search provider preference and optional API key."""

    provider_aliases = {
        "": "auto",
        "auto": "auto",
        "duckduckgo": "duckduckgo",
        "ddg": "duckduckgo",
        "brave": "brave",
        "brave-search": "brave",
        "perplexity": "perplexity",
        "gemini": "gemini",
        "google": "gemini",
        "grok": "grok",
        "xai": "grok",
        "kimi": "kimi",
        "moonshot": "kimi",
        "tavily": "tavily",
    }

    def _persist_provider(preferred_provider: str) -> bool:
        try:
            import yaml  # type: ignore[import]

            config_path = navig_dir / "config.yaml"
            cfg: dict[str, Any] = {}
            if config_path.exists():
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("web", {}).setdefault("search", {})["provider"] = preferred_provider
            config_path.write_text(
                yaml.dump(cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            return True
        except Exception:  # noqa: BLE001
            return False

    def _persist_key(preferred_provider: str, api_key: str) -> str:
        if not api_key:
            return "none"

        vault_label = f"web/{preferred_provider}_api_key"
        try:
            from navig.vault.core import get_vault

            vault = get_vault()
            if vault is not None:
                vault.put(vault_label, json.dumps({"value": api_key}).encode())
                return "vault"
        except Exception:  # noqa: BLE001
            pass

        try:
            import yaml  # type: ignore[import]

            config_path = navig_dir / "config.yaml"
            cfg: dict[str, Any] = {}
            if config_path.exists():
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            search_cfg = cfg.setdefault("web", {}).setdefault("search", {})
            api_keys = search_cfg.setdefault("api_keys", {})
            api_keys[preferred_provider] = api_key
            if preferred_provider == "brave":
                search_cfg["api_key"] = api_key
            config_path.write_text(
                yaml.dump(cfg, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            return "config"
        except Exception:  # noqa: BLE001
            return "none"

    def _resolve_env_key(env_vars: tuple[str, ...]) -> str:
        for env_name in env_vars:
            value = os.environ.get(env_name, "").strip()
            if value:
                return value
        return ""

    def run() -> StepResult:
        env_provider_raw = os.environ.get("NAVIG_WEB_SEARCH_PROVIDER", "").strip().lower()
        env_provider = provider_aliases.get(env_provider_raw, "")
        if env_provider_raw and env_provider_raw != "skip":
            catalog = {
                pid: (label, env_vars) for pid, label, env_vars in _WEB_SEARCH_PROVIDER_CATALOG
            }
            if env_provider in catalog:
                _, env_vars = catalog[env_provider]
                env_key = _resolve_env_key(env_vars)
                _persist_provider(env_provider)
                key_source = _persist_key(env_provider, env_key) if env_key else "none"
                return StepResult(
                    status="completed",
                    output={
                        "provider": env_provider,
                        "keySource": "environment" if env_key else key_source,
                    },
                )
            if env_provider in {"auto", "duckduckgo"}:
                _persist_provider(env_provider)
                return StepResult(
                    status="completed",
                    output={
                        "provider": env_provider,
                        "keySource": "none",
                    },
                )
            _persist_provider("auto")
            return StepResult(
                status="skipped",
                output={
                    "reason": "invalid NAVIG_WEB_SEARCH_PROVIDER value",
                    "provider": "auto",
                },
            )

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        from navig import console_helper as ch

        # Read existing configured provider (if any) to show in reconfigure mode.
        current_search_provider = ""
        try:
            import yaml  # type: ignore[import]

            _cfg_path = navig_dir / "config.yaml"
            if _cfg_path.exists():
                _cfg = yaml.safe_load(_cfg_path.read_text(encoding="utf-8")) or {}
                current_search_provider = str(
                    ((_cfg.get("web") or {}).get("search") or {}).get("provider") or ""
                ).strip()
        except (OSError, yaml.YAMLError):
            pass

        ch.info("Search provider")
        for idx, (pid, label, _) in enumerate(_WEB_SEARCH_PROVIDER_CATALOG, start=1):
            current_tag = "  ← current" if pid == current_search_provider else ""
            ch.dim(f"    [{idx}] {label}{current_tag}")
        skip_hint = " (keep existing)" if current_search_provider else " for now"
        ch.dim(f"    [s] Skip{skip_hint}")

        try:
            choice_raw = typer.prompt("  Provider", default="s").strip().lower()
        except KeyboardInterrupt:
            raise
        except EOFError:
            choice_raw = "s"

        if choice_raw in ("s", "skip", ""):
            # In reconfigure mode, preserve the existing provider value.
            # On fresh init, fall back to "auto" so the config entry exists.
            if not current_search_provider:
                _persist_provider("auto")
            return StepResult(status="skipped", output={"reason": "skipped by user"})

        try:
            idx = int(choice_raw) - 1
        except ValueError:
            return StepResult(status="skipped", output={"reason": "invalid provider selection"})

        if idx < 0 or idx >= len(_WEB_SEARCH_PROVIDER_CATALOG):
            return StepResult(status="skipped", output={"reason": "invalid provider selection"})

        provider_id, provider_label, env_vars = _WEB_SEARCH_PROVIDER_CATALOG[idx]
        env_key = _resolve_env_key(env_vars)
        entered_key = env_key

        if not entered_key:
            try:
                entered_key = _prompt_masked(
                    f"  {provider_label.split('(')[0].strip()} API key", default=""
                ).strip()
            except KeyboardInterrupt:
                raise
            except EOFError:
                entered_key = ""

        if not entered_key:
            # Provider selected but no API key supplied — save the provider choice
            # so the user doesn't have to re-select it, but report the step as
            # skipped because the provider is not functional without a key.
            _persist_provider(provider_id)
            return StepResult(
                status="skipped",
                output={
                    "provider": provider_id,
                    "reason": f"API key required for {provider_id} but not provided",
                },
            )

        # entered_key is guaranteed non-empty here (the `if not entered_key` guard
        # above returned early if it was empty). keySource reflects how the key
        # was obtained: env variable vs. interactive prompt.
        provider_written = _persist_provider(provider_id)
        key_target = _persist_key(provider_id, entered_key)

        return StepResult(
            status="completed",
            output={
                "provider": provider_id,
                "providerSaved": provider_written,
                "keySource": "environment"
                if env_key
                else ("interactive" if entered_key else "none"),
                "keyTarget": key_target,
            },
        )

    def verify() -> bool:
        try:
            import yaml  # type: ignore[import]

            config_path = navig_dir / "config.yaml"
            if not config_path.exists():
                return False
            cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            provider = str(
                ((cfg.get("web") or {}).get("search") or {}).get("provider") or ""
            ).strip()
            return bool(provider)
        except Exception:  # noqa: BLE001
            return False

    return OnboardingStep(
        id="web-search-provider",
        title="Configure web search provider",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="recommended",
    )


def _step_telegram_bot(navig_dir: Path) -> OnboardingStep:
    """Optionally configure a Telegram bot token for notifications.

    Token storage strategy:
    1. Vault  — primary, secure (navig.vault.core); requires vault-init step.
    2. .env   — legacy; used by the shell/PS1 installers and daemon env loading.

    Both writes are individually non-fatal.  Missing any one does not fail
    the step — the token is preserved in at least one location.

    Writing the token to config.yaml in plaintext is deprecated and has been
    removed.  Use `navig vault set telegram_bot_token <token>` to store or
    update the token at any time.
    """
    marker = navig_dir / ".telegram_configured"

    def _verify_token_remote(token: str) -> tuple[bool, str]:
        try:
            import httpx

            response = httpx.get(f"https://api.telegram.org/bot{token}/getMe", timeout=10)
            payload = response.json()
            if response.status_code == 200 and payload.get("ok"):
                return True, "token valid"
            description = payload.get("description") or f"HTTP {response.status_code}"
            return False, str(description)
        except Exception as exc:  # noqa: BLE001
            return False, f"validation request failed: {exc}"

    def run() -> StepResult:
        if marker.exists():
            return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        try:
            token = typer.prompt(
                "  Telegram bot token (leave blank to skip)",
                default="",
            ).strip()
        except KeyboardInterrupt:
            raise
        except EOFError:
            token = ""

        if not token:
            return StepResult(status="skipped", output={"reason": "no token entered"})

        ok, verify_message = _verify_token_remote(token)
        if not ok:
            return StepResult(
                status="failed",
                output={"reason": "telegram token validation failed"},
                error=f"Telegram token check failed: {verify_message}",
                fix_hint="Get a valid token from @BotFather and run 'navig init' again.",
            )

        writes: list[str] = []

        # 1. Primary: vault (secure, per-user credential store)
        try:
            from navig.vault.core import get_vault

            vault = get_vault()
            if vault is not None:
                vault.put(
                    "telegram_bot_token",
                    json.dumps({"value": token}).encode(),
                )
                writes.append("vault")
        except Exception:  # noqa: BLE001
            pass

        # 2. Legacy: .env file (used by install.sh / install.ps1 and daemon env loading)
        try:
            env_path = navig_dir / ".env"
            existing = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
            lines = [ln for ln in existing.splitlines() if not ln.startswith("TELEGRAM_BOT_TOKEN=")]
            lines.append(f"TELEGRAM_BOT_TOKEN={token}")
            env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            try:
                env_path.chmod(0o600)
            except (OSError, PermissionError):
                pass  # best-effort: skip on access/IO error
            writes.append(".env")
        except Exception:  # noqa: BLE001
            pass

        # 3. Prompt for owner Telegram user ID (stored in vault; skipped if already set)
        try:
            from navig.messaging.secrets import resolve_telegram_uid

            if not resolve_telegram_uid():
                import typer as _typer

                try:
                    uid_raw = _typer.prompt(
                        "  Your Telegram user ID (leave blank to skip — find it via @userinfobot)",
                        default="",
                    ).strip()
                except (EOFError, KeyboardInterrupt):
                    uid_raw = ""
                if uid_raw and uid_raw.isdigit():
                    from navig.vault.core import get_vault as _gv2

                    _v2 = _gv2()
                    if _v2 is not None:
                        _v2.put("telegram/user_id", json.dumps({"value": uid_raw}).encode())
                    # Also persist to .env for env-based fallback
                    try:
                        env_path = navig_dir / ".env"
                        _existing = (
                            env_path.read_text(encoding="utf-8") if env_path.exists() else ""
                        )
                        _lines = [
                            ln
                            for ln in _existing.splitlines()
                            if not ln.startswith("NAVIG_TELEGRAM_UID=")
                        ]
                        _lines.append(f"NAVIG_TELEGRAM_UID={uid_raw}")
                        env_path.write_text("\n".join(_lines) + "\n", encoding="utf-8")
                    except Exception:  # noqa: BLE001
                        pass
                    writes.append("uid")
        except Exception:  # noqa: BLE001
            pass  # UID capture is best-effort; do not fail the token step

        marker.write_text("1", encoding="utf-8")
        return StepResult(
            status="completed",
            output={"note": f"token saved ({', '.join(writes) or 'nowhere'})", "validated": True},
        )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="telegram-bot",
        title="Enable Telegram notifications",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="optional",
    )


def _step_skills_activation(navig_dir: Path) -> OnboardingStep:
    """Let user choose which skill packs to activate."""
    marker = navig_dir / ".skills_configured"
    config_path = navig_dir / "config.yaml"

    def run() -> StepResult:
        if marker.exists():
            return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        # Read available packs from config
        available: list[str] = []
        try:
            import yaml  # type: ignore[import]

            cfg: dict[str, Any] = {}
            if config_path.exists():
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            available = cfg.get("skills", {}).get("available_packs", [])
        except Exception:  # noqa: BLE001
            pass

        if not available:
            marker.write_text("none", encoding="utf-8")
            return StepResult(status="skipped", output={"reason": "no packs configured"})

        import typer

        from navig import console_helper as ch

        ch.info("Available skill packs:")
        for i, pack in enumerate(available, start=1):
            ch.dim(f"    [{i}] {pack}")

        try:
            selection = typer.prompt(
                "  Packs to activate (comma-separated numbers, or 'all')",
                default="all",
            ).strip()
        except KeyboardInterrupt:
            raise
        except EOFError:
            selection = "all"

        if selection.lower() == "all":
            chosen = available
        else:
            chosen = []
            for part in selection.split(","):
                try:
                    idx = int(part.strip()) - 1
                    if 0 <= idx < len(available):
                        chosen.append(available[idx])
                except ValueError:
                    pass  # malformed value; skip

        try:
            import yaml  # type: ignore[import]

            cfg2: dict[str, Any] = {}
            if config_path.exists():
                cfg2 = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg2.setdefault("skills", {})["active_packs"] = chosen
            config_path.write_text(yaml.dump(cfg2, allow_unicode=True), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

        marker.write_text(",".join(chosen), encoding="utf-8")
        return StepResult(
            status="completed",
            output={"activePacks": chosen},
        )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="skills-activation",
        title="Activate skill packs",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="optional",
    )


# ── Phase 1 bootstrap steps (added in v2) ─────────────────────────────────────


def _step_sigil_genesis(navig_dir: Path, genesis: GenesisData) -> OnboardingStep:
    """Initialise the node's cryptographic sigil identity."""
    marker = navig_dir / "state" / ".sigil_initialized"

    def run() -> StepResult:
        if marker.exists():
            return StepResult(status="completed", output={"note": "already initialized"})

        try:
            from navig.identity.sigil_store import ensure_sigil  # type: ignore[import]

            ensure_sigil(genesis)
        except Exception:  # noqa: BLE001
            pass

        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(getattr(genesis, "nodeId", ""), encoding="utf-8")
        return StepResult(
            status="completed",
            output={"nodeId": getattr(genesis, "nodeId", "")},
        )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="sigil-genesis",
        title="Initialise node sigil",
        run=run,
        verify=verify,
        on_failure="skip",
        phase="bootstrap",
    )


def _step_core_navig(navig_dir: Path) -> OnboardingStep:
    """Verify core NAVIG config structure is in place."""
    marker = navig_dir / ".core_navig_initialized"

    def run() -> StepResult:
        if marker.exists():
            return StepResult(status="completed", output={"note": "already initialized"})

        # Ensure the base directory tree exists
        for sub in ("state", "logs", "vault"):
            (navig_dir / sub).mkdir(parents=True, exist_ok=True)

        marker.write_text("1", encoding="utf-8")

        # Advisory: remind user to install fzf for the best picker experience.
        # readchar is now a hard dep so Tier 2 is always available post-install;
        # fzf (Tier 1) is a system binary we cannot install via pip.
        if shutil.which("fzf") is None:
            from navig import console_helper as ch  # noqa: PLC0415

            ch.dim("  Tip: install fzf for the best picker UI (Tier 1 selector):")
            if sys.platform == "win32":
                ch.dim("    winget install junegunn.fzf")
            elif sys.platform == "darwin":
                ch.dim("    brew install fzf")
            else:
                ch.dim("    sudo apt install fzf   # or: pacman -S fzf / dnf install fzf")

        return StepResult(status="completed", output={"navig_dir": str(navig_dir)})

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="core-navig",
        title="Verify core NAVIG structure",
        run=run,
        verify=verify,
        on_failure="skip",
        phase="bootstrap",
    )


# ── Phase 2 integration steps (added in v2) ───────────────────────────────────


def _step_matrix(navig_dir: Path) -> OnboardingStep:
    """Configure Matrix homeserver integration."""
    marker = navig_dir / ".matrix_configured"
    config_path = navig_dir / "config.yaml"

    def run() -> StepResult:
        if marker.exists():
            content = marker.read_text(encoding="utf-8").strip()
            if content and content != "skipped":
                return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        try:
            homeserver_url = typer.prompt(
                "  Matrix homeserver URL (leave blank to skip)",
                default="",
            ).strip()
        except (KeyboardInterrupt, EOFError):
            homeserver_url = ""

        if not homeserver_url:
            marker.write_text("skipped", encoding="utf-8")
            return StepResult(status="skipped", output={"reason": "no homeserver URL provided"})

        try:
            access_token = typer.prompt(
                "  Matrix access token (leave blank to skip)",
                default="",
            ).strip()
        except (KeyboardInterrupt, EOFError):
            access_token = ""

        if not access_token:
            marker.write_text("skipped", encoding="utf-8")
            return StepResult(status="skipped", output={"reason": "no access token provided"})

        # Validate — import here to avoid circular deps at module load
        from navig.onboarding.validators import validate_matrix

        validation = validate_matrix(homeserver_url, access_token)

        if not validation.ok:
            msgs = "; ".join(e.get("message", "") for e in validation.errors)
            typer.echo(f"  ✗ Validation failed: {msgs}", err=True)
            return StepResult(status="skipped", output={"reason": msgs})

        if not typer.confirm("  Token validated. Save and continue?", default=True):
            return StepResult(status="skipped", output={"reason": "user declined"})

        default_room_id = typer.prompt("  Default room ID (e.g. !abc:matrix.org)").strip()

        # Persist token to vault
        try:
            from navig.vault.core import get_vault  # type: ignore[import]

            vault = get_vault()
            vault.put("matrix/access_token", json.dumps({"value": access_token}).encode())
        except Exception:  # noqa: BLE001
            pass

        # Persist config
        try:
            import yaml  # type: ignore[import]

            cfg: dict[str, Any] = {}
            if config_path.exists():
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("matrix", {})["homeserver_url"] = homeserver_url
            cfg["matrix"]["default_room_id"] = default_room_id
            config_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

        marker.write_text("1", encoding="utf-8")
        return StepResult(
            status="completed",
            output={
                "homeserver_url": homeserver_url,
                "default_room_id": default_room_id,
            },
        )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="matrix",
        title="Configure Matrix homeserver",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="optional",
    )


def _step_email(navig_dir: Path) -> OnboardingStep:
    """Configure outbound SMTP / email settings."""
    marker = navig_dir / ".email_configured"
    config_path = navig_dir / "config.yaml"

    def run() -> StepResult:
        if marker.exists():
            content = marker.read_text(encoding="utf-8").strip()
            if content and content != "skipped":
                return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        smtp_host = typer.prompt("  SMTP host (or blank to skip)", default="").strip()
        if not smtp_host:
            marker.write_text("skipped", encoding="utf-8")
            return StepResult(status="skipped", output={"reason": "no SMTP host provided"})

        smtp_port_str = typer.prompt("  SMTP port", default="587").strip()
        try:
            smtp_port = int(smtp_port_str)
        except ValueError:
            smtp_port = 587

        from navig.onboarding.validators import validate_smtp

        validation = validate_smtp(smtp_host, smtp_port)

        if not validation.ok:
            msgs = "; ".join(e.get("message", "") for e in validation.errors)
            typer.echo(f"  ✗ Validation failed: {msgs}", err=True)
            return StepResult(status="skipped", output={"reason": msgs})

        try:
            import yaml  # type: ignore[import]

            cfg: dict[str, Any] = {}
            if config_path.exists():
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("email", {})["smtp_host"] = smtp_host
            cfg["email"]["smtp_port"] = smtp_port
            config_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

        marker.write_text("1", encoding="utf-8")
        return StepResult(
            status="completed",
            output={"smtp_host": smtp_host, "smtp_port": smtp_port},
        )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="email",
        title="Configure email / SMTP",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="optional",
    )


def _step_social_networks(navig_dir: Path) -> OnboardingStep:
    """Configure social-network integrations (Twitter/X, Mastodon, etc.)."""
    marker = navig_dir / ".social_configured"

    def run() -> StepResult:
        if marker.exists():
            content = marker.read_text(encoding="utf-8").strip()
            if content and content != "skipped":
                return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        configure = typer.confirm("  Configure social network integrations?", default=False)
        if not configure:
            marker.write_text("skipped", encoding="utf-8")
            return StepResult(status="skipped", output={"reason": "user declined"})

        marker.write_text("1", encoding="utf-8")
        return StepResult(status="completed", output={})

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="social-networks",
        title="Configure social networks",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="optional",
    )


# Known env vars to offer importing into vault at the runtime-secrets step.
# AI-provider keys (OPENAI_API_KEY, ANTHROPIC_API_KEY, GROQ_API_KEY, etc.) are
# detected and imported earlier at the ai-provider step; only non-provider keys
# belong here.
_ENV_KEY_IMPORTS: list[tuple[str, str, str]] = [
    ("SERPAPI_KEY", "serpapi/key", "SerpAPI key"),
    ("DEEPGRAM_API_KEY", "deepgram/api_key", "Deepgram API key"),
]

# AI-provider keys are handled at ai-provider step first, but runtime-secrets can
# still import them as a best-effort backfill when present in environment.
_AI_RUNTIME_KEY_IMPORTS: list[tuple[str, str, str]] = [
    ("OPENAI_API_KEY", "openai/api_key", "OpenAI key"),
    ("ANTHROPIC_API_KEY", "anthropic/api_key", "Anthropic key"),
]

# Subset of _ENV_KEY_IMPORTS that map to NAVIG AI-provider IDs.
# When runtime-secrets imports one of these, the ai-provider step is
# retroactively marked as completed so the onboarding summary is accurate.
_AI_PROVIDER_ENV_MAP: dict[str, str] = {
    "OPENAI_API_KEY": "openai",
    "ANTHROPIC_API_KEY": "anthropic",
}


def _step_runtime_secrets(navig_dir: Path) -> OnboardingStep:
    """Import runtime API keys from environment into the NAVIG vault."""
    marker = navig_dir / ".runtime_secrets_configured"

    def run() -> StepResult:  # noqa: C901
        if marker.exists():
            return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        try:
            from navig.vault.core import get_vault  # type: ignore[import]

            vault = get_vault()
        except Exception:  # noqa: BLE001
            vault = None
        if vault is None:
            try:
                from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]

                vault = get_vault_v2()
            except Exception:  # noqa: BLE001
                vault = None

        imported: list[str] = []
        # Tracks the first AI-provider key imported so we can retroactively
        # mark the ai-provider step as completed in the onboarding summary.
        _ai_provider_retroactive: dict | None = None
        _ai_provider_marker = navig_dir / ".ai_provider_configured"

        # ── 1. Offer to import env-var API keys ──────────────────────────
        import typer

        from navig import console_helper as ch

        for env_var, vault_label, display_name in _ENV_KEY_IMPORTS:
            val = os.environ.get(env_var, "").strip()
            if not val:
                continue
            try:
                if typer.confirm(f"  Import {display_name} from environment?", default=True):
                    if vault is not None:
                        vault.put(vault_label, json.dumps({"value": val}).encode())
                    imported.append(display_name)
                    # If this is an AI-provider key and we haven't already
                    # scheduled a retroactive update, prepare one now and write
                    # the ai-provider marker so verify() reflects reality.
                    is_ai_provider_key = env_var in _AI_PROVIDER_ENV_MAP
                    should_emit_ai_update = _ai_provider_retroactive is None and is_ai_provider_key
                    if should_emit_ai_update:
                        provider_id = _AI_PROVIDER_ENV_MAP[env_var]
                        _ai_provider_marker.write_text(provider_id, encoding="utf-8")
                        _ai_provider_retroactive = {
                            "id": "ai-provider",
                            "status": "completed",
                            "output": {
                                "provider": provider_id,
                                "keySource": "environment",
                                "note": "configured via runtime-secrets",
                            },
                        }
            except KeyboardInterrupt:
                raise
            except (EOFError, OSError):
                break

        # ── 1b. Backfill AI-provider env keys without prompting ───────────
        # These should not be re-offered in runtime-secrets UI, but importing
        # them here keeps setup resilient when ai-provider was skipped.
        for env_var, vault_label, display_name in _AI_RUNTIME_KEY_IMPORTS:
            val = os.environ.get(env_var, "").strip()
            if not val:
                continue
            try:
                if vault is not None:
                    vault.put(vault_label, json.dumps({"value": val}).encode())
                imported.append(display_name)
                is_ai_provider_key = env_var in _AI_PROVIDER_ENV_MAP
                should_emit_ai_update = _ai_provider_retroactive is None and is_ai_provider_key
                if should_emit_ai_update:
                    provider_id = _AI_PROVIDER_ENV_MAP[env_var]
                    _ai_provider_marker.write_text(provider_id, encoding="utf-8")
                    _ai_provider_retroactive = {
                        "id": "ai-provider",
                        "status": "completed",
                        "output": {
                            "provider": provider_id,
                            "keySource": "environment",
                            "note": "configured via runtime-secrets",
                        },
                    }
            except Exception:  # noqa: BLE001
                pass

        # ── 2. Offer Google service-account JSON ─────────────────────────
        ch.dim("  Paste Google service account JSON (or blank to skip):")

        lines: list[str] = []
        try:
            while True:
                line = input()
                if line.strip() in ("", "END"):
                    break
                lines.append(line)
        except KeyboardInterrupt:
            raise
        except EOFError:
            lines = []

        if lines:
            json_str = "\n".join(lines)
            try:
                json.loads(json_str)  # validate JSON
                if vault is not None:
                    vault.put_json_file("google/vision-service-account", json_str)
                    vault.put_json_file("google/tts-service-account", json_str)
            except (ValueError, Exception):  # noqa: BLE001
                pass

        marker.write_text("1", encoding="utf-8")
        out: dict[str, object] = {"importedFromEnv": imported}
        if _ai_provider_retroactive is not None:
            out["_retroactiveUpdates"] = [_ai_provider_retroactive]
        return StepResult(
            status="completed",
            output=out,
        )

    def verify() -> bool:
        return marker.exists()

    return OnboardingStep(
        id="runtime-secrets",
        title="Import runtime secrets",
        run=run,
        verify=verify,
        on_failure="skip",
        independent=True,
        phase="configuration",
        tier="optional",
    )


def _step_review(navig_dir: Path, step_titles: dict[str, str] | None = None) -> OnboardingStep:
    """Show onboarding summary and let the user revisit any step."""
    artifact = navig_dir / "onboarding.json"
    _step_titles: dict[str, str] = dict(step_titles or {})

    # Phase grouping for summary display.
    _PHASE_GROUPS: list[tuple[str, list[str]]] = [
        (
            "Bootstrap",
            [
                "workspace-init",
                "workspace-templates",
                "config-file",
                "configure-ssh",
                "verify-network",
                "sigil-genesis",
                "core-navig",
            ],
        ),
        (
            "Configuration",
            [
                "ai-provider",
                "vault-init",
                "web-search-provider",
                "first-host",
            ],
        ),
        (
            "Integrations & Optional",
            [
                "telegram-bot",
                "matrix",
                "email",
                "social-networks",
                "runtime-secrets",
                "skills-activation",
                "review",
            ],
        ),
    ]

    def run() -> StepResult:
        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        from navig import console_helper as ch

        # Print summary from artifact if available; collect known step IDs.
        known_ids: list[str] = []
        if artifact.exists():
            try:
                data = json.loads(artifact.read_text(encoding="utf-8"))
                steps_summary = data.get("steps", [])
                status_by_id: dict[str, str] = {}
                for s in steps_summary:
                    sid = s.get("id", "?")
                    status_by_id[sid] = s.get("status", "?")
                    if sid and sid != "?":
                        known_ids.append(sid)
                sys.stdout.write("\n")
                ch.subheader("Onboarding Summary")

                for phase_label, phase_ids in _PHASE_GROUPS:
                    # Only show phases that have at least one step present.
                    present = [sid for sid in phase_ids if sid in status_by_id]
                    if not present:
                        continue
                    ch.dim(f"  {phase_label}")
                    for sid in present:
                        status = status_by_id.get(sid, "?")
                        title = _step_titles.get(sid, sid)
                        if status == "completed":
                            ch.success(f"  {sid:<24} {title}")
                        elif status == "failed":
                            ch.error(f"  {sid:<24} {title}")
                        else:
                            ch.dim(f"    [·] {sid:<24} {title}")
            except Exception:  # noqa: BLE001
                pass

        if typer.confirm("  All settings look good?", default=True):
            return StepResult(status="completed", output={})

        # Show the valid step IDs so the user can make an informed choice.
        if known_ids:
            ch.dim("  Valid step IDs: " + ", ".join(known_ids))

        jump_to = ""
        while True:
            try:
                jump_to = typer.prompt("  Which step ID to revisit?").strip()
            except EOFError:
                jump_to = ""
                break
            except KeyboardInterrupt:
                raise

            if not jump_to:
                break

            if not known_ids or jump_to in known_ids:
                # Valid choice (or no known IDs to validate against).
                break

            ch.warning(f"  Unknown step ID '{jump_to}'.")
            ch.dim(f"  Valid IDs: {', '.join(known_ids)}")

        return StepResult(status="skipped", output={"jumpTo": jump_to})

    def verify() -> bool:
        return False  # always reruns

    return OnboardingStep(
        id="review",
        title="Review onboarding summary",
        run=run,
        verify=verify,
        on_failure="skip",
        phase="configuration",
        tier="optional",
    )
