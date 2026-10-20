"""``navig signals`` — manage inbound Signals ingest sources.

A *signal source* is one signed endpoint your own website/backend fires events
at. Verified events fan out to the deck (bell + Inbox + toast) and every channel
you enabled for their type in Settings → Notifications — so a
``payment_success`` POST can land in Telegram in two lines of website code.

A source can also be a **Stripe webhook endpoint** (``--provider stripe``): paste
its URL into the Stripe dashboard and every sale rings in NAVIG with no code in
the project at all — and a sale made while this PC is off still arrives, because
Stripe retries until the brain is back. ``connect`` does the dashboard step for
you when the project's Stripe key is on disk (here, or on its server).

    navig signals add shop --preset payment_success
    navig signals add grabnite --provider stripe --secret whsec_…
    navig signals connect gaze --key apps/gaze/.dev.vars::STRIPE_LIVE_SECRET_KEY
    navig signals list
    navig signals edit shop --priority high
    navig signals test grabnite
    navig signals rotate shop
    navig signals remove shop
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import NoReturn

import typer

from navig import console_helper as ch
from navig.notify.types import PRIORITIES, TYPE_KEYS

signals_app = typer.Typer(
    help="Inbound Signals — signed webhooks from your own apps (or Stripe) → deck + Telegram.",
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


def _print_endpoint(source: str) -> str | None:
    public = _public_ingest_url(source)
    if public:
        ch.info("Public URL:", public)
    else:
        ch.info("Local URL:", _local_ingest_url(source))
        ch.dim("Lighthouse not configured — run `navig lighthouse deploy` for a public URL.")
    return public


def _print_stripe_setup(source: str) -> None:
    from navig.notify.stripe_events import RECOMMENDED_EVENTS

    public = _print_endpoint(source)
    ch.dim("In Stripe: Developers → Webhooks → Add endpoint → paste the URL above.")
    ch.dim("Listen to events on 'Your account' and tick:")
    for ev in RECOMMENDED_EVENTS:
        ch.dim(f"  • {ev}")
    ch.dim("Payment Element with no Checkout? Tick payment_intent.succeeded as well — "
           "one payment still rings once.")
    if not public:
        ch.warning(
            "Stripe cannot reach a local URL.",
            "Deploy Lighthouse first (`navig lighthouse deploy`) so Stripe has a public endpoint.",
        )


@signals_app.command("add")
def add(
    name: str = typer.Argument(..., help="Source slug, e.g. shop or grabnite (a-z 0-9 _ -)."),
    preset: str = typer.Option(None, "--preset", "-p", help="Preset shape (see `navig signals presets`)."),
    type: str = typer.Option(None, "--type", help="Route into an existing category instead of its own row."),
    priority: str = typer.Option(None, "--priority", help=f"One of {', '.join(PRIORITIES)} (preset sets a default)."),
    title_tmpl: str = typer.Option(None, "--title-tmpl", help="Override the title template, e.g. 'Paid: {amount}'."),
    body_tmpl: str = typer.Option(None, "--body-tmpl", help="Override the body template over payload fields."),
    provider: str = typer.Option(
        None, "--provider", help="'stripe' = a Stripe webhook endpoint (no code in your app)."
    ),
    secret: str = typer.Option(
        None, "--secret", help="Provider signing secret (Stripe: whsec_…). Prompted, hidden, if omitted."
    ),
) -> None:
    """Create a signed ingest source.

    NAVIG-signed (default): prints a generated secret ONCE — store it in your app.
    Stripe (--provider stripe): give it the endpoint's whsec_ signing secret, then
    paste the printed URL into the Stripe dashboard. Sales ring like Store sales.
    """
    from navig.notify import signals

    if type is not None and type not in TYPE_KEYS:
        ch.error(f"Unknown type '{type}'.", f"Pick one of: {', '.join(TYPE_KEYS)}")
        raise typer.Exit(1)
    if priority is not None and priority not in PRIORITIES:
        ch.error(f"Unknown priority '{priority}'.", f"Pick one of: {', '.join(PRIORITIES)}")
        raise typer.Exit(1)
    shown_setup = False
    if provider and not secret:
        # Stripe only shows the whsec_ AFTER the endpoint exists, and the endpoint
        # needs our address — so print the address first, then ask.
        ch.info(f"Set up the Stripe endpoint for '{name}' first:")
        _print_stripe_setup(name.strip().lower())
        secret = ch.prompt_input(
            "Then paste the endpoint's signing secret (whsec_…)", password=True
        )
        shown_setup = True
    try:
        row = signals.add_source(
            name, preset=preset, notify_type=type, priority=priority,
            title_tmpl=title_tmpl, body_tmpl=body_tmpl, provider=provider, secret=secret,
        )
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1)

    if row.get("provider") == "stripe":
        ch.success(f"Stripe endpoint '{row['name']}' created.", f"Sales route to '{row['notify_type']}'.")
        if not shown_setup:
            _print_stripe_setup(row["name"])
        ch.dim(f"Then prove it: navig signals test {row['name']}")
        return

    ch.success(f"Signal source '{row['name']}' created{f' ({preset})' if preset else ''}.")
    ch.info("Secret (shown once):", row["secret"])
    ch.dim("Sign requests as: X-Navig-Signature: sha256=HMAC_SHA256(secret, f\"{ts}.{body}\")")
    ch.dim("Send also: X-Navig-Timestamp: <unix seconds>  (must be within 5 min).")
    ch.dim("Easiest: install the SDK — `npm i @navig/signals` or `pip install navig-signals`.")
    _print_endpoint(row["name"])


def _run_connect_on_host(host: str, env_path: str, var: str, url: str, events: list[str]) -> dict:
    """Run the connect step ON *host*: the account key is read and used there only."""
    from navig.commands.remote import _encode_b64_command
    from navig.config import get_config_manager
    from navig.core import host_lock
    from navig.notify import stripe_connect
    from navig.remote import RemoteOperations

    cm = get_config_manager()
    if not cm.host_exists(host):
        return {"status": "error", "error": f"no navig host named '{host}' (see `navig host list`)"}
    host_lock.guard_remote(cm, host, f"navig signals connect: {url.rsplit('/', 1)[-1]}")
    script = stripe_connect.remote_script(env_path, var, url, events)
    command = _encode_b64_command(script) or script
    proc = RemoteOperations(cm).execute_command(command, cm.load_host_config(host), capture_output=True)
    result = stripe_connect.parse_output(proc.stdout or "")
    if result.get("status") == "error" and proc.returncode not in (0, None):
        result["error"] = f"{result.get('error')} (ssh exit {proc.returncode})"
    return result


@signals_app.command("connect")
def connect(
    name: str = typer.Argument(..., help="Source slug for the project, e.g. schema or gaze (a-z 0-9 _ -)."),
    key: str = typer.Option(
        ..., "--key",
        help="Where the project's LIVE Stripe key is: <env-file>::<VAR>, "
        "e.g. apps/gaze/.dev.vars::STRIPE_LIVE_SECRET_KEY.",
    ),
    host: str = typer.Option(
        None, "--host",
        help="Read --key on this navig host (a production env file). The key never leaves it.",
    ),
    events: str = typer.Option(
        "checkout", "--events",
        help="checkout = Stripe Checkout / subscriptions; elements = Payment Element / PaymentIntents.",
    ),
    json_out: bool = typer.Option(False, "--json", help="Machine-readable result (ids only, never a secret)."),
) -> None:
    """Create the Stripe webhook endpoint for a project and store its secret — one step.

    Uses the project's own Stripe key to list its webhook endpoints, reuses NAVIG's
    if it is already there, otherwise creates it with the events for --events and
    stores the signing secret Stripe returns. Idempotent: re-running is a no-op.

        navig signals connect gaze --key apps/gaze/.dev.vars::STRIPE_LIVE_SECRET_KEY
        navig signals connect schema --host prod --key /srv/schema/api/.env::STRIPE_SECRET_KEY
        navig signals connect tidymark --key .dev.vars::STRIPE_LIVE_SECRET_KEY --events elements
    """
    from navig.notify import signals, stripe_connect
    from navig.notify.stripe_events import EVENT_PROFILES

    def _fail(message: str, hint: str = "") -> NoReturn:
        if json_out:
            ch.emit_json({"ok": False, "source": name, "error": message})
        elif hint:
            ch.error(message, hint)
        else:
            ch.error(message)
        raise typer.Exit(1)

    profile = EVENT_PROFILES.get(events.strip().lower())
    if profile is None:
        _fail(f"Unknown --events '{events}'.", f"Pick one of: {', '.join(EVENT_PROFILES)}")
    env_path, sep, var = key.rpartition("::")
    if not sep or not env_path or not var:
        _fail("--key must be <env-file>::<VAR>.", "e.g. apps/gaze/.dev.vars::STRIPE_LIVE_SECRET_KEY")
    try:
        name = signals.normalize_name(name)
    except ValueError as exc:
        _fail(str(exc))
    existing = signals.get_source(name)
    if existing is not None and existing.get("provider") != "stripe":
        _fail(f"'{name}' is already a website source.", "Pick another name for the Stripe one.")
    url = _public_ingest_url(name)
    if not url:
        _fail(
            "Stripe cannot reach this NAVIG yet — there is no public address.",
            "Deploy Lighthouse first: navig lighthouse deploy",
        )

    if host:
        result = _run_connect_on_host(host, env_path, var, url, list(profile))
    else:
        from navig.platform.paths import resolve_user_path

        result = stripe_connect.connect(str(resolve_user_path(env_path)), var, url, list(profile))

    status = result.get("status")
    account = result.get("account_name") or result.get("account_id") or "Stripe"
    if status == "error":
        _fail(f"Could not connect '{name}': {result.get('error') or 'unknown error'}")
    if status == "exists":
        if existing is not None:
            if json_out:
                ch.emit_json({"ok": True, "source": name, "status": "already_connected",
                              "account": result.get("account_id"), "endpoint": result.get("endpoint_id")})
            else:
                ch.success(f"'{name}' is already connected.", f"{account} · endpoint {result.get('endpoint_id')}")
            return
        _fail(
            f"{account} already has an endpoint for this address, but NAVIG has no secret for it.",
            f"Reveal its signing secret in Stripe (Developers → Webhooks → {result.get('endpoint_id')}), "
            f"then: navig signals add {name} --provider stripe --secret whsec_…",
        )
    secret = result.get("secret") or ""
    try:
        if existing is None:
            signals.add_source(name, provider="stripe", secret=secret)
        else:
            signals.rotate_secret(name, secret)
    except ValueError as exc:
        _fail(
            f"Stripe endpoint {result.get('endpoint_id')} was created but could not be stored: {exc}",
            "Delete that endpoint in Stripe, then run this again.",
        )
    if json_out:
        ch.emit_json({"ok": True, "source": name, "status": "created", "account": result.get("account_id"),
                      "endpoint": result.get("endpoint_id"), "events": result.get("events"),
                      "livemode": result.get("livemode")})
        return
    ch.success(
        f"'{name}' connected to {account}.",
        f"endpoint {result.get('endpoint_id')} · {result.get('events')} events · "
        f"{'live' if result.get('livemode') else 'TEST'} mode · secret stored (not shown)",
    )
    ch.dim(f"Prove it: navig signals test {name}")


@signals_app.command("edit")
def edit(
    name: str = typer.Argument(..., help="Source slug to edit."),
    type: str = typer.Option(
        None, "--type", help="Route into an existing category; '' = back to the source's own row."
    ),
    priority: str = typer.Option(None, "--priority", help=f"One of {', '.join(PRIORITIES)}."),
    title_tmpl: str = typer.Option(None, "--title-tmpl", help="New title template ('' clears it)."),
    body_tmpl: str = typer.Option(None, "--body-tmpl", help="New body template ('' clears it)."),
) -> None:
    """Change where a source routes, its priority, or its templates (URL and secret stay)."""
    from navig.notify import signals

    if all(v is None for v in (type, priority, title_tmpl, body_tmpl)):
        ch.error("Nothing to change.", "Pass --type, --priority, --title-tmpl or --body-tmpl.")
        raise typer.Exit(1)
    try:
        row = signals.update_source(
            name, notify_type=type, priority=priority, title_tmpl=title_tmpl, body_tmpl=body_tmpl
        )
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1)
    ch.success(f"Updated '{name}'.", f"{row['notify_type']} · {row['priority']}")


@signals_app.command("presets")
def presets() -> None:
    """List the built-in event presets (polished title/body shapes)."""
    from navig.notify.signal_presets import list_presets

    rows = list_presets()
    ch.header("Signal presets", f"{len(rows)} available — use `navig signals add <name> --preset <key>`")
    for p in rows:
        ch.info(f"{p['emoji']} {p['key']}", f"{p['label']} · {p['priority']} · “{p['title']}”")


@signals_app.command("list")
def list_cmd(
    json_out: bool = typer.Option(False, "--json", help="Print the sources as JSON."),
) -> None:
    """List signal sources (secrets masked) with hit counts."""
    from navig.notify import signals

    rows = signals.list_sources()
    if json_out:
        ch.emit_json(rows)
        return
    if not rows:
        ch.info("No signal sources yet.", "Create one with `navig signals add <name>`.")
        return

    from navig.console_helper import Table

    table = Table(box=None, show_header=True, padding=(0, 2))
    table.add_column("Source", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("Kind", no_wrap=True)
    table.add_column("Routes to", no_wrap=True)
    table.add_column("Priority", no_wrap=True)
    table.add_column("Hits", justify="right", no_wrap=True)
    table.add_column("Last event")
    for r in rows:
        state = "[green]● on[/green]" if r["enabled"] else "[dim]○ off[/dim]"
        kind = "Stripe" if r.get("provider") == "stripe" else "NAVIG-signed"
        table.add_row(
            f"{r.get('emoji') or '📡'} {r['name']}", state, kind, r["notify_type"],
            r["priority"], str(r["hit_count"]), r.get("last_event_at") or "[dim]never[/dim]",
        )
    ch.console.print(table)
    ch.dim(f"{len(rows)} source(s) · manage in NAVIG OS → Settings → Signals, or `navig signals edit <name>`")


@signals_app.command("remove")
def remove(name: str = typer.Argument(..., help="Source slug to delete.")) -> None:
    """Delete a signal source (its URL stops accepting events immediately)."""
    from navig.notify import signals

    if signals.remove_source(name):
        ch.success(f"Removed signal source '{name}'.")
    else:
        ch.error(f"No signal source named '{name}'.")
        raise typer.Exit(1)


@signals_app.command("rotate")
def rotate(
    name: str = typer.Argument(..., help="Source slug to re-key."),
    secret: str = typer.Option(
        None, "--secret", help="Stripe sources: the NEW whsec_ secret from the dashboard (prompted if omitted)."
    ),
) -> None:
    """Replace a source's secret (the old one stops working immediately)."""
    from navig.notify import signals

    src = signals.get_source(name)
    if src is None:
        ch.error(f"No signal source named '{name}'.")
        raise typer.Exit(1)
    if src.get("provider") and not secret:
        secret = ch.prompt_input("New signing secret from the Stripe endpoint (whsec_…)", password=True)
    try:
        new_secret = signals.rotate_secret(name, secret)
    except ValueError as exc:
        ch.error(str(exc))
        raise typer.Exit(1)
    if src.get("provider"):
        ch.success(f"Stored the new Stripe signing secret for '{name}'.")
        return
    ch.success(f"Rotated secret for '{name}'.")
    ch.info("New secret (shown once):", new_secret)


@signals_app.command("test")
def test(
    name: str = typer.Argument(..., help="Source slug to fire a sample event at."),
    message: str = typer.Option("Test signal from `navig signals test`", "--message", "-m"),
) -> None:
    """Sign a sample event and POST it to the local gateway to prove the round-trip.

    A Stripe source gets a Stripe-signed sample ``checkout.session.completed`` — the
    exact path a real sale takes, tagged ``[test]``.
    """
    import urllib.error
    import urllib.request

    from navig.notify import signals

    src = signals.get_source(name)
    if src is None:
        ch.error(f"No signal source named '{name}'.")
        raise typer.Exit(1)

    if src.get("provider") == "stripe":
        from navig.notify import stripe_events

        body = json.dumps(stripe_events.sample_event()).encode()
        headers = {
            "Content-Type": "application/json",
            stripe_events.SIG_HEADER: stripe_events.sign(src["secret"], body),
        }
    else:
        body = json.dumps({"event": "test", "message": message}).encode()
        ts = str(int(time.time()))
        headers = {
            "Content-Type": "application/json",
            "X-Navig-Timestamp": ts,
            "X-Navig-Signature": _sign(src["secret"], body, ts),
        }
    url = _local_ingest_url(name)
    req = urllib.request.Request(url, data=body, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 — loopback
            data = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        ch.error(f"Gateway returned {exc.code}.", (exc.read() or b"").decode("utf-8", "replace"))
        raise typer.Exit(1)
    except urllib.error.URLError as exc:
        ch.error("Could not reach the gateway.", f"{exc} — is the daemon running? ({url})")
        raise typer.Exit(1)

    delivered = data.get("delivered") or []
    if delivered:
        ch.success("Signal accepted.", f"Delivered to: {', '.join(delivered)}")
        ch.dim("Check the deck bell/Inbox and your Telegram chat.")
    else:
        # Verified + accepted, but the fan-out reached zero channels — every channel
        # for this type is off/muted. Say so honestly instead of a green "delivered".
        ch.warning(
            "Signal accepted but delivered to no channels.",
            "Every channel for this notification type is off or muted — "
            "enable one in Settings → Notifications.",
        )
        raise typer.Exit(1)
