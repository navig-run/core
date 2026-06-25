"""
``navig miniapp register`` — auto-set the bot's Mini App menu button via
the Telegram Bot API.

This is the "magic UX moment" in the Phase 3 plan: instead of telling
users to paste a URL into ``@BotFather``, we use the bot token already
configured in their vault to call ``setChatMenuButton`` on the bot's own
behalf. The user runs three commands and their bot has a working Mini
App:

    navig gateway start              # daemon up
    navig cloud tailscale --enable   # stable HTTPS via Funnel
    navig miniapp register           # menu button updated automatically

The URL defaults to ``cloud.public_url`` (set by ``tailscale``/`direct`)
or the cloudflared tunnel URL if neither is configured. ``--url``
overrides explicitly.
"""

from __future__ import annotations

import asyncio as _aio
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

import urllib.error
import urllib.request

import typer

logger = logging.getLogger(__name__)

app = typer.Typer(help="Telegram Mini App menu button management.", no_args_is_help=True)


_TG_API = "https://api.telegram.org"


def _ch():
    from navig import console_helper
    return console_helper


def _bot_token() -> str:
    """Resolve the configured Telegram bot token from vault/config/env."""
    try:
        from navig.messaging.secrets import resolve_telegram_bot_token
        from navig.config import get_config_manager
        cfg = get_config_manager().global_config or {}
        token = resolve_telegram_bot_token(cfg) or ""
        return token.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("token resolve failed: %r", exc)
        return ""


def _connect_url(base_url: str) -> str:
    """The Mini App entry URL — a ``/connect?key=<deck.api_key>`` link.

    The Telegram Mini App must authenticate to the Lighthouse edge, which routes
    by ``sha256(api_key)``; a bare deck URL carries no key, so the deck can't
    reach the brain and renders blank. The ``/connect`` page seeds the key into
    localStorage, then the deck works. Falls back to the bare URL if no key.

    SECURITY: this embeds the deck api_key in the bot's (global) menu button —
    appropriate for a personal/single-owner bot. The api_key bypasses the
    allow-list, so for a SHARED bot, reset the key and prefer a per-user flow.
    """
    base = (base_url or "").rstrip("/")
    try:
        from navig.config import get_config_manager
        gc = get_config_manager().global_config or {}
        key = str((gc.get("deck", {}) or {}).get("api_key", "") or "").strip()
    except Exception:  # noqa: BLE001
        key = ""
    if base and key:
        from urllib.parse import quote
        return f"{base}/connect?key={quote(key, safe='')}"
    return base


def _resolve_public_url(explicit: str = "") -> str:
    """Best-effort: explicit > config.cloud.public_url > running daemon's tunnel."""
    if explicit:
        return explicit.strip().rstrip("/")
    from navig.core import Config
    cfg = Config()
    cfg_url = (cfg.get("cloud.public_url") or "").strip()
    if cfg_url:
        return cfg_url.rstrip("/")
    # Last resort: ask the running daemon for its current tunnel URL
    try:
        # Canonical resolver — reads nested gateway.port + falls back to the
        # gateway default (8789, not the daemon-IPC 8765).
        from navig.gateway_client import gateway_cli_defaults

        port = gateway_cli_defaults()[0]
        api_key = cfg.get("deck.api_key", "") or ""
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/api/deck/cloud/status",
            headers={"Authorization": f"Bearer {api_key}"} if api_key else {},
        )
        with urllib.request.urlopen(req, timeout=2) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        url = (data.get("tunnel_url") or "").strip()
        if url:
            return url.rstrip("/")
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        pass
    return ""


def _tg_call(token: str, method: str, body: dict | None = None, *, timeout: float = 10.0) -> dict:
    """POST JSON to a Telegram Bot API method. Raises on transport error;
    callers should check the ``ok`` field on the response."""
    if not token:
        return {"ok": False, "description": "no_bot_token"}
    url = f"{_TG_API}/bot{token}/{method}"
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return {"ok": False, "description": f"HTTP {exc.code}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"ok": False, "description": str(exc)}


# ── Deck deploy helpers ───────────────────────────────────────────────────────


def _find_deck_dir(explicit: str = "") -> Path | None:
    """Locate the navig-deck *source* package: --dir → $NAVIG_DECK_DIR → search up
    from cwd → sibling of the navig-core repo. Must contain a package.json.

    This is the *developer* path — it requires the (private) navig-deck source and
    Node to run ``npm run cf:build``. End users instead get the prebuilt static
    bundle via :func:`_find_prebuilt_deck_out` (see there)."""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser())
    env = os.environ.get("NAVIG_DECK_DIR")
    if env:
        candidates.append(Path(env).expanduser())
    # Walk up from cwd looking for a sibling navig-deck.
    cur = Path.cwd()
    for parent in [cur, *cur.parents]:
        candidates.append(parent / "navig-deck")
    # Relative to this installed package (…/navig-core/navig/commands/miniapp.py).
    here = Path(__file__).resolve()
    for up in (4, 5):
        if len(here.parents) > up:
            candidates.append(here.parents[up] / "navig-deck")
    for c in candidates:
        try:
            if (c / "package.json").is_file():
                return c.resolve()
        except OSError:
            continue
    return None


def _find_prebuilt_deck_out(explicit: str = "") -> Path | None:
    """Locate a *prebuilt* deck static bundle (a directory containing index.html) —
    no Node, no source required. This is the primary path for end users.

    Resolution order (mirrors the gateway's
    ``navig.gateway.deck.routes.static_assets._find_deck_static_dir`` so the
    deployed deck matches the locally-served one):
      1. Explicit ``--dir`` / $NAVIG_DECK_DIR pointing straight at a built bundle.
      2. Installed ``navig-deck`` wheel — ``navig_deck.static_dir()``
         (``pip install navig`` pulls it; compiled ``out/`` ships as package data).
      3. Dev-tree neighbours (monorepo): ``navig-deck/out``, ``navig-deck/dist``,
         the wheel-builder staging dir, and ``navig-core/deck-static``.
    """
    def _ok(p: Path) -> bool:
        try:
            return p.is_dir() and (p / "index.html").is_file()
        except OSError:
            return False

    # 1. Explicit override may itself be a built bundle (or a source dir whose out/).
    for raw in (explicit, os.environ.get("NAVIG_DECK_DIR", "")):
        if not raw:
            continue
        base = Path(raw).expanduser()
        for cand in (base, base / "out", base / "dist"):
            if _ok(cand):
                return cand.resolve()

    # 2. Installed wheel — the canonical end-user distribution path.
    try:
        import navig_deck  # type: ignore[import-not-found]

        installed = navig_deck.static_dir()
        if _ok(installed):
            return installed.resolve()
    except ImportError:
        pass  # navig-deck wheel not installed; fall through to dev tree
    except Exception as exc:  # noqa: BLE001
        logger.debug("navig_deck.static_dir() raised %r", exc)

    # 3. Dev-tree neighbours (monorepo development without the installed wheel).
    here = Path(__file__).resolve()
    repo_roots = {here.parents[up] for up in (4, 5) if len(here.parents) > up}
    repo_roots.add(Path.cwd())
    repo_roots.update(Path.cwd().parents)
    for root in repo_roots:
        for rel in (
            "navig-deck/out",
            "navig-deck/dist",
            "navig-deck/python/navig_deck/static",
            "navig-core/deck-static",
            "deck-static",
        ):
            cand = root / rel
            if _ok(cand):
                return cand.resolve()
    return None


# Sentinel the navig-deck *wheel* build can embed for its lighthouse URL
# (``NEXT_PUBLIC_LIGHTHOUSE_URL=__NAVIG_LIGHTHOUSE_URL__``) so a single prebuilt
# bundle can be re-pointed at each user's edge at deploy time. See
# _bake_lighthouse_into_prebuilt.
_LIGHTHOUSE_SENTINEL = "__NAVIG_LIGHTHOUSE_URL__"


def _bake_lighthouse_into_prebuilt(out_dir: Path, lighthouse_url: str, *, log=None) -> Path:
    """Inject the user's lighthouse URL into a prebuilt deck bundle.

    A prebuilt wheel bundle is built once, without any one user's edge URL. If that
    build embedded the sentinel ``__NAVIG_LIGHTHOUSE_URL__``, replace it here with
    the real URL in a writable temp copy and return that copy's path.

    If no sentinel is present (older wheels), this is a no-op: the original dir is
    returned and the deck falls back to its runtime Settings override
    (``localStorage`` ``navig_lighthouse_url``). Never raises.
    """
    import tempfile

    def _emit(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:  # noqa: BLE001
                pass

    text_suffixes = {".js", ".mjs", ".cjs", ".html", ".json", ".txt", ".css"}
    needle = _LIGHTHOUSE_SENTINEL.encode()
    try:
        hits = [
            p for p in out_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in text_suffixes and needle in p.read_bytes()
        ]
    except OSError:
        return out_dir
    if not hits:
        return out_dir  # no sentinel — deck uses its runtime Settings override

    try:
        staging = Path(tempfile.mkdtemp(prefix="navig-deck-deploy-"))
        dest = staging / out_dir.name
        shutil.copytree(out_dir, dest)
        repl = lighthouse_url.encode()
        for p in hits:
            tp = dest / p.relative_to(out_dir)
            tp.write_bytes(tp.read_bytes().replace(needle, repl))
        _emit(f"Baked your edge URL into {len(hits)} prebuilt file(s): {lighthouse_url}")
        return dest
    except Exception as exc:  # noqa: BLE001
        _emit(f"Could not bake edge URL into the prebuilt bundle ({exc}); deploying as-is.")
        return out_dir


def _parse_pages_url(output: str, project: str) -> str:
    """Pull the deployed Pages URL from wrangler output; prefer the stable
    production alias ``https://<project>.pages.dev`` over the per-deploy hash."""
    urls = re.findall(r"https://[a-z0-9.\-]+\.pages\.dev", output, re.IGNORECASE)
    stable = f"https://{project}.pages.dev"
    if any(u.rstrip("/") == stable for u in urls):
        return stable
    # Otherwise return the longest match (the deployment URL) or the stable guess.
    return (max(urls, key=len) if urls else stable).rstrip("/")


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    # Force UTF-8 decoding: wrangler/next emit UTF-8 (emoji, box chars), but on a
    # non-UTF-8 Windows locale (e.g. cp1251) the default text decoder crashes the
    # reader threads, leaving stdout/stderr None. errors="replace" keeps output readable.
    return subprocess.run(
        cmd, cwd=str(cwd), env=env, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )


# ── Commands ────────────────────────────────────────────────────────────────


def run_miniapp_deploy(
    *,
    deck_dir: str = "",
    project: str = "navig-deck",
    lighthouse_url: str = "",
    skip_build: bool = False,
    register: bool = True,
    via_wrangler: bool = False,
    log=None,
) -> dict:
    """Build the Deck and deploy it to the user's own Cloudflare.

    Default (pure-Python): uploads the static ``out/`` to **Workers Static Assets**
    via the Cloudflare REST API, reusing the same credential as Lighthouse — no
    wrangler, no separate login, no Pages scope. ``via_wrangler=True`` uses the
    legacy ``wrangler pages deploy`` path (Cloudflare Pages) instead.

    Reusable core shared by the ``deploy`` CLI command and the onboarding wizard.
    Never raises / never prints — returns a structured result:
        {ok, status, url?, error?, lighthouse_url?, registered, register_error?, deploy_output?}
    ``status`` ∈ {deployed, no_deck, no_node, no_cf_token, build_failed, deploy_failed}.
    Pass ``log`` (callable[str]) for progress messages.
    """
    def _log(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:  # noqa: BLE001
                pass

    from navig.core import Config

    cfg = Config()

    deck = _find_deck_dir(deck_dir)   # navig-deck *source* (developer path) or None

    lh = (lighthouse_url or cfg.get("cloud.lighthouse_url") or "").strip().rstrip("/")

    npm = shutil.which("npm")
    build_env = {**os.environ}
    if lh:
        build_env["NEXT_PUBLIC_LIGHTHOUSE_URL"] = lh

    # ── Resolve the static bundle to upload ───────────────────────────────
    # Developer path: build a fresh static export from the navig-deck source.
    # This is the ONLY path that bakes the lighthouse URL via the build env.
    out_dir: Path | None = None
    used_prebuilt = False
    if deck is not None and npm and not skip_build:
        _log(f"Building the deck (static export) in {deck} …")
        proc = _run([npm, "run", "cf:build"], deck, build_env)
        if proc.returncode != 0:
            return {"ok": False, "status": "build_failed", "error": (proc.stderr or proc.stdout or "")[-1500:]}
        cand = deck / "out"
        if cand.is_dir():
            out_dir = cand
    elif deck is not None and skip_build and (deck / "out").is_dir():
        out_dir = deck / "out"

    # End-user path (and fallback when the source can't be built): upload the
    # prebuilt bundle shipped with the navig-deck wheel — no Node, no source.
    if out_dir is None:
        pre = _find_prebuilt_deck_out(deck_dir)
        if pre is not None:
            out_dir = pre
            used_prebuilt = True
            _log(f"Using prebuilt deck bundle (no build needed): {out_dir}")

    if out_dir is None or not out_dir.is_dir():
        if deck is not None and not npm:
            return {
                "ok": False, "status": "no_node",
                "error": "Node.js (npm) is needed to build navig-deck from source, and no "
                         "prebuilt deck bundle was found. Install Node 18+, or `pip install navig-deck`.",
            }
        return {
            "ok": False, "status": "no_deck",
            "error": "no deck to deploy — found neither navig-deck source + a built out/, "
                     "nor the prebuilt navig_deck wheel bundle.",
        }

    # Bake the lighthouse URL into a prebuilt bundle (built without it) when the
    # wheel ships a replaceable sentinel. Harmless no-op otherwise; the deck also
    # honours a runtime override set in its Settings (localStorage).
    if used_prebuilt and lh:
        out_dir = _bake_lighthouse_into_prebuilt(out_dir, lh, log=_log)

    # ── Deploy ────────────────────────────────────────────────────────────
    if via_wrangler:
        url = None
        npx = shutil.which("npx")
        if not npx:
            return {"ok": False, "status": "no_node", "error": "npx (wrangler) not found"}
        try:
            from navig.commands.lighthouse import resolve_cf_api_token

            tok = resolve_cf_api_token()  # API token only — wrangler rejects OAuth
            if tok and "CLOUDFLARE_API_TOKEN" not in build_env:
                build_env["CLOUDFLARE_API_TOKEN"] = tok
            acct = (cfg.get("cloud.lighthouse_account_id") or "").strip()
            if acct and "CLOUDFLARE_ACCOUNT_ID" not in build_env:
                build_env["CLOUDFLARE_ACCOUNT_ID"] = acct
        except Exception:  # noqa: BLE001
            pass
        # cwd = the bundle's parent, target = its dir name — works for both a
        # source build (deck/out) and a prebuilt bundle (…/navig_deck/static).
        cwd, target = out_dir.parent, out_dir.name
        _log(f"Deploying to Cloudflare Pages via wrangler (project '{project}') …")
        _run([npx, "wrangler", "pages", "project", "create", project, "--production-branch", "main"], cwd, build_env)
        dep = _run(
            [npx, "wrangler", "pages", "deploy", target, "--project-name", project, "--branch", "main"],
            cwd, build_env,
        )
        combined = f"{dep.stdout or ''}\n{dep.stderr or ''}".strip()
        if dep.returncode != 0:
            return {"ok": False, "status": "deploy_failed", "error": combined[-1800:], "deploy_output": combined}
        url = _parse_pages_url(combined, project)
    else:
        # Pure-Python: upload to Workers Static Assets, reusing the Lighthouse credential.
        from navig.commands.lighthouse import resolve_cf_token

        tok = resolve_cf_token()
        if not tok:
            return {
                "ok": False, "status": "no_cf_token",
                "error": "no Cloudflare credential — run `navig lighthouse login` or `navig vault add cloudflare`",
            }
        try:
            from navig.cloud import deck_deploy
            from navig.cloud.lighthouse_deploy import DeployError

            account_id = (cfg.get("cloud.lighthouse_account_id") or "").strip() or None
            _log("Uploading the deck to Cloudflare (Workers Static Assets, no wrangler) …")
            res = deck_deploy.deploy(out_dir, token=tok, account_id=account_id, worker_name=project)
            url = res.url
        except DeployError as exc:
            return {"ok": False, "status": "deploy_failed", "error": str(exc), "deploy_output": str(exc)}

    cfg.set("deck.public_url", url, scope="global")
    cfg.set("cloud.public_url", url, scope="global")  # so `miniapp register` / status reuse it
    cfg.save(scope="global")

    # First-party producer: announce the deck deploy (best-effort).
    try:
        from navig.notify.producers.events import report_deploy_sync

        report_deploy_sync("Deck (Mini App)", note=f"Live at {url}")
    except Exception:  # noqa: BLE001
        pass

    result = {
        "ok": True, "status": "deployed", "url": url, "lighthouse_url": lh or None,
        "registered": False, "used_prebuilt": used_prebuilt,
    }
    if register:
        token = _bot_token()
        if token:
            r = _tg_call(token, "setChatMenuButton", {
                "menu_button": {"type": "web_app", "text": "NAVIG Deck", "web_app": {"url": _connect_url(url)}},
            })
            result["registered"] = bool(r.get("ok"))
            if not r.get("ok"):
                result["register_error"] = r.get("description")
    return result


@app.command("deploy")
def miniapp_deploy(
    deck_dir: str = typer.Option("", "--dir", help="Path to navig-deck (auto-detected if omitted)."),
    project: str = typer.Option("navig-deck", "--project", help="Cloudflare Worker/Pages name."),
    lighthouse_url: str = typer.Option(
        "", "--lighthouse-url", help="Your edge URL to target (default: cloud.lighthouse_url)."
    ),
    skip_build: bool = typer.Option(False, "--skip-build", help="Reuse an existing out/ build."),
    register: bool = typer.Option(
        True, "--register/--no-register", help="Set the bot's Mini App button to the deployed URL."
    ),
    wrangler: bool = typer.Option(
        False, "--wrangler", help="Deploy via wrangler (Cloudflare Pages) instead of the pure-Python upload."
    ),
) -> None:
    """Deploy the Deck to YOUR own Cloudflare, pointed at your edge.

    End users (``pip install navig``): uploads the **prebuilt** deck bundle that
    ships with the navig-deck wheel — no Node, no source, no build. Developers with
    the navig-deck source + Node get a fresh ``next build`` static export instead,
    baking the Lighthouse URL in via ``NEXT_PUBLIC_LIGHTHOUSE_URL``.

    Either way it uploads to **Workers Static Assets** via the Cloudflare REST API by
    default, reusing the same credential as Lighthouse (no wrangler, no separate
    login). ``--wrangler`` uses ``wrangler pages deploy`` instead. Finally sets the
    bot's Mini App button.
    """
    ch = _ch()
    from navig.core import Config

    lh = (lighthouse_url or Config().get("cloud.lighthouse_url") or "").strip().rstrip("/")
    if not lh:
        ch.warning("No Lighthouse URL — the deployed deck won't know your brain's edge.")
        ch.dim("Deploy the edge first: `navig lighthouse login` (or `deploy`). Continuing anyway.")
    else:
        ch.info(f"Targeting your edge URL:  {lh}")

    res = run_miniapp_deploy(
        deck_dir=deck_dir, project=project, lighthouse_url=lighthouse_url,
        skip_build=skip_build, register=register, via_wrangler=wrangler, log=ch.info,
    )

    if not res["ok"]:
        status = res.get("status")
        if status == "no_deck":
            ch.warning("Could not find a deck to deploy (no prebuilt bundle, no source).")
            ch.dim("End users: `pip install navig-deck` (ships the prebuilt UI — no Node).")
            ch.dim("Developers: pass --dir <path-to-navig-deck> or set $NAVIG_DECK_DIR.")
            raise typer.Exit(2)
        if status == "no_node":
            ch.warning("Found the navig-deck source but no Node.js to build it, and no prebuilt bundle.")
            ch.dim("Install Node 18+ from https://nodejs.org, or `pip install navig-deck` for the prebuilt UI.")
            raise typer.Exit(2)
        if status == "no_cf_token":
            ch.warning("No Cloudflare credential found.")
            ch.dim("Run `navig lighthouse login` (recommended) or `navig vault add cloudflare`, then re-run.")
            raise typer.Exit(2)
        if status == "build_failed":
            ch.error("Deck build failed:")
            ch.dim(res.get("error") or "")
            raise typer.Exit(1)
        # deploy_failed
        ch.error("Deploy failed:")
        ch.dim(res.get("error") or "")
        low = (res.get("deploy_output") or "").lower()
        if any(k in low for k in ("authenticat", "unauthorized", "10000", "403", "permission")):
            ch.dim("")
            ch.dim("Cloudflare rejected the upload. Your token needs Workers Scripts: Edit +")
            ch.dim("Account Settings: Read (the 'Edit Cloudflare Workers' template). `navig")
            ch.dim("lighthouse login` grants this. Fallback: `npx wrangler login` then re-run")
            ch.dim("with `navig miniapp deploy --wrangler`.")
        raise typer.Exit(1)

    ch.success(f"Deck deployed: {res['url']}")
    if res.get("used_prebuilt"):
        ch.dim("Deployed the prebuilt deck bundle (no source/Node needed).")
        # A prebuilt bundle only carries the edge URL if the wheel embedded the
        # sentinel and we baked it above; otherwise point the deck at the brain
        # via its Settings (persisted per-browser).
        if not lh:
            ch.dim("Set your edge URL in the deck → Settings (or run `navig lighthouse login` first).")
    if register:
        if res.get("registered"):
            ch.success("Mini App button set — open your bot and tap the menu button.")
        elif res.get("register_error"):
            ch.warning(f"Couldn't set the Mini App button: {res['register_error']}")
            ch.dim("Set it later with `navig miniapp register`.")
        else:
            ch.dim("No bot token — skipped Mini App button. Run `navig miniapp register` later.")

    ch.dim("")
    ch.dim(f"Your deck link (use anywhere): {res['url']}")


@app.command("register")
def miniapp_register(
    url: str = typer.Option(
        "",
        "--url",
        help="Public HTTPS URL to register as the Mini App entry point. "
        "Defaults to cloud.public_url, then the running daemon's tunnel URL.",
    ),
    text: str = typer.Option(
        "NAVIG Deck",
        "--text",
        help="Button label shown next to the chat input.",
    ),
    description: bool = typer.Option(
        True,
        "--set-description/--no-set-description",
        help="Also update the bot's short description via setMyDescription.",
    ),
) -> None:
    """Auto-register the Deck URL as your bot's Mini App menu button.

    Uses the configured Telegram bot token (vault/env) to call
    ``setChatMenuButton`` on Telegram's API. After this runs, the Mini
    App button appears next to the chat input in every conversation
    with your bot, pointing at your daemon (via Tailscale Funnel, direct
    VPS mode, or cloudflared).
    """
    ch = _ch()

    token = _bot_token()
    if not token:
        ch.warning("No Telegram bot token configured.")
        ch.dim("Set it during `navig init` or store via the vault.")
        raise typer.Exit(code=2)

    resolved_url = _resolve_public_url(url)
    if not resolved_url:
        ch.warning("No URL to register. Provide --url or run one of:")
        ch.dim("  navig cloud tailscale --enable    (recommended: free, stable *.ts.net)")
        ch.dim("  navig cloud direct https://your.domain")
        ch.dim("  navig cloud connect               (cloudflared quick tunnel)")
        raise typer.Exit(code=2)

    if not resolved_url.lower().startswith("https://"):
        ch.warning(f"URL must be HTTPS (Telegram requires it). Got: {resolved_url}")
        raise typer.Exit(code=2)

    ch.info(f"Registering Mini App menu button:")
    ch.info(f"  url:  {resolved_url}")
    ch.info(f"  text: {text!r}")

    # setChatMenuButton — chat_id omitted means "default for all users"
    payload = {
        "menu_button": {
            "type": "web_app",
            "text": text,
            "web_app": {"url": _connect_url(resolved_url)},
        }
    }
    result = _tg_call(token, "setChatMenuButton", payload)
    if not result.get("ok"):
        ch.warning(f"Telegram rejected the update: {result.get('description')}")
        ch.dim("")
        ch.dim("Common causes:")
        ch.dim("  - The URL must respond with HTTPS (check `curl -I` works)")
        ch.dim("  - Your bot token must have permission to set menu button (it does by default)")
        ch.dim("  - Some Telegram clients cache the button for ~60s; wait and retry")
        raise typer.Exit(code=1)

    ch.success("Mini App menu button registered.")

    if description:
        desc = "NAVIG Deck — tap the menu button for your dashboard."
        # 120-char limit on bot descriptions; keep it short.
        desc_result = _tg_call(token, "setMyDescription", {"description": desc})
        if desc_result.get("ok"):
            ch.dim(f"  description set: {desc!r}")
        else:
            # Non-fatal -- description update is a nice-to-have.
            ch.dim(f"  description update skipped: {desc_result.get('description')}")

    ch.dim("")
    ch.dim("Telegram clients refresh the menu button within ~60 seconds. Tap your")
    ch.dim("bot's chat → the menu button should now open the Deck.")


@app.command("status")
def miniapp_status() -> None:
    """Show the bot's current Mini App menu button configuration."""
    ch = _ch()

    token = _bot_token()
    if not token:
        ch.warning("No Telegram bot token configured.")
        raise typer.Exit(code=2)

    # getMe gives the bot's identity for display
    me = _tg_call(token, "getMe", {})
    if me.get("ok"):
        bot = me.get("result", {})
        ch.info(f"Bot: @{bot.get('username')} (id={bot.get('id')})")
    else:
        ch.warning(f"getMe failed: {me.get('description')}")

    # getChatMenuButton returns the default if chat_id omitted
    button = _tg_call(token, "getChatMenuButton", {})
    if not button.get("ok"):
        ch.warning(f"getChatMenuButton failed: {button.get('description')}")
        raise typer.Exit(code=1)
    info = button.get("result") or {}
    btype = info.get("type") or "default"
    ch.info(f"Menu button type: {btype}")
    if btype == "web_app":
        ch.info(f"  text: {info.get('text')!r}")
        ch.info(f"  url:  {(info.get('web_app') or {}).get('url')}")
    elif btype == "default":
        ch.dim("  (no Mini App button set — clients show the default 'commands' menu)")
        ch.dim("  Run `navig miniapp register` to set one.")
    elif btype == "commands":
        ch.dim("  (configured to show the bot's command list)")


@app.command("unregister")
def miniapp_unregister(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation."),
) -> None:
    """Revert the bot's menu button to the default (commands list)."""
    ch = _ch()

    token = _bot_token()
    if not token:
        ch.warning("No Telegram bot token configured.")
        raise typer.Exit(code=2)

    if not yes:
        confirm = typer.prompt("Reset the Mini App menu button to default? Type 'yes' to continue")
        if confirm.strip().lower() != "yes":
            ch.warning("Cancelled.")
            return

    result = _tg_call(token, "setChatMenuButton", {"menu_button": {"type": "default"}})
    if not result.get("ok"):
        ch.warning(f"Telegram rejected the reset: {result.get('description')}")
        raise typer.Exit(code=1)
    ch.success("Menu button reset to default.")
