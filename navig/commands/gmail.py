"""``navig gmail`` — compose & send Gmail in your logged-in browser profile.

Reliable, deterministic (no LLM, no fragile UI clicking): opens Gmail's compose
deep-link on your profile's visible browser. Requires the profile to be signed
into Gmail once (session-first). Sending is off by default — pass ``--send``.
"""

from __future__ import annotations

import json as _json

import typer

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

gmail_app = typer.Typer(
    name="gmail",
    help="Compose & send Gmail in your logged-in browser profile (deep-link, no LLM).",
    no_args_is_help=True,
)


def _run(coro):
    from navig.browser.cdp_runtime import run as _rt_run

    return _rt_run(coro)


def _resolve_and_open(profile: str | None, headless: bool):
    """Resolve the active/--profile browser, open/reuse it, raise it. Returns (Profile, port)."""
    from navig.browser import cdp_actions
    from navig.browser import profiles as p

    prof = p.resolve_active(profile)
    if prof is None:
        existing = [x.name for x in p.list_profiles()]
        if existing:
            ch.error("You have profiles but none is active. Pick one with --profile, or:")
            for name in existing:
                ch.info(f"  navig cdp profile use {name}")
        else:
            ch.error("No profile yet. Create one, then sign into Gmail once:")
            ch.info('  navig cdp profile new cybesis --note "Cybesis"')
            ch.info("  navig cdp open cybesis     # sign into Gmail in the window that opens")
        raise typer.Exit(1)
    r = cdp_actions.profile_open(prof.name, headless=headless)
    if not r.get("ok"):
        ch.error(r.get("error", "could not open profile"))
        raise typer.Exit(1)
    port = int(r["port"])
    if not headless:
        try:
            _run(cdp_actions.bring_to_front(port))  # raise the window so you see it
        except Exception:  # noqa: BLE001
            pass
    return prof, port


@gmail_app.command("compose")
def gmail_compose_cmd(
    to: str = typer.Option("", "--to", "-t", help="Recipient(s), comma-separated."),
    subject: str = typer.Option("", "--subject", "-s", help="Subject line."),
    body: str = typer.Option("", "--body", "-b", help="Message body."),
    cc: str = typer.Option("", "--cc", help="Cc recipient(s)."),
    bcc: str = typer.Option("", "--bcc", help="Bcc recipient(s)."),
    send: bool = typer.Option(False, "--send", help="Actually send (default: compose only)."),
    profile: str | None = typer.Option(None, "--profile", "-P", help="Profile (default: active)."),
    account: str = typer.Option(
        "", "--account",
        help="Which Gmail: an email (me@gmail.com) or index (0,1,2…). Default: the profile's saved one.",
    ),
    remember: bool = typer.Option(False, "--remember", help="Save --account as this profile's default Gmail."),
    headless: bool = typer.Option(False, "--headless", help="No visible window."),
    json_out: bool = typer.Option(False, "--json"),
):
    """Open a pre-filled Gmail compose window (and optionally send it)."""
    from navig.browser import cdp_actions
    from navig.browser import profiles as p

    prof, port = _resolve_and_open(profile, headless)
    # account: explicit --account → the profile's remembered default → 0
    acct = account or prof.default_account or "0"
    if remember and account:
        if p.set_default_account(prof.name, account):
            if not json_out:
                ch.info(f"default Gmail for '{prof.name}' → {account}")
        elif not json_out:
            ch.warning("could not save the default account (disk full or permissions?)")

    if send and not json_out:
        if not typer.confirm(f"Send this email to {to or '(no recipient)'} (from {acct})?"):
            ch.warning("cancelled — leaving the compose window open")
            send = False

    result = _run(cdp_actions.gmail_compose(
        port, to=to, subject=subject, body=body, cc=cc, bcc=bcc,
        send=send, account=acct,
    ))

    if json_out:
        ch.console.print_json(_json.dumps(result))
        raise typer.Exit(0 if result.get("ok") else 1)

    status = result.get("status")
    if status == "sent":
        ch.success(f"Email sent to {to}")
    elif status == "composed":
        ch.success(f"Compose window ready{' for ' + to if to else ''} — review & send in the browser")
        ch.info("Add --send to send automatically next time.")
    elif status == "not_signed_in":
        ch.error("This profile isn't signed into Gmail.")
        ch.info(f"Sign in once: navig cdp open {profile or '<profile>'}  → then retry.")
        raise typer.Exit(1)
    else:
        ch.error(result.get("error") or result.get("detail") or "compose failed")
        raise typer.Exit(1)
