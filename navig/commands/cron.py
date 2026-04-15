"""
NAVIG Cron CLI Commands

Commands for managing persistent job scheduling via the gateway.
"""

from typing import Any

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

_CRON_REQUEST_TIMEOUT: int = 5  # Short timeout for local gateway cron API calls

cron_app = typer.Typer(
    name="cron",
    help="Persistent job scheduling",
    invoke_without_command=True,
    no_args_is_help=False,
)


@cron_app.callback()
def _cron_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            print(ctx.get_help())
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("cron", cron_app)
@cron_app.command("list")
def cron_list():
    """List all scheduled jobs."""
    try:
        import requests

        response = requests.get("http://localhost:8789/cron/jobs", timeout=_CRON_REQUEST_TIMEOUT)
        if response.status_code == 200:
            jobs = response.json().get("jobs", [])
            if jobs:
                ch.info(f"Scheduled jobs ({len(jobs)}):")
                for job in jobs:
                    status = "✅" if job.get("enabled") else "⏸️"
                    next_run = job.get("next_run", "N/A")
                    ch.info(f"  {status} [{job.get('id')}] {job.get('name')}")
                    ch.info(f"      Schedule: {job.get('schedule')}")
                    ch.info(f"      Next run: {next_run}")
            else:
                ch.info("No scheduled jobs")
                ch.info(
                    'Add one with: navig cron add "job name" "every 30 minutes" "navig host test"'
                )
        else:
            ch.error(f"Failed to list jobs: {response.status_code}")
    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__) or "Connection refused" in str(e):
            ch.warning("Gateway is not running")
            ch.info("Start with: navig gateway start")
        else:
            ch.error(f"Error: {e}")


@cron_app.command("add")
def cron_add(
    name: str = typer.Argument(..., help="Job name"),
    schedule: str = typer.Argument(..., help="Schedule (e.g., 'every 30 minutes', '0 * * * *')"),
    command: str = typer.Argument(..., help="Command to run"),
    disabled: bool = typer.Option(False, "--disabled", help="Create job in disabled state"),
):
    """
    Add a new scheduled job.

    Schedule formats:
    - Natural language: "every 30 minutes", "hourly", "daily"
    - Cron expression: "*/5 * * * *", "0 9 * * *"

    Examples:
        navig cron add "Disk check" "every 30 minutes" "navig host monitor disk"
        navig cron add "Daily backup" "0 2 * * *" "navig backup export"
        navig cron add "Health check" "hourly" "Check all hosts and report issues"
    """
    try:
        import requests

        response = requests.post(
            "http://localhost:8789/cron/jobs",
            json={
                "name": name,
                "schedule": schedule,
                "command": command,
                "enabled": not disabled,
            },
            timeout=_CRON_REQUEST_TIMEOUT,
        )
        if response.status_code == 200:
            job = response.json()
            ch.success(f"Created job: {job.get('id')}")
            ch.info(f"  Name: {name}")
            ch.info(f"  Schedule: {schedule}")
            ch.info(f"  Next run: {job.get('next_run', 'N/A')}")
        else:
            ch.error(f"Failed to create job: {response.status_code}")
    except ImportError:
        ch.error("Missing dependency: requests")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__):
            ch.warning("Gateway is not running")
            ch.info("Start with: navig gateway start")
        else:
            ch.error(f"Error: {e}")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    try:
        import requests

        response = requests.delete(f"http://localhost:8789/cron/jobs/{job_id}", timeout=_CRON_REQUEST_TIMEOUT)
        if response.status_code == 200:
            ch.success(f"Removed job: {job_id}")
        else:
            ch.error(f"Failed to remove job: {response.status_code}")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__):
            ch.warning("Gateway is not running")
        else:
            ch.error(f"Error: {e}")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
):
    """Run a job immediately."""
    ch.info(f"Running job {job_id}...")

    try:
        import requests

        response = requests.post(f"http://localhost:8789/cron/jobs/{job_id}/run", timeout=300)
        if response.status_code == 200:
            result = response.json()
            if result.get("success"):
                ch.success("Job completed successfully")
                if result.get("output"):
                    ch.info(f"Output:\n{result['output'][:1000]}")
            else:
                ch.error(f"Job failed: {result.get('error', 'unknown')}")
        else:
            ch.error(f"Failed to run job: {response.status_code}")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__):
            ch.warning("Gateway is not running")
        else:
            ch.error(f"Error: {e}")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID to enable"),
):
    """Enable a disabled job."""
    try:
        import requests

        response = requests.post(f"http://localhost:8789/cron/jobs/{job_id}/enable", timeout=_CRON_REQUEST_TIMEOUT)
        if response.status_code == 200:
            ch.success(f"Enabled job: {job_id}")
        else:
            ch.error(f"Failed to enable job: {response.status_code}")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__):
            ch.warning("Gateway is not running")
        else:
            ch.error(f"Error: {e}")


@cron_app.command("disable")
def cron_disable(
    job_id: str = typer.Argument(..., help="Job ID to disable"),
):
    """Disable a job without removing it."""
    try:
        import requests

        response = requests.post(f"http://localhost:8789/cron/jobs/{job_id}/disable", timeout=_CRON_REQUEST_TIMEOUT)
        if response.status_code == 200:
            ch.success(f"Disabled job: {job_id}")
        else:
            ch.error(f"Failed to disable job: {response.status_code}")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__):
            ch.warning("Gateway is not running")
        else:
            ch.error(f"Error: {e}")


@cron_app.command("status")
def cron_status():
    """Show cron service status."""
    try:
        import requests

        response = requests.get("http://localhost:8789/status", timeout=_CRON_REQUEST_TIMEOUT)
        if response.status_code == 200:
            data = response.json()
            cron = data.get("cron", {})

            # Cron is running if gateway is up and jobs exist
            total_jobs = cron.get("jobs", cron.get("total_jobs", 0))
            enabled_jobs = cron.get("enabled_jobs", 0)

            if data.get("status") == "running":
                ch.success("Cron service is running")
                ch.info(f"  Total jobs: {total_jobs}")
                ch.info(f"  Enabled jobs: {enabled_jobs}")
                if cron.get("next_job"):
                    ch.info(f"  Next job: {cron.get('next_job')} in {cron.get('next_run_in', '?')}")
            else:
                ch.warning("Cron service is not running")
                ch.info("Start gateway to enable cron: navig gateway start")
        else:
            ch.error(f"Failed to get status: {response.status_code}")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__):
            ch.warning("Gateway is not running")
            ch.info("Start gateway to enable cron: navig gateway start")
        else:
            ch.error(f"Error: {e}")


# ============================================================================
# Interactive Menu Wrapper Functions
# ============================================================================
# These functions provide a consistent interface for the interactive menu system.
# Each wrapper calls the underlying Typer command with appropriate defaults.


def list_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for cron list command (interactive menu)."""
    cron_list()


def add_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for cron add command (interactive menu).

    Note: This is a simplified wrapper - the interactive menu
    will need to prompt for schedule and command separately.
    """
    from rich.prompt import Prompt

    schedule = Prompt.ask("Schedule (e.g., 'every 30 minutes', '0 * * * *')")
    command = Prompt.ask("Command to run")

    if schedule and command:
        cron_add(name, schedule, command, disabled=False)
    else:
        ch.warning("Cancelled - schedule and command are required")


def run_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for cron run command (interactive menu)."""
    cron_run(name)


def enable_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for cron enable command (interactive menu)."""
    cron_enable(name)


def disable_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for cron disable command (interactive menu)."""
    cron_disable(name)


def remove_cmd(name: str, ctx: dict[str, Any]) -> None:
    """Wrapper for cron remove command (interactive menu)."""
    cron_remove(name)


def status_cmd(ctx: dict[str, Any]) -> None:
    """Wrapper for cron status command (interactive menu)."""
    cron_status()
