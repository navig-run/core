"""
NAVIG Cron CLI Commands

Commands for managing persistent job scheduling via the gateway.
"""

from typing import Any

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

_CRON_REQUEST_TIMEOUT: int = 5  # Short timeout for local gateway cron API calls


def _cron_base() -> str:
    """Base URL of the local gateway that serves the cron API (canonical host/port)."""
    from navig.gateway_client import gateway_base_url

    return gateway_base_url()


def _unwrap(body: Any) -> Any:
    """Payload out of the gateway's ``json_ok`` envelope.

    Every cron route answers ``json_ok(...)`` = ``{"ok": …, "data": <payload>, "error": …}``,
    so reading a field straight off ``response.json()`` always missed and this whole CLI
    reported nothing: `cron list` showed no jobs, `cron add` printed "Created job: None",
    `cron status` was blank, and `cron run` reported "Job failed" even on success. Lazily
    imported to keep CLI startup fast.
    """
    from navig.gateway_client import unwrap_envelope

    return unwrap_envelope(body)


def _cron_api(
    method: str,
    path: str,
    *,
    action: str,
    json_body: dict[str, Any] | None = None,
    timeout: float = _CRON_REQUEST_TIMEOUT,
    expect_payload: bool = True,
) -> dict[str, Any]:
    """Call the gateway's cron API, or exit non-zero saying why.

    Every command in this module carried its own copy of this block — import
    requests, call, check for 200, catch ImportError, sniff for ConnectionError —
    and **not one of the failure branches exited non-zero**. So
    ``navig cron add "nightly backup" "0 2 * * *" "navig backup export"`` with the
    gateway down printed "⚠ Gateway is not running" and exited **0**: a
    provisioning script recorded a scheduled job that does not exist and will
    never run. 17 such paths across 7 commands.

    Centralising it also settles three inconsistencies the copies had drifted into:
    ``ImportError`` for a missing ``requests`` was handled in 2 of 7 commands, the
    "gateway is not running" hint was worded three different ways (and omitted
    entirely in three commands), and the glyph disagreed with the exit code.

    Returns the unwrapped payload (``{}`` when ``expect_payload`` is False, for the
    callers that only care that the call succeeded).
    """
    try:
        import requests  # noqa: PLC0415 — lazy: keeps `navig --help` fast
    except ImportError as exc:
        ch.error(f"Cannot {action}: the 'requests' package is not installed")
        ch.info("Install with: pip install requests")
        raise typer.Exit(1) from exc

    # Authenticated, like every other CLI->gateway call. Omitting this made all
    # SEVEN `navig cron` commands 401 against any gateway with a token configured
    # -- which is the default on the operator's own machine. The gateway's own 401
    # body says "(The NAVIG CLI does this for you.)"; it did not.
    #
    # `gateway_request_headers()` is the one place that knows where the token lives
    # (gateway.auth.token, with two legacy fallbacks) and returns just an X-Actor
    # header when no token is configured -- so this is correct for an unauthenticated
    # gateway too. Imported from `navig.gateway_client` directly, NOT via
    # `navig.gateway.client`: that shim's package __init__ has a circular import
    # chain that deadlocks under Python 3.14.
    from navig.gateway_client import gateway_request_headers  # noqa: PLC0415 — lazy

    url = f"{_cron_base()}{path}"
    try:
        response = requests.request(
            method, url, json=json_body, timeout=timeout, headers=gateway_request_headers()
        )
    except requests.exceptions.ConnectionError as exc:
        # Matched on the EXCEPTION TYPE, not `"ConnectionError" in str(type(e))`.
        # The string sniff the copies used also matched anything whose class name
        # merely contained the word, and missed a subclass that did not.
        ch.error(f"Cannot {action}: the gateway is not running")
        ch.info("Start it with: navig gateway start")
        raise typer.Exit(1) from exc
    except requests.exceptions.Timeout as exc:
        ch.error(f"Cannot {action}: the gateway did not answer within {timeout:g}s")
        raise typer.Exit(1) from exc
    except Exception as exc:  # noqa: BLE001 — reported with its type, then exits
        ch.error(f"Cannot {action}: {type(exc).__name__}: {exc}")
        raise typer.Exit(1) from exc

    if response.status_code != 200:
        ch.error(f"Failed to {action}: HTTP {response.status_code}")
        detail = (response.text or "").strip()
        if detail:
            ch.info(f"  {detail[:300]}")
        raise typer.Exit(1)

    if not expect_payload:
        return {}

    try:
        payload = _unwrap(response.json())
    except Exception as exc:  # noqa: BLE001 — a 200 we cannot read is not a success
        # Returning {} here would render as "No scheduled jobs" for a server that
        # actually answered with something unreadable.
        ch.error(f"Cannot {action}: the gateway sent a response that could not be read")
        raise typer.Exit(1) from exc
    return payload if isinstance(payload, dict) else {"data": payload}


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
    jobs = _cron_api("GET", "/cron/jobs", action="list jobs").get("jobs", [])
    if not jobs:
        # An empty schedule is a real, successful answer — exit 0.
        ch.info("No scheduled jobs")
        ch.info('Add one with: navig cron add "job name" "every 30 minutes" "navig host test"')
        return

    # A table, not stacked lines — and it fixes a real bug: the old format string
    # was f"  {status} [{job.get('id')}] {job.get('name')}", and `ch.info` renders
    # Rich markup, so `[job_8]` was parsed as an unknown style tag and SILENTLY
    # DROPPED. The id never appeared — and the id is exactly what `cron remove`,
    # `cron enable` and `cron disable` all take as their argument, so the command
    # that lists your jobs did not show you how to act on any of them.
    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("", no_wrap=True)  # status glyph
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Schedule", no_wrap=True)
    table.add_column("Next run", no_wrap=True)
    table.add_column("Name")  # the one wrappable column

    enabled = 0
    for job in jobs:
        is_on = bool(job.get("enabled"))
        enabled += is_on
        table.add_row(
            "[green]●[/green]" if is_on else "[dim]○[/dim]",
            str(job.get("id") or "?"),
            str(job.get("schedule") or "?"),
            str(job.get("next_run") or "—"),
            str(job.get("name") or "[dim]unnamed[/dim]"),
        )

    ch.console.print(table)
    ch.dim(
        f"  {len(jobs)} job(s) · {enabled} enabled · "
        f"run one now with navig cron run <ID>"
    )


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
    job = _cron_api(
        "POST",
        "/cron/jobs",
        action="create job",
        json_body={
            "name": name,
            "schedule": schedule,
            "command": command,
            "enabled": not disabled,
        },
    )
    ch.success(f"Created job: {job.get('id')}")
    ch.info(f"  Name: {name}")
    ch.info(f"  Schedule: {schedule}")
    ch.info(f"  Next run: {job.get('next_run', 'N/A')}")


@cron_app.command("remove")
def cron_remove(
    job_id: str = typer.Argument(..., help="Job ID to remove"),
):
    """Remove a scheduled job."""
    _cron_api(
        "DELETE", f"/cron/jobs/{job_id}", action=f"remove job {job_id}", expect_payload=False
    )
    ch.success(f"Removed job: {job_id}")


@cron_app.command("run")
def cron_run(
    job_id: str = typer.Argument(..., help="Job ID to run"),
):
    """Run a job immediately."""
    ch.info(f"Running job {job_id}...")

    # 300s, not the short API timeout: this waits for the job itself to finish.
    result = _cron_api(
        "POST", f"/cron/jobs/{job_id}/run", action=f"run job {job_id}", timeout=300
    )
    if not result.get("success"):
        # The CALL succeeded; the JOB failed. `navig cron run x && <next>` must not
        # continue on a job that errored.
        ch.error(f"Job failed: {result.get('error', 'unknown')}")
        raise typer.Exit(1)

    ch.success("Job completed successfully")
    if result.get("output"):
        ch.info(f"Output:\n{result['output'][:1000]}")


@cron_app.command("enable")
def cron_enable(
    job_id: str = typer.Argument(..., help="Job ID to enable"),
):
    """Enable a disabled job."""
    _cron_api(
        "POST",
        f"/cron/jobs/{job_id}/enable",
        action=f"enable job {job_id}",
        expect_payload=False,
    )
    ch.success(f"Enabled job: {job_id}")


@cron_app.command("disable")
def cron_disable(
    job_id: str = typer.Argument(..., help="Job ID to disable"),
):
    """Disable a job without removing it."""
    _cron_api(
        "POST",
        f"/cron/jobs/{job_id}/disable",
        action=f"disable job {job_id}",
        expect_payload=False,
    )
    ch.success(f"Disabled job: {job_id}")


@cron_app.command("status")
def cron_status():
    """Show cron service status."""
    data = _cron_api("GET", "/status", action="get cron status")
    cron = data.get("cron", {})

    if data.get("status") != "running":
        # A status query that successfully reports "not running" SUCCEEDED — the
        # answer is just unwelcome. Exit 0; only an unanswerable query exits 1,
        # which `_cron_api` already did above.
        ch.warning("Cron service is not running")
        ch.info("Start gateway to enable cron: navig gateway start")
        return

    ch.success("Cron service is running")
    ch.info(f"  Total jobs: {cron.get('jobs', cron.get('total_jobs', 0))}")
    ch.info(f"  Enabled jobs: {cron.get('enabled_jobs', 0)}")
    if cron.get("next_job"):
        ch.info(f"  Next job: {cron.get('next_job')} in {cron.get('next_run_in', '?')}")


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
