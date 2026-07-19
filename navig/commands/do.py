"""``navig do`` — ask the AI to do a task in your browser, one-shot from the CLI.

Resolves a **profile** (``--profile`` or the active one), opens/reuses its **visible** browser,
**bridges** the agent's ``browser_tool`` to that exact window (so you watch it work), then runs the
agent loop for a single task.

Safeguards (Stage-B, folded from the plan's risks):
- Runs under the standard approval gate (dangerous tools still gated).
- **Outward/irreversible actions confirm by default**: without ``--yes`` the agent runs in SAFE mode
  (prepare/compose but do NOT finally send/submit/publish/pay/delete); ``--dry-run`` plans only.
- **Every run is audited** to ``~/.navig/history/do.jsonl`` (task + profile + flags; no secrets).
"""

from __future__ import annotations

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

do_app = typer.Typer(
    name="do",
    help='Ask the AI to do a task in your browser, e.g. navig do "open gmail and email bob@x.com".',
    no_args_is_help=True,
)


def _print_no_profile_help(profiles_mod) -> None:
    """Help text when no profile can be resolved — lists existing ones if any."""
    existing = [p.name for p in profiles_mod.list_profiles()]
    if existing:
        ch.error("You have profiles but none is active. Pick one:")
        for name in existing:
            ch.info(f"  navig cdp profile use {name}")
        ch.info('Or target it directly: navig do --profile <name> "<task>"')
    else:
        ch.error("No profile yet. Create one, then sign in once:")
        ch.info('  navig cdp profile new cybesis --note "Cybesis"')
        ch.info("  navig cdp open cybesis      # sign into your accounts in the window")
        ch.info('Then: navig do "<task>"   (or add --headless for a throwaway browser)')


def _guardrail(dry_run: bool, yes: bool) -> str:
    if dry_run:
        return (
            "DRY-RUN MODE. Do NOT send, submit, publish, pay, delete, or take any irreversible "
            "action. Navigate, read, and describe exactly what you WOULD do, then stop."
        )
    if not yes:
        return (
            "SAFE MODE. You may navigate, read, type, and PREPARE content (e.g. fill a form or "
            "compose a draft email). Do NOT finally send, submit, publish, pay, or delete anything. "
            "Prepare the action and report what is ready for the user to confirm. "
            "(The user can re-run with --yes to allow completing send/publish actions.)"
        )
    return ""


def _audit(task: str, profile: str | None, dry_run: bool, yes: bool) -> None:
    import json  # noqa: PLC0415
    import time  # noqa: PLC0415

    from navig.platform.paths import config_dir  # noqa: PLC0415

    try:
        path = config_dir() / "history" / "do.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": int(time.time()), "task": task, "profile": profile,
               "dry_run": dry_run, "yes": yes}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — auditing must never break the run
        pass


async def _agent_run(
    task: str, session_key: str, max_iterations: int
) -> tuple[str, dict | None]:
    """Run the agentic task; return ``(reply, account_fallback)``.

    ``account_fallback`` is set when a capped/failed account was rotated to a
    sibling subscription mid-run, so the CLI can surface which account answered.
    """
    from navig.agent.conv import ConversationalAgent  # noqa: PLC0415

    agent = ConversationalAgent()
    reply = await agent.run_agentic(
        task, max_iterations=max_iterations, toolset=["browser", "core"], session_key=session_key
    )
    return reply, getattr(agent, "_last_account_fallback", None)


def _prepare_bridge(profile_name: str | None, headless: bool) -> tuple[str, str | None]:
    """Open/reuse the profile and bridge the agent to it. Returns (session_key, profile_name)."""
    from navig.agent.tools.browser_session import register_desktop_endpoint  # noqa: PLC0415
    from navig.browser import cdp_actions  # noqa: PLC0415
    from navig.browser import profiles as profiles_mod  # noqa: PLC0415

    prof = profiles_mod.resolve_active(profile_name)
    session_key = f"do-{(prof.name if prof else profile_name) or 'headless'}"

    if prof is None:
        # An explicitly-named profile that doesn't exist is always an error —
        # even with --headless — so we never silently ignore the user's choice.
        if profile_name:
            ch.error(f"no profile '{profile_name}' — create it: "
                     f"navig cdp profile new {profile_name}")
            raise typer.Exit(1)
        if not headless:
            _print_no_profile_help(profiles_mod)
            raise typer.Exit(1)
        return session_key, None  # headless: agent uses its own throwaway browser, no bridge

    r = cdp_actions.profile_open(prof.name, headless=headless)
    if not r.get("ok"):
        ch.error(r.get("error", "could not open profile"))
        raise typer.Exit(1)
    register_desktop_endpoint(session_key, f"http://127.0.0.1:{r['port']}")
    if not headless:
        try:
            from navig.browser.cdp_runtime import run as _rt  # noqa: PLC0415

            _rt(cdp_actions.bring_to_front(int(r["port"])))  # raise the window so you see it
        except Exception:  # noqa: BLE001
            pass
    ch.info(f"profile '{prof.name}' ready on port {r['port']} — the AI will drive this window")
    return session_key, prof.name


@do_app.callback(invoke_without_command=True)
def do_main(
    ctx: typer.Context,
    task: str = typer.Argument(
        None, help='What to do, e.g. "open gmail and write an email to bob@x.com about Friday".'
    ),
    profile: str | None = typer.Option(None, "--profile", "-P", help="Profile (default: active)."),
    headless: bool = typer.Option(False, "--headless", help="No visible window (drives the resolved profile headlessly; a throwaway browser only if no profile is set)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Allow send/publish/delete without pausing."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Plan only; stop before any outward action."),
    max_steps: int = typer.Option(24, "--max-steps", help="Max agent iterations."),
):
    """Run a one-shot AI browser task on the active (or --profile) browser."""
    if ctx.invoked_subcommand is not None:
        return
    if not task:
        ch.error('Give a task, e.g. navig do "open gmail and write an email to …"')
        raise typer.Exit(1)

    session_key, resolved = _prepare_bridge(profile, headless)
    _audit(task, resolved, dry_run, yes)

    guardrail = _guardrail(dry_run, yes)
    full_task = f"{guardrail}\n\nTask: {task}" if guardrail else task
    if dry_run:
        ch.info("dry-run — no send/submit/publish/delete will be performed")
    elif not yes:
        ch.info("safe mode — will prepare but not finally send/publish (pass --yes to complete)")

    import asyncio  # noqa: PLC0415

    from navig.browser import safe_mode  # noqa: PLC0415

    # HARD gate (not just the prompt above): block outward clicks (send/submit/
    # publish/pay/delete) at the tool layer unless the user passed --yes.
    safe_mode.set_level("dry_run" if dry_run else ("off" if yes else "safe"))
    try:
        result, _acct_fb = asyncio.run(_agent_run(full_task, session_key, max_steps))
    except KeyboardInterrupt:
        ch.warning("interrupted")
        raise typer.Exit(130) from None
    except Exception as exc:  # noqa: BLE001
        ch.error(f"agent run failed: {exc}")
        raise typer.Exit(1) from None
    finally:
        safe_mode.set_level("off")

    # Surface an account rotation (e.g. a capped Claude Max account → a sibling
    # subscription) so it's never a silent swap.
    if _acct_fb:
        _reason = _acct_fb.get("reason") or "unavailable"
        _to = _acct_fb.get("to") or "another account"
        ch.warning(f"Primary account was {_reason} — answered with {_to}.")

    ch.console.print(result)
