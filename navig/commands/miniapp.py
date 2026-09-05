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

The URL defaults to ``deck.public_url`` (written by ``miniapp deploy``), falling
back to the legacy ``cloud.public_url`` and then the cloudflared tunnel URL.
``--url`` overrides explicitly.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path

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
        from navig.config import get_config_manager
        from navig.messaging.secrets import resolve_telegram_bot_token
        cfg = get_config_manager().global_config or {}
        token = resolve_telegram_bot_token(cfg) or ""
        return token.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("token resolve failed: %r", exc)
        return ""


def _connect_url(base_url: str, *, version: str = "") -> str:
    """The Mini App entry URL — a ``/connect?key=<deck.api_key>`` link.

    The Telegram Mini App must authenticate to the Lighthouse edge, which routes
    by ``sha256(api_key)``; a bare deck URL carries no key, so the deck can't
    reach the brain and renders blank. The ``/connect`` page seeds the key into
    localStorage, then the deck works. Falls back to the bare URL if no key.

    ``version`` appends a ``v=<sig>`` cache-bust: Telegram caches the Mini App by
    URL and does NOT honour ``Cache-Control`` (the deck ships ``max-age=0,
    must-revalidate`` yet Telegram still serves its sticky WebView cache), so a
    redeploy renders the OLD bundle until the button URL itself changes. A
    content signature that changes iff the deck bundle changes forces a fresh
    fetch exactly when — and only when — there's something new to fetch.

    SECURITY: this embeds the deck api_key in the bot's (global) menu button —
    appropriate for a personal/single-owner bot. The api_key bypasses the
    allow-list, so for a SHARED bot, reset the key and prefer a per-user flow.
    """
    base = (base_url or "").rstrip("/")
    if not base:
        return base
    try:
        from navig.config import get_config_manager
        gc = get_config_manager().global_config or {}
        key = str((gc.get("deck", {}) or {}).get("api_key", "") or "").strip()
    except Exception:  # noqa: BLE001
        key = ""
    from urllib.parse import quote
    if key:
        url = f"{base}/connect?key={quote(key, safe='')}"
    else:
        url = base
    if version:
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}v={quote(version, safe='')}"
    return url


# Files under the deck's `public/` that Next copies into `out/` verbatim and that
# CONFIGURE the deployment rather than being part of the compiled app. `--skip-build`
# reuses a previous `out/`, so an edit to one of these would silently not ship — the
# deploy reports success and the change is simply absent. That is exactly the
# "looks applied, isn't" class `_headers` itself belongs to, and it bit during the
# work that introduced the header pipeline: a `_headers` edit deployed cleanly and
# changed nothing.
_PASSTHROUGH_ASSETS = ("_headers", "_redirects")


def _refresh_passthrough_assets(deck: Path, out_dir: Path, *, log=None) -> list[str]:
    """Copy deploy-config files from `public/` over `out/`. Returns what changed.

    Only these named files, never a general `public/` sync: a stale `out/` may
    legitimately differ from `public/` in ways a rebuild would resolve, and quietly
    papering over that would trade one invisible staleness for another.
    """
    changed: list[str] = []
    src_dir = deck / "public"
    for name in _PASSTHROUGH_ASSETS:
        src, dst = src_dir / name, out_dir / name
        if not src.is_file():
            continue
        try:
            new = src.read_bytes()
            if dst.is_file() and dst.read_bytes() == new:
                continue
            dst.write_bytes(new)
            changed.append(name)
        except OSError as exc:  # noqa: PERF203 — report, never fail a deploy on this
            if log:
                log(f"Note: could not refresh {name} from public/: {exc}")
    if changed and log:
        log(f"Refreshed from public/ (skipped build would have shipped the old ones): {', '.join(changed)}")
    return changed


def _deck_bundle_signature(out_dir: Path) -> str:
    """Short signature of the built deck's content-hashed asset filenames.

    Next.js names its chunks by content hash, so the sorted set of asset names is
    a stable fingerprint of the bundle that changes iff the deck build changes.
    Used to cache-bust the Telegram Mini App button URL (see ``_connect_url``).
    Never raises — an unreadable dir just yields ``""`` (no bust)."""
    try:
        static = out_dir / "_next" / "static"
        names = sorted(p.name for p in static.rglob("*") if p.is_file())
        if not names:
            return ""
        import hashlib

        return hashlib.sha1("\n".join(names).encode("utf-8")).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return ""


def _resolve_public_url(explicit: str = "") -> str:
    """Where the DECK lives: explicit > deck.public_url > cloud.public_url > daemon tunnel.

    ``deck.public_url`` first — this resolves the URL the bot's Mini App button
    should point at, which is the DECK, not the brain. ``cloud.public_url`` is the
    BRAIN's direct-mode ingress (``navig cloud direct`` / tailscale funnel write it,
    CloudManager reads it) and is only kept here as a legacy fallback for decks
    deployed before ``deck.public_url`` existed.
    """
    if explicit:
        return explicit.strip().rstrip("/")
    from navig.core import Config
    cfg = Config()
    deck_url = (cfg.get("deck.public_url") or "").strip()
    if deck_url:
        return deck_url.rstrip("/")
    cfg_url = (cfg.get("cloud.public_url") or "").strip()
    if cfg_url:
        return cfg_url.rstrip("/")
    # Last resort: ask the running daemon for its current tunnel URL
    try:
        # Live-first resolver — follows the self-healed port from
        # ~/.navig/gateway.json, else nested gateway.port (8789 default,
        # not the daemon-IPC 8765).
        from navig.gateway_client import gateway_live_defaults

        port = gateway_live_defaults()[0]
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


def redact_key_in_url(url: str) -> str:
    """The Mini App button URL with its ``key=`` value replaced.

    The button URL embeds the RAW ``deck.api_key`` (see :func:`_connect_url`), and
    that key is the install's identity: it bypasses Telegram auth on the deck API
    and its sha256 IS the lighthouse tenant. `navig miniapp status` is a read-only
    diagnostic people run repeatedly and paste into issues and screenshots, so it
    printed a live credential every time for no diagnostic gain: what the command
    is actually judging is the ORIGIN and the ``v=`` cache-bust, both of which stay
    visible here.

    Everything except the key's value is preserved, deliberately — a redaction that
    hides the whole URL would take the answer away with the secret. Setup flows that
    hand the operator their magic link on purpose (``navig cloud connect``) are a
    different contract and are left alone.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    if not url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.query:
            return url
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if not any(k == "key" for k, _ in pairs):
            return url
        # A placeholder with no URL-special characters: "<redacted>" survives
        # urlencode as "%3Credacted%3E", which is unreadable in a status line.
        masked = [(k, "REDACTED" if k == "key" else v) for k, v in pairs]
        return urlunsplit(
            (parts.scheme, parts.netloc, parts.path, urlencode(masked), parts.fragment)
        )
    except Exception:  # noqa: BLE001 — never let a display helper break the command
        # Unparseable: say so rather than risk echoing the key.
        return "<unparseable url — not shown, it embeds the deck api_key>"


# ── Mini App button health ────────────────────────────────────────────────────


def miniapp_button_health(*, timeout: float = 6.0) -> tuple[bool, str, bool] | None:
    """Does the bot's Mini App button point at the CURRENT deck bundle?

    Telegram caches a Mini App **by URL** and ignores ``Cache-Control``, so the only
    thing that makes a client re-fetch after a deploy is a change to the button URL
    itself — which is why :func:`_connect_url` appends ``v=<deck.bundle_sig>``. A
    button whose ``v=`` is missing or stale means every Telegram client keeps
    rendering the bundle it cached the last time the URL changed, while the deploy,
    the edge and the uplink all report perfectly healthy.

    That is not hypothetical. This install's button had never carried a ``v=``, so
    Telegram Desktop and iOS both served a months-old deck; that bundle predates the
    baked ``NEXT_PUBLIC_LIGHTHOUSE_URL``, so it resolved the daemon through the broker
    instead and died with HTTP 530 against a long-dead cloudflared host.

    Returns ``(ok, detail, warn)`` for a doctor-style row, or ``None`` when no deck is
    deployed and the question does not apply. ``ok=False, warn=True`` is the
    could-not-verify state — a check that did not run must never render a green tick.

    Reads config through a fresh :class:`ConfigManager` rather than ``Config()``: the
    latter is a process-wide singleton that never re-reads the config dir, so a health
    check built on it can silently describe the wrong install.
    """
    from urllib.parse import parse_qs, urlsplit

    try:
        from navig.config import ConfigManager

        cm = ConfigManager()
        deck_url = str(cm.get("deck.public_url", "") or "").strip().rstrip("/")
        want_sig = str(cm.get("deck.bundle_sig", "") or "").strip()
        want_key = str(cm.get("deck.api_key", "") or "").strip()
    except Exception as exc:  # noqa: BLE001 — a health check must never crash its caller
        return (False, f"COULD NOT VERIFY ({exc})", True)

    if not deck_url:
        return None  # no deck deployed — the button is not this install's concern

    token = _bot_token()
    if not token:
        return (False, "could not check — no Telegram bot token configured", True)

    resp = _tg_call(token, "getChatMenuButton", {}, timeout=timeout)
    if not resp.get("ok"):
        desc = resp.get("description") or "Telegram call failed"
        return (False, f"COULD NOT VERIFY ({desc})", True)

    info = resp.get("result") or {}
    if str(info.get("type") or "") != "web_app":
        return (False, "no Mini App button set — run `navig miniapp register`", True)

    live = str((info.get("web_app") or {}).get("url") or "").strip()
    parts = urlsplit(live)
    live_origin = f"{parts.scheme}://{parts.netloc}" if parts.netloc else live
    if live_origin.rstrip("/") != deck_url:
        return (
            False,
            f"points at {live_origin} but your deck is deployed at {deck_url} — "
            "run `navig miniapp register`",
            False,
        )

    # The button's key is the install's IDENTITY, not a detail: the edge derives the
    # tenant from sha256(deck.api_key), so a button still carrying a RETIRED key sends
    # every Mini App session to a Durable Object with no uplink. Rotation is supposed to
    # re-point the button (navig/cloud/rotation.py), but when that half fails the button
    # keeps a perfect origin and a perfect v= — so the two checks either side of this one
    # both pass while the deck cannot reach the brain at all. Compared, never printed.
    have_key = (parse_qs(parts.query).get("key") or [""])[0].strip()
    if not want_key:
        return (
            False,
            "no deck.api_key in config, so the button's key cannot be checked",
            True,
        )
    if not have_key:
        return (
            False,
            "button URL carries no key=, so the Mini App cannot authenticate — "
            "run `navig miniapp register`",
            False,
        )
    if have_key != want_key:
        return (
            False,
            "button URL carries a DIFFERENT deck.api_key than this install — the Mini "
            "App resolves to a retired edge tenant with no brain attached — run "
            "`navig miniapp register`",
            False,
        )

    have_sig = (parse_qs(parts.query).get("v") or [""])[0].strip()
    if not want_sig:
        return (
            False,
            "no deck.bundle_sig recorded, so the button's cache-bust cannot be "
            "checked — redeploy with `navig miniapp deploy`",
            True,
        )
    if not have_sig:
        return (
            False,
            "button URL carries no v= cache-bust, so every Telegram client keeps "
            "serving the deck bundle it already cached — run `navig miniapp register`",
            False,
        )
    if have_sig != want_sig:
        return (
            False,
            f"button cache-bust v={have_sig} is stale (deployed bundle is {want_sig}), "
            "so Telegram clients are on an older deck — run `navig miniapp register`",
            False,
        )
    return (True, f"current bundle (v={want_sig})", False)


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
    # Walk up from cwd: the monorepo home (apps/deck) first, then the legacy
    # polyrepo sibling name (navig-deck) for old checkouts.
    cur = Path.cwd()
    for parent in [cur, *cur.parents]:
        candidates.append(parent / "apps" / "deck")
        candidates.append(parent / "navig-deck")  # dead-path-ok: legacy polyrepo checkout, tried after apps/deck
    # Relative to this installed package. An editable install resolves
    # __file__ into <repo>/core/navig/commands/miniapp.py — parents[3] is the
    # repo root, so <repo>/apps/deck is the monorepo source.
    here = Path(__file__).resolve()
    for up in (3, 4, 5):
        if len(here.parents) > up:
            candidates.append(here.parents[up] / "apps" / "deck")
            candidates.append(here.parents[up] / "navig-deck")  # dead-path-ok: legacy polyrepo checkout
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
      3. Dev-tree neighbours (monorepo): ``apps/deck/out``, ``apps/deck/dist``,
         the wheel-builder staging dir, and ``core/deck-static``.
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
    # parents[3] IS the monorepo root (core/navig/commands/<f> -> core -> root). The
    # 4/5 entries date from the sibling-repo layout, where the deck lived NEXT TO
    # navig-core rather than inside one tree; kept so a legacy checkout still works,
    # but without 3 the relative paths below could never resolve in this repo.
    repo_roots = {here.parents[up] for up in (3, 4, 5) if len(here.parents) > up}
    repo_roots.add(Path.cwd())
    repo_roots.update(Path.cwd().parents)
    for root in repo_roots:
        for rel in (
            "apps/deck/out",
            "apps/deck/dist",
            "apps/deck/python/navig_deck/static",
            "core/deck-static",
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
    telegram_only: bool = True,
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

    # Telegram-only lockdown (default ON — the deployed Mini App is meant to run
    # ONLY inside Telegram). Bake the client gate into the (source) build AND lock
    # the daemon so /api/deck + /api/events accept only valid Telegram initData
    # REMOTELY: the Bearer api_key and the dev header stop working.
    #
    # Genuinely-local loopback keeps its desktop bypass (deck/auth.py
    # `_local_desktop_bypass`), so this does NOT cut off the desktop OS app
    # talking to its own daemon. What it DOES disable is the Bearer api_key —
    # i.e. a browser deck, or the OS pointed at a REMOTE brain over
    # Lighthouse/tunnel. Opt out at deploy time with `--no-telegram-only`
    # (or afterwards: `navig config set deck.telegram_only false`).
    # NOTE: only the source build (npm cf:build) re-bakes the client env; the
    # prebuilt end-user bundle keeps whatever was baked at wheel time, but the
    # daemon is still locked regardless (server-side enforcement is the real gate).
    if telegram_only:
        build_env["NEXT_PUBLIC_DECK_TELEGRAM_ONLY"] = "1"
        try:
            cfg.set("deck.telegram_only", True, scope="global")
            _log("Locked the Deck to the Telegram Mini App (deck.telegram_only=true).")
        except Exception as exc:  # noqa: BLE001
            _log(f"Warning: couldn't persist deck.telegram_only — set it manually "
                 f"(`navig config set deck.telegram_only true`): {exc}")

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
        _refresh_passthrough_assets(deck, out_dir, log=_log)

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
    pre_bake_dir = out_dir
    if used_prebuilt and lh:
        out_dir = _bake_lighthouse_into_prebuilt(out_dir, lh, log=_log)

    # `_bake_lighthouse_into_prebuilt` copies the whole bundle into a temp dir
    # and hands the path back; nothing removed it, so every deploy leaked a full
    # staging copy (91 `navig-deck-deploy-*` dirs on the operator's machine when
    # this was found). try/finally rather than a call before each `return`: there
    # are five exits below and a sixth added later would leak again silently.
    staging = out_dir.parent if out_dir != pre_bake_dir else None
    try:
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

        # DO NOT write cloud.public_url here. It is the BRAIN's direct-mode ingress —
        # `navig cloud direct <url>` and the tailscale funnel write it, and CloudManager
        # reads it to decide "this brain is publicly reachable at <url>, no tunnel
        # needed" (cloud/manager.py). Pointing it at the Deck (a *static asset* Worker
        # that cannot serve /api/deck/*) made the brain believe it was directly
        # reachable at a site that can't answer it — breaking reachability, and making
        # `_reachable_ready()` report "configured" when nothing was.
        # `miniapp register` now resolves the deck's URL from deck.public_url instead.
        #
        # Self-heal the provably-wrong value an older navig wrote: if cloud.public_url
        # IS the deck URL, it was never a valid brain ingress — clear it so CloudManager
        # falls back to its real mode (lighthouse / tunnel) instead of dialling a static site.
        try:
            stale = (cfg.get("cloud.public_url") or "").strip().rstrip("/")
            if stale and stale == url.rstrip("/"):
                cfg.set("cloud.public_url", "", scope="global")
                _log(
                    "Cleared cloud.public_url — an older navig set it to the Deck's URL, "
                    "which is not the brain's address (reachability now resolves correctly)."
                )
        except Exception as exc:  # noqa: BLE001 — never fail a successful deploy on cleanup
            _log(f"Note: could not clean up a stale cloud.public_url: {exc}")
        # Stamp the navig version this deck was built from, so `navig miniapp version`
        # / `navig update` can tell when the deployed deck is behind a new release.
        try:
            from navig import __version__ as _nv
            cfg.set("deck.deployed_version", _nv, scope="global")
        except Exception:  # noqa: BLE001
            pass
        # Content signature of the just-built bundle → cache-busts the Telegram Mini
        # App button URL so a redeploy is actually fetched (Telegram's WebView cache
        # ignores Cache-Control). Persisted so `miniapp register` reuses the same sig.
        bundle_sig = _deck_bundle_signature(out_dir)
        if bundle_sig:
            try:
                cfg.set("deck.bundle_sig", bundle_sig, scope="global")
            except Exception:  # noqa: BLE001
                pass
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
                    "menu_button": {
                        "type": "web_app",
                        "text": "NAVIG Deck",
                        "web_app": {"url": _connect_url(url, version=bundle_sig)},
                    },
                })
                result["registered"] = bool(r.get("ok"))
                if not r.get("ok"):
                    result["register_error"] = r.get("description")
        return result
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


@app.command("version")
def miniapp_version() -> None:
    """Show the deployed deck version vs the version in this navig release."""
    from navig import __version__ as cur
    from navig import console_helper as ch
    from navig.core import Config

    cfg = Config()
    url = str(cfg.get("deck.public_url", "") or "").strip()
    deployed = str(cfg.get("deck.deployed_version", "") or "").strip()
    if not url:
        ch.info(f"Deck not deployed. This navig: v{cur}.")
        return
    ch.info(f"Deployed deck: v{deployed or '?'}   ({url})")
    ch.info(f"This navig:    v{cur}")
    if deployed and deployed != cur:
        ch.warning("Update available — run `navig miniapp deploy`.")
    elif deployed == cur:
        ch.success("Up to date.")
    else:
        ch.dim("Deployed before version tracking — `navig miniapp deploy` to refresh.")


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
    telegram_only: bool = typer.Option(
        True,
        "--telegram-only/--no-telegram-only",
        help="Lock the deployed deck to the Telegram Mini App (default). "
        "--no-telegram-only keeps the Bearer api_key working, so a browser deck — or the "
        "desktop app pointed at a REMOTE brain — can still reach it. Local loopback is "
        "unaffected either way.",
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
        skip_build=skip_build, register=register, via_wrangler=wrangler,
        telegram_only=telegram_only, log=ch.info,
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
    # A deploy whose menu-button update did not land is NOT a footnote. Telegram keys
    # its Mini App cache on the button URL and ignores Cache-Control, so until that URL
    # changes every client keeps rendering the bundle it already cached — the assets are
    # live and nobody can see them. Reporting that with ch.dim() is what let this install
    # sit on a months-old deck (which then failed to reach the brain entirely).
    _STALE_CACHE_NOTE = (
        "Telegram caches the Mini App by URL, so until the button URL changes every "
        "client keeps serving the deck it already cached — this deploy will be invisible."
    )
    if register:
        if res.get("registered"):
            ch.success("Mini App button set — open your bot and tap the menu button.")
        elif res.get("register_error"):
            ch.warning(f"Couldn't set the Mini App button: {res['register_error']}")
            ch.warning(_STALE_CACHE_NOTE)
            ch.dim("Fix with: navig miniapp register")
        else:
            ch.warning("No Telegram bot token — the Mini App button was NOT updated.")
            ch.warning(_STALE_CACHE_NOTE)
            ch.dim("Fix with: navig miniapp register")
    else:
        ch.warning("--no-register: the Mini App button was NOT updated.")
        ch.warning(_STALE_CACHE_NOTE)
        ch.dim("Fix with: navig miniapp register")

    ch.dim("")
    ch.dim(f"Your deck link (use anywhere): {res['url']}")


@app.command("register")
def miniapp_register(
    url: str = typer.Option(
        "",
        "--url",
        help="Public HTTPS URL to register as the Mini App entry point. "
        "Defaults to deck.public_url (set by `miniapp deploy`), then the legacy "
        "cloud.public_url, then the running daemon's tunnel URL.",
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

    # Reuse the last deploy's bundle signature so the button URL carries the same
    # cache-bust the deploy set (Telegram re-fetches the Mini App only when the URL
    # changes; see _connect_url).
    try:
        from navig.core import Config

        _sig = str(Config().get("deck.bundle_sig") or "").strip()
    except Exception:  # noqa: BLE001
        _sig = ""
    # setChatMenuButton — chat_id omitted means "default for all users"
    payload = {
        "menu_button": {
            "type": "web_app",
            "text": text,
            "web_app": {"url": _connect_url(resolved_url, version=_sig)},
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
        ch.info(f"  url:  {redact_key_in_url(str((info.get('web_app') or {}).get('url') or ''))}")
        # Printing the URL is not the same as judging it. The URL can look perfectly
        # right and still pin every Telegram client to a bundle from months ago —
        # see miniapp_button_health() for why the v= cache-bust is the whole story.
        verdict = miniapp_button_health()
        if verdict is not None:
            ok, detail, warn = verdict
            if ok:
                ch.success(f"Bundle: {detail}")
            elif warn:
                ch.warning(f"Bundle: {detail}")
            else:
                ch.error(f"Bundle: {detail}")
                raise typer.Exit(code=1)
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
