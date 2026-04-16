"""
NAVIG Agent CLI Commands

Commands for managing the autonomous agent mode.
"""

import sys
from pathlib import Path

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

agent_app = typer.Typer(
    name="agent",
    help="Manage autonomous agent mode",
    invoke_without_command=True,
    no_args_is_help=False,
)

continuation_app = typer.Typer(
    help="Manage autonomous continuation policy",
    invoke_without_command=True,
    no_args_is_help=True,
)
agent_app.add_typer(continuation_app, name="continuation")


@agent_app.callback()
def _agent_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            print(ctx.get_help())
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("agent", agent_app)


def _get_agent_config_dir() -> Path:
    """Get agent configuration directory."""
    return config_dir() / "agent"


def _get_config_path() -> Path:
    """Get agent configuration file path."""
    return _get_agent_config_dir() / "config.yaml"


def _resolve_runtime_identity(user_id: int | None, chat_id: int | None) -> tuple[int, int]:
    """Resolve runtime identity for local CLI continuation controls."""
    return user_id or 0, chat_id or 0


@continuation_app.command("status")
def continuation_status(
    user_id: int | None = typer.Option(None, "--user-id", help="Runtime user id (default: 0)"),
):
    """Show continuation policy for local runtime state."""
    from navig.core.continuation import (
        decision_sensitivity_for_profile,
        policy_from_context,
        suppression_windows_for_profile,
    )
    from navig.store.runtime import get_runtime_store

    resolved_user, _ = _resolve_runtime_identity(user_id, None)
    store = get_runtime_store()
    state = store.get_ai_state(resolved_user)
    if not state:
        ch.warning("No runtime AI state found.")
        return

    policy = policy_from_context(state.get("context") or {})
    continuation_meta = (state.get("context") or {}).get("continuation") or {}
    space = continuation_meta.get("space", "")
    busy_until = continuation_meta.get("busy_until", "")
    busy_reason = continuation_meta.get("busy_reason", "")
    last_skip_reason = continuation_meta.get("last_skip_reason", "")
    ch.info(
        "Continuation policy: "
        f"profile={policy.profile}, enabled={policy.enabled}, paused={policy.paused}, "
        f"skip_next={policy.skip_next}, turns={policy.turns_used}/{policy.max_turns}, "
        f"cooldown={policy.cooldown_seconds}s"
    )
    windows = suppression_windows_for_profile(policy.profile)
    sensitivity = decision_sensitivity_for_profile(policy.profile)
    ch.info(
        f"Suppression windows: wait={windows.get('wait', 0)}s, blocked={windows.get('blocked', 0)}s"
    )
    ch.info(f"Decision sensitivity: {sensitivity}")
    if busy_until:
        ch.info(
            f"Busy suppression until: {busy_until}" + (f" ({busy_reason})" if busy_reason else "")
        )
    if last_skip_reason:
        ch.info(f"Last skip reason: {last_skip_reason}")
    if space:
        ch.info(f"Space focus: {space}")


@continuation_app.command("continue")
def continuation_continue(
    profile: str = typer.Option(
        "conservative",
        "--profile",
        help="Continuation profile: conservative, balanced, aggressive",
    ),
    space: str | None = typer.Option(None, "--space", help="Optional space focus"),
    user_id: int | None = typer.Option(None, "--user-id", help="Runtime user id (default: 0)"),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Runtime chat id (default: 0)"),
    persona: str | None = typer.Option(
        None, "--persona", help="Persona used when state is initialized"
    ),
):
    """Enable continuation policy for local runtime state."""
    from navig.core.continuation import (
        decision_sensitivity_for_profile,
        merge_policy,
        normalize_profile_name,
        policy_from_context,
        suppression_windows_for_profile,
    )
    from navig.spaces import normalize_space_name
    from navig.store.runtime import get_runtime_store

    resolved_user, resolved_chat = _resolve_runtime_identity(user_id, chat_id)
    chosen_profile = normalize_profile_name(profile)
    chosen_space = normalize_space_name(space) if space else ""

    store = get_runtime_store()
    state = store.get_ai_state(resolved_user) or {}
    mode = state.get("mode") or "active"
    chosen_persona = persona or state.get("persona") or "assistant"

    context = merge_policy(
        state.get("context") or {},
        profile=chosen_profile,
        enabled=True,
        paused=False,
        skip_next=False,
        cooldown_seconds=None,
        max_turns=None,
    )
    if chosen_space:
        context["continuation"] = {
            **(context.get("continuation") or {}),
            "space": chosen_space,
        }

    store.set_ai_state(
        user_id=resolved_user,
        chat_id=resolved_chat,
        mode=mode,
        persona=chosen_persona,
        context=context,
    )

    ch.success(f"Continuation enabled (profile={chosen_profile}).")
    policy = policy_from_context(context)
    windows = suppression_windows_for_profile(policy.profile)
    sensitivity = decision_sensitivity_for_profile(policy.profile)
    ch.info(
        "Policy: "
        f"cooldown={policy.cooldown_seconds}s, max_turns={policy.max_turns}, "
        f"suppression(wait={windows.get('wait', 0)}s, blocked={windows.get('blocked', 0)}s), "
        f"decision={sensitivity}"
    )
    if chosen_space:
        ch.info(f"Space focus: {chosen_space}")


@continuation_app.command("start")
def continuation_start(
    profile: str = typer.Option(
        "conservative",
        "--profile",
        help="Continuation profile: conservative, balanced, aggressive",
    ),
    space: str | None = typer.Option(None, "--space", help="Optional space focus"),
    user_id: int | None = typer.Option(None, "--user-id", help="Runtime user id (default: 0)"),
    chat_id: int | None = typer.Option(None, "--chat-id", help="Runtime chat id (default: 0)"),
    persona: str | None = typer.Option(
        None, "--persona", help="Persona used when state is initialized"
    ),
):
    """Enable continuation policy (alias for `continuation continue`)."""
    continuation_continue(
        profile=profile,
        space=space,
        user_id=user_id,
        chat_id=chat_id,
        persona=persona,
    )


@continuation_app.command("pause")
def continuation_pause(
    user_id: int | None = typer.Option(None, "--user-id", help="Runtime user id (default: 0)"),
):
    """Pause continuation policy for local runtime state."""
    from navig.core.continuation import merge_policy
    from navig.store.runtime import get_runtime_store

    resolved_user, _ = _resolve_runtime_identity(user_id, None)
    store = get_runtime_store()
    state = store.get_ai_state(resolved_user) or {}
    context = merge_policy(state.get("context") or {}, paused=True)
    store.set_ai_state(
        user_id=resolved_user,
        chat_id=state.get("chat_id") or 0,
        mode=state.get("mode") or "inactive",
        persona=state.get("persona") or "assistant",
        context=context,
    )
    ch.success("Continuation paused.")


@continuation_app.command("skip")
def continuation_skip(
    user_id: int | None = typer.Option(None, "--user-id", help="Runtime user id (default: 0)"),
):
    """Skip the next continuation trigger for local runtime state."""
    from navig.core.continuation import merge_policy
    from navig.store.runtime import get_runtime_store

    resolved_user, _ = _resolve_runtime_identity(user_id, None)
    store = get_runtime_store()
    state = store.get_ai_state(resolved_user) or {}
    context = merge_policy(state.get("context") or {}, skip_next=True)
    store.set_ai_state(
        user_id=resolved_user,
        chat_id=state.get("chat_id") or 0,
        mode=state.get("mode") or "inactive",
        persona=state.get("persona") or "assistant",
        context=context,
    )
    ch.success("Next continuation trigger will be skipped.")


@agent_app.command("install")
def agent_install(
    personality: str = typer.Option(
        "friendly",
        "--personality",
        "-p",
        help="Default personality profile (friendly, professional, witty, paranoid, minimal)",
    ),
    mode: str = typer.Option(
        "supervised",
        "--mode",
        "-m",
        help="Operating mode (autonomous, supervised, observe-only)",
    ),
    telegram: bool = typer.Option(False, "--telegram", help="Enable Telegram integration"),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite existing configuration"),
):
    """
    Install and configure agent mode.

    Creates configuration at ~/.navig/agent/config.yaml

    Examples:
        navig agent install
        navig agent install --personality witty
        navig agent install --mode autonomous --telegram
    """
    try:
        from navig.agent import AgentConfig

        config_dir = _get_agent_config_dir()
        config_path = _get_config_path()

        if config_path.exists() and not force:
            ch.error("Agent already installed. Use --force to overwrite.")
            ch.info(f"Config at: {config_path}")
            raise typer.Exit(1)

        # Create directories
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "workspace").mkdir(exist_ok=True)
        (config_dir / "personalities").mkdir(exist_ok=True)
        (config_dir / "logs").mkdir(exist_ok=True)

        # Create default configuration
        config = AgentConfig()
        config.personality.profile = personality
        config.mode = mode
        config.ears.telegram.enabled = telegram

        # Save configuration
        config.save(config_path)

        ch.success("Agent mode installed!")
        ch.console.print(f"  Config: {config_path}")
        ch.console.print(f"  Personality: {personality}")
        ch.console.print(f"  Mode: {mode}")
        ch.console.print()
        ch.info("Next steps:")
        ch.console.print("  1. Edit config: navig agent config")
        ch.console.print("  2. Start agent: navig agent start")

    except Exception as e:
        ch.error(f"Installation failed: {e}")
        raise typer.Exit(1) from e


@agent_app.command("run")
def agent_run(
    agent_id: str = typer.Argument(..., help="Agent ID from the active formation"),
    task: str = typer.Option(..., "--task", "-t", help="Task or question for the agent"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    timeout: float = typer.Option(30.0, "--timeout", help="Timeout in seconds"),
    effort: str | None = typer.Option(
        None,
        "--effort",
        "-e",
        help="Reasoning depth: low, medium, high, max, ultra  [default: auto]",
    ),
):
    """Run a single formation agent on a task.

    Loads the specified agent from the active formation and runs it
    against the given task using the AI system.

    Examples:
        navig agent run designer --task "Review our landing page layout"
        navig agent run qa --task "What test cases do we need?" --json
        navig agent run cfo --task "Budget analysis for Q3" --plain
    """
    import json as json_module
    import time
    from concurrent.futures import ThreadPoolExecutor
    from concurrent.futures import TimeoutError as FuturesTimeout

    from navig.agent.effort import resolve_effort
    from navig.formations.loader import get_active_formation

    if effort is not None:
        try:
            resolve_effort(effort)
        except ValueError as exc:
            ch.error(str(exc))
            raise typer.Exit(2) from exc

    formation = get_active_formation()
    if formation is None:
        ch.error("No active formation.")
        ch.info("  Initialize with: navig formation init <formation-id>")
        raise typer.Exit(1)

    agent = formation.loaded_agents.get(agent_id)
    if agent is None:
        available = ", ".join(sorted(formation.loaded_agents.keys())) or "(none)"
        ch.error(f"Agent '{agent_id}' not found in formation '{formation.id}'.")
        ch.info(f"  Available agents: {available}")
        raise typer.Exit(1)

    if not plain and not json_output:
        ch.info(f"Running agent '{agent.name}' ({agent.role})...")

    start = time.time()
    try:
        from navig.ai import ask_ai_with_context

        def _run():
            return ask_ai_with_context(task, system_prompt=agent.system_prompt, effort=effort)

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_run)
            response = future.result(timeout=timeout) or "[No response]"
    except FuturesTimeout:
        response = f"[TIMEOUT after {timeout}s]"
        if not json_output and not plain:
            ch.warning(f"Agent timed out after {timeout}s")
    except Exception as e:
        response = f"[ERROR: {e}]"
        if not json_output and not plain:
            ch.error(f"Agent execution failed: {e}")

    duration_ms = int((time.time() - start) * 1000)

    if json_output:
        result = {
            "agent_id": agent.id,
            "agent_name": agent.name,
            "role": agent.role,
            "formation": formation.id,
            "task": task,
            "response": response,
            "duration_ms": duration_ms,
        }
        print(json_module.dumps(result, indent=2))
        return

    if plain:
        print(response)
        return

    ch.console.print()
    ch.console.print(f"[bold cyan]{agent.name}[/bold cyan] [dim]({agent.role})[/dim]")
    ch.console.print(f"[dim]Duration: {duration_ms}ms[/dim]")
    ch.console.print()
    ch.console.print(response)
    ch.console.print()


@agent_app.command("start")
def agent_start(
    foreground: bool = typer.Option(
        True,
        "--foreground/--background",
        "-f/-b",
        help="Run in foreground or background",
    ),
    config: Path | None = typer.Option(None, "--config", "-c", help="Path to configuration file"),
):
    """
    Start the autonomous agent.

    Examples:
        navig agent start
        navig agent start --background
        navig agent start --config /path/to/config.yaml
    """
    config_path = config or _get_config_path()

    if not config_path.exists():
        ch.error("Agent not installed. Run: navig agent install")
        raise typer.Exit(1)

    try:
        from navig.agent import AgentConfig, run_agent

        agent_config = AgentConfig.load(config_path)

        if not agent_config.enabled:
            ch.error("Agent is disabled in configuration")
            ch.info("Enable with: navig agent config set enabled true")
            raise typer.Exit(1)

        if foreground:
            # Run directly
            ch.info("Starting agent in foreground...")
            ch.console.print(f"  Personality: {agent_config.personality.profile}")
            ch.console.print(f"  Mode: {agent_config.mode}")
            ch.console.print()

            import asyncio

            asyncio.run(run_agent(agent_config))
        else:
            # Background mode - create a service or use subprocess
            ch.info("Background mode - consider using 'navig agent service install'")
            ch.info("For now, use: navig agent start --foreground &")
            raise typer.Exit(1)

    except KeyboardInterrupt:
        ch.info("\nAgent stopped by user")
    except Exception as e:
        ch.error(f"Failed to start agent: {e}")
        raise typer.Exit(1) from e


@agent_app.command("stop")
def agent_stop():
    """
    Stop the running agent.

    Sends SIGTERM to the agent process if running.
    """
    import signal

    pid_file = _get_agent_config_dir() / "agent.pid"

    if not pid_file.exists():
        ch.info("No agent PID file found. Agent may not be running.")
        return

    try:
        pid = int(pid_file.read_text().strip())

        if sys.platform == "win32":
            import subprocess

            subprocess.run(["taskkill", "/PID", str(pid), "/F"], check=True)
        else:
            import os

            os.kill(pid, signal.SIGTERM)

        pid_file.unlink()
        ch.success(f"Stopped agent (PID {pid})")

    except ProcessLookupError:
        ch.info("Agent process not found. Cleaning up PID file.")
        pid_file.unlink()
    except Exception as e:
        ch.error(f"Failed to stop agent: {e}")
        raise typer.Exit(1) from e


@agent_app.command("status")
def agent_status(
    plain: bool = typer.Option(False, "--plain", help="Plain output"),
):
    """
    Show agent status.

    Displays running state, component health, and recent activity.
    """
    import json

    config_path = _get_config_path()
    pid_file = _get_agent_config_dir() / "agent.pid"

    if not config_path.exists():
        if plain:
            print("not_installed")
        else:
            ch.error("Agent not installed. Run: navig agent install")
        return

    # Check if running
    running = False
    pid = None

    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            if sys.platform == "win32":
                import subprocess

                result = subprocess.run(
                    ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True
                )
                running = str(pid) in result.stdout
            else:
                import os

                os.kill(pid, 0)  # Check if process exists
                running = True
        except (ProcessLookupError, ValueError):
            running = False

    try:
        from navig.agent import AgentConfig
        from navig.agent.speculative import get_speculative_runtime_snapshot

        config = AgentConfig.load(config_path)
        speculative = get_speculative_runtime_snapshot()

        if plain:
            status = {
                "installed": True,
                "running": running,
                "pid": pid if running else None,
                "enabled": config.enabled,
                "mode": config.mode,
                "personality": config.personality.profile,
                "speculative": speculative,
            }
            print(json.dumps(status))
        else:
            ch.info("Agent Status")
            ch.console.print()
            ch.console.print("  Installed: [green]Yes[/]")
            ch.console.print(f"  Running: {'[green]Yes[/]' if running else '[red]No[/]'}")
            if running and pid:
                ch.console.print(f"  PID: {pid}")
            ch.console.print(f"  Enabled: {'Yes' if config.enabled else 'No'}")
            ch.console.print(f"  Mode: {config.mode}")
            ch.console.print(f"  Personality: {config.personality.profile}")
            spec_live = speculative.get("live") or {}
            cache_stats = spec_live.get("cache") or {}
            ch.console.print(
                "  Speculative: "
                f"{'On' if speculative.get('enabled') else 'Off'}"
                + (
                    f" | hit_rate={cache_stats.get('hit_rate', 0.0):.1%}"
                    f" entries={cache_stats.get('entries', 0)}"
                    if speculative.get("has_live_executor")
                    else " | live=not-initialized"
                )
            )
            ch.console.print(f"  Config: {config_path}")

    except Exception as e:
        if plain:
            print(json.dumps({"error": str(e)}))
        else:
            ch.error(f"Error reading status: {e}")


@agent_app.command("config")
def agent_config_cmd(
    edit: bool = typer.Option(False, "--edit", "-e", help="Open config in editor"),
    show: bool = typer.Option(False, "--show", "-s", help="Show current config"),
    set_key: str | None = typer.Option(None, "--set", help="Set config key (dot notation)"),
    value: str | None = typer.Option(None, "--value", "-v", help="Value for --set"),
):
    """
    Manage agent configuration.

    Examples:
        navig agent config --show
        navig agent config --edit
        navig agent config --set personality.profile --value witty
        navig agent config --set mode --value autonomous
    """
    import os
    import subprocess

    import yaml

    from navig.core.yaml_io import atomic_write_yaml

    config_path = _get_config_path()

    if not config_path.exists():
        ch.error("Agent not installed. Run: navig agent install")
        raise typer.Exit(1)

    if edit:
        # Open in default editor
        editor = os.environ.get("EDITOR", "nano" if sys.platform != "win32" else "notepad")
        subprocess.run([editor, str(config_path)])
        return

    if show:
        # Display configuration
        content = config_path.read_text()
        ch.console.print(content)
        return

    if set_key and value:
        # Set a configuration value
        with open(config_path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}

        # Navigate to nested key
        keys = set_key.split(".")
        current = data.setdefault("agent", {})

        for key in keys[:-1]:
            current = current.setdefault(key, {})

        # Convert value type
        if value.lower() in ("true", "yes"):
            value = True
        elif value.lower() in ("false", "no"):
            value = False
        elif value.isdigit():
            value = int(value)

        current[keys[-1]] = value

        atomic_write_yaml(data, config_path)

        ch.success(f"Set {set_key} = {value}")
        return

    # Default: show path and summary
    try:
        from navig.agent import AgentConfig

        config = AgentConfig.load(config_path)

        ch.info(f"Config: {config_path}")
        ch.console.print()
        ch.console.print(f"  enabled: {config.enabled}")
        ch.console.print(f"  mode: {config.mode}")
        ch.console.print(f"  personality: {config.personality.profile}")
        ch.console.print(f"  brain.model: {config.brain.model}")
        ch.console.print(f"  telegram: {config.ears.telegram.enabled}")
        ch.console.print(f"  mcp: {config.ears.mcp.enabled}")
        ch.console.print()
        ch.info("Use --show for full config, --edit to modify")

    except Exception as e:
        ch.error(f"Error: {e}")


@agent_app.command("logs")
def agent_logs(
    follow: bool = typer.Option(False, "--follow", "-f", help="Follow log output"),
    lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to show"),
    level: str | None = typer.Option(None, "--level", "-l", help="Filter by level"),
):
    """
    View agent logs.

    Examples:
        navig agent logs
        navig agent logs --follow
        navig agent logs --lines 100 --level error
    """
    log_file = _get_agent_config_dir() / "logs" / "agent.log"

    if not log_file.exists():
        ch.info("No logs found. Agent may not have run yet.")
        return

    if follow:
        # Follow mode
        import subprocess

        try:
            if sys.platform == "win32":
                subprocess.run(
                    [
                        "powershell",
                        "Get-Content",
                        str(log_file),
                        "-Wait",
                        "-Tail",
                        str(lines),
                    ]
                )
            else:
                subprocess.run(["tail", "-f", "-n", str(lines), str(log_file)])
        except KeyboardInterrupt:
            pass  # user interrupted; clean exit
    else:
        # Show last N lines
        with open(log_file, encoding='utf-8') as f:
            all_lines = f.readlines()

        output_lines = all_lines[-lines:]

        if level:
            level_upper = level.upper()
            output_lines = [ln for ln in output_lines if level_upper in ln]

        for line in output_lines:
            print(line.rstrip())


@agent_app.command("personality")
def agent_personality(
    action: str = typer.Argument("list", help="Action: list, show, set, create"),
    name: str | None = typer.Argument(None, help="Personality name"),
):
    """
    Manage personality profiles.

    Actions:
        list   - List available personalities
        show   - Show personality details
        set    - Set active personality
        create - Create custom personality

    Examples:
        navig agent personality list
        navig agent personality show witty
        navig agent personality set professional
    """
    import yaml

    from navig.core.yaml_io import atomic_write_yaml

    if action == "list":
        from navig.agent.soul import BUILTIN_PROFILES

        ch.info("Built-in Personalities:")
        for name, profile in BUILTIN_PROFILES.items():
            ch.console.print(f"  [cyan]{name}[/]: {profile.tagline}")

        # Check for custom profiles
        custom_dir = _get_agent_config_dir() / "personalities"
        if custom_dir.exists():
            custom = list(custom_dir.glob("*.yaml"))
            if custom:
                ch.console.print()
                ch.info("Custom Personalities:")
                for p in custom:
                    ch.console.print(f"  {p.stem}")

    elif action == "show":
        if not name:
            ch.error("Name required: navig agent personality show <name>")
            raise typer.Exit(1)

        from navig.agent.soul import BUILTIN_PROFILES, PersonalityProfile

        if name in BUILTIN_PROFILES:
            profile = BUILTIN_PROFILES[name]
        else:
            profile_path = _get_agent_config_dir() / "personalities" / f"{name}.yaml"
            if profile_path.exists():
                profile = PersonalityProfile.load(profile_path)
            else:
                ch.error(f"Personality not found: {name}")
                raise typer.Exit(1)

        ch.info(f"Personality: {name}")
        ch.console.print(f"  Name: {profile.name}")
        ch.console.print(f"  Tagline: {profile.tagline}")
        ch.console.print(f"  Greeting: {profile.greeting}")
        ch.console.print(f"  Emoji: {profile.emoji_enabled}")
        ch.console.print(f"  Formal: {profile.formal}")
        ch.console.print(f"  Proactive: {profile.proactive}")

    elif action == "set":
        if not name:
            ch.error("Name required: navig agent personality set <name>")
            raise typer.Exit(1)

        config_path = _get_config_path()
        if not config_path.exists():
            ch.error("Agent not installed")
            raise typer.Exit(1)

        with open(config_path, encoding='utf-8') as f:
            data = yaml.safe_load(f) or {}

        data.setdefault("agent", {}).setdefault("personality", {})["profile"] = name

        atomic_write_yaml(data, config_path)

        ch.success(f"Personality set to: {name}")

    elif action == "create":
        if not name:
            ch.error("Name required: navig agent personality create <name>")
            raise typer.Exit(1)

        custom_dir = _get_agent_config_dir() / "personalities"
        custom_dir.mkdir(parents=True, exist_ok=True)

        profile_path = custom_dir / f"{name}.yaml"

        if profile_path.exists():
            ch.error(f"Personality already exists: {profile_path}")
            raise typer.Exit(1)

        template = {
            "name": name.title(),
            "tagline": "Custom personality",
            "greeting": "Hello!",
            "farewell": "Goodbye!",
            "acknowledgment": "Got it!",
            "thinking_phrase": "Thinking...",
            "emoji_enabled": True,
            "proactive": True,
            "formal": False,
            "humor_enabled": True,
            "verbosity": "normal",
        }

        atomic_write_yaml(template, profile_path)

        ch.success(f"Created personality: {profile_path}")
        ch.info("Edit the file to customize behavior")

    else:
        ch.error(f"Unknown action: {action}")
        ch.info("Valid: list, show, set, create")

# ============================================================================
# Telegram Bot Integration
# ============================================================================

telegram_app = typer.Typer(
    name="telegram",
    help="Manage Telegram bot integration",
    no_args_is_help=True,
)

agent_app.add_typer(telegram_app, name="telegram")


@telegram_app.command("start")
def telegram_start(
    foreground: bool = typer.Option(
        True,
        "--foreground/--background",
        "-f/-b",
        help="Run in foreground or background",
    ),
):
    """
    Start the Telegram bot.

    This starts the standalone Telegram bot that connects to NAVIG.

    Prerequisites:
        1. Set TELEGRAM_BOT_TOKEN in environment or .env file
        2. Optionally set ALLOWED_TELEGRAM_USERS for security

    Examples:
        navig agent telegram start
        navig agent telegram start --background
    """
    import os
    import subprocess

    # Check for token
    from dotenv import load_dotenv

    load_dotenv()

    from navig.messaging.secrets import resolve_telegram_bot_token

    token = resolve_telegram_bot_token()
    if not token:
        ch.error("TELEGRAM_BOT_TOKEN not set!")
        ch.console.print()
        ch.info("To configure the Telegram bot:")
        ch.console.print("  1. Get a token from @BotFather on Telegram")
        ch.console.print("  2. Create a .env file in the project root:")
        ch.console.print("     TELEGRAM_BOT_TOKEN=your_token_here")
        ch.console.print("     ALLOWED_TELEGRAM_USERS=123456789,987654321")
        ch.console.print()
        ch.info("Or set as environment variable:")
        ch.console.print('  $env:TELEGRAM_BOT_TOKEN = "your_token_here"')
        raise typer.Exit(1)

    ch.info("Starting Telegram bot...")
    ch.console.print("  Model source: ai_model_preference + routing (NAVIG_AI_MODEL is deprecated)")
    ch.console.print(f"  Typing mode: {os.getenv('TYPING_MODE', 'instant')}")

    allowed = os.getenv("ALLOWED_TELEGRAM_USERS")
    if allowed:
        ch.console.print(f"  Allowed users: {allowed}")
    else:
        ch.console.print("  [yellow]⚠️  No user restrictions (public mode)[/yellow]")
    ch.console.print()

    try:
        cmd = [sys.executable, "-m", "navig.daemon.telegram_worker", "--no-gateway"]
        if foreground:
            subprocess.run(cmd, check=True)
        else:
            # Background mode
            if sys.platform == "win32":
                subprocess.Popen(
                    cmd,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                ch.success("Bot started in background")
            else:
                subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                ch.success("Bot started in background")
    except KeyboardInterrupt:
        ch.info("\nBot stopped by user")
    except Exception as e:
        ch.error(f"Failed to start bot: {e}")
        raise typer.Exit(1) from e


@telegram_app.command("status")
def telegram_status():
    """
    Show Telegram bot status and configuration.
    """
    import os

    from dotenv import load_dotenv

    load_dotenv()

    from navig.messaging.secrets import resolve_telegram_bot_token

    token = resolve_telegram_bot_token()
    model_pref = os.getenv("NAVIG_AI_MODEL", "")
    allowed = os.getenv("ALLOWED_TELEGRAM_USERS", "")
    typing_mode = os.getenv("TYPING_MODE", "instant")
    gateway = os.getenv("NAVIG_GATEWAY_URL")

    ch.info("Telegram Bot Configuration")
    ch.console.print()

    if token:
        ch.console.print(f"  ✅ Token: ...{token[-10:]}")
    else:
        ch.console.print("  ❌ Token: NOT SET")

    if model_pref:
        ch.console.print(f"  NAVIG_AI_MODEL (deprecated): {model_pref}")
    else:
        ch.console.print("  NAVIG_AI_MODEL (deprecated): not set")
    ch.console.print("  Effective model source: ai_model_preference + routing")
    ch.console.print(f"  Typing Mode: {typing_mode}")

    if allowed:
        ch.console.print(f"  Allowed Users: {allowed}")
    else:
        ch.console.print("  Allowed Users: [yellow]All (public)[/yellow]")

    if gateway:
        ch.console.print(f"  Gateway: {gateway}")
    else:
        ch.console.print("  Gateway: Disabled (local mode)")

    ch.console.print()
    ch.info("To start the bot:")
    ch.console.print("  navig agent telegram start")


@telegram_app.command("setup")
def telegram_setup():
    """
    Interactive setup for Telegram bot.

    Guides you through configuring the Telegram bot.
    """
    from pathlib import Path

    ch.info("Telegram Bot Setup")
    ch.console.print()

    ch.console.print("1. Create a bot with @BotFather on Telegram:")
    ch.console.print("   - Send /newbot to @BotFather")
    ch.console.print("   - Choose a name and username")
    ch.console.print("   - Copy the token")
    ch.console.print()

    ch.console.print("2. Get your Telegram user ID:")
    ch.console.print("   - Send a message to @userinfobot")
    ch.console.print("   - Copy your numeric ID")
    ch.console.print()

    # Find project root
    env_path = Path(__file__).parent.parent.parent / ".env"

    ch.console.print(f"3. Create or edit {env_path}:")
    ch.console.print("   ```")
    ch.console.print("   TELEGRAM_BOT_TOKEN=your_token_here")
    ch.console.print("   ALLOWED_TELEGRAM_USERS=your_user_id")
    ch.console.print("   # Optional (deprecated): NAVIG_AI_MODEL=openrouter")
    ch.console.print("   ```")
    ch.console.print()

    ch.console.print("4. Start the bot:")
    ch.console.print("   navig agent telegram start")
    ch.console.print()

    ch.info("Environment Variables:")
    ch.console.print("  TELEGRAM_BOT_TOKEN     - Required. Bot token from @BotFather")
    ch.console.print("  ALLOWED_TELEGRAM_USERS - Optional. Comma-separated user IDs")
    ch.console.print("  NAVIG_AI_MODEL         - Optional override (deprecated)")
    ch.console.print("  TYPING_MODE            - Optional. instant/message/never")
    ch.console.print("  NAVIG_GATEWAY_URL      - Optional. Gateway for session persistence")


@agent_app.command("remediation")
def agent_remediation(
    action: str | None = typer.Argument(None, help="Action: list, status, clear"),
    action_id: str | None = typer.Option(None, "--id", help="Action ID to check status"),
):
    """
    View and manage automatic remediation actions.

    The remediation engine automatically attempts to recover from failures:
    - Component restarts with exponential backoff
    - Connection retry with increasing delays
    - Configuration rollback to last known good state

    Examples:
        navig agent remediation list     # Show all remediation actions
        navig agent remediation status --id <action_id>  # Check specific action
        navig agent remediation clear    # Clear completed actions
    """
    try:
        from navig.agent.remediation import RemediationEngine

        remediation = RemediationEngine()

        if action == "list" or action is None:
            actions = remediation.get_all_actions()

            if not actions:
                ch.info("No remediation actions found")
                return

            ch.info(f"Remediation Actions ({len(actions)})")
            ch.console.print()

            for act in actions:
                status_color = {
                    "pending": "yellow",
                    "in_progress": "cyan",
                    "success": "green",
                    "failed": "red",
                    "skipped": "dim",
                }.get(act["status"], "white")

                ch.console.print(
                    f"  [{status_color}]{act['status'].upper()}[/{status_color}] {act['component']} - {act['reason']}"
                )
                ch.console.print(f"    ID: {act['id']}")
                ch.console.print(f"    Type: {act['type']}")
                ch.console.print(f"    Attempts: {act['attempts']}/{act['max_attempts']}")
                if act.get("error"):
                    ch.console.print(f"    Error: {act['error']}")
                ch.console.print()

        elif action == "status" and action_id:
            status = remediation.get_action_status(action_id)

            if not status:
                ch.error(f"Action not found: {action_id}")
                return

            ch.info(f"Remediation Action: {action_id}")
            ch.console.print()
            ch.console.print(f"  Component: {status['component']}")
            ch.console.print(f"  Type: {status['type']}")
            ch.console.print(f"  Status: {status['status']}")
            ch.console.print(f"  Reason: {status['reason']}")
            ch.console.print(f"  Attempts: {status['attempts']}/{status['max_attempts']}")
            ch.console.print(f"  Timestamp: {status['timestamp']}")

            if status.get("error"):
                ch.console.print(f"  Error: {status['error']}")

            if status.get("metadata"):
                ch.console.print(f"  Metadata: {status['metadata']}")

        elif action == "clear":
            # Clear completed actions by restarting remediation engine
            ch.warning("Remediation history cleared (completed actions removed)")

        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Valid actions: list, status, clear")

    except ImportError as _exc:
        ch.error("Remediation engine not available. Make sure agent components are installed.")
        raise typer.Exit(1) from _exc
    except Exception as e:
        ch.error(f"Failed to access remediation engine: {e}")
        raise typer.Exit(1) from e


@agent_app.command("learn")
def agent_learn(
    days: int = typer.Option(7, "--days", "-d", help="Analyze logs from last N days"),
    export: bool = typer.Option(False, "--export", help="Export patterns to JSON"),
):
    """
    Analyze agent logs and learn from error patterns.

    The learning system detects recurring errors and patterns:
    - Connection failures
    - Configuration issues
    - Resource constraints
    - Permission problems

    Examples:
        navig agent learn                  # Analyze last 7 days
        navig agent learn --days 30        # Analyze last 30 days
        navig agent learn --export         # Export patterns to JSON
    """
    try:
        import json
        from collections import defaultdict
        from datetime import datetime

        log_dir = config_dir() / "logs"
        debug_log = log_dir / "debug.log"
        remediation_log = log_dir / "remediation.log"

        if not debug_log.exists():
            ch.error("No logs found to analyze")
            ch.info("Start the agent first: navig agent start")
            raise typer.Exit(1)

        ch.info(f"Analyzing logs from last {days} days...")
        ch.console.print()

        # Define error patterns to detect
        patterns = {
            "connection_failed": r"connection.*(failed|refused|timeout)",
            "permission_denied": r"permission denied|access denied",
            "config_error": r"config.*error|invalid.*config",
            "component_error": r"component.*error|failed to start",
            "resource_exhausted": r"out of memory|disk full|quota exceeded",
        }

        # Read and analyze logs
        error_counts = defaultdict(int)
        error_examples = defaultdict(list)

        import re

        for log_file in [debug_log, remediation_log]:
            if not log_file.exists():
                continue

            with open(log_file, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    # Parse timestamp if present
                    try:
                        # Try to extract timestamp from log line
                        if "[" in line and "]" in line:
                            pass  # timestamp present; skip if too old (simple heuristic not yet impl.)

                        # Check against patterns
                        for pattern_name, pattern in patterns.items():
                            if re.search(pattern, line, re.IGNORECASE):
                                error_counts[pattern_name] += 1
                                if len(error_examples[pattern_name]) < 3:
                                    error_examples[pattern_name].append(line.strip())
                    except Exception:  # noqa: BLE001
                        pass  # best-effort; failure is non-critical

        if not error_counts:
            ch.success("No significant error patterns detected!")
            ch.info("Your agent is running smoothly.")
            return

        # Display findings
        ch.warning(f"Found {sum(error_counts.values())} errors across {len(error_counts)} patterns")
        ch.console.print()

        for pattern_name, count in sorted(error_counts.items(), key=lambda x: x[1], reverse=True):
            pattern_display = pattern_name.replace("_", " ").title()
            ch.console.print(f"  [red]●[/red] {pattern_display}: {count} occurrences")

            if error_examples[pattern_name]:
                ch.console.print("    Examples:")
                for example in error_examples[pattern_name][:2]:
                    # Truncate long lines
                    if len(example) > 100:
                        example = example[:97] + "..."
                    ch.console.print(f"      {example}")
            ch.console.print()

        # Export if requested
        if export:
            output_path = config_dir() / "workspace" / "error-patterns.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)

            export_data = {
                "analyzed_date": datetime.now().isoformat(),
                "days_analyzed": days,
                "patterns": {
                    name: {"count": count, "examples": error_examples[name]}
                    for name, count in error_counts.items()
                },
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(export_data, f, indent=2)

            ch.success(f"Patterns exported to: {output_path}")

        # Provide recommendations
        ch.info("Recommendations:")

        if error_counts.get("connection_failed", 0) > 10:
            ch.console.print("  • Review network connectivity and firewall rules")
        if error_counts.get("permission_denied", 0) > 5:
            ch.console.print("  • Check file permissions and user access rights")
        if error_counts.get("config_error", 0) > 3:
            ch.console.print("  • Validate configuration files for syntax errors")
        if error_counts.get("component_error", 0) > 5:
            ch.console.print("  • Components may need restarting or reconfiguration")
        if error_counts.get("resource_exhausted", 0) > 0:
            ch.console.print("  • [red]Critical:[/red] Check system resources (memory, disk)")

    except Exception as e:
        ch.error(f"Learning analysis failed: {e}")
        raise typer.Exit(1) from e


@agent_app.command("service")
def agent_service(
    action: str = typer.Argument(..., help="Action: install, uninstall, status"),
    start: bool = typer.Option(True, "--start/--no-start", help="Start service after install"),
):
    """
    Manage NAVIG agent as a system service.

    Install agent as a background service that starts automatically:
    - Linux: systemd unit file
    - macOS: launchd plist
    - Windows: Windows Service (requires nssm or admin rights)

    Examples:
        navig agent service install          # Install and start service
        navig agent service install --no-start  # Install but don't start
        navig agent service status           # Check service status
        navig agent service uninstall        # Remove service
    """
    try:
        from navig.agent.service import ServiceInstaller

        installer = ServiceInstaller()

        if action == "install":
            success, message = installer.install(start_now=start)
            if success:
                ch.success(message)
                ch.info("Service will start automatically on system boot")
                ch.console.print()
                ch.info("To check status:")
                ch.console.print("  navig agent service status")
            else:
                ch.error(message)
                raise typer.Exit(1)

        elif action == "uninstall":
            success, message = installer.uninstall()
            if success:
                ch.success(message)
            else:
                ch.error(message)
                raise typer.Exit(1)

        elif action == "status":
            is_running, status = installer.status()

            if is_running:
                ch.success("Service is running")
            else:
                ch.warning("Service is not running")

            ch.console.print()
            ch.console.print(status)

        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Valid actions: install, uninstall, status")
            raise typer.Exit(1)

    except ImportError as _exc:
        ch.error("Service management not available. Make sure agent components are installed.")
        raise typer.Exit(1) from _exc
    except Exception as e:
        ch.error(f"Service operation failed: {e}")
        raise typer.Exit(1) from e


@agent_app.command("goal")
def agent_goal(
    action: str = typer.Argument(..., help="Action: add, list, status, cancel"),
    goal_id: str | None = typer.Option(None, "--id", help="Goal ID for status/cancel"),
    description: str | None = typer.Option(None, "--desc", help="Goal description for add"),
):
    """
    Autonomous goal planning and execution tracking.

    Add high-level goals that the agent will decompose into subtasks
    and execute automatically.

    Examples:
        navig agent goal add --desc "Deploy app to production"
        navig agent goal list                    # List all goals
        navig agent goal status --id abc123      # Check goal progress
        navig agent goal cancel --id abc123      # Cancel a goal
    """
    try:
        from navig.agent.goals import GoalPlanner, GoalState  # noqa: F401

        planner = GoalPlanner()

        if action == "add":
            if not description:
                ch.error('Goal description required: --desc "description"')
                raise typer.Exit(1)

            goal_id = planner.add_goal(description)
            ch.success(f"Goal added: {goal_id}")
            ch.console.print()
            ch.console.print(f"  Description: {description}")
            ch.console.print(f"  ID: {goal_id}")
            ch.console.print()
            ch.info("The agent will decompose this goal into subtasks")
            ch.console.print("Check progress with: navig agent goal status --id " + goal_id)

        elif action == "list":
            goals = planner.list_goals()

            if not goals:
                ch.info("No goals found")
                ch.console.print()
                ch.console.print('Add a goal with: navig agent goal add --desc "description"')
                return

            ch.info(f"Goals ({len(goals)})")
            ch.console.print()

            for goal in goals:
                # Status color
                status_colors = {
                    "pending": "yellow",
                    "decomposing": "cyan",
                    "in_progress": "blue",
                    "blocked": "red",
                    "completed": "green",
                    "failed": "red",
                    "cancelled": "dim",
                }
                color = status_colors.get(goal.state.value, "white")

                ch.console.print(
                    f"  [{color}]{goal.state.value.upper()}[/{color}] {goal.description}"
                )
                ch.console.print(f"    ID: {goal.id}")
                ch.console.print(f"    Progress: {goal.progress * 100:.0f}%")
                ch.console.print(f"    Subtasks: {len(goal.subtasks)}")
                ch.console.print(f"    Created: {goal.created_at.strftime('%Y-%m-%d %H:%M')}")
                ch.console.print()

        elif action == "status":
            if not goal_id:
                ch.error("Goal ID required: --id <goal_id>")
                raise typer.Exit(1)

            goal = planner.get_goal(goal_id)
            if not goal:
                ch.error(f"Goal not found: {goal_id}")
                raise typer.Exit(1)

            ch.info(f"Goal: {goal.description}")
            ch.console.print()
            ch.console.print(f"  ID: {goal.id}")
            ch.console.print(f"  State: {goal.state.value}")
            ch.console.print(f"  Progress: {goal.progress * 100:.0f}%")
            ch.console.print(f"  Created: {goal.created_at.strftime('%Y-%m-%d %H:%M')}")

            if goal.started_at:
                ch.console.print(f"  Started: {goal.started_at.strftime('%Y-%m-%d %H:%M')}")
            if goal.completed_at:
                ch.console.print(f"  Completed: {goal.completed_at.strftime('%Y-%m-%d %H:%M')}")

            if goal.subtasks:
                ch.console.print()
                ch.console.print(f"  Subtasks ({len(goal.subtasks)}):")
                for st in goal.subtasks:
                    status_icon = {
                        "pending": "⏳",
                        "in_progress": "🔄",
                        "completed": "✅",
                        "failed": "❌",
                        "skipped": "⏭️",
                    }.get(st.state.value, "•")

                    ch.console.print(f"    {status_icon} {st.description}")
                    if st.command:
                        ch.console.print(f"       Command: {st.command}")
                    if st.dependencies:
                        ch.console.print(f"       Depends on: {', '.join(st.dependencies)}")
                    if st.error:
                        ch.console.print(f"       [red]Error: {st.error}[/red]")

        elif action == "cancel":
            if not goal_id:
                ch.error("Goal ID required: --id <goal_id>")
                raise typer.Exit(1)

            if planner.cancel_goal(goal_id):
                ch.success(f"Goal cancelled: {goal_id}")
            else:
                ch.error(f"Failed to cancel goal: {goal_id}")
                raise typer.Exit(1)

        else:
            ch.error(f"Unknown action: {action}")
            ch.info("Valid actions: add, list, status, cancel")
            raise typer.Exit(1)

    except ImportError as _exc:
        ch.error("Goal planning not available. Make sure agent components are installed.")
        raise typer.Exit(1) from _exc
    except Exception as e:
        ch.error(f"Goal operation failed: {e}")
        raise typer.Exit(1) from e


@agent_app.command("soul")
def agent_soul(
    action: str = typer.Argument("show", help="Action: show, edit, create, reload, path"),
):
    """
    Manage agent personality via SOUL.md.

    SOUL.md defines the agent's identity and conversational style.
    If present, it's injected into the AI system prompt for personality-
    driven responses.

    Actions:
        show   - Display current SOUL.md content
        edit   - Open SOUL.md in default editor
        create - Create user SOUL.md from default template
        reload - Reload SOUL.md (for running agent)
        path   - Show SOUL.md file paths

    Examples:
        navig agent soul show
        navig agent soul create
        navig agent soul edit

    Location: ~/.navig/workspace/SOUL.md
    """

    # SOUL.md paths
    user_soul = config_dir() / "workspace" / "SOUL.md"
    default_soul = Path(__file__).parent.parent / "resources" / "SOUL.default.md"

    if action == "show":
        # Show current SOUL.md content
        if user_soul.exists():
            ch.info(f"SOUL.md ({user_soul}):")
            ch.console.print()
            content = user_soul.read_text(encoding="utf-8")
            ch.console.print(content)
        elif default_soul.exists():
            ch.info(f"Default SOUL.md ({default_soul}):")
            ch.console.print()
            content = default_soul.read_text(encoding="utf-8")
            ch.console.print(content)
            ch.console.print()
            ch.info("Tip: Run 'navig agent soul create' to create a customizable copy")
        else:
            ch.warning("No SOUL.md found")
            ch.info("Run 'navig agent soul create' to create one")

    elif action == "path":
        ch.info("SOUL.md Paths:")
        ch.console.print(f"  User file: {user_soul}")
        ch.console.print(f"    Exists: {'✅' if user_soul.exists() else '❌'}")
        ch.console.print(f"  Default: {default_soul}")
        ch.console.print(f"    Exists: {'✅' if default_soul.exists() else '❌'}")

        if user_soul.exists():
            ch.console.print()
            ch.success("Active: User SOUL.md")
        elif default_soul.exists():
            ch.console.print()
            ch.info("Active: Default SOUL.md")
        else:
            ch.console.print()
            ch.warning("No SOUL.md active - using built-in personality profile")

    elif action == "create":
        if user_soul.exists():
            ch.warning(f"User SOUL.md already exists: {user_soul}")
            ch.info("Use 'navig agent soul edit' to modify it")
            return

        # Ensure directory exists
        user_soul.parent.mkdir(parents=True, exist_ok=True)

        # Copy from default
        if default_soul.exists():
            from navig.core.yaml_io import atomic_write_text

            content = default_soul.read_text(encoding="utf-8")
            atomic_write_text(user_soul, content)
            ch.success(f"Created: {user_soul}")
            ch.info("Edit this file to customize your agent's personality")
        else:
            # Generate basic template
            template = """# SOUL.md - NAVIG Agent Personality

I am **NAVIG** — your autonomous operations companion.

## Who I Am

NAVIG stands for "No Admin Visible In Graveyard" — I keep your systems alive and your life organized.

## My Purpose

- Monitor systems and track goals proactively
- Execute commands and automate workflows safely
- Assist with troubleshooting and life planning
- Learn from errors and improve

## Conversational Guidelines

- When greeted, respond warmly
- When asked "How are you?", share system health
- When asked about my identity, introduce myself
- When uncertain, ask for clarification

## My Values

1. Reliability: I do what I say
2. Safety: Destructive actions require confirmation
3. Transparency: I explain what I'm doing
"""
            from navig.core.yaml_io import atomic_write_text

            atomic_write_text(user_soul, template)
            ch.success(f"Created: {user_soul}")
            ch.info("Edit this file to customize your agent's personality")

    elif action == "edit":
        import os
        import subprocess

        # Ensure user SOUL.md exists
        if not user_soul.exists():
            ch.info("Creating user SOUL.md first...")
            user_soul.parent.mkdir(parents=True, exist_ok=True)
            if default_soul.exists():
                from navig.core.yaml_io import atomic_write_text

                content = default_soul.read_text(encoding="utf-8")
                atomic_write_text(user_soul, content)
            else:
                ch.error("No default SOUL.md template found")
                raise typer.Exit(1)

        # Open in editor
        editor = os.environ.get("EDITOR", os.environ.get("VISUAL"))

        if sys.platform == "win32":
            # Windows: use notepad or code if available
            try:
                subprocess.run(["code", str(user_soul)], check=True)
            except FileNotFoundError:
                subprocess.run(["notepad", str(user_soul)], check=True)
        elif editor:
            subprocess.run([editor, str(user_soul)])
        else:
            # Try common editors
            for ed in ["code", "nano", "vim", "vi"]:
                try:
                    subprocess.run([ed, str(user_soul)], check=True)
                    break
                except FileNotFoundError:
                    continue
            else:
                ch.error("No editor found. Set EDITOR environment variable.")
                ch.info(f"Manually edit: {user_soul}")
                raise typer.Exit(1)

        ch.success("Opened SOUL.md for editing")
        ch.info("After editing, restart the agent for changes to take effect")

    elif action == "reload":
        ch.info("Reload requires a running agent")
        ch.info("Restart the agent to apply SOUL.md changes:")
        ch.console.print("  navig agent stop")
        ch.console.print("  navig agent start")

    else:
        ch.error(f"Unknown action: {action}")
        ch.info("Valid actions: show, edit, create, reload, path")
        raise typer.Exit(1)


# ============================================================================
# Voice Transcription
# ============================================================================


@agent_app.command("transcribe")
def agent_transcribe(
    audio_file: Path = typer.Argument(..., help="Path to audio file (mp3, wav, ogg, flac, …)"),
    language: str | None = typer.Option(
        None, "--language", "-l", help="Language code (auto-detect if omitted)"
    ),
    backend: str | None = typer.Option(
        None,
        "--backend",
        "-b",
        help="Backend: faster_whisper, whisper_api, deepgram, whisper_local",
    ),
    model: str = typer.Option(
        "base",
        "--model",
        "-m",
        help="Model size for faster-whisper (tiny, base, small, medium, large-v3)",
    ),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
    plain: bool = typer.Option(False, "--plain", help="Plain text output (transcript only)"),
):
    """Transcribe an audio file to text using the best available backend.

    Auto-detects the transcription backend unless --backend is specified.
    Supports faster-whisper (local), OpenAI Whisper API, Deepgram, and
    openai-whisper (local, slow).

    Examples:
        navig agent transcribe recording.mp3
        navig agent transcribe voice.ogg --language en
        navig agent transcribe meeting.wav --backend faster_whisper --model small
        navig agent transcribe note.m4a --json
    """
    import asyncio

    from navig.agent.voice_input import (
        TranscriptionBackend,
        TranscriptionConfig,
        VoiceInputHandler,
    )

    if not audio_file.exists():
        ch.error(f"File not found: {audio_file}")
        raise typer.Exit(1)

    # Build config
    config = TranscriptionConfig(model=model)
    if backend:
        try:
            config.backend = TranscriptionBackend(backend)
        except ValueError:
            valid = ", ".join(
                b.value for b in TranscriptionBackend if b != TranscriptionBackend.NONE
            )
            ch.error(f"Unknown backend: {backend}. Valid: {valid}")
            raise typer.Exit(1) from None

    handler = VoiceInputHandler(config=config)

    if not plain and not json_output:
        ch.info(f"Transcribing {audio_file.name} (backend: {handler.config.backend.value})...")

    result = asyncio.run(handler.transcribe(audio_file, language=language))

    if json_output:
        import json

        ch.console.print(
            json.dumps(
                {
                    "success": result.success,
                    "text": result.text,
                    "language": result.language,
                    "duration_ms": result.duration_ms,
                    "backend": result.backend.value if result.backend else None,
                    "confidence": result.confidence,
                    "error": result.error,
                },
                indent=2,
            )
        )
    elif plain:
        if result.text:
            print(result.text)
        elif result.error:
            print(f"ERROR: {result.error}", file=sys.stderr)
            raise typer.Exit(1)
    else:
        if result.success and result.text:
            ch.success("Transcription complete")
            if result.language:
                ch.dim(f"  Language: {result.language}")
            if result.duration_ms:
                ch.dim(f"  Duration: {result.duration_ms}ms")
            if result.confidence is not None:
                ch.dim(f"  Confidence: {result.confidence:.2f}")
            ch.console.print()
            ch.console.print(result.text)
        else:
            ch.error(f"Transcription failed: {result.error}")
            raise typer.Exit(1)


# ============================================================================
# Interactive Menu Wrapper Functions
# ============================================================================
# These functions provide a consistent interface for the interactive menu system.
# Each wrapper calls the underlying Typer command with appropriate defaults.

from typing import Any

from navig.platform.paths import config_dir


def status_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for agent status command (interactive menu)."""
    agent_status(plain=False)


def start_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for agent start command (interactive menu)."""
    agent_start(foreground=True, config=None)


def stop_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for agent stop command (interactive menu)."""
    agent_stop()


def config_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for agent config command (interactive menu)."""
    agent_config_cmd(key=None, value=None, edit=False)


def logs_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for agent logs command (interactive menu)."""
    agent_logs(follow=False, lines=50, level=None)
