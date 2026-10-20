"""Signals — inbound public ingest sources.

A *signal source* is one HMAC-signed endpoint your own website/backend fires
events at (``POST /api/ingest/<name>``). Each source owns a symmetric secret; an
event is mapped onto a notification type/priority (optionally via title/body
templates) and handed to the existing ``notify.dispatch`` fan-out, so it lands in
the deck + every channel you left enabled for that type in Settings → Notifications.

Two layers live here, kept deliberately separate so the lower one can later be
lifted into a standalone product (a hosted edge + dev SDK):

  * **Store** (``add_source``/``get_source``/… ) — NAVIG-specific, talks to notify.db.
  * **``verify_and_render``** — the *pure* core: stdlib-only (hmac/hashlib/json/time
    + ``str.format_map``), no notify/gateway/store imports. Given a source row, the
    request headers and the raw body it returns a fully-rendered dispatch tuple or
    a typed failure. The route handler owns the stateful replay LRU; the dispatch
    sink (``notify.dispatch``) is injected by the caller. That single seam is what
    makes this extractable.

Signing scheme (Stripe-style, replay-resistant):
    signed = f"{timestamp}.{utf8(body)}"
    X-Navig-Signature: sha256=<hex(hmac_sha256(secret, signed))>
    X-Navig-Timestamp: <unix seconds>
The timestamp is part of the MAC and must be within ``tolerance`` seconds of now.

A source may instead have ``provider="stripe"``: it is pasted into the Stripe
dashboard as a webhook endpoint and verified with Stripe's own ``whsec_`` scheme
(``navig.notify.stripe_events``) — zero code in the project that takes payments.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets as _secrets
import time
from dataclasses import dataclass, field
from typing import Any

from navig.notify import prefs, store, stripe_events
from navig.notify.signal_presets import DEFAULT_CHANNELS, get_preset, preset_emoji
from navig.notify.types import PRIORITIES, TYPE_KEYS

logger = logging.getLogger("navig.notify")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_SECRET_PREFIX = "sk_sig_"
DEFAULT_TOLERANCE_S = 300
SIG_HEADER = "X-Navig-Signature"
TS_HEADER = "X-Navig-Timestamp"
PROVIDERS = ("stripe",)  # None = NAVIG HMAC signing
# Provider events that are not sales (refunds, disputes, failed payments) land
# here, so a source's own row stays "the ring for money coming in".
PROVIDER_ALERT_TYPE = "finance_alert"
_SEEN_RETENTION_DAYS = 7


# ── Pure core (no NAVIG imports beyond constants — safe to extract) ───────────


@dataclass
class IngestResult:
    """Outcome of verifying + rendering one inbound event."""

    ok: bool
    http_status: int = 200
    error: str = ""
    notify_type: str = "signal_event"
    title: str = ""
    body: str = ""
    priority: str = "normal"
    data: dict[str, Any] = field(default_factory=dict)
    signature: str = ""  # surfaced so the route can dedupe replays
    # Provider sources: a verified event we deliberately don't notify on (200).
    ignored: bool = False
    # Provider sources: the provider's own event id — the durable dedupe key.
    event_id: str = ""


class _SafeDict(dict):
    """format_map mapping that renders missing keys as empty (never KeyError)."""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return ""


def _header(headers: dict[str, str], name: str) -> str:
    target = name.lower()
    for k, v in headers.items():
        if k.lower() == target:
            return v or ""
    return ""


def render(tmpl: str | None, payload: dict[str, Any], default: str) -> str:
    """Render a user template against the payload; fall back to *default*."""
    if not tmpl:
        return default
    try:
        return tmpl.format_map(_SafeDict(payload))
    except Exception:  # noqa: BLE001 — never let a bad template break ingest
        return default


def render_event(
    source: dict[str, Any], payload: dict[str, Any]
) -> tuple[str, str, str, str, dict[str, Any]]:
    """Map a payload onto ``(notify_type, title, body, priority, data)``.

    Pure — shared by ``verify_and_render`` (real events) and the deck "test"
    action, so a test renders through the exact same path a real event does.
    """
    name = source.get("name", "signal")
    notify_type = source.get("notify_type") or "signal_event"
    priority = source.get("priority") or "normal"
    title = render(source.get("title_tmpl"), payload, default=f"Signal: {name}")
    body = render(source.get("body_tmpl"), payload, default=json.dumps(payload)[:500])
    return notify_type, title, body, priority, {"source": name, "payload": payload}


# Representative fields so a test renders any preset template nicely.
SAMPLE_PAYLOAD: dict[str, Any] = {
    "amount": "$42", "customer": "ada@example.com", "email": "ada@example.com",
    "name": "Ada Lovelace", "plan": "Pro", "message": "This is a Signals test.",
    "status": "ok", "service": "demo", "version": "v1.2.3", "title": "Test brief",
    "summary": "A sample context brief.", "event": "test", "url": "/checkout",
    "user": "ada", "reason": "test", "rating": "5", "subject": "Test ticket",
    "detail": "sample detail", "ip": "127.0.0.1", "metric": "signups", "job": "nightly",
    "item": "Sample item", "note": "",
}


def verify_and_render(
    source: dict[str, Any],
    headers: dict[str, str],
    body: bytes,
    *,
    now: float | None = None,
    tolerance: int = DEFAULT_TOLERANCE_S,
) -> IngestResult:
    """Verify the HMAC + timestamp and render the dispatch tuple. Pure & stateless.

    Returns an ``IngestResult`` — ``ok=False`` carries an HTTP status + reason. The
    caller is responsible for the unknown-source 404 and for replay-dedupe using
    ``result.signature`` (or ``result.event_id`` for a provider source).
    """
    if source.get("provider") == "stripe":
        return _verify_stripe(source, headers, body, now=now, tolerance=tolerance)

    secret = source.get("secret") or ""
    if not secret:
        return IngestResult(False, 500, "source misconfigured: no secret")

    sig = _header(headers, SIG_HEADER)
    ts = _header(headers, TS_HEADER)
    if not sig or not ts:
        return IngestResult(False, 401, "missing signature or timestamp")

    # Timestamp window (replay defence #1).
    try:
        ts_int = int(ts)
    except (TypeError, ValueError):
        return IngestResult(False, 401, "bad timestamp")
    if abs((time.time() if now is None else now) - ts_int) > tolerance:
        return IngestResult(False, 401, "timestamp out of tolerance")

    # Constant-time HMAC over "{ts}.{body}".
    provided = sig[len("sha256=") :] if sig.startswith("sha256=") else sig
    # A signature is hex. Reject a non-hex/non-ASCII value cleanly as 401 — else
    # hmac.compare_digest raises TypeError on non-ASCII and the route 500s.
    if not _HEX_RE.match(provided):
        return IngestResult(False, 401, "bad signature")
    signed = ts.encode() + b"." + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected.lower(), provided.lower()):
        return IngestResult(False, 401, "bad signature")

    try:
        payload = json.loads(body) if body else {}
        if not isinstance(payload, dict):
            payload = {"value": payload}
    except (ValueError, TypeError):
        return IngestResult(False, 400, "invalid JSON body")

    notify_type, title, body, priority, data = render_event(source, payload)
    return IngestResult(
        ok=True,
        notify_type=notify_type,
        title=title,
        body=body,
        priority=priority,
        data=data,
        # Canonical (lowercase) — the replay-dedupe key must NOT vary with the
        # attacker-controlled hex case (verification compares case-insensitively,
        # so re-casing the hex passes HMAC but would otherwise mint a fresh
        # _SEEN key, defeating replay defence #2).
        signature=provided.lower(),
    )


def render_provider_event(
    source: dict[str, Any], event: dict[str, Any]
) -> tuple[str, str, str, str, dict[str, Any]] | None:
    """Render a provider event the way ``render_event`` renders a payload; None = ignore."""
    rendered = stripe_events.render(event)
    if rendered is None:
        return None
    if rendered.kind == "sale":
        notify_type = source.get("notify_type") or "sale"
        # The source's priority governs its sales (e.g. "high" to always ring)…
        priority = source.get("priority") or rendered.priority
    else:
        # …but never an alert: a dispute stays high whatever the sales row says.
        notify_type = PROVIDER_ALERT_TYPE
        priority = rendered.priority
    data = {"source": source.get("name", "stripe"), **rendered.data}
    return notify_type, rendered.title, rendered.body, priority, data


def _verify_stripe(
    source: dict[str, Any],
    headers: dict[str, str],
    body: bytes,
    *,
    now: float | None,
    tolerance: int,
) -> IngestResult:
    verdict = stripe_events.verify(
        headers, body, source.get("secret") or "", now=now, tolerance=tolerance
    )
    if not verdict.ok:
        return IngestResult(False, verdict.http_status, verdict.error)
    event = verdict.event
    event_id = str(event.get("id") or "")
    rendered = render_provider_event(source, event)
    if rendered is None:
        return IngestResult(
            ok=True, ignored=True, event_id=event_id, data={"event_type": event.get("type")}
        )
    notify_type, title, text, priority, data = rendered
    return IngestResult(
        ok=True,
        notify_type=notify_type,
        title=title,
        body=text,
        priority=priority,
        data=data,
        event_id=event_id,
    )


def render_test(source: dict[str, Any]) -> tuple[str, str, str, str, dict[str, Any]]:
    """What a "Test" button sends: a sample shaped like the source's real events."""
    if source.get("provider") == "stripe":
        rendered = render_provider_event(source, stripe_events.sample_event())
        assert rendered is not None  # noqa: S101 — the sample is a paid checkout
        return rendered
    return render_event(source, SAMPLE_PAYLOAD)


# ── Store (NAVIG-specific) ────────────────────────────────────────────────────


def _row_to_dict(r: Any, *, mask: bool) -> dict[str, Any]:
    keys = r.keys()
    preset = r["preset"] if "preset" in keys else None
    provider = (r["provider"] if "provider" in keys else None) or None
    out = {
        "name": r["name"],
        "provider": provider,
        "enabled": bool(r["enabled"]),
        "notify_type": r["notify_type"],
        "priority": r["priority"],
        "title_tmpl": r["title_tmpl"],
        "body_tmpl": r["body_tmpl"],
        "preset": preset,
        "emoji": "💳" if provider == "stripe" else preset_emoji(preset),
        "created_at": r["created_at"],
        "last_event_at": r["last_event_at"],
        "hit_count": r["hit_count"],
    }
    out["secret"] = mask_secret(r["secret"]) if mask else r["secret"]
    return out


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:10]}…{secret[-4:]}" if len(secret) > 16 else f"{secret[:7]}…"


def _gen_secret() -> str:
    return _SECRET_PREFIX + _secrets.token_urlsafe(32)


def normalize_name(name: str) -> str:
    """The stored form of a source name; ``ValueError`` if it can't be one.

    Public so a caller that must act BEFORE creating the source (``signals
    connect`` creates a Stripe endpoint first) rejects a bad name up front.
    """
    name = (name or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError(
            "name must be 2-64 chars, lowercase letters/digits/_/- (start alnum)"
        )
    return name


def add_source(
    name: str,
    *,
    preset: str | None = None,
    notify_type: str | None = None,
    priority: str | None = None,
    title_tmpl: str | None = None,
    body_tmpl: str | None = None,
    provider: str | None = None,
    secret: str | None = None,
) -> dict[str, Any]:
    """Create a source. Returns the row WITH the full secret (shown once).

    ``provider="stripe"`` makes a Stripe webhook endpoint: *secret* is the
    ``whsec_…`` signing secret Stripe shows for that endpoint (Stripe mints it,
    not us), and sales route to the shared ``sale`` type unless *notify_type*
    says otherwise — so they ring exactly like Microsoft Store sales.

    A *preset* pre-fills priority + title/body templates (the caller can still
    override any of them). By default the source routes to its OWN matrix row
    ``signal:<name>`` (seeded under the Signals category) so it can be muted
    independently; pass ``notify_type`` to route into an existing category instead.
    """
    name = normalize_name(name)

    provider = (provider or "").strip().lower() or None
    if provider is not None and provider not in PROVIDERS:
        raise ValueError(f"unknown provider: {provider} (supported: {', '.join(PROVIDERS)})")
    if provider is not None:
        secret = _validate_provider_secret(provider, secret)
        if preset:
            raise ValueError("presets apply to NAVIG-signed sources, not provider endpoints")
        notify_type = notify_type or "sale"
    elif secret:
        raise ValueError("a secret can only be supplied for a provider source")

    p = get_preset(preset)
    if preset and p is None:
        raise ValueError(f"unknown preset: {preset}")
    if p is not None:
        priority = priority or p["priority"]
        title_tmpl = title_tmpl if title_tmpl is not None else p["title"]
        body_tmpl = body_tmpl if body_tmpl is not None else p["body"]
        default_channels = p.get("default_channels") or DEFAULT_CHANNELS
    else:
        default_channels = DEFAULT_CHANNELS
    priority = priority or "normal"
    if priority not in PRIORITIES:
        raise ValueError(f"unknown priority: {priority}")

    # Default: a dedicated per-source row so it's independently mutable. An
    # explicit notify_type routes into an existing category instead.
    routing_type = notify_type or f"signal:{name}"
    if not (routing_type in TYPE_KEYS or routing_type.startswith("signal:")):
        raise ValueError(f"unknown notification type: {routing_type}")

    store.init_db()
    if get_source(name) is not None:
        raise ValueError(f"signal source already exists: {name}")
    secret = secret or _gen_secret()
    c = store.conn()
    with c:
        c.execute(
            "INSERT INTO notify_signal_sources "
            "(name, secret, enabled, notify_type, priority, title_tmpl, body_tmpl, preset, "
            "provider, created_at) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?)",
            (
                name, secret, routing_type, priority, title_tmpl, body_tmpl, preset,
                provider, store.now_iso(),
            ),
        )
    # Seed the per-source matrix row so the deck shows a mutable toggle for it.
    if routing_type.startswith("signal:"):
        prefs.seed_type(routing_type, default_channels)
    row = get_source(name)
    assert row is not None  # noqa: S101 — type-narrowing invariant, not validation
    return row


def get_source(name: str) -> dict[str, Any] | None:
    """Full row INCLUDING the raw secret (used by the verifier). Not for UI."""
    store.init_db()
    r = store.conn().execute(
        "SELECT * FROM notify_signal_sources WHERE name = ?", (name,)
    ).fetchone()
    return _row_to_dict(r, mask=False) if r else None


def list_sources() -> list[dict[str, Any]]:
    """All sources with secrets MASKED — safe for CLI/UI."""
    store.init_db()
    rows = store.conn().execute(
        "SELECT * FROM notify_signal_sources ORDER BY created_at"
    ).fetchall()
    return [_row_to_dict(r, mask=True) for r in rows]


def dynamic_types() -> list[dict[str, Any]]:
    """Per-source matrix rows as NOTIFICATION_TYPES-shaped metadata for the deck,
    so each source renders its own mutable row under the Signals category."""
    out: list[dict[str, Any]] = []
    for s in list_sources():
        nt = str(s.get("notify_type", ""))
        if nt.startswith("signal:"):
            out.append({
                "key": nt,
                "label": s["name"],
                "category": "Signals",
                "default_channels": [],
                "emoji": s.get("emoji") or "📡",
            })
    return out


def remove_source(name: str) -> bool:
    store.init_db()
    existing = get_source(name)
    c = store.conn()
    with c:
        cur = c.execute("DELETE FROM notify_signal_sources WHERE name = ?", (name,))
        c.execute("DELETE FROM notify_signal_seen WHERE source = ?", (name,))
    # Drop the per-source matrix row too so it stops cluttering the matrix.
    if existing and str(existing.get("notify_type", "")).startswith("signal:"):
        prefs.delete_type(existing["notify_type"])
    return cur.rowcount > 0


def set_enabled(name: str, enabled: bool) -> bool:
    store.init_db()
    c = store.conn()
    with c:
        cur = c.execute(
            "UPDATE notify_signal_sources SET enabled = ? WHERE name = ?",
            (1 if enabled else 0, name),
        )
    return cur.rowcount > 0


def _validate_provider_secret(provider: str, secret: str | None) -> str:
    value = (secret or "").strip()
    if provider == "stripe" and not value.startswith(stripe_events.SECRET_PREFIX):
        raise ValueError(
            "a Stripe endpoint needs its signing secret (whsec_…) — Stripe Dashboard → "
            "Developers → Webhooks → your endpoint → Signing secret"
        )
    return value


def update_source(
    name: str,
    *,
    notify_type: str | None = None,
    priority: str | None = None,
    title_tmpl: str | None = None,
    body_tmpl: str | None = None,
) -> dict[str, Any]:
    """Edit a source in place. ``None`` leaves a field alone; ``""`` clears it.

    The name, provider and secret are not editable here: the name IS the URL
    (renaming would orphan every sender) and secrets change through
    ``rotate_secret``. ``notify_type=""`` routes back to the source's own row.
    Returns the masked row.
    """
    existing = get_source(name)
    if existing is None:
        raise ValueError(f"unknown signal source: {name}")
    fields: dict[str, Any] = {}
    if priority is not None:
        if priority not in PRIORITIES:
            raise ValueError(f"unknown priority: {priority}")
        fields["priority"] = priority
    if title_tmpl is not None:
        fields["title_tmpl"] = title_tmpl or None
    if body_tmpl is not None:
        fields["body_tmpl"] = body_tmpl or None
    old_type = str(existing.get("notify_type") or "")
    new_type = old_type
    if notify_type is not None:
        own_row = f"signal:{name}"
        new_type = notify_type or own_row
        if not (new_type in TYPE_KEYS or new_type == own_row):
            raise ValueError(f"unknown notification type: {new_type}")
        fields["notify_type"] = new_type
    if fields:
        assignments = ", ".join(f"{k} = ?" for k in fields)  # keys are the fixed set above
        c = store.conn()
        with c:
            c.execute(
                f"UPDATE notify_signal_sources SET {assignments} WHERE name = ?",  # noqa: S608
                (*fields.values(), name),
            )
    # Keep the per-source matrix row in step with where the source now routes.
    if new_type != old_type:
        if old_type.startswith("signal:"):
            prefs.delete_type(old_type)
        if new_type.startswith("signal:"):
            prefs.seed_type(new_type, DEFAULT_CHANNELS)
    return next(s for s in list_sources() if s["name"] == name)


def claim_event(source: str, event_id: str) -> bool:
    """Record a provider event as delivered; False = already seen (a redelivery).

    Durable (notify.db): a Stripe retry days later, or after a daemon restart,
    is still recognised. Pair with ``release_event`` when delivery then fails,
    so the provider's retry is let through instead of swallowed as a duplicate.
    """
    store.init_db()
    c = store.conn()
    with c:
        c.execute(
            "DELETE FROM notify_signal_seen WHERE seen_at < datetime('now', ?)",
            (f"-{_SEEN_RETENTION_DAYS} days",),
        )
        cur = c.execute(
            "INSERT OR IGNORE INTO notify_signal_seen (source, event_id, seen_at) "
            "VALUES (?, ?, datetime('now'))",
            (source, event_id),
        )
    return cur.rowcount > 0


def release_event(source: str, event_id: str) -> None:
    """Undo ``claim_event`` after a failed delivery. Best-effort — never raises."""
    try:
        c = store.conn()
        with c:
            c.execute(
                "DELETE FROM notify_signal_seen WHERE source = ? AND event_id = ?",
                (source, event_id),
            )
    except Exception:  # noqa: BLE001 — worst case the retry is acked as a duplicate
        logger.warning("could not release signal event %s/%s", source, event_id, exc_info=True)


def rotate_secret(name: str, new_secret: str | None = None) -> str:
    """Replace *name*'s secret; returns the full secret (shown once).

    A NAVIG-signed source gets a freshly generated secret. A provider source
    cannot — its provider mints the secret — so the caller passes the new one.
    """
    existing = get_source(name)
    if existing is None:
        raise ValueError(f"unknown signal source: {name}")
    provider = existing.get("provider")
    if provider:
        secret = _validate_provider_secret(provider, new_secret)
    elif new_secret:
        raise ValueError("a NAVIG-signed source gets a generated secret; don't pass one")
    else:
        secret = _gen_secret()
    c = store.conn()
    with c:
        cur = c.execute(
            "UPDATE notify_signal_sources SET secret = ? WHERE name = ?", (secret, name)
        )
    if cur.rowcount == 0:
        raise ValueError(f"unknown signal source: {name}")
    return secret


def record_hit(name: str) -> None:
    """Bump the hit counter + last-seen timestamp. Best-effort — never raises.

    Called on the ingest success path AFTER delivery, so a transient DB blip
    (an AV/backup lock on the UPDATE) must not turn a delivered event into a 500.
    """
    try:
        store.init_db()
        c = store.conn()
        with c:
            c.execute(
                "UPDATE notify_signal_sources "
                "SET hit_count = hit_count + 1, last_event_at = ? WHERE name = ?",
                (store.now_iso(), name),
            )
    except Exception:  # noqa: BLE001 — a stats blip must never fail an ingest
        logger.debug("record_hit failed for %s", name, exc_info=True)
