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
    return [
        # ── Phase 1: bootstrap ────────────────────────────────────────────
        _step_workspace_init(navig_dir),
        _step_workspace_templates(navig_dir),
        _step_config_file(navig_dir, genesis, config.reset),
        _step_configure_ssh(navig_dir),
        _step_verify_network(),
        _step_sigil_genesis(navig_dir, genesis),
        _step_core_navig(navig_dir),
        # ── Phase 2: interactive configuration ───────────────────────────
        _step_ai_provider(navig_dir),
        _step_vault_init(navig_dir),
        _step_first_host(navig_dir),
        _step_matrix(navig_dir),
        _step_telegram_bot(navig_dir),
        _step_email(navig_dir),
        _step_social_networks(navig_dir),
        _step_runtime_secrets(navig_dir),
        _step_skills_activation(navig_dir),
        _step_review(navig_dir),
    ]


# ── TTY helper ────────────────────────────────────────────────────────────

def _tty_check() -> StepResult | None:
    """Return a skipped StepResult if stdin is not a TTY, else None."""
    if not sys.stdin.isatty():
        return StepResult(
            status="skipped",
            output={"reason": "non-interactive environment"},
        )
    return None


# ── Individual step factories ──────────────────────────────────────────────

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
    config_path   = navig_dir / "config.yaml"
    config_content = (
        f"# NAVIG configuration\n"
        f"# Node: {genesis.nodeId}\n"
        f"# Generated: {genesis.bornAt}\n\n"
        f"node:\n"
        f"  id: {genesis.nodeId}\n"
        f"  name: {genesis.name}\n\n"
        f"agents:\n"
        f"  defaults:\n"
        f"    model: openrouter\n"
        f"    workspace: {navig_dir / 'workspace'}\n"
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
                SimpleNamespace(id="openrouter",    display_name="OpenRouter",     requires_key=True,  tier="cloud"),
                SimpleNamespace(id="openai",         display_name="OpenAI",         requires_key=True,  tier="cloud"),
                SimpleNamespace(id="anthropic",      display_name="Anthropic",      requires_key=True,  tier="cloud"),
                SimpleNamespace(id="ollama",         display_name="Ollama (local)", requires_key=False, tier="local"),
            ]

    def _fast_path_key(provider_id: str) -> str:
        """Return an env-var API key for *provider_id* if one is set, else ''."""
        try:
            from navig.providers.registry import get_provider
            manifest = get_provider(provider_id)
            if manifest:
                for var in manifest.env_vars:
                    val = os.environ.get(var, "").strip()
                    if val:
                        return val
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical
        # Fallback: check common vars by ID
        _COMMON = {
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
        for var in _COMMON.get(provider_id, []):
            val = os.environ.get(var, "").strip()
            if val:
                return val
        return ""

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
        providers = _load_providers()

        sys.stdout.write("\n  Choose your AI provider:\n")
        for i, p in enumerate(providers, start=1):
            local_tag = "  (local, no key needed)" if not getattr(p, "requires_key", True) else ""
            sys.stdout.write(f"    [{i}] {p.display_name}{local_tag}\n")
        sys.stdout.write("\n")
        sys.stdout.flush()

        try:
            choice_raw = typer.prompt("  Provider", default="1")
            idx = int(choice_raw.strip()) - 1
            if idx < 0 or idx >= len(providers):
                raise ValueError("out of range")
        except (ValueError, KeyboardInterrupt, EOFError):
            return StepResult(
                status="skipped",
                output={"reason": "invalid selection or interrupted"},
            )

        chosen = providers[idx]
        pid = chosen.id
        label = chosen.display_name
        requires_key = getattr(chosen, "requires_key", True)

        if not requires_key:
            # Local provider — no key needed
            api_key = "local"
        else:
            try:
                api_key = typer.prompt(
                    f"  {label} API key",
                    hide_input=True,
                    default="",
                ).strip()
            except (KeyboardInterrupt, EOFError):
                return StepResult(status="skipped", output={"reason": "interrupted"})
            if not api_key:
                return StepResult(status="skipped", output={"reason": "no key entered"})

        # Persist in vault if available, else fall back to marker file
        try:
            from navig.vault.core_v2 import get_vault_v2
            vault = get_vault_v2()
            if vault is not None:
                vault.put(
                    f"{pid}/api_key",
                    json.dumps({"value": api_key}).encode(),
                )
        except Exception:  # noqa: BLE001
            pass

        marker.write_text(pid, encoding="utf-8")
        return StepResult(
            status="completed",
            output={"provider": pid, "keySource": "interactive"},
        )

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
    )



def _step_configure_ssh(navig_dir: Path) -> OnboardingStep:
    ssh_dir  = Path.home() / ".ssh"
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
                    "ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key_path),
                    "-C", f"navig-key@{socket.gethostname()}",
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

        import typer
        try:
            pw = typer.prompt("  Vault passphrase", hide_input=True, default="").strip()
            if not pw:
                return StepResult(status="skipped", output={"reason": "empty passphrase"})
            confirm = typer.prompt("  Confirm passphrase", hide_input=True, default="").strip()
        except (KeyboardInterrupt, EOFError):
            return StepResult(status="skipped", output={"reason": "interrupted"})

        if pw != confirm:
            return StepResult(status="skipped", output={"reason": "passphrases did not match"})

        try:
            from navig.vault.core_v2 import VaultV2
            vault_dir = navig_dir / "vault"
            vault_dir.mkdir(parents=True, exist_ok=True)
            VaultV2(vault_dir).unlock(passphrase=pw.encode())
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
            answer = typer.prompt(
                "  Add a remote host now? (y/N)",
                default="n",
            ).strip().lower()
        except (KeyboardInterrupt, EOFError):
            answer = "n"

        if answer == "y":
            import subprocess
            subprocess.run([sys.executable, "-m", "navig", "host", "add"], check=False)
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
    )


def _step_telegram_bot(navig_dir: Path) -> OnboardingStep:
    """Optionally configure a Telegram bot token for notifications.

    Token storage strategy (dual-write for broad compatibility):
    1. Vault  — primary, secure (navig.vault.core_v2); requires vault-init step.
    2. .env   — legacy; used by the shell/PS1 installers and daemon env loading.
    3. config.yaml — legacy fallback for pre-vault installs; matches install.sh pattern.

    All three writes are individually non-fatal.  Missing any one does not fail
    the step \u2014 the token is preserved in at least one location.
    """
    marker  = navig_dir / ".telegram_configured"
    config_path = navig_dir / "config.yaml"

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
        except (KeyboardInterrupt, EOFError):
            token = ""

        if not token:
            return StepResult(status="skipped", output={"reason": "no token entered"})

        writes: list[str] = []

        # 1. Primary: vault (secure, per-user credential store)
        try:
            from navig.vault.core_v2 import get_vault_v2
            vault = get_vault_v2()
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
            env_path.chmod(0o600)
            writes.append(".env")
        except Exception:  # noqa: BLE001
            pass

        # 3. Legacy: config.yaml (backward compat for pre-vault setups; mirrors installer pattern)
        try:
            import yaml  # type: ignore[import]
            cfg: dict[str, Any] = {}
            if config_path.exists():
                cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            cfg.setdefault("telegram", {})["bot_token"] = token
            config_path.write_text(yaml.dump(cfg, allow_unicode=True), encoding="utf-8")
            writes.append("config.yaml")
        except Exception:  # noqa: BLE001
            pass

        marker.write_text("1", encoding="utf-8")
        return StepResult(
            status="completed",
            output={"note": f"token saved ({', '.join(writes) or 'nowhere'})"},
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
    )


def _step_skills_activation(navig_dir: Path) -> OnboardingStep:
    """Let user choose which skill packs to activate."""
    marker      = navig_dir / ".skills_configured"
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
        sys.stdout.write("\n  Available skill packs:\n")
        for i, pack in enumerate(available, start=1):
            sys.stdout.write(f"    [{i}] {pack}\n")
        sys.stdout.write("\n")
        sys.stdout.flush()

        try:
            selection = typer.prompt(
                "  Packs to activate (comma-separated numbers, or 'all')",
                default="all",
            ).strip()
        except (KeyboardInterrupt, EOFError):
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
    )


# ── Phase 1 bootstrap steps (added in v2) ─────────────────────────────────────

def _step_sigil_genesis(navig_dir: Path, genesis: "GenesisData") -> OnboardingStep:
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
    marker      = navig_dir / ".matrix_configured"
    config_path = navig_dir / "config.yaml"

    def run() -> StepResult:
        if marker.exists():
            return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        homeserver_url = typer.prompt("  Matrix homeserver URL").strip()
        access_token   = typer.prompt("  Matrix access token").strip()

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
            from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]
            vault = get_vault_v2()
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
    )


def _step_email(navig_dir: Path) -> OnboardingStep:
    """Configure outbound SMTP / email settings."""
    marker      = navig_dir / ".email_configured"
    config_path = navig_dir / "config.yaml"

    def run() -> StepResult:
        if marker.exists():
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
    )


def _step_social_networks(navig_dir: Path) -> OnboardingStep:
    """Configure social-network integrations (Twitter/X, Mastodon, etc.)."""
    marker = navig_dir / ".social_configured"

    def run() -> StepResult:
        if marker.exists():
            return StepResult(status="completed", output={"note": "already configured"})

        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        configure = typer.confirm(
            "  Configure social network integrations?", default=False
        )
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
    )


# Known env vars to offer importing into vault
_ENV_KEY_IMPORTS: list[tuple[str, str, str]] = [
    ("OPENAI_API_KEY",    "openai/api_key",    "OpenAI API key"),
    ("ANTHROPIC_API_KEY", "anthropic/api_key", "Anthropic API key"),
    ("SERPAPI_KEY",       "serpapi/key",       "SerpAPI key"),
    ("DEEPGRAM_API_KEY",  "deepgram/api_key",  "Deepgram API key"),
]


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
            from navig.vault.core_v2 import get_vault_v2  # type: ignore[import]
            vault = get_vault_v2()
        except Exception:  # noqa: BLE001
            vault = None

        imported: list[str] = []

        # ── 1. Offer to import env-var API keys ──────────────────────────
        import typer

        for env_var, vault_label, display_name in _ENV_KEY_IMPORTS:
            val = os.environ.get(env_var, "").strip()
            if not val:
                continue
            try:
                if typer.confirm(f"  Import {display_name} from environment?", default=True):
                    if vault is not None:
                        vault.put(vault_label, json.dumps({"value": val}).encode())
                    imported.append(display_name)
            except (KeyboardInterrupt, EOFError):
                break

        # ── 2. Offer Google service-account JSON ─────────────────────────
        sys.stdout.write(
            "  Paste Google service account JSON (or blank to skip):\n"
        )
        sys.stdout.flush()

        lines: list[str] = []
        try:
            while True:
                line = input()
                if line.strip() in ("", "END"):
                    break
                lines.append(line)
        except (KeyboardInterrupt, EOFError):
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
        return StepResult(
            status="completed",
            output={"importedFromEnv": imported},
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
    )


def _step_review(navig_dir: Path) -> OnboardingStep:
    """Show onboarding summary and let the user revisit any step."""
    artifact = navig_dir / "onboarding.json"

    def run() -> StepResult:
        tty = _tty_check()
        if tty is not None:
            return tty

        import typer

        # Print summary from artifact if available
        if artifact.exists():
            try:
                data = json.loads(artifact.read_text(encoding="utf-8"))
                steps_summary = data.get("steps", [])
                sys.stdout.write("\n  Onboarding summary:\n")
                for s in steps_summary:
                    status_icon = "✓" if s.get("status") == "completed" else "·"
                    sys.stdout.write(f"    [{status_icon}] {s.get('id', '?')} — {s.get('status', '?')}\n")
                sys.stdout.write("\n")
                sys.stdout.flush()
            except Exception:  # noqa: BLE001
                pass

        if typer.confirm("  All settings look good?", default=True):
            return StepResult(status="completed", output={})

        try:
            jump_to = typer.prompt("  Which step ID to revisit?").strip()
        except (KeyboardInterrupt, EOFError):
            jump_to = ""

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
    )
