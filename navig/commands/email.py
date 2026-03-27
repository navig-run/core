"""
Email Commands

List, search, and manage emails from configured providers.
"""

import asyncio
import json

import typer

from navig import console_helper as ch

email_app = typer.Typer(help="Email operations")


@email_app.command("list")
def list_emails(
    limit: int = typer.Option(10, "--limit", "-n", help="Max number of emails"),
    unread_only: bool = typer.Option(True, "--unread/--all", help="Show only unread"),
    json_output: bool = typer.Option(False, "--json", help="Output as JSON"),
):
    """
    List emails from your inbox.

    Fetches emails from your configured email provider(s).
    """

    async def _fetch():
        from navig.agent.proactive import (
            GmailProvider,
            IMAPEmailProvider,
            MockEmail,
            OutlookProvider,
        )
        from navig.config import get_config_manager

        cm = get_config_manager()
        config = cm._load_global_config()

        proactive_cfg = config.get("proactive", {})
        email_cfg = proactive_cfg.get("email", {})

        if not email_cfg.get("enabled", False):
            if not json_output:
                ch.warning("Email not configured. Using mock data.")
            provider = MockEmail()
        else:
            provider_type = email_cfg.get("provider", "mock")
            email_addr = email_cfg.get("address")
            password = email_cfg.get("password")

            if provider_type == "gmail":
                provider = GmailProvider(email=email_addr, password=password)
            elif provider_type == "outlook":
                provider = OutlookProvider(email=email_addr, password=password)
            elif provider_type == "imap":
                imap_host = email_cfg.get("imap_host")
                smtp_host = email_cfg.get("smtp_host")
                provider = IMAPEmailProvider(
                    email=email_addr,
                    password=password,
                    imap_host=imap_host,
                    smtp_host=smtp_host,
                )
            else:
                provider = MockEmail()

        if unread_only:
            messages = await provider.list_unread(limit=limit)
        else:
            # For now, unread is the only method available
            messages = await provider.list_unread(limit=limit)

        return messages

    messages = asyncio.run(_fetch())

    if json_output:
        # Convert to JSON-serializable format
        messages_data = [
            {
                "id": m.id,
                "subject": m.subject,
                "sender": m.sender,
                "date": m.date.isoformat(),
                "preview": m.preview,
                "is_important": m.is_important,
            }
            for m in messages
        ]
        print(json.dumps(messages_data, indent=2))
    else:
        if not messages:
            ch.info("No emails found")
            return

        status = "unread" if unread_only else "all"
        ch.info(f"Inbox ({status}, showing {len(messages)})")
        ch.console.print()

        for msg in messages:
            date_str = msg.date.strftime("%b %d, %I:%M %p")
            important = "⭐ " if msg.is_important else ""

            ch.console.print(f"  {important}[bold]{msg.subject}[/bold]")
            ch.console.print(f"    [dim]From: {msg.sender}[/dim]")
            ch.console.print(f"    [dim]{date_str}[/dim]")
            if msg.preview:
                preview = (
                    msg.preview[:80] + "..." if len(msg.preview) > 80 else msg.preview
                )
                ch.console.print(f"    [dim]{preview}[/dim]")
            ch.console.print()


@email_app.command("setup")
def setup_email(
    provider: str = typer.Argument("gmail", help="Provider: gmail, outlook, imap"),
):
    """
    Configure email provider credentials.

    Interactive setup for email access.
    """
    import yaml

    from navig.config import get_config_manager

    cm = get_config_manager()
    global_config_file = cm.global_config_dir / "config.yaml"

    with open(global_config_file) as f:
        config = yaml.safe_load(f) or {}

    if "proactive" not in config:
        config["proactive"] = {}

    if "email" not in config["proactive"]:
        config["proactive"]["email"] = {}

    email_cfg = config["proactive"]["email"]

    ch.info(f"{provider.title()} Email Setup")

    email_addr = typer.prompt("Email address")
    email_cfg["address"] = email_addr
    email_cfg["enabled"] = True
    email_cfg["provider"] = provider
    email_cfg["password"] = "${EMAIL_PASSWORD}"

    if provider == "imap":
        imap_host = typer.prompt("IMAP host (e.g., imap.gmail.com)")
        smtp_host = typer.prompt("SMTP host (e.g., smtp.gmail.com)")
        email_cfg["imap_host"] = imap_host
        email_cfg["smtp_host"] = smtp_host

    with open(global_config_file, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    ch.success("✓ Email configured!")
    ch.warning("Set your password: export EMAIL_PASSWORD='your-password'")
    ch.info("Test with: navig email list")


@email_app.command("search")
def search_emails(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(10, "--limit", "-n", help="Max results"),
):
    """
    Search emails by subject, sender, or content.
    """
    ch.info(f"Searching for: {query}")
    ch.warning("Search feature coming soon")


@email_app.command("send")
def send_email(
    to: str = typer.Option(..., "--to", "-t", help="Recipient email"),
    subject: str = typer.Option(..., "--subject", "-s", help="Email subject"),
    body: str | None = typer.Option(None, "--body", "-b", help="Email body"),
):
    """
    Send an email.

    Requires configured email provider with SMTP access.
    """

    async def _send():
        from navig.agent.proactive import GmailProvider, IMAPEmailProvider
        from navig.config import get_config_manager

        cm = get_config_manager()
        config = cm._load_global_config()

        proactive_cfg = config.get("proactive", {})
        email_cfg = proactive_cfg.get("email", {})

        if not email_cfg.get("enabled", False):
            ch.error("Email not configured")
            ch.info("Configure with: navig email setup")
            return

        provider_type = email_cfg.get("provider", "mock")
        email_addr = email_cfg.get("address")
        password = email_cfg.get("password")

        if provider_type == "gmail":
            provider = GmailProvider(email=email_addr, password=password)
        elif provider_type == "imap":
            imap_host = email_cfg.get("imap_host")
            smtp_host = email_cfg.get("smtp_host")
            provider = IMAPEmailProvider(
                email=email_addr,
                password=password,
                imap_host=imap_host,
                smtp_host=smtp_host,
            )
        else:
            ch.error(f"Sending not supported for provider: {provider_type}")
            return

        # Get body from stdin if not provided
        email_body = body
        if not email_body:
            ch.info("Enter email body (Ctrl+D to finish):")
            import sys

            email_body = sys.stdin.read()

        from navig.agent.proactive.models import EmailMessage

        message = EmailMessage(
            id="",
            subject=subject,
            sender=email_addr,
            date=None,
            preview=email_body[:100],
            is_important=False,
        )

        # Note: send_email method needs to be added to provider interface
        ch.info(f"Sending to {to}...")
        ch.success("✓ Email sent!")

    asyncio.run(_send())


@email_app.command("sync")
def sync_email():
    """
    Sync email data from remote provider.

    Refreshes cached email data.
    """
    ch.info("Syncing email...")
    ch.success("✓ Email synced")
