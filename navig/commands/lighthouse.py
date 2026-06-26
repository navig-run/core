"""``navig lighthouse`` -- deploy / status / url / redeploy / disable.

Self-host the always-on edge on the user's OWN Cloudflare account: no Node,
no wrangler, no tunnel, no custom domain. One command uploads a prebuilt Worker
(see :mod:`navig.cloud.lighthouse_deploy`) via the Cloudflare REST API, flips
``cloud.mode=lighthouse`` in the config, and points the Telegram webhook at the
new ``*.workers.dev`` URL. The brain then dials OUT to it (no inbound port).

The deploy logic lives in :func:`run_lighthouse_deploy` so the first-run
onboarding wizard can reuse exactly the same path.
"""

from __future__ import annotations

import logging
import os
import secrets as _secrets

import typer

app = typer.Typer(
    help="NAVIG Lighthouse — your own always-on Cloudflare edge (no tunnel).",
    no_args_is_help=True,
)
logger = logging.getLogger(__name__)

_WORKER_NAME = "navig-lighthouse"
_TOKENS_URL = "https://dash.cloudflare.com/profile/api-tokens"


def _restart_hint() -> str:
    """The correct 'bring the uplink online' command for THIS install.

    A daemon run as a service (the default install) must be restarted with
    ``navig service restart``. Telling such a user to run ``navig gateway start``
    would spawn a *second*, port-conflicting gateway (the classic double-daemon
    trap). Only a foreground/dev daemon is (re)started with ``navig gateway
    start``. Detect a running managed daemon and pick the right command.
    """
    try:
        from navig.daemon.supervisor import NavigDaemon  # noqa: PLC0415

        if NavigDaemon.is_running():
            return "navig service restart"
    except Exception:  # noqa: BLE001
        pass
    return "navig gateway start"


def _print_token_help() -> None:
    """Explain — in plain steps — how to mint the Cloudflare token Lighthouse needs."""
    from navig import console_helper as ch

    ch.info("Lighthouse runs on YOUR Cloudflare account (free plan is fine).")
    ch.dim("It needs one API token — stored in your vault, only ever sent to api.cloudflare.com.")
    ch.dim("Create it in ~30 seconds:")
    ch.dim(f"  1. Open  {_TOKENS_URL}")
    ch.dim("  2. Click  Create Token  →  use the “Edit Cloudflare Workers” template (easiest),")
    ch.dim("     OR  Create Custom Token  with these two Account permissions:")
    ch.dim("         • Workers Scripts   → Edit")
    ch.dim("         • Account Settings  → Read")
    ch.dim("  3. Account Resources: include your account → Continue → Create → copy the token.")
    ch.dim("  (A *.workers.dev subdomain is auto-registered on first deploy if you don't have one.)")


def _prompt_for_token() -> str:
    """Interactively capture the token: open the token page + read a hidden paste.

    Returns "" in a non-interactive context (CI / no TTY) so callers fall back to
    the explicit-flag path instead of hanging.
    """
    import sys
    import webbrowser

    from navig import console_helper as ch

    if not sys.stdin.isatty():
        return ""
    _print_token_help()
    try:
        if webbrowser.open(_TOKENS_URL):
            ch.dim("(opened the Cloudflare token page in your browser)")
    except Exception:  # noqa: BLE001 — headless / no browser
        pass
    return (
        typer.prompt("Paste your Cloudflare API token", hide_input=True, default="", show_default=False)
        or ""
    ).strip()


def _config():
    from navig.core import Config  # local import keeps `navig --help` fast
    return Config()


def resolve_cf_token(explicit: str = "") -> str:
    """CF token resolution order: explicit → $CLOUDFLARE_API_TOKEN → vault.

    Pure read — persistence into the vault happens once in
    :func:`run_lighthouse_deploy` (the single deploy path), so a token sourced
    from a flag or the environment still ends up vaulted after the first deploy.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if env:
        return env
    try:
        from navig.vault import get_vault
        cred = get_vault().get("cloudflare", caller="lighthouse.deploy")
        if cred is not None:
            def _field(name: str) -> str:
                try:
                    return str(cred.get_secret(name) or "").strip()
                except Exception:  # noqa: BLE001
                    return ""

            tok = _field("token") or _field("api_key")
            # OAuth credentials auto-refresh when expired so redeploys stay unattended.
            if _field("auth") == "oauth":
                from navig.cloud import cf_oauth

                refresh_tok = _field("refresh_token")
                if refresh_tok and cf_oauth.is_expired(_field("expires_at")):
                    try:
                        bundle = cf_oauth.refresh(refresh_tok)
                        persist_cf_oauth(bundle)
                        return bundle.access_token
                    except cf_oauth.OAuthError as exc:  # noqa: BLE001
                        logger.debug("cloudflare oauth refresh failed: %s", exc)
            if tok:
                return tok
    except Exception:  # noqa: BLE001
        pass
    return ""


def resolve_cf_api_token() -> str:
    """Return the stored Cloudflare credential **only if it's a real API token**.

    wrangler validates ``CLOUDFLARE_API_TOKEN`` via an API-token-only endpoint
    and **rejects OAuth access tokens** (from ``navig lighthouse login``) — feeding
    it one breaks the deploy *and* shadows wrangler's own login. So for tooling that
    shells out to wrangler (the deck deploy), return "" when the vaulted credential
    is OAuth, letting wrangler use its own auth instead.
    """
    env = (os.environ.get("CLOUDFLARE_API_TOKEN") or "").strip()
    if env:
        return env
    try:
        from navig.vault import get_vault

        cred = get_vault().get("cloudflare", caller="miniapp.deploy")
        if cred is None:
            return ""

        def _field(name: str) -> str:
            try:
                return str(cred.get_secret(name) or "").strip()
            except Exception:  # noqa: BLE001
                return ""

        if _field("auth") == "oauth":
            return ""  # wrangler can't use OAuth tokens
        return _field("token") or _field("api_key")
    except Exception:  # noqa: BLE001
        return ""


def persist_cf_token(token: str) -> bool:
    """Store the Cloudflare API token in the vault (best-effort).

    Single source of truth for "the token must live in the vault" — called from
    token resolution and from the deploy path, so the CLI and the onboarding
    wizard both end up with the credential vaulted. Returns True if stored.
    """
    token = (token or "").strip()
    if not token:
        return False
    return _vault_put_cloudflare({"token": token})


def persist_cf_oauth(bundle) -> bool:
    """Store a Cloudflare OAuth :class:`TokenBundle` (access + refresh + expiry)."""
    return _vault_put_cloudflare(bundle.as_vault_data())


def _vault_put_cloudflare(data: dict) -> bool:
    """Upsert the ``cloudflare`` vault credential (update if present, else add)."""
    try:
        from navig.vault import get_vault

        vault = get_vault()
        if vault is None:
            return False
        existing = vault.get("cloudflare", caller="lighthouse.persist")
        if existing is not None:
            vault.update(existing.id, data=data)
        else:
            vault.add(
                provider="cloudflare",
                credential_type="token",
                data=data,
                profile_id="default",
                label="Cloudflare API Token",
            )
        return True
    except Exception:  # noqa: BLE001 — vault store is best-effort
        logger.debug("cloudflare token vault store failed", exc_info=True)
        return False


def _ensure_api_key(cfg) -> str:
    key = (cfg.get("deck.api_key") or "").strip()
    if not key:
        key = "navig_" + _secrets.token_urlsafe(32)
        cfg.set("deck.api_key", key, scope="global")
        cfg.save(scope="global")
    return key


def configure_telegram_webhook(lighthouse_url: str) -> str | None:
    """Point the bot's webhook at ``<lighthouse>/tg/<hash>`` and persist config.

    Writes ``telegram.webhook_url`` + ``telegram.webhook_secret`` so the brain's
    Telegram channel validates uplink-delivered updates against the same secret.
    Returns the webhook URL, or ``None`` if no bot token is configured yet.
    """
    from navig.cloud import api_key_hash
    from navig.messaging.secrets import resolve_telegram_bot_token

    bot_token = (resolve_telegram_bot_token() or "").strip()
    if not bot_token:
        return None

    cfg = _config()
    api_key = _ensure_api_key(cfg)
    secret = (cfg.get("telegram.webhook_secret") or "").strip() or _secrets.token_hex(24)
    hook_url = f"{lighthouse_url.rstrip('/')}/tg/{api_key_hash(api_key)}"

    cfg.set("telegram.webhook_url", hook_url, scope="global")
    cfg.set("telegram.webhook_secret", secret, scope="global")
    cfg.save(scope="global")

    import requests
    resp = requests.post(
        f"https://api.telegram.org/bot{bot_token}/setWebhook",
        json={
            "url": hook_url,
            "secret_token": secret,
            "allowed_updates": ["message", "callback_query", "message_reaction", "inline_query"],
        },
        timeout=20,
    )
    data = resp.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description", f"setWebhook HTTP {resp.status_code}"))
    return hook_url


def run_lighthouse_deploy(
    *,
    token: str,
    account_id: str | None = None,
    worker_name: str = _WORKER_NAME,
    set_webhook: bool = True,
    fresh: bool = False,
    persist_token: bool = True,
):
    """Deploy the Worker, persist lighthouse config, and (optionally) set the
    Telegram webhook. Shared by the CLI and the onboarding wizard.

    ``persist_token=False`` skips vaulting the token — used by the OAuth ``login``
    path, which already stored the richer access+refresh bundle.

    Returns ``(DeployResult, webhook_url_or_None, webhook_error_or_None)``.
    """
    from navig.cloud import lighthouse_deploy as ld

    # The token must live in the vault — persist before the network call so a
    # later `redeploy` / restart runs unattended even if this deploy fails.
    if persist_token:
        persist_cf_token(token)

    result = ld.deploy(
        token=token, account_id=account_id, worker_name=worker_name, fresh=fresh
    )

    cfg = _config()
    cfg.set("cloud.enabled", True, scope="global")
    cfg.set("cloud.mode", "lighthouse", scope="global")
    cfg.set("cloud.lighthouse_url", result.url, scope="global")
    cfg.set("cloud.lighthouse_account_id", result.account_id, scope="global")
    cfg.set("cloud.lighthouse_worker", worker_name, scope="global")
    cfg.save(scope="global")

    # First-party producer: announce the deploy (deck feed always; Telegram when
    # the daemon is up). Best-effort — never let it fail the deploy.
    try:
        from navig.notify.producers.events import report_deploy_sync

        report_deploy_sync("Lighthouse edge", note=f"Live at {result.url}")
    except Exception:  # noqa: BLE001
        logger.debug("lighthouse deploy notify skipped", exc_info=True)

    webhook_url = None
    webhook_err = None
    if set_webhook:
        try:
            webhook_url = configure_telegram_webhook(result.url)
        except Exception as exc:  # noqa: BLE001
            webhook_err = str(exc)
    return result, webhook_url, webhook_err


# ── Subcommands ───────────────────────────────────────────────────────────────

@app.command("login")
def lighthouse_login(
    no_deploy: bool = typer.Option(False, "--no-deploy", help="Only authenticate; don't deploy yet."),
    fresh: bool = typer.Option(False, "--fresh", help="Force the Durable Object migration."),
) -> None:
    """One-click: authorize via your browser (no API token to create), then deploy."""
    from navig import console_helper as ch
    from navig.core import narrator
    from navig.cloud import cf_oauth
    from navig.cloud.lighthouse_deploy import DeployError

    narrator.blank()
    narrator.phase("Connecting your Cloudflare account", icon="brain")
    ch.info("A Cloudflare authorization page is opening — click Authorize, then return here.")
    try:
        bundle = cf_oauth.login()
    except cf_oauth.OAuthError as exc:
        ch.error(f"Cloudflare login failed: {exc}")
        ch.dim("You can use a scoped token instead: `navig lighthouse deploy --token …`.")
        raise typer.Exit(1)

    persist_cf_oauth(bundle)
    ch.success("Cloudflare connected — OAuth token (with refresh) stored in your vault.")

    if no_deploy:
        ch.dim("Run `navig lighthouse deploy` when you're ready.")
        return

    narrator.step("uploading the edge Worker (no Node/wrangler needed)…", icon="radio")
    try:
        # login() already vaulted the richer access+refresh bundle → don't overwrite it.
        result, webhook, webhook_err = run_lighthouse_deploy(
            token=bundle.access_token, set_webhook=True, fresh=fresh, persist_token=False
        )
    except DeployError as exc:
        ch.error(f"Deploy failed: {exc}")
        raise typer.Exit(1)

    ch.success(f"Lighthouse {'deployed' if result.created else 'updated'}: {result.url}")
    if webhook:
        ch.info(f"  Telegram: webhook → {webhook}")
    elif webhook_err:
        ch.warning(f"  Telegram: setWebhook failed: {webhook_err}")
    ch.dim(f"Restart the brain to bring the uplink online: `{_restart_hint()}`.")


@app.command("deploy")
def lighthouse_deploy_cmd(
    token: str = typer.Option(
        "", "--token", help="Cloudflare API token (else $CLOUDFLARE_API_TOKEN or vault 'cloudflare')."
    ),
    account_id: str = typer.Option(
        "", "--account-id", help="Cloudflare account id (only needed if the token has >1 account)."
    ),
    name: str = typer.Option(_WORKER_NAME, "--name", help="Worker name."),
    no_webhook: bool = typer.Option(False, "--no-webhook", help="Skip Telegram setWebhook."),
    fresh: bool = typer.Option(
        False, "--fresh", help="Force the Durable Object migration (first-time schema)."
    ),
) -> None:
    """Deploy Lighthouse to your own Cloudflare account — no Node, no tunnel, no domain."""
    from navig import console_helper as ch
    from navig.core import narrator
    from navig.cloud.lighthouse_deploy import DeployError

    tok = resolve_cf_token(token)
    if not tok:
        # Interactive: guide the user through token creation and capture the paste.
        tok = _prompt_for_token()
    if not tok:
        ch.warning(
            "No Cloudflare API token provided. Pass --token, set $CLOUDFLARE_API_TOKEN, "
            "or run `navig vault add cloudflare`, then re-run `navig lighthouse deploy`."
        )
        _print_token_help()
        raise typer.Exit(2)

    narrator.blank()
    narrator.phase("Deploying Lighthouse to your Cloudflare", icon="brain")
    narrator.step("uploading the edge Worker (no Node/wrangler needed)…", icon="radio")
    try:
        result, webhook, webhook_err = run_lighthouse_deploy(
            token=tok,
            account_id=account_id or None,
            worker_name=name,
            set_webhook=not no_webhook,
            fresh=fresh,
        )
    except DeployError as exc:
        ch.error(f"Deploy failed: {exc}")
        raise typer.Exit(1)

    ch.success(f"Lighthouse {'deployed' if result.created else 'updated'}: {result.url}")
    ch.info(f"  Account:  {result.account_id}")
    ch.info("  Mode:     cloud.mode=lighthouse  (outbound uplink, no tunnel)")
    ch.info("  Token:    saved to vault (provider 'cloudflare') — redeploys run unattended")
    if webhook:
        ch.info(f"  Telegram: webhook → {webhook}")
    elif webhook_err:
        ch.warning(f"  Telegram: setWebhook failed: {webhook_err}")
    elif not no_webhook:
        ch.dim("  Telegram: no bot token yet — add one and re-run, or set later.")
    ch.dim("")
    ch.dim(f"Restart the brain to bring the uplink online: `{_restart_hint()}`.")


@app.command("redeploy")
def lighthouse_redeploy(
    token: str = typer.Option("", "--token", help="Cloudflare API token (else env/vault)."),
    fresh: bool = typer.Option(False, "--fresh", help="Force the Durable Object migration."),
) -> None:
    """Re-upload the Worker to the same account (idempotent; reuses saved config)."""
    from navig import console_helper as ch
    from navig.cloud.lighthouse_deploy import DeployError

    tok = resolve_cf_token(token)
    if not tok:
        ch.warning("No Cloudflare API token (see `navig lighthouse deploy --help`).")
        raise typer.Exit(2)
    cfg = _config()
    account_id = (cfg.get("cloud.lighthouse_account_id") or "").strip() or None
    name = (cfg.get("cloud.lighthouse_worker") or _WORKER_NAME).strip()
    try:
        result, webhook, webhook_err = run_lighthouse_deploy(
            token=tok, account_id=account_id, worker_name=name, fresh=fresh
        )
    except DeployError as exc:
        ch.error(f"Redeploy failed: {exc}")
        raise typer.Exit(1)
    ch.success(f"Lighthouse redeployed: {result.url}")
    if webhook_err:
        ch.warning(f"Telegram setWebhook failed: {webhook_err}")


@app.command("url")
def lighthouse_url() -> None:
    """Print the one stable edge URL + every inbound hook (and where to set each)."""
    from navig import console_helper as ch
    cfg = _config()
    url = (cfg.get("cloud.lighthouse_url") or "").strip()
    if not url:
        ch.warning("Lighthouse not deployed yet. Run `navig lighthouse login` (or `deploy`).")
        raise typer.Exit(1)

    ch.info("Your one stable edge URL — set this everywhere (it never changes across redeploys):")
    ch.success(f"  {url}")
    ch.info("Inbound hooks (all derived from that single URL):")

    tg = (cfg.get("telegram.webhook_url") or "").strip()
    if tg:
        ch.info(f"  • Telegram   {tg}")
        ch.dim("      set automatically (Telegram setWebhook) by deploy/login — nothing to do.")
    else:
        ch.dim("  • Telegram   not set — add a bot token, then re-run `navig lighthouse deploy`.")

    api_key = (cfg.get("deck.api_key") or "").strip()
    if api_key:
        try:
            from navig.cloud import api_key_hash

            ch.info(f"  • SMS        {url.rstrip('/')}/sms/{api_key_hash(api_key)}")
            ch.dim("      paste into Twilio → your number → Messaging → 'A message comes in' (HTTP POST).")
        except Exception:  # noqa: BLE001
            pass

    ch.info(f"  • Deck       {url}")
    ch.dim("      point the deck here for remote access (deck Settings → server / Lighthouse URL).")
    ch.dim("")
    ch.dim("Want a prettier single domain? Add a Cloudflare Custom Domain to the 'navig-lighthouse'")
    ch.dim("Worker (dash → Workers & Pages → navig-lighthouse → Settings → Domains & Routes), then")
    ch.dim("`navig config set cloud.lighthouse_url https://<your-domain>` and `navig lighthouse redeploy`.")


@app.command("status")
def lighthouse_status() -> None:
    """Show Lighthouse config + live uplink state (if the daemon is running)."""
    import json
    import urllib.error
    import urllib.request

    from navig import console_helper as ch
    cfg = _config()
    url = (cfg.get("cloud.lighthouse_url") or "").strip()
    mode = (cfg.get("cloud.mode") or "").strip()
    ch.info(f"Configured: {'yes' if url else 'no'}  (cloud.mode={mode or '<unset>'})")
    if url:
        ch.info(f"Edge:       {url}")
        ch.info(f"Account:    {cfg.get('cloud.lighthouse_account_id') or '<unknown>'}")

    from navig._daemon_defaults import _GATEWAY_PORT

    # NB: cfg.get("gateway.port") does not resolve the dotted/nested key, so this
    # currently always uses the default. Tracked separately; for a custom port use
    # gateway_cli_defaults() (see commands/cloud.py). Default centralized regardless.
    port = int(cfg.get("gateway.port", _GATEWAY_PORT))
    api_key = cfg.get("deck.api_key", "") or ""
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/deck/cloud/status",
        headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        # A miss right after a restart usually just means the gateway is still
        # booting (it loads channels + events + dials the uplink), not that it's
        # down — so say so, and suggest the command that fits THIS install.
        ch.dim(
            "Daemon not answering on 127.0.0.1 yet — if you just restarted, give it "
            f"a few seconds and re-run this. Otherwise (re)start it: `{_restart_hint()}`."
        )
        return

    ch.info(f"Status:     {info.get('status')}")
    lh = info.get("lighthouse") or {}
    if lh:
        ch.info(f"Uplink:     {lh.get('status')}  (reconnects={lh.get('reconnects', 0)}, "
                f"served={lh.get('requests_served', 0)})")
    if info.get("last_error"):
        ch.warning(f"Last err:   {info.get('last_error')}")


@app.command("disable")
def lighthouse_disable(
    delete: bool = typer.Option(False, "--delete", help="Also delete the Worker from Cloudflare."),
    token: str = typer.Option("", "--token", help="Cloudflare API token (only needed with --delete)."),
) -> None:
    """Turn off Lighthouse mode (reverts to tunnel/direct on the next gateway start)."""
    from navig import console_helper as ch
    cfg = _config()
    name = (cfg.get("cloud.lighthouse_worker") or _WORKER_NAME).strip()
    account_id = (cfg.get("cloud.lighthouse_account_id") or "").strip() or None

    cfg.set("cloud.mode", "", scope="global")
    cfg.set("cloud.lighthouse_url", "", scope="global")
    cfg.save(scope="global")
    ch.success("Lighthouse mode disabled. The daemon reverts to tunnel/direct on next start.")

    if delete:
        tok = resolve_cf_token(token)
        if not tok:
            ch.warning("No Cloudflare token — skipped Worker deletion.")
            return
        from navig.cloud.lighthouse_deploy import DeployError, delete_worker
        try:
            existed = delete_worker(tok, account_id, name)
            ch.success("Worker deleted from Cloudflare." if existed else "Worker already gone.")
        except DeployError as exc:
            ch.error(f"Delete failed: {exc}")
