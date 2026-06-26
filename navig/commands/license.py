"""
``navig license`` — paste / show / status / remove the daemon's license key.

The license is just a signed token. This CLI is the safety net + scripting
target; the primary activation path is the post-purchase web flow on
``navig.run/activate`` which calls ``POST /api/deck/license/paste`` for you.
"""

from __future__ import annotations

import sys

import typer

app = typer.Typer(help="Show, paste, or remove the NAVIG Deck license.", no_args_is_help=True)


def _ch():
    from navig import console_helper
    return console_helper


@app.command("paste")
def license_paste(
    token: str = typer.Argument(
        ...,
        help="The signed token (NAVIG-LICENSE-v1:...). Paste from your activation email.",
    ),
) -> None:
    """Validate a license token and persist it to ``~/.navig/license.key``."""
    from navig.license import paste_license, truncate_for_display
    ch = _ch()

    status = paste_license(token)
    if status.valid:
        ch.success(f"License accepted: {status.effective_tier.upper()}")
        ch.info(f"  host_limit:   {status.host_limit}")
        ch.info(f"  capabilities: {', '.join(status.capabilities)}")
        if status.subscription_until:
            ch.info(f"  subscription_until: {status.subscription_until.isoformat()}")
        elif status.billing_period == "one_time":
            ch.info(f"  billing_period: one-time perpetual (no expiry)")
        ch.dim(f"  saved to:     ~/.navig/license.key")
        ch.dim(f"  token:        {truncate_for_display(token)}")
        ch.dim("")
        ch.dim("Restart `navig gateway` if it's running so the daemon picks up the new license.")
        return

    # Validation failed -- explain why.
    ch.warning(f"License REJECTED: {status.reason}")
    if status.reason == "malformed":
        ch.dim("The token isn't a valid NAVIG-LICENSE-v1 string. Re-copy it from your")
        ch.dim("activation email -- avoid newlines and partial selections.")
    elif status.reason == "invalid_signature":
        ch.dim("The token's cryptographic signature did not verify. Possible causes:")
        ch.dim("  - token corrupted during copy/paste")
        ch.dim("  - your NAVIG Deck version is older than the signing key (`navig update`)")
    elif status.reason == "unsupported_version":
        ch.dim("This token uses a newer license version than your NAVIG can read.")
        ch.dim("Run: navig update")
    elif status.reason == "revoked":
        ch.dim("This license has been revoked. Contact support@navig.run for re-issue.")
    raise typer.Exit(code=1)


@app.command("show")
def license_show() -> None:
    """Print the persisted license token in truncated form."""
    from navig.license import read_raw_token, truncate_for_display, license_path
    ch = _ch()
    token = read_raw_token()
    if token is None:
        ch.info("No license installed.")
        ch.dim(f"  file: {license_path()} (not present)")
        ch.dim("Solo (Free) tier active: 1 host, Core Ops only.")
        return
    ch.info(f"License token: {truncate_for_display(token)}")
    ch.dim(f"  file: {license_path()}")
    ch.dim("Use `navig license status` for entitlement details.")


@app.command("status")
def license_status() -> None:
    """Show detailed entitlement (tier, hosts, modules, subscription)."""
    from navig.license import current_status
    ch = _ch()
    s = current_status()
    if not s.valid:
        if s.reason == "missing":
            ch.info("No license installed -- running on Solo (Free) tier.")
        else:
            ch.warning(f"License invalid: {s.reason} -- running on Solo (Free) tier.")
        ch.info(f"  tier:         {s.effective_tier}")
        ch.info(f"  host_limit:   {s.host_limit}")
        ch.info(f"  capabilities: {', '.join(s.capabilities)}")
        return

    ch.success(f"License valid: {s.effective_tier.upper()}")
    ch.info(f"  host_limit:        {s.host_limit}")
    ch.info(f"  capabilities:      {', '.join(s.capabilities)}")
    if s.perpetual_modules:
        ch.info(f"  perpetual modules: {', '.join(s.perpetual_modules)}")
    if s.subscription_until:
        ch.info(f"  subscription_until: {s.subscription_until.isoformat()}")
        ch.info(f"  subscription_active: {s.subscription_active}")
    elif s.billing_period == "one_time":
        ch.info(f"  billing_period:    one-time perpetual (no expiry)")
    if s.issued_at:
        ch.dim(f"  issued_at:         {s.issued_at.isoformat()}")
    if s.license_id:
        ch.dim(f"  license_id:        {s.license_id}")
    if s.branding:
        ch.dim(f"  branding:          {s.branding}")


@app.command("remove")
def license_remove(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt."),
) -> None:
    """Delete the persisted license (drops you back to Solo / Free tier)."""
    from navig.license import remove_license, read_raw_token
    ch = _ch()

    if read_raw_token() is None:
        ch.info("No license installed -- nothing to remove.")
        return

    if not yes:
        confirm = typer.prompt("This will drop you to Solo (Free) tier. Type 'yes' to continue")
        if confirm.strip().lower() != "yes":
            ch.warning("Cancelled.")
            return

    status = remove_license()
    ch.success("License removed.")
    ch.info(f"  effective tier now: {status.effective_tier}")
    ch.dim("Modules you bought one-time are gone too because the perpetual entitlement")
    ch.dim("lived inside the same token. Re-paste your license to restore.")
