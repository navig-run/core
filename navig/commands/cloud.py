"""``navig cloud`` -- connect / status / disconnect / key.

Thin CLI that flips ``cloud.enabled`` in the config and surfaces broker state.
The actual cloudflared subprocess + heartbeat live in the gateway lifespan
(see :class:`navig.cloud.CloudManager`). This CLI is the user-facing toggle;
the daemon owns the lifecycle.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import urllib.error
import urllib.request

import typer

app = typer.Typer(help="NAVIG cloud (Cloudflare quick-tunnel broker)", no_args_is_help=True)
logger = logging.getLogger(__name__)


# Broker (tunnel routing API, POST /api/cloud/*) and the hosted Relay frontend
# (relay.navig.run — serves the /connect magic-link page and reaches a
# self-installed daemon over cloudflared + outbound uplink, no VPS) are
# separate hosts. The Relay surface was previously named deck.navig.run.
_BROKER_DEFAULT = "https://api.navig.run"
_RELAY_DEFAULT = "https://relay.navig.run"
_DECK_DEFAULT = _RELAY_DEFAULT  # back-compat alias for the old name
_TRUNCATE_PREFIX = 4
_TRUNCATE_SUFFIX = 4


def _deck_url(cfg) -> str:
    """The hosted Relay frontend base for /connect magic links (NOT the broker).

    Prefers the new ``cloud.relay_url`` key; falls back to the legacy
    ``cloud.deck_url`` for existing configs, then the relay.navig.run default.
    """
    return str(
        cfg.get("cloud.relay_url", cfg.get("cloud.deck_url", _RELAY_DEFAULT))
    ).rstrip("/")


def _config():
    from navig.core import Config  # local import keeps `navig --help` fast
    return Config()


def _truncate(key: str) -> str:
    if not key:
        return "<unset>"
    if len(key) <= _TRUNCATE_PREFIX + _TRUNCATE_SUFFIX + 1:
        return "***"
    return f"{key[:_TRUNCATE_PREFIX]}…{key[-_TRUNCATE_SUFFIX:]}"


def _ensure_api_key() -> str:
    cfg = _config()
    key = (cfg.get("deck.api_key") or "").strip()
    if not key:
        key = "navig_" + secrets.token_urlsafe(32)
        cfg.set("deck.api_key", key, scope="global")
        cfg.save(scope="global")
    return key


def _gateway_port() -> int:
    # Canonical resolver: reads the nested ``gateway.port`` from config (the flat
    # ``cfg.get("gateway.port")`` here does not resolve the dotted key and would fall
    # back to a stale default, making status probe the wrong port).
    from navig.gateway_client import gateway_cli_defaults

    return gateway_cli_defaults()[0]


def _gateway_status_url() -> str:
    port = _gateway_port()
    return f"http://127.0.0.1:{port}/api/deck/cloud/status"


def _try_daemon_status() -> dict | None:
    """Hit the local daemon's /api/deck/cloud/status. Returns None if down."""
    import json
    cfg = _config()
    api_key = cfg.get("deck.api_key", "") or ""
    req = urllib.request.Request(
        _gateway_status_url(),
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


async def _wait_online(timeout_s: float = 30.0) -> dict | None:
    """Poll the local daemon's status endpoint until status=online or timeout."""
    import time
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        info = _try_daemon_status()
        if info and info.get("status") == "online":
            return info
        await asyncio.sleep(1.0)
    return _try_daemon_status()


def _print_success(api_key: str, broker_url: str, tunnel_url: str | None, deck_url: str = _DECK_DEFAULT) -> None:
    from navig import console_helper as ch
    magic = f"{deck_url.rstrip('/')}/connect?key={api_key}"
    ch.success("Cloud connected.")
    ch.info(f"  Broker:    {broker_url}")
    ch.info(f"  Daemon:    {tunnel_url or '<provisioning>'}")
    ch.info(f"  API key:   {api_key}   (save this — full key shown once)")
    ch.info(f"  Magic link: {magic}")
    ch.info("Open the link in any browser, or send /start to your Telegram bot.")


# ── Subcommands ───────────────────────────────────────────────────────────────

@app.command("connect")
def cloud_connect(
    broker_url: str = typer.Option(_BROKER_DEFAULT, "--broker", help="Broker base URL."),
    label: str = typer.Option("", "--label", help="Friendly tunnel label (default: hostname)."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Skip the wait-for-online step."),
    no_service: bool = typer.Option(
        False, "--no-service",
        help="Skip auto-start registration (systemd/launchd/Scheduled Task).",
    ),
) -> None:
    """Enable cloud routing on this daemon and register with the broker.

    Sets ``cloud.enabled=true`` in the global config. If the gateway is
    already running, you must restart it for the change to take effect --
    this command prints a reminder when the daemon is unreachable.
    """
    from navig import console_helper as ch
    from navig.core import narrator

    narrator.blank()
    narrator.phase("NAVIG cloud connect", icon="brain")

    cfg = _config()
    deck_url = _deck_url(cfg)
    narrator.step("minting / loading deck.api_key", icon="gear")
    api_key = _ensure_api_key()
    narrator.step("persisting cloud config (enabled=true)", icon="gear")
    cfg.set("cloud.enabled", True, scope="global")
    cfg.set("cloud.broker_url", broker_url, scope="global")
    if label:
        cfg.set("cloud.tunnel_label", label, scope="global")
    cfg.save(scope="global")

    running = _try_daemon_status() is not None
    if not running:
        narrator.step("daemon not running yet -- settings saved for next boot", icon="warn")
        narrator.blank()
        narrator.metrics([
            ("cloud.enabled", "true"),
            ("cloud.broker_url", broker_url),
            ("deck.api_key", api_key),
            ("Magic link", f"{deck_url}/connect?key={api_key}"),
        ])
        narrator.blank()
        narrator.verdict("Run `navig gateway start` to bring cloud online.", icon="dot")
        if no_service:
            ch.dim("Auto-start registration skipped (--no-service).")
        return

    info = None
    if not no_wait:
        narrator.step("waiting for tunnel to come online (up to 30s)…", icon="radio")
        try:
            info = asyncio.run(_wait_online(30.0))
        except KeyboardInterrupt:
            narrator.verdict("Interrupted while waiting.", icon="warn")
            return

    tunnel = (info or {}).get("tunnel_url") if info else None
    if tunnel:
        narrator.step(f"tunnel registered: {tunnel}", icon="anchor")
    else:
        narrator.step("tunnel URL not yet visible (cloudflared may still be handshaking)", icon="warn")

    narrator.blank()
    narrator.phase("Access points", icon="spark")
    narrator.step(f"Browser:  {deck_url}/connect?key={api_key}", icon="globe")
    narrator.step("Telegram: send /start to your bot, then tap the Mini App", icon="anchor")

    narrator.blank()
    narrator.verdict("Cloud routing enabled.", icon="check")

    if no_service:
        ch.dim("Auto-start registration skipped (--no-service).")
    else:
        ch.dim(
            "Auto-start: run `navig service install` to register a user-scope "
            "systemd/launchd/Scheduled Task entry (no admin required)."
        )


@app.command("direct")
def cloud_direct(
    url: str = typer.Argument(
        "",
        help="Public HTTPS URL of your reverse proxy (e.g. https://navig.example.com). "
        "Leave empty with --clear to revert to cloudflared mode.",
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Clear cloud.public_url and revert to cloudflared mode."
    ),
) -> None:
    """Switch to DIRECT mode for VPS / reverse-proxy deployments.

    When set, the daemon skips cloudflared entirely and registers your URL
    with the broker. You're responsible for terminating TLS on this hostname
    (nginx, Caddy, Traefik) and forwarding to gateway.host:gateway.port.

    See docs/CLOUD.md for full VPS setup recipes.
    """
    from urllib.parse import urlparse

    from navig import console_helper as ch
    cfg = _config()

    if clear or url.strip().lower() in ("--clear", "clear", "off"):
        cfg.set("cloud.public_url", "", scope="global")
        cfg.save(scope="global")
        ch.success("Direct mode cleared. Cloudflared will spawn on next gateway start.")
        ch.dim("Restart the gateway: `navig gateway start` (after stopping the running one).")
        return

    if not url:
        ch.warning("Usage: navig cloud direct <https://your.domain> | --clear")
        raise typer.Exit(code=2)

    u = url.strip().rstrip("/")
    parsed = urlparse(u)
    if parsed.scheme != "https" or not parsed.netloc:
        ch.warning(f"URL must be a full https://... URL with a hostname (got {u!r}).")
        raise typer.Exit(code=2)

    host = parsed.netloc.lower()
    if host.endswith(".trycloudflare.com"):
        ch.warning(
            "That looks like a cloudflared quick-tunnel URL -- you probably want "
            "cloudflared mode instead (run `navig cloud direct --clear` and let "
            "the daemon manage cloudflared)."
        )

    cfg.set("cloud.public_url", u, scope="global")
    cfg.set("cloud.enabled", True, scope="global")  # implied
    cfg.save(scope="global")

    ch.success(f"Direct mode set: {u}")
    ch.info("On gateway start the daemon will register this URL with the broker")
    ch.info("instead of spawning cloudflared.")
    ch.dim("")
    ch.dim("Restart the gateway to apply: `navig gateway start` (after stopping the running one).")
    ch.dim("Or set $NAVIG_PUBLIC_URL in your systemd unit to skip the config edit.")


@app.command("tailscale")
def cloud_tailscale(
    enable: bool = typer.Option(False, "--enable", help="Enable Tailscale Funnel for the daemon's port."),
    disable: bool = typer.Option(False, "--disable", help="Tear down Tailscale Funnel and clear cloud.public_url."),
    show_status: bool = typer.Option(False, "--status", help="Show current Tailscale Funnel state."),
) -> None:
    """Self-host the Mini App via Tailscale Funnel — stable HTTPS, no domain needed.

    Tailscale Funnel gives every user a free, stable HTTPS URL on their
    own ``*.ts.net`` subdomain with automatic TLS. The daemon registers
    THAT URL with the broker (direct mode), so cloudflared is never
    spawned and the broker only stores routing metadata.

    Prerequisites: install Tailscale, run ``tailscale up``, and enable
    Funnel in your tailnet admin console.
    """
    import asyncio as _aio

    import navig.cloud.tailscale as ts
    from navig import console_helper as ch

    cfg = _config()
    # Canonical resolver — reads nested gateway.port and falls back to the
    # gateway default (8789), NOT the daemon-IPC port. A flat
    # cfg.get("gateway.port", …) here does not resolve the dotted key.
    port = _gateway_port()

    if not (enable or disable or show_status):
        ch.warning("Usage: navig cloud tailscale [--enable | --disable | --status]")
        raise typer.Exit(code=2)

    if show_status:
        st = _aio.run(ts.status(port=port))
        ch.info("Tailscale Funnel status:")
        ch.info(f"  installed:      {st.installed}")
        ch.info(f"  logged_in:      {st.logged_in}")
        ch.info(f"  funnel_enabled: {st.funnel_enabled}")
        ch.info(f"  public_url:     {st.public_url or '<none>'}")
        ch.info(f"  forwarded_port: {st.forwarded_port or '<none>'}")
        if st.error:
            ch.warning(f"  error: {st.error}")
        return

    if disable:
        st = _aio.run(ts.disable(port=port))
        ts.clear_public_url()
        ch.success("Tailscale Funnel disabled and cloud.public_url cleared.")
        ch.dim("Restart the gateway -- it will fall back to cloudflared tunnel mode.")
        if st.error:
            ch.warning(st.error)
        return

    # enable path
    if not ts.tailscale_binary():
        ch.warning("Tailscale CLI not found on PATH.")
        ch.info(ts.install_hint())
        raise typer.Exit(code=2)

    ch.info(f"Enabling Tailscale Funnel for port {port}...")
    st = _aio.run(ts.enable(port=port))
    if not st.funnel_enabled or not st.public_url:
        ch.warning("Funnel did not come up cleanly.")
        if st.error:
            ch.warning(f"  reason: {st.error}")
        ch.dim("")
        ch.dim("Common fixes:")
        ch.dim("  1. Run `tailscale up` and sign in")
        ch.dim("  2. Enable HTTPS Certificates: https://login.tailscale.com/admin/dns")
        ch.dim("  3. Add 'funnel' node attribute in ACLs: https://login.tailscale.com/admin/acls")
        raise typer.Exit(code=1)

    ts.persist_public_url(st.public_url)
    ch.success(f"Tailscale Funnel online: {st.public_url}")
    ch.info(f"  forwarding -> http://127.0.0.1:{port}")
    ch.info(f"  cloud.public_url written to ~/.navig/config.yaml")
    ch.dim("")
    ch.dim("Next steps:")
    ch.dim(f"  1. `navig miniapp register --url {st.public_url}`")
    ch.dim("     (registers this URL as your bot's Mini App menu button)")
    ch.dim("  2. Restart the gateway: `navig gateway start`")


@app.command("status")
def cloud_status() -> None:
    """Show cloud + tunnel status. Works whether or not the daemon is running."""
    from navig import console_helper as ch
    cfg = _config()
    enabled = bool(cfg.get("cloud.enabled", False))
    broker_url = cfg.get("cloud.broker_url", _BROKER_DEFAULT)
    api_key = cfg.get("deck.api_key", "") or ""

    # Mirror the gateway's actual mode decision (server.py::_start_cloud_manager):
    # lighthouse_url (config OR NAVIG_LIGHTHOUSE_URL env) takes precedence over a
    # public_url, which takes precedence over the default cloudflared tunnel.
    # Without the lighthouse branch this reported "direct" while the daemon ran
    # "lighthouse".
    import os as _os
    public_url = (cfg.get("cloud.public_url") or "").strip()
    lighthouse_url = (
        cfg.get("cloud.lighthouse_url") or _os.environ.get("NAVIG_LIGHTHOUSE_URL") or ""
    ).strip()
    cloud_mode_cfg = (cfg.get("cloud.mode") or "").strip().lower()
    if lighthouse_url and cloud_mode_cfg in ("", "lighthouse"):
        mode = "lighthouse"
    elif public_url:
        mode = "direct"
    else:
        mode = "cloudflared"
    ch.info(f"Cloud:     {'enabled' if enabled else 'disabled'}  (mode: {mode})")
    ch.info(f"Broker:    {broker_url}")
    if mode == "lighthouse":
        ch.info(f"Edge:      {lighthouse_url}    (your lighthouse worker)")
    elif public_url:
        ch.info(f"Public:    {public_url}    (your reverse proxy)")
    ch.info(f"API key:   {_truncate(api_key)}    (use --reveal on `navig cloud key` to show)")

    info = _try_daemon_status()
    if info is None:
        ch.dim("Daemon not reachable on 127.0.0.1 -- start with `navig gateway start`.")
        return

    ch.info(f"Status:    {info.get('status')}")
    ch.info(f"Tunnel:    {info.get('tunnel_url') or '<none>'}")
    if info.get("mode"):
        ch.dim(f"Mode:      {info.get('mode')}  (from running daemon)")
    if info.get("last_heartbeat_at"):
        # Render the raw epoch as a readable clock + relative age instead of a
        # bare float like "1781937891.0556474".
        beat_raw = info.get("last_heartbeat_at")
        try:
            import time as _time
            from datetime import datetime as _dt
            beat = float(beat_raw)
            ago = max(0, int(_time.time() - beat))
            ch.info(f"Last beat: {_dt.fromtimestamp(beat):%H:%M:%S}  ({ago}s ago)")
        except (TypeError, ValueError, OSError):
            ch.info(f"Last beat: {beat_raw}")
    if info.get("last_error"):
        ch.warning(f"Last err:  {info.get('last_error')}")
    if info.get("rotations"):
        ch.dim(f"Rotations: {info.get('rotations')}")


@app.command("disconnect")
def cloud_disconnect() -> None:
    """Disable cloud routing. Daemon stops cloudflared and unregisters."""
    from navig import console_helper as ch
    cfg = _config()
    cfg.set("cloud.enabled", False, scope="global")
    cfg.save(scope="global")
    ch.success("Cloud disabled. The daemon will tear down cloudflared on the next tick.")
    if _try_daemon_status() is not None:
        ch.dim("If you want to force-stop now, restart the gateway: `navig gateway restart`.")


@app.command("key")
def cloud_key(
    reveal: bool = typer.Option(False, "--reveal", help="Print the full api_key (sensitive!)."),
    rotate: bool = typer.Option(False, "--rotate", help="Mint a fresh api_key and re-register."),
) -> None:
    """Show / rotate the api_key the broker maps to this daemon."""
    from navig import console_helper as ch
    cfg = _config()
    key = cfg.get("deck.api_key", "") or ""
    if rotate:
        confirm = typer.prompt(
            "Rotating revokes the current api_key and all bound Telegram users will need /start again. Type 'yes' to continue"
        )
        if confirm.strip().lower() != "yes":
            ch.warning("Cancelled.")
            return
        key = "navig_" + secrets.token_urlsafe(32)
        cfg.set("deck.api_key", key, scope="global")
        cfg.save(scope="global")
        ch.success(f"New api_key: {key}")
        ch.dim("Restart the gateway so the new key is registered with the broker.")
        return

    if reveal:
        ch.warning(f"api_key (FULL, sensitive): {key or '<unset>'}")
    else:
        ch.info(f"api_key: {_truncate(key)}    (use --reveal to print in full)")
