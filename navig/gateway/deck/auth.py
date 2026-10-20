"""
Deck API Authentication

Middleware and utilities for verifying Telegram WebApp initData.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import time
from typing import Any
from urllib.parse import unquote

from navig.core.coerce import coerce_bool, coerce_id_rejects, coerce_id_set, coerce_int

try:
    from aiohttp import web
except ImportError:
    web = None

logger = logging.getLogger(__name__)

# Coalesce the very chatty "local request → desktop bypass" debug line. Every
# Deck SPA poll hits auth, so logging each one floods --debug. Instead we count
# them and emit a single summary line at most once per window.
_LOCAL_BYPASS_WINDOW = 30.0  # seconds
_local_bypass_state: dict[str, float | int] = {"count": 0, "last_flush": 0.0}


def _log_local_bypass(origin: str) -> None:
    """Emit the desktop-bypass debug line at most once per window, with a count.

    First hit in a window logs immediately (so it's visible); subsequent hits
    are tallied and folded into the next window's summary as "(xN in last 30s)".
    """
    if not logger.isEnabledFor(logging.DEBUG):
        return
    now = time.monotonic()
    state = _local_bypass_state
    state["count"] = int(state["count"]) + 1
    if now - float(state["last_flush"]) >= _LOCAL_BYPASS_WINDOW:
        count = int(state["count"])
        suffix = f" (x{count} in last {int(_LOCAL_BYPASS_WINDOW)}s)" if count > 1 else ""
        logger.debug("Deck API: local request (origin=%r) → desktop bypass%s", origin, suffix)
        state["count"] = 0
        state["last_flush"] = now


def _coerce_user_ids(raw: Any) -> tuple[set[int], bool]:
    """Parse an allowed-users config value into ``(ids, was_configured)``.

    Delegates the parsing to :func:`navig.core.coerce.coerce_id_set` — the same
    helper the Telegram channel uses — so the deck and the bot can never disagree
    about which ids an operator listed. Rejected entries are reported here, because
    a dropped id is a person who cannot reach the deck.

    ``was_configured`` is the half that matters. The middleware treats an EMPTY
    allowlist as "no restriction", so a value that is present but yields no usable id
    (``allowed_users: "nobody"``) must not collapse into the same state as *unset* —
    that would turn a typo into an OPEN deck.
    """
    ids, configured = coerce_id_set(raw)
    rejected = coerce_id_rejects(raw)
    if rejected:
        logger.error(
            "Deck allowed_users: ignoring %d unparseable entr%s (%s) — those users "
            "will be DENIED. Telegram user ids are integers.",
            len(rejected),
            "y" if len(rejected) == 1 else "ies",
            ", ".join(rejected),
        )
    return ids, configured


_deck_config: dict[str, Any] = {
    "bot_token": "",
    "allowed_users": set(),
    # Whether an allowlist was CONFIGURED at all — distinct from it being empty.
    # An empty allowlist means "no restriction"; a configured-but-unusable one must
    # not silently mean the same thing.
    "allowed_users_configured": False,
    "require_auth": True,
    "dev_mode": False,
    "auth_max_age": 86400,
    "api_key": "",  # Bearer token accepted from desktop browser
    # Lock the Deck to the Telegram Mini App only: when true, ONLY valid Telegram
    # initData is accepted — the api_key Bearer, dev_mode header, and loopback
    # auto-bypass are all disabled (see _get_user_id).
    "telegram_only": False,
}


def configure_deck_auth(
    bot_token: str,
    allowed_users: list[int],
    require_auth: bool = True,
    dev_mode: bool = False,
    auth_max_age: int = 86400,
    api_key: str = "",
    telegram_only: bool = False,
) -> None:
    """Set the module-level auth config for Deck API."""
    _deck_config["bot_token"] = bot_token
    ids, configured = _coerce_user_ids(allowed_users)
    _deck_config["allowed_users"] = ids
    _deck_config["allowed_users_configured"] = configured
    _deck_config["require_auth"] = require_auth
    # coerce_bool: these arrive from config, where `navig config set` stores raw
    # STRINGS — and bool("false") is True, so a deck.dev_mode / deck.telegram_only
    # the operator disabled would silently stay ON. Both are security-relevant:
    # dev_mode gates a local auth bypass, telegram_only is the remote lockdown.
    # (require_auth is intentionally left raw — it's read in several other
    # subsystems too, so coercing it only here would make auth inconsistent.)
    _deck_config["dev_mode"] = coerce_bool(dev_mode, default=False)
    # coerce_int, not raw: `navig config set deck.auth_max_age 86400` stores the
    # STRING "86400", and validate_init_data does `time.time() - auth_date > max_age`
    # — `float > str` RAISES TypeError, which its `except` swallows into "not
    # valid". The result is not a wrong number; it is EVERY Mini App request
    # rejected as unauthorized, silently, the moment an operator tunes this key.
    # (Same class the coerce_bool lines above exist for.) Floor at 60s to match
    # the config schema's `ge=60`.
    _deck_config["auth_max_age"] = coerce_int(auth_max_age, 86400, minimum=60)
    _deck_config["api_key"] = api_key or ""
    _deck_config["telegram_only"] = coerce_bool(telegram_only, default=False)
    logger.info(
        "Deck auth configured: require_auth=%s, allowed_users=%d, dev_mode=%s, api_key=%s, telegram_only=%s",
        require_auth,
        len(_deck_config["allowed_users"]),
        dev_mode,
        "set" if api_key else "not set",
        telegram_only,
    )


def deck_telegram_only() -> bool:
    """True when the Deck is locked to the Telegram Mini App (no Bearer/loopback)."""
    return bool(_deck_config.get("telegram_only"))


def deck_bot_token() -> str:
    """The configured Telegram bot token used to validate initData."""
    return str(_deck_config.get("bot_token") or "")


def deck_auth_max_age() -> int:
    """Max age (seconds) accepted for Telegram initData auth_date."""
    return coerce_int(_deck_config.get("auth_max_age"), 86400, minimum=60)


# Fields EXCLUDED from the data-check-string for the BOT-TOKEN HMAC: `hash` only.
# ⚠ Do not add `signature`. Bot API 8.0 added it for the separate THIRD-PARTY
# Ed25519 check, whose string excludes "hash and signature" — but Telegram computes
# `hash` over every other field, `signature` included. Excluding it here (as #1567
# did, verified only against initData forged with the same wrong rule) rejects
# every genuine launch as "hash mismatch".
# Spec: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
_DCS_EXCLUDED_FIELDS = frozenset({"hash"})


def _parse_init_data(init_data: str) -> dict[str, str]:
    """Split initData the way the spec does: `&` pairs, first `=`, ONE percent-decode.

    `parse_qs` is subtly wrong here: it turns a literal `+` into a space, and the
    old code then ran `unquote` over the already-decoded value a second time, so
    any value holding `%` after the first decode was changed before hashing.
    """
    fields: dict[str, str] = {}
    for pair in init_data.split("&"):
        if not pair:
            continue
        key, _, value = pair.partition("=")
        fields.setdefault(unquote(key), unquote(value))
    return fields


def validate_init_data(
    init_data: str,
    bot_token: str,
    max_age: int = 86400,
    *,
    reason: list[str] | None = None,
) -> dict[str, Any] | None:
    """Validate a Telegram Mini App `initData` string; return the user payload or None.

    Every failure returns an identical None, which is exactly why a single wrong
    config value (a stale window, a rotated bot token) took a multi-agent
    investigation to diagnose instead of one log line. Pass a `reason` list to
    capture WHY validation failed — the caller folds it into its existing single
    "unauthorized" WARNING (no extra log volume). A reason is a FIXED label plus,
    for staleness, integer ages only — it MUST NEVER contain the initData string,
    the hash, or the bot token (repo rule: no secrets in logs, even at --debug).
    """

    def _reject(why: str) -> None:
        if reason is not None:
            reason.append(why)
        return None

    if not init_data or not bot_token:
        return _reject("no initData presented")

    # Defensive: this is where the whole class bites — `time.time() - auth_date >
    # max_age` raises TypeError if max_age is a str ("86400" from `navig config
    # set`), and the `except` below turns that into a silent "invalid". Coerce
    # here too so the validator is correct no matter how a caller sourced max_age.
    max_age = coerce_int(max_age, 86400, minimum=60)
    try:
        fields = _parse_init_data(init_data)
        received_hash = fields.get("hash")
        if not received_hash:
            return _reject("initData carried no hash")

        data_check_string = "\n".join(
            f"{key}={fields[key]}" for key in sorted(fields) if key not in _DCS_EXCLUDED_FIELDS
        )

        secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        computed_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(computed_hash, received_hash):
            # Deliberately does not say which — wrong bot token vs. a tampered
            # payload are indistinguishable here, and naming a guess misleads.
            return _reject("initData hash mismatch (wrong bot token, or tampered)")

        auth_date = int(fields.get("auth_date") or 0)
        age = int(time.time() - auth_date)
        if age > max_age:
            return _reject(f"initData stale (age={age}s > max={max_age}s) — reopen the Mini App")

        user_str = fields.get("user")
        user = json.loads(user_str) if user_str else None

        return {
            "user": user,
            "auth_date": auth_date,
            "valid": True,
        }
    except Exception as e:
        logger.debug("initData validation failed: %s", e)
        return _reject("initData malformed")


_DEV_BYPASS_SENTINEL = -1  # Negative = dev/localhost bypass; skips allowlist

_LOOPBACK_REMOTES = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}


def _forwarded_client_ip(request: "web.Request") -> str | None:
    """Real client IP when the request was relayed by a proxy/tunnel, else None.

    Cloudflare (edge + cloudflared tunnel) stamps the genuine remote address in
    CF-Connecting-IP / True-Client-IP and the client cannot override it. A
    generic/local reverse proxy (incl. the Next.js dev-server rewrite) uses
    X-Forwarded-For. None means no forwarding headers → a direct connection.
    """
    for h in ("CF-Connecting-IP", "True-Client-IP"):
        v = request.headers.get(h, "").strip()
        if v:
            return v
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()  # first hop = original client
    return None


# Set by navig/cloud/uplink.py on every request Lighthouse relays in. Any request
# carrying it came from outside this machine, whatever its socket address says.
RELAY_VIA_HEADER = "X-Navig-Via"


def _request_is_local(request: "web.Request") -> bool:
    """True only if the request genuinely originates from this machine.

    Accepts a direct loopback connection AND a *local* reverse proxy / Next dev
    rewrite (whose forwarded client IP is itself loopback). Rejects anything that
    arrived via the cloudflared tunnel: Cloudflare always stamps CF-Ray plus a
    public CF-Connecting-IP that the caller cannot strip, so tunneled traffic can
    never masquerade as local — even though cloudflared connects to the daemon
    from 127.0.0.1.
    """
    # Lighthouse replays remote requests into the gateway FROM 127.0.0.1, and the uplink
    # stamps every one of them. Check it first: it is the rule that does not depend on
    # which Cloudflare headers happened to survive the trip.
    if request.headers.get(RELAY_VIA_HEADER):
        return False
    # CF-Ray is only ever present on Cloudflare edge/tunnel traffic.
    if request.headers.get("CF-Ray"):
        return False
    fwd = _forwarded_client_ip(request)
    if fwd is not None:
        try:
            return ipaddress.ip_address(fwd).is_loopback
        except ValueError:
            return False
    remote = getattr(request, "remote", "") or ""
    return remote in _LOOPBACK_REMOTES


def is_local_request(request: "web.Request") -> bool:
    """Public name for :func:`_request_is_local`, for plugin routes that must never be
    reachable through the tunnel / Lighthouse / Mini App (e.g. navig-cabinet's routes,
    which list someone's ID and medical documents). Same rule, one implementation."""
    return _request_is_local(request)


def is_trusted_local_caller(request: "web.Request") -> bool:
    """Public name for :func:`_local_desktop_bypass`: a local request whose browser
    Origin (if any) is this machine's own UI or an installed extension — never a
    website. For routes outside ``/api/deck`` that must refuse a page's forged
    cross-origin POST (the gateway's ``/api/voice/*``)."""
    return _local_desktop_bypass(request)


def _local_desktop_bypass(request: "web.Request") -> bool:
    """True when a genuinely-local request qualifies for the desktop bypass.

    _request_is_local() has already excluded tunneled traffic (Cloudflare
    stamps CF-Ray + CF-Connecting-IP beyond the caller's control, and the
    Lighthouse worker forwards them), so any genuinely-local request is
    trusted here: a direct same-origin call from the desktop SPA / the OS
    app's server proxy (no Origin), a cross-origin call from the React dev
    server (Origin: http://localhost:7432), or that same call relayed by the
    Next dev-rewrite proxy (X-Forwarded-For: 127.0.0.1).

    The Origin allowlist blocks a malicious WEBSITE's fetch to localhost
    (which always carries its own http(s) origin). The user's own installed
    browser extension (NAVIG Dock / web bar) reads its license/entitlement
    over loopback — the browser sets `chrome-extension://<id>` /
    `moz-extension://<id>` only for genuine extension contexts.

    That origin proves *an* extension, not *which* one: any extension the user
    installed could otherwise call every deck route. Once the NAVIG Dock has paired
    (``navig.dock.pairing``), only the paired extension id is trusted; before the
    first pairing the old behaviour holds so an updated Dock can still pair itself.
    """
    if not _request_is_local(request):
        return False
    origin = request.headers.get("Origin", "")
    if origin.startswith("chrome-extension://") or origin.startswith("moz-extension://"):
        from navig.dock.pairing import extension_origin_trusted

        return extension_origin_trusted(origin, request.path)
    return (
        not origin
        or origin.startswith("http://localhost:")
        or origin.startswith("http://127.0.0.1:")
    )


def _get_user_id(request: "web.Request", bot_token: str = "") -> int | None:
    """Return the authenticated user_id, or None if not authenticated.

    Returns _DEV_BYPASS_SENTINEL (negative) for dev/localhost bypass requests
    so the auth middleware knows to skip the allowlist check.
    """
    token = bot_token or _deck_config["bot_token"]
    max_age = _deck_config["auth_max_age"]
    telegram_only = bool(_deck_config.get("telegram_only"))

    # Bearer token: accepted from desktop browser (api_key set in deck config).
    # Disabled in telegram_only mode — Telegram initData is then the ONLY credential.
    auth_header = request.headers.get("Authorization", "")
    if not telegram_only and auth_header.startswith("Bearer "):
        bearer = auth_header[7:].strip()
        configured_key = _deck_config.get("api_key", "")
        # Constant-time compare to avoid leaking the api_key byte-by-byte via
        # response timing (matches routes/common.py:require_bearer_auth).
        if bearer and configured_key and hmac.compare_digest(bearer, configured_key):
            logger.debug("Deck API: valid Bearer token (api_key match)")
            return _DEV_BYPASS_SENTINEL

    # Bearer = a BRAIN-LINK device token: another NAVIG OS the owner linked to this
    # computer (navig.store.os_devices, kind "brain"). Accepted even under
    # telegram_only, on purpose: that lock retires the deck KEY — one static secret
    # shared by every surface — as a remote credential. A link token is per machine,
    # minted only by a local-only action, revocable one machine at a time, and cut by
    # the remote-access switch below. It counts as the operator (like the key), and the
    # local-only routes still refuse it: it only ever arrives relayed.
    if auth_header.startswith("Bearer "):
        device_id = _brain_link_device(request, auth_header[7:].strip())
        if device_id:
            request["deck_brain_device"] = device_id
            return _DEV_BYPASS_SENTINEL

    init_data = request.headers.get("X-Telegram-Init-Data", "")
    if init_data and token:
        reasons: list[str] = []
        result = validate_init_data(init_data, token, max_age, reason=reasons)
        if result and result.get("user"):
            return result["user"]["id"]
        # Keep the specific reason for the middleware's one WARNING line. A valid
        # signature with no `user` field falls here too (rare) — say so.
        request["_deck_auth_reason"] = reasons[0] if reasons else "initData had no user"

    # Locked to the Telegram Mini App: valid initData above is the ONLY
    # *remote* credential — no Bearer api_key, no dev-header. Genuinely-local
    # requests keep the desktop bypass: the desktop OS app proxies to its own
    # daemon over plain loopback, so an absolute lock here would cut the
    # operator's own desktop off whenever the public deck is deployed
    # (`navig miniapp deploy` sets deck.telegram_only=true). Tunneled traffic
    # can never claim this path — see _local_desktop_bypass.
    if telegram_only:
        if trusted_local_desktop(request):
            _log_local_bypass(request.headers.get("Origin", ""))
            return _DEV_BYPASS_SENTINEL
        return None

    if _deck_config["dev_mode"]:
        user_header = request.headers.get("X-Telegram-User", "")
        if user_header.isdigit():
            return int(user_header)
        # dev_mode with no X-Telegram-User header → deny (return None → 401).
        # The loopback bypass below is intentionally skipped in dev_mode.

    # Auto-bypass for requests genuinely local to this machine — the desktop
    # user talking to their own daemon. SECURITY: must never fire for tunneled
    # traffic (cloudflared connects from 127.0.0.1 too) — _local_desktop_bypass
    # refuses any request carrying edge/proxy headers, forcing tunneled callers
    # to present a real Bearer api_key or Telegram initData.
    if trusted_local_desktop(request):
        _log_local_bypass(request.headers.get("Origin", ""))
        return _DEV_BYPASS_SENTINEL

    return None


# last_seen is written at most once a minute per device — not on every deck call.
_BRAIN_TOUCH_EVERY_S = 60.0
_brain_touched: dict[str, float] = {}


def _brain_link_device(request: "web.Request", bearer: str) -> str | None:
    """The id of the live ``brain`` device holding *bearer*, or None.

    None too when remote access is switched off (``os.remote.enabled``): the kill switch
    has to cut a linked NAVIG OS as well as a paired phone. Never raises — an unreadable
    store denies, it does not crash the auth middleware.
    """
    if not bearer or len(bearer) > 128:
        return None
    if not _remote_access_enabled(request):
        return None
    try:
        from navig.store.os_devices import get_os_device_store

        store = get_os_device_store()
        device = store.device_for_token(bearer, kind="brain")
        if device is None:
            return None
        now = time.monotonic()
        if now - _brain_touched.get(device["id"], -_BRAIN_TOUCH_EVERY_S) >= _BRAIN_TOUCH_EVERY_S:
            _brain_touched[device["id"]] = now
            store.touch(device["id"])
        return str(device["id"])
    except Exception:  # noqa: BLE001 — deny, never 500
        logger.warning("Deck API: brain-link token check failed", exc_info=True)
        return None


def _remote_access_enabled(request: "web.Request") -> bool:
    """The live kill switch: the relay's in-memory flag when the gateway has one (it is
    what ``PATCH /api/deck/os/remote`` flips), else the config value."""
    try:
        gw = request.app.get("gateway") or request.app.get("navig_gateway")
        cm = getattr(gw, "cloud_manager", None) if gw is not None else None
        relay = getattr(getattr(cm, "_uplink", None), "os_relay", None)
        if relay is not None:
            return bool(relay.enabled)
        from navig.cloud.manager import _os_remote_enabled

        return _os_remote_enabled()
    except Exception:  # noqa: BLE001 — unknown is not "on"
        return False


def trusted_local_desktop(request: "web.Request") -> bool:
    """The desktop exception, as ONE rule for every deck surface: a genuinely-local
    request (never tunneled / relayed — see :func:`_local_desktop_bypass`) outside
    dev_mode. It holds under ``telegram_only`` too, which locks out REMOTE
    credentials, not the operator's own desktop.

    The SSE route (``routes/events.py``) carried its own copy of the lockdown check
    without this exception, so with ``telegram_only`` on, the desktop app's event
    bridge got 401 on every reconnect and received no live event at all — the sale
    ring, module refreshes, everything — while every REST call worked.
    """
    return not _deck_config["dev_mode"] and _local_desktop_bypass(request)


if web:

    # Paths under /api/deck that bypass auth. The OAuth provider redirect lands
    # here with no bearer token; security is the unguessable PKCE `state` token
    # (validated against an in-memory pending entry created by /connect).
    _PUBLIC_DECK_PATHS = frozenset({
        "/api/deck/connectors/oauth/callback",
    })

    @web.middleware
    async def deck_auth_middleware(request: "web.Request", handler):
        path = request.path

        if not path.startswith("/api/deck"):
            return await handler(request)

        if request.method == "OPTIONS":
            return await handler(request)

        if path in _PUBLIC_DECK_PATHS:
            return await handler(request)

        user_id = _get_user_id(request)

        _CORS_HEADERS = {
            "Access-Control-Allow-Origin": "*",
            # Mirrors the preflight list. On THIS response (an error, never a
            # preflight) Allow-Methods is advisory, but a divergent copy of a
            # hand-maintained allow-list is exactly how the Mini App broke:
            # tests/gateway/test_cors_parity.py keeps every copy identical.
            "Access-Control-Allow-Methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, Authorization, X-Telegram-Init-Data, X-Telegram-User",
        }

        if user_id is None:
            # Name the reason (set by _get_user_id) so a recurring auth failure is
            # a one-line diagnosis, not the multi-agent hunt this class caused once.
            logger.warning(
                "Deck API unauthorized: %s (%s %s, origin=%s)",
                request.get("_deck_auth_reason", "no credential presented"),
                request.method,
                path,
                request.headers.get("Origin", "-"),
            )
            return web.json_response(
                {
                    "error": "unauthorized",
                    "detail": "Valid Telegram WebApp initData required",
                },
                status=401,
                headers=_CORS_HEADERS,
            )

        allowed = _deck_config["allowed_users"]
        require_auth = _deck_config["require_auth"]
        # Bypass: dev/localhost sentinel — skip the allowlist check entirely
        # `allowed_users_configured`, not `allowed`: an allowlist the operator set
        # but which parsed to nothing must DENY, not fall through to "no
        # restriction". See _coerce_user_ids.
        configured = _deck_config.get("allowed_users_configured", bool(allowed))
        if (
            user_id != _DEV_BYPASS_SENTINEL
            and require_auth
            and configured
            and user_id not in allowed
        ):
            logger.warning("Deck API forbidden: user %d not in allowed_users", user_id)
            return web.json_response(
                {"error": "forbidden", "detail": "User not authorized for Deck"},
                status=403,
                headers=_CORS_HEADERS,
            )

        request["deck_user_id"] = abs(user_id)  # store positive ID for handlers
        # `abs(-1)` is 1, so a handler cannot tell "the local desktop / a Bearer key"
        # from "Telegram user 1" by the id. Operator-only routes read this instead.
        request["deck_auth_local"] = user_id == _DEV_BYPASS_SENTINEL
        return await handler(request)
