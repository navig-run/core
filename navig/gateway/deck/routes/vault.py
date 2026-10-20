"""Vault credential endpoints for the Deck API."""

import asyncio
import logging
import sys
from typing import Any

try:
    from aiohttp import web
except ImportError:
    web = None

from navig.core.aio_subprocess import STREAM_LIMIT
from navig.core.background import spawn
from navig.vault.secret_str import mask_secret

logger = logging.getLogger(__name__)

_VAULT_ALLOWED_PROVIDERS = {
    # AI / LLM
    "openai",
    "openrouter",
    "anthropic",
    "groq",
    "google",
    "deepseek",
    "mistral",
    "cohere",
    "perplexity",
    "fireworks",
    "nvidia",
    "together",
    "replicate",
    "huggingface",
    "siliconflow",
    "grok",
    "xai",
    "ollama",
    "cerebras",
    "ai21",
    # Voice / Audio
    "deepgram",
    "elevenlabs",
    # Media generation (image / video)
    "recraft",
    "stability",
    "runway",
    "luma",
    # Developer tools
    "github",
    "gitlab",
    "vercel",
    "netlify",
    "linear",
    "notion",
    # Infrastructure / Cloud
    "aws",
    "gcp",
    "azure",
    "hetzner",
    "digitalocean",
    "cloudflare",
    "fly",
    # Data / SaaS
    "stripe",
    "supabase",
    "pinecone",
    "neon",
    "redis",
    # Search / research / enrichment
    "tavily",
    "serpapi",
    "brave",
    "deepseek_search",
    "hunter",
    "apollo",
    # Messaging / social adapters
    "telegram",
    "discord",
    "slack",
    "whatsapp",
    "sms",
    "linkedin",
    "linkedin_page",
    "reddit",
    "instagram_meta",
    "instagram",
    "facebook",
    "threads",
    "youtube",
    "vk",
    "medium",
    "devto",
}


from navig.gateway.deck.routes._utils import _get_vault


async def handle_deck_vault_list(request: "web.Request") -> "web.Response":
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    try:
        creds = vault.list()
        items = []
        for c in creds:
            meta = c.metadata or {}
            profile_id = meta.get("profile_id", "")
            raw_ct = meta.get("credential_type", "")
            credential_type = (
                raw_ct.value if hasattr(raw_ct, "value") else str(raw_ct)
            ) if raw_ct else ""
            items.append(
                {
                    "id": c.id,
                    "provider": c.provider,
                    "profile_id": profile_id,
                    "credential_type": credential_type,
                    "label": c.label,
                    "enabled": c.enabled,
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                    "last_used_at": (c.last_used_at.isoformat() if c.last_used_at else None),
                    "metadata": meta,
                }
            )

            try:
                full_cred = vault.get_by_id(c.id, caller="deck_vault_list")
                if full_cred and full_cred.data:
                    key_val = full_cred.data.get("api_key", "") or full_cred.data.get("token", "")
                    items[-1]["key_preview"] = mask_secret(key_val, show_prefix=6)
                else:
                    items[-1]["key_preview"] = "••••••••"
            except Exception:
                items[-1]["key_preview"] = "••••••••"

        # Probe whether openai-whisper local package is installed
        whisper_installed = False
        try:
            import whisper as _whisper_pkg  # noqa: F401
            whisper_installed = True
        except ImportError:
            pass

        return web.json_response(
            {
                "credentials": items,
                "allowed_providers": sorted(_VAULT_ALLOWED_PROVIDERS),
                "whisper_installed": whisper_installed,
            }
        )
    except Exception as e:
        logger.error("Vault list error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_add(request: "web.Request") -> "web.Response":
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    provider = body.get("provider", "").strip().lower()
    api_key = body.get("api_key", "").strip()
    label = body.get("label", "").strip()

    if not provider:
        return web.json_response({"error": "provider is required"}, status=400)
    if provider not in _VAULT_ALLOWED_PROVIDERS:
        return web.json_response(
            {
                "error": f"provider not allowed. valid: {', '.join(sorted(_VAULT_ALLOWED_PROVIDERS))}"
            },
            status=400,
        )
    if not api_key:
        return web.json_response({"error": "api_key is required"}, status=400)
    if len(api_key) < 10:
        return web.json_response({"error": "api_key too short"}, status=400)

    try:
        cred_id = vault.add(
            provider=provider,
            credential_type="api_key",
            data={"api_key": api_key},
            profile_id="default",
            label=label or f"{provider.title()} Key",
        )
        return web.json_response(
            {
                "ok": True,
                "id": cred_id,
                "provider": provider,
                "key_preview": mask_secret(api_key, show_prefix=6),
            }
        )
    except Exception as e:
        logger.error("Vault add error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_delete(request: "web.Request") -> "web.Response":
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    cred_id = request.match_info.get("cred_id", "")
    if not cred_id:
        return web.json_response({"error": "credential id required"}, status=400)

    try:
        deleted = vault.delete(cred_id)
        return web.json_response({"ok": deleted})
    except Exception as e:
        logger.error("Vault delete error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_export_svg(request: "web.Request") -> "web.Response":
    """Seal credentials into an SVG export — the desktop app's "Export" button.

    Body: ``{"ids": [credential ids]}`` or ``{"all": true}``, optional ``"each": true``.
    Returns ``{"files": [{name, svg, passphrase, labels}], "unreadable": [...]}``: the SVG
    text travels back to the app, which saves it where the user chooses — the daemon writes
    nothing. Engine: navig_vault.svg_export (shared with `navig vault export --svg`).

    THIS COMPUTER ONLY. Every other vault route returns masked metadata; this one returns
    decryptable secrets plus the key to them, so it must not be reachable through the
    tunnel, Lighthouse or the Telegram Mini App — "hand over every password" is not
    something a phone should be one tap away from.
    """
    from navig.gateway.deck.auth import is_local_request

    if not is_local_request(request):
        return web.json_response(
            {"error": "vault export is only available on this computer"}, status=403
        )
    try:
        from navig_vault import svg as _svg
        from navig_vault import svg_export
    except ImportError:
        return web.json_response(
            {"error": "this navig-vault is too old for SVG exports — pip install -U navig-vault"},
            status=501,
        )
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "invalid json"}, status=400)
    ids = body.get("ids")
    export_all = body.get("all") is True
    if export_all == bool(ids) or (ids is not None and not (
        isinstance(ids, list) and all(isinstance(i, str) and i for i in ids)
    )):
        return web.json_response(
            {"error": "send either a list of credential ids or all: true"}, status=400
        )
    try:
        sealed = await asyncio.to_thread(
            svg_export.seal_texts, vault, None if export_all else ids, each=body.get("each") is True
        )
    except (svg_export.ExportError, _svg.VaultSvgError) as e:
        return web.json_response({"error": str(e)}, status=404 if "matches" in str(e) else 400)
    except Exception as e:
        logger.error("Vault SVG export error: %s", e)
        return web.json_response({"error": "export failed"}, status=500)
    logger.info("Vault SVG export: %d file(s), %d item(s)", len(sealed.files),
                sum(len(f.labels) for f in sealed.files))
    return web.json_response({
        "files": [
            {"name": f.name, "svg": f.text, "passphrase": f.passphrase, "labels": f.labels}
            for f in sealed.files
        ],
        "unreadable": sealed.unreadable,
    })


async def handle_deck_vault_toggle(request: "web.Request") -> "web.Response":
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    cred_id = request.match_info.get("cred_id", "")
    if not cred_id:
        return web.json_response({"error": "credential id required"}, status=400)

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid json"}, status=400)

    enabled = body.get("enabled", True)

    try:
        if enabled:
            result = vault.enable(cred_id)
        else:
            result = vault.disable(cred_id)
        return web.json_response({"ok": result, "enabled": enabled})
    except Exception as e:
        logger.error("Vault toggle error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


async def handle_deck_vault_test(request: "web.Request") -> "web.Response":
    vault = _get_vault()
    if not vault:
        return web.json_response({"error": "vault unavailable"}, status=500)

    cred_id = request.match_info.get("cred_id", "")
    if not cred_id:
        return web.json_response({"error": "credential id required"}, status=400)

    try:
        result = vault.test(cred_id)
        return web.json_response(
            {
                "ok": result.success,
                "message": result.message,
                "details": result.details,
            }
        )
    except Exception as e:
        logger.error("Vault test error: %s", e)
        return web.json_response({"error": str(e)}, status=500)


# ── Whisper install: background runner + status polling ──────────────────────
#
# `pip install openai-whisper` pulls torch (~2 GB) and easily exceeds any
# reasonable HTTP timeout. We spawn the install as a background task and let
# the client poll /whisper/install/status until it finishes.

_WHISPER_INSTALL_STATE: dict[str, Any] = {
    "running": False,
    "ok": None,        # None while running, True/False when done
    "message": "",     # latest line of output / final outcome
    "started_at": 0.0,
    "finished_at": 0.0,
}


def _whisper_already_installed() -> bool:
    """Cheap check — try importing the module without invoking it."""
    try:
        import importlib.util
        return importlib.util.find_spec("whisper") is not None
    except Exception:
        return False


async def _run_whisper_install() -> None:
    """Background coroutine: runs `pip install openai-whisper` to completion."""
    import time as _time
    _WHISPER_INSTALL_STATE.update({
        "running": True,
        "ok": None,
        "message": "Starting pip install openai-whisper…",
        "started_at": _time.time(),
        "finished_at": 0.0,
    })
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "pip", "install", "openai-whisper",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,  # merge so we see warnings too
            # readline()d below — an unset limit turns one long pip line (a resolver
            # conflict dump) into a raise that fails an install which actually succeeded.
            limit=STREAM_LIMIT,
        )
        # Read line-by-line so the latest progress line is always visible.
        last_line = ""
        assert proc.stdout is not None  # noqa: S101 — type-narrowing invariant, not validation
        while True:
            chunk = await proc.stdout.readline()
            if not chunk:
                break
            line = chunk.decode(errors="replace").rstrip()
            if line:
                last_line = line
                _WHISPER_INSTALL_STATE["message"] = line[:240]
        await proc.wait()
        ok = proc.returncode == 0
        _WHISPER_INSTALL_STATE.update({
            "running": False,
            "ok": ok,
            "message": (last_line or ("Installed successfully" if ok else "Install failed"))[:240],
            "finished_at": _time.time(),
        })
    except Exception as exc:
        logger.exception("Whisper install error")
        _WHISPER_INSTALL_STATE.update({
            "running": False,
            "ok": False,
            "message": f"Install failed: {exc}",
            "finished_at": _time.time(),
        })


async def handle_deck_whisper_install(request: "web.Request") -> "web.Response":
    """Kick off the openai-whisper install in the background.

    Returns immediately with `{ok: true, started: bool, already_installed: bool}`.
    Long-running pip resolution / torch download happens asynchronously.
    Clients should poll `/api/deck/whisper/install/status` to track progress.
    """
    if _whisper_already_installed():
        _WHISPER_INSTALL_STATE.update({
            "running": False, "ok": True, "message": "Already installed",
        })
        return web.json_response({"ok": True, "started": False, "already_installed": True})

    if _WHISPER_INSTALL_STATE["running"]:
        return web.json_response({"ok": True, "started": False, "already_running": True})

    # Fire and forget: the task updates the shared dict above. spawn() holds a
    # strong ref so the install can't be GC-collected before it runs (a discarded
    # create_task can be), which would leave the state stuck "running".
    spawn(_run_whisper_install())
    return web.json_response({"ok": True, "started": True})


async def handle_deck_whisper_install_status(request: "web.Request") -> "web.Response":
    """Poll endpoint — returns the current state of the install."""
    state = dict(_WHISPER_INSTALL_STATE)
    state["already_installed"] = _whisper_already_installed()
    return web.json_response(state)


# ── Website logins (`navig vault login …`) — list / capture / remove ─────────
#
# Capture runs `navig vault login capture --capture-only` as a SUBPROCESS: browser
# work must stay off the daemon loop (the same pattern as
# /api/deck/games/login/capture). ONE endpoint, called twice by the client:
#   1st call → the persistent profile opens the sign-in page → {ok:false, opened:true}
#   the human signs in in that window, then
#   2nd call → session + IdP linkage land in the vault → {ok:true, …}

_capture_lock: dict[str, Any] = {"running": False}
_CAPTURE_TIMEOUT_S = 150


def _login_json_from_output(out: bytes) -> dict | None:
    """Parse the JSON object a ``navig … --json`` command prints.

    Stray log lines may wrap it — fall back to the ``{`` … ``}`` substring.
    Returns None when nothing parses to a dict.
    """
    import json as _json

    text = (out or b"").decode("utf-8", "replace").strip()
    if not text:
        return None
    candidates = [text]
    i, j = text.find("{"), text.rfind("}")
    if 0 <= i < j:
        candidates.append(text[i : j + 1])
    for candidate in candidates:
        try:
            data = _json.loads(candidate)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


async def handle_deck_vault_logins_list(request: "web.Request") -> "web.Response":
    """GET — stored website logins (non-secret summaries, incl. the sign-in method)."""
    try:
        from navig.vault import list_logins  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": f"vault unavailable: {exc}"}, status=500)
    try:
        logins = [
            {
                "domain": i.domain,
                "username": i.username,
                "auth_method": i.auth_method,
                "idp": i.idp,
                "api_ref": i.api_ref,
                "has_totp": i.has_totp,
                "url": i.url,
                "last_used_at": i.last_used_at.isoformat() if i.last_used_at else None,
            }
            for i in sorted(list_logins(), key=lambda x: (x.domain, x.username))
        ]
        return web.json_response({"logins": logins})
    except Exception as exc:  # noqa: BLE001
        logger.error("Vault logins list error: %s", exc)
        return web.json_response({"error": str(exc)}, status=500)


async def handle_deck_vault_login_capture(request: "web.Request") -> "web.Response":
    """POST {domain, username?, idp?, idp_user?, url?} — two-phase SSO capture.

    Phase 1 opens the sign-in page ({ok:false, opened:true}); phase 2 (after the
    human signed in) stores the session + IdP linkage ({ok:true}). 409 while a
    capture is already running.
    """
    if _capture_lock["running"]:
        return web.json_response(
            {"ok": False, "error": "a capture is already running"}, status=409
        )
    try:
        body = await request.json() if getattr(request, "can_read_body", False) else {}
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    domain = str(body.get("domain") or "").strip()
    if not domain:
        return web.json_response({"ok": False, "error": "domain required"}, status=400)

    argv = ["navig", "vault", "login", "capture", domain, "--capture-only", "--json"]
    for opt, key in (
        ("--username", "username"),
        ("--idp", "idp"),
        ("--idp-user", "idp_user"),
        ("--url", "url"),
    ):
        if body.get(key):
            argv.extend([opt, str(body[key])])

    _capture_lock["running"] = True
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # This file also line-reads (the whisper runner) — the stream-limit
            # guard requires an explicit limit on EVERY spawn in it.
            limit=STREAM_LIMIT,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=_CAPTURE_TIMEOUT_S)
        except asyncio.TimeoutError:
            # wait_for cancels only communicate() — the headful capture browser
            # would keep running orphaned. Kill the child before surfacing.
            proc.kill()
            await proc.wait()
            raise
        data = _login_json_from_output(out) or {}
        if not data:
            errtext = (err or b"").decode("utf-8", "replace").strip()
            return web.json_response(
                {"ok": False, "error": errtext[-300:] or "capture failed"}, status=500
            )
        return web.json_response(
            {
                "ok": bool(data.get("ok")),
                "opened": bool(data.get("opened")),
                "domain": data.get("domain") or domain,
                "username": data.get("username"),
                "idp": data.get("idp"),
                "verified": data.get("verified"),
                "login_recorded": data.get("login_recorded"),
                "error": data.get("error"),
            }
        )
    except asyncio.TimeoutError:
        return web.json_response(
            {"ok": False, "error": "capture timed out — finish in the browser, then try again"},
            status=504,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("vault login capture route: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
    finally:
        _capture_lock["running"] = False


async def handle_deck_vault_login_remove(request: "web.Request") -> "web.Response":
    """POST {domain, username} — delete a stored website login and its session."""
    try:
        body = await request.json() if getattr(request, "can_read_body", False) else {}
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    domain = str(body.get("domain") or "").strip()
    username = str(body.get("username") or "").strip()
    if not domain or not username:
        return web.json_response(
            {"ok": False, "error": "domain and username required"}, status=400
        )
    try:
        from navig.vault import remove_login  # noqa: PLC0415
        from navig.vault.sessions import remove_session  # noqa: PLC0415

        removed = remove_login(domain, username)
        # The session may exist even when the login row does not (session-only
        # captures) — clear both so nothing keeps restoring after a delete.
        session_removed = remove_session(domain, username)
        return web.json_response(
            {"ok": bool(removed or session_removed), "removed": bool(removed)}
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Vault login remove error: %s", exc)
        return web.json_response({"ok": False, "error": str(exc)}, status=500)
