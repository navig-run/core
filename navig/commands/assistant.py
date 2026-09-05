"""
Assistant Commands

CLI commands for the proactive AI assistant system.
"""

import json
from typing import Any

import pyperclip
import typer

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.proactive_assistant import ProactiveAssistant
from navig.remote import RemoteOperations


def status_cmd(ctx_obj: dict[str, Any]):
    """Display assistant health and statistics."""
    config = get_config_manager()
    assistant = ProactiveAssistant(config)

    ch.header("🤖 Proactive Assistant Status")

    # Configuration. Each row names the key that changes it: these are real toggles
    # that were unreachable in practice — undocumented AND read as raw strings, so
    # `navig config set proactive_assistant.auto_analysis false` appeared to do nothing and the
    # status view gave no hint about what to set.
    ch.info("\n📋 Configuration:")
    from navig.console_helper import Table

    cfg_table = Table(box=None, show_header=True, padding=(0, 2))
    cfg_table.add_column("Setting", no_wrap=True)
    cfg_table.add_column("Value", no_wrap=True)
    cfg_table.add_column("Config key", no_wrap=True)
    for label, value, key in (
        ("Enabled", assistant.is_enabled(), "proactive_assistant.enabled"),
        ("Suggestion level", assistant.get_suggestion_level(), "proactive_assistant.suggestion_level"),
        ("Auto analysis", assistant.should_auto_analyze(), "proactive_assistant.auto_analysis"),
        (
            "Confirm destructive",
            assistant.requires_confirmation(),
            "proactive_assistant.confirmation_required",
        ),
    ):
        rendered = (
            f"[green]on[/green]" if value is True
            else "[dim]off[/dim]" if value is False
            else str(value)
        )
        cfg_table.add_row(label, rendered, f"[dim]{key}[/dim]")
    ch.console.print(cfg_table)
    ch.dim("  change one with: navig config set <config key> <value>")

    # Statistics
    ch.info("\n📊 Statistics:")

    # Command history
    history_file = assistant.ai_context_dir / "command_history.json"
    if history_file.exists():
        with open(history_file, encoding='utf-8') as f:
            history = json.load(f)
        ch.dim(f"  Commands Logged: {len(history)}")

    # Error statistics
    if hasattr(assistant, "error_resolution"):
        stats = assistant.error_resolution.get_error_statistics(hours=24)
        ch.dim(f"  Errors (24h): {stats.get('total_errors', 0)}")

        if stats.get("by_category"):
            ch.dim("  Error Categories:")
            for cat, count in stats["by_category"].items():
                ch.dim(f"    - {cat}: {count}")

    # Active issues
    issues_file = assistant.ai_context_dir / "detected_issues.json"
    if issues_file.exists():
        with open(issues_file, encoding='utf-8') as f:
            issues = json.load(f)
        active_issues = [i for i in issues if i.get("status") == "active"]
        ch.dim(f"  Active Issues: {len(active_issues)}")

    ch.success("\n✓ Assistant is operational")


# The checks `analyze_cmd` attempts. Named here so the closing summary can say
# which ones were SKIPPED rather than claim the whole analysis completed.
_ANALYSIS_CHECKS = ("metrics", "issues")


def analyze_cmd(ctx_obj: dict[str, Any]):
    """Manually trigger comprehensive system analysis."""
    # void: manual analysis. because sometimes you need to see it yourself.
    config = get_config_manager()
    assistant = ProactiveAssistant(config)

    ch.header("🔍 Running System Analysis...")

    # Which of the checks actually ran, so the closing line can tell the truth.
    checks_done: list[str] = []

    try:
        # Get active server
        server_name = config.get_active_server()
        if not server_name:
            ch.error("No active server. Use 'navig host use <name>' first")
            raise typer.Exit(1)

        # Load server configuration
        try:
            server_config = config.load_server_config(server_name)
        except Exception as e:
            ch.error(f"Failed to load server configuration: {e}")
            raise typer.Exit(1) from e

        # Initialize remote operations
        try:
            remote_ops = RemoteOperations(config)
        except Exception as e:
            ch.error(f"Failed to initialize remote operations: {e}")
            raise typer.Exit(1) from e

        # Collect performance metrics
        ch.info("\n📊 Collecting performance metrics...")
        try:
            metrics = assistant.auto_detection.collect_performance_metrics(
                remote_ops, server_config
            )

            ch.dim(f"  CPU: {metrics.get('cpu_percent', 0):.1f}%")
            ch.dim(f"  Memory: {metrics.get('memory_percent', 0):.1f}%")
            ch.dim(f"  Disk: {metrics.get('disk_percent', 0):.1f}%")
            ch.dim(f"  Status: {metrics.get('status', 'unknown')}")

            if metrics.get("alerts"):
                ch.warning("\n⚠️  Alerts:")
                for alert in metrics["alerts"]:
                    ch.warning(f"  - {alert}")

            # Update baseline
            ch.info("\n💾 Updating performance baseline...")
            assistant.auto_detection.update_performance_baseline(server_name, metrics)
            checks_done.append("metrics")
        except Exception as e:
            ch.warning(f"Could not collect metrics: {e}")

        # Check for active issues
        ch.info("\n🔎 Checking for active issues...")
        try:
            issues_file = assistant.ai_context_dir / "detected_issues.json"
            if issues_file.exists():
                with open(issues_file, encoding='utf-8') as f:
                    issues = json.load(f)
                active_issues = [i for i in issues if i.get("status") == "active"]

                if active_issues:
                    ch.warning(f"\n⚠️  Found {len(active_issues)} active issue(s):")
                    for issue in active_issues[:5]:
                        ch.warning(f"  - \\[{issue.get('severity')}] {issue.get('description')}")
                else:
                    ch.success("\n✓ No active issues detected")
            checks_done.append("issues")
        except Exception as e:
            ch.warning(f"Could not check issues: {e}")

        # "✓ Analysis complete" used to print unconditionally. Both inner steps
        # degrade to a warning on failure, so an analysis that collected no metrics
        # AND read no issues still announced itself as complete — a green tick over
        # nothing, which is the whole reason this command exists.
        if not checks_done:
            ch.error("Analysis produced nothing — every check failed (see above)")
            raise typer.Exit(1)
        if len(checks_done) < len(_ANALYSIS_CHECKS):
            missed = sorted(set(_ANALYSIS_CHECKS) - set(checks_done))
            ch.warning(f"\n⚠️  Analysis incomplete — skipped: {', '.join(missed)}")
        else:
            ch.success("\n✓ Analysis complete")

    except typer.Exit:
        raise  # deliberate exit; the catch-all below would rewrite its code
    except Exception as e:
        ch.error(f"Analysis failed: {e}")
        raise typer.Exit(1) from e


def context_cmd(ctx_obj: dict[str, Any], clipboard: bool = False, file_path: str | None = None):
    """Generate AI copilot context summary."""
    config = get_config_manager()
    assistant = ProactiveAssistant(config)

    ch.header("🤖 Generating AI Context Summary...")

    try:
        # Get active server
        server_name = config.get_active_server()
        remote_ops = None

        if server_name:
            try:
                remote_ops = RemoteOperations(config)
            except Exception as e:
                ch.dim(f"Could not connect to server for live data: {e}")

        # Generate context (works even without server connection)
        try:
            context = assistant.context_generator.generate_context_summary(config, remote_ops)
        except Exception as e:
            ch.error(f"Failed to generate context: {e}")
            raise typer.Exit(1) from e

        # Format as JSON
        context_json = json.dumps(context, indent=2)

        # Output based on options
        # void: we export our system state to external AIs. trust, but verify their suggestions.
        if clipboard:
            try:
                pyperclip.copy(context_json)
                ch.success("✓ Context copied to clipboard")
            except Exception as e:
                # A warning, not an error: the caller still receives the context on
                # stdout, so this degraded successfully and exits 0. An ✗ glyph over
                # a zero exit is the same disagreement, pointing the other way.
                ch.warning(f"Could not copy to clipboard: {e}")
                ch.info("\nContext JSON:")
                print(context_json)
        elif file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(context_json)
                ch.success(f"✓ Context saved to {file_path}")
            except Exception as e:
                # `navig ai show --context --file x.json && upload x.json` used to
                # upload a file that was never written.
                ch.error(f"Could not save to file: {e}")
                raise typer.Exit(1) from e
        else:
            # Display to console
            ch.info("\nContext JSON:")
            print(context_json)

            ch.info("\n💡 Tip: Use --clipboard to copy to clipboard")
            ch.info("💡 Tip: Use --file <path> to save to file")

    except typer.Exit:
        raise  # deliberate exit; the catch-all below would rewrite its code
    except Exception as e:
        ch.error(f"Unexpected error: {e}")
        raise typer.Exit(1) from e


def reset_cmd(ctx_obj: dict[str, Any]):
    """Clear all learning data and reset to defaults."""
    # void: sometimes you need to forget. start fresh. erase the past.
    config = get_config_manager()
    assistant = ProactiveAssistant(config)

    # Require confirmation
    if not ctx_obj.get("yes"):
        ch.warning("⚠️  This will delete all assistant learning data:")
        ch.warning("  - Command history")
        ch.warning("  - Error logs")
        ch.warning("  - Performance baselines")
        ch.warning("  - Detected issues")
        ch.warning("  - Solution feedback")

        confirm = input("\nType 'yes' to confirm: ")
        if confirm.lower() != "yes":
            ch.warning("Reset cancelled — nothing was deleted")
            # Exit 1, matching `database restore`: the requested operation did not
            # happen, and a wrapper cannot otherwise tell "reset" from "declined".
            raise typer.Exit(1)

    try:
        # Clear JSON files
        files_to_clear = [
            "command_history.json",
            "error_log.json",
            "detected_issues.json",
            "performance_baselines.json",
            "workflow_patterns.json",
        ]

        cleared: list[str] = []
        for filename in files_to_clear:
            file_path = assistant.ai_context_dir / filename
            if file_path.exists():
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump([], f)
                cleared.append(filename)

        # Clear baselines directory
        baselines_dir = assistant.navig_dir / "baselines"
        if baselines_dir.exists():
            for baseline_file in baselines_dir.glob("*.json"):
                baseline_file.unlink()

        ch.success("✓ Assistant data reset successfully")

    except Exception as e:
        ch.error(f"Reset failed: {e}")
        # This loop mutates file by file, so a failure part-way leaves some data
        # cleared and some intact. Exiting 0 without saying so left the operator
        # unable to tell which.
        if cleared:
            ch.warning(
                f"  Partially reset before the failure: {', '.join(cleared)}"
            )
        raise typer.Exit(1) from e


def config_cmd(ctx_obj: dict[str, Any]):
    """Interactive configuration wizard for assistant settings."""
    ch.header("⚙️  Assistant Configuration")
    ch.info("\nCurrent configuration is stored in ~/.navig/config.yaml")
    ch.info("under the 'proactive_assistant' section")
    ch.info("\nRefer to documentation for available settings")
