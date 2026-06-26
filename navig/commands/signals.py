"""``navig signals`` — manage inbound Signals ingest sources.

A *signal source* is one HMAC-signed endpoint your own website/backend fires
events at. Verified events fan out to the deck (bell + Inbox + toast) and every
channel you enabled for their type in Settings → Notifications — so a
``payment_success`` POST can land in Telegram in two lines of website code.

    navig signals add stripe-prod --priority high
    navig signals list
    navig signals test stripe-prod
    navig signals rotate stripe-prod
    navig signals remove stripe-prod
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import typer

from navig import console_helper as ch
from navig.notify.types import PRIORITIES, TYPE_KEYS

signals_app = typer.Typer(
    help="Inbound Signals — signed webhooks from your own apps → deck + Telegram.",
    no_args_is_help=True,
)


def _config():
    from navig.core import Config  # local import keeps `navig --help` fast

    return Config()


def _public_ingest_url(source: str) -> str | None:
    """Lighthouse public URL for *source*, or None if Lighthouse isn't configured."""
    cfg = _config()
    url = (cfg.get("cloud.lighthouse_url") or "").strip()
    key = (cfg.get("deck.api_key") or "").strip()
    if not url or not key:
        return None
    from navig.cloud import api_key_hash

    return f"{url.rstrip('/')}/ingest/{api_key_hash(key)}/{source}"


def _local_ingest_url(source: str) -> str:
    from navig.gateway_client import gateway_base_url

    return f"{gateway_base_url().rstrip('/')}/api/ingest/{source}"


def _sign(secret: str, body: bytes, ts: str) -> str:
    mac = hmac.new(secret.encode(), ts.encode() + b"." + body, hashlib.sha256)
    return "sha256=" + mac.hexdigest()


def _print_endpoint(source: str) -> None:
    public = _public_ingest_url(source)
    if public:
        ch.info("Public URL:", public)
    else:
        ch.info("Local URL:", _local_ingest_url(source))
        ch.dim("Lighthouse not configured — run `navig lighthouse deploy` for a public URL.")


@signals_app.command("add")
def add(
    name: str = typer.Argument(..., help="Source slug, e.g. stripe-prod (a-z 0-9 _ -)."),
    preset: str = typer.Option(None, "--preset", "-p", help="Preset shape (see `navig signals presets`)."),
    type: str = typer.Option(None, "--type", help="Route into an existing category instead of its own row."),
    priority: str = typer.Option(None, "--priority", help=f"One of {', '.join(PRIORITIES)} (preset sets a default)."),
    title_tmpl: str = typer.Option(None, "--title-tmpl", help="Override the title template, e.g. 'Paid: {amount}'."),
    body_tmpl: str = typer.Option(None, "--body-tmpl", help="Override the body template over payload fields."),
) -> None:
    """Create a signed ingest source. Prints the secret ONCE — store it now.

    By default the source gets its OWN mutable row under the Signals category so
    you can toggle it per-source. A --preset pre-fills a polished title/body.
    """
    from navig.notify import signals

    if type is not None and type not in TYPE_KEYS:
        ch.error(f"Unknown type '{type}'.", f"Pick one of: {', '.join(TYPE_KEYS)}")
        raise typer.Exit(1)
    if priority is not None and priority not in PRIORITIES:
        ch.error(f"Unknown priority '{priority}'.", f"Pick one of: {', '.join(PRIORITIES)}")
        raise typer.Exit(1)
    try:
        row = signals.add_source(
            name, preset=preset, notify_type=type, priority=priority,
            title_tmpl=title_tmpl, body_tmpl=body_tmpl,
        )
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1)

    ch.success(f"Signal source '{row['name']}' created{f' ({preset})' if preset else ''}.")
    ch.info("Secret (shown once):", row["secret"])
    ch.dim("Sign requests as: X-Navig-Signature: sha256=HMAC_SHA256(secret, f\"{ts}.{body}\")")
    ch.dim("Send also: X-Navig-Timestamp: <unix seconds>  (must be within 5 min).")
    ch.dim("Easiest: install the SDK — `npm i @navig/signals` or `pip install navig-signals`.")
    _print_endpoint(row["name"])


@signals_app.command("presets")
def presets() -> None:
    """List the built-in event presets (polished title/body shapes)."""
    from navig.notify.signal_presets import list_presets

    rows = list_presets()
    ch.header("Signal presets", f"{len(rows)} available — use `navig signals add <name> --preset <key>`")
    for p in rows:
        ch.info(f"{p['emoji']} {p['key']}", f"{p['label']} · {p['priority']} · “{p['title']}”")


@signals_app.command("list")
def list_cmd() -> None:
    """List signal sources (secrets masked) with hit counts."""
    from navig.notify import signals

    rows = signals.list_sources()
    if not rows:
        ch.info("No signal sources yet.", "Create one with `navig signals add <name>`.")
        return
    ch.header("Signal sources", f"{len(rows)} configured")
    for r in rows:
        state = "on " if r["enabled"] else "off"
        ch.info(
            f"[{state}] {r['name']}",
            f"{r['notify_type']} · {r['priority']} · hits={r['hit_count']} · {r['secret']}",
        )


@signals_app.command("remove")
def remove(name: str = typer.Argument(..., help="Source slug to delete.")) -> None:
    """Delete a signal source."""
    from navig.notify import signals

    if signals.remove_source(name):
        ch.success(f"Removed signal source '{name}'.")
    else:
        ch.error(f"No signal source named '{name}'.")
        raise typer.Exit(1)


@signals_app.command("rotate")
def rotate(name: str = typer.Argument(..., help="Source slug to re-key.")) -> None:
    """Issue a new secret (old one stops working immediately). Shown once."""
    from navig.notify import signals

    try:
        secret = signals.rotate_secret(name)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1)
    ch.success(f"Rotated secret for '{name}'.")
    ch.info("New secret (shown once):", secret)


@signals_app.command("test")
def test(
    name: str = typer.Argument(..., help="Source slug to fire a sample event at."),
    message: str = typer.Option("Test signal from `navig signals test`", "--message", "-m"),
) -> None:
    """Sign a sample payload and POST it to the local gateway to prove the round-trip."""
    import urllib.error
    import urllib.request

    from navig.notify import signals

    src = signals.get_source(name)
    if src is None:
        ch.error(f"No signal source named '{name}'.")
        raise typer.Exit(1)

    body = json.dumps({"event": "test", "message": message}).encode()
    ts = str(int(time.time()))
    url = _local_ingest_url(name)
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Navig-Timestamp": ts,
            "X-Navig-Signature": _sign(src["secret"], body, ts),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        ch.error(f"Gateway returned {exc.code}.", (exc.read() or b"").decode("utf-8", "replace"))
        raise typer.Exit(1)
    except urllib.error.URLError as exc:
        ch.error("Could not reach the gateway.", f"{exc} — is the daemon running? ({url})")
        raise typer.Exit(1)

    delivered = ", ".join(data.get("delivered") or []) or "no channels enabled"
    ch.success("Signal accepted.", f"Delivered to: {delivered}")
    ch.dim("Check the deck bell/Inbox and your Telegram chat.")
