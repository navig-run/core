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
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets as _secrets
import time
from dataclasses import dataclass, field
from typing import Any

from navig.notify import prefs, store
from navig.notify.signal_presets import DEFAULT_CHANNELS, get_preset, preset_emoji
from navig.notify.types import PRIORITIES, TYPE_KEYS

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SECRET_PREFIX = "sk_sig_"
DEFAULT_TOLERANCE_S = 300
SIG_HEADER = "X-Navig-Signature"
TS_HEADER = "X-Navig-Timestamp"


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
    ``result.signature``.
    """
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
        signature=provided,
    )


# ── Store (NAVIG-specific) ────────────────────────────────────────────────────


def _row_to_dict(r: Any, *, mask: bool) -> dict[str, Any]:
    preset = r["preset"] if "preset" in r.keys() else None
    out = {
        "name": r["name"],
        "enabled": bool(r["enabled"]),
        "notify_type": r["notify_type"],
        "priority": r["priority"],
        "title_tmpl": r["title_tmpl"],
        "body_tmpl": r["body_tmpl"],
        "preset": preset,
        "emoji": preset_emoji(preset),
        "created_at": r["created_at"],
        "last_event_at": r["last_event_at"],
        "hit_count": r["hit_count"],
    }
    out["secret"] = mask_secret(r["secret"]) if mask else r["secret"]
    return out


def mask_secret(secret: str) -> str:
    if not secret:
        return ""
    return f"{secret[:10]}…{secret[-4:]}" if len(secret) > 16 else "sk_sig_…"


def _gen_secret() -> str:
    return _SECRET_PREFIX + _secrets.token_urlsafe(32)


def add_source(
    name: str,
    *,
    preset: str | None = None,
    notify_type: str | None = None,
    priority: str | None = None,
    title_tmpl: str | None = None,
    body_tmpl: str | None = None,
) -> dict[str, Any]:
    """Create a source. Returns the row WITH the full secret (shown once).

    A *preset* pre-fills priority + title/body templates (the caller can still
    override any of them). By default the source routes to its OWN matrix row
    ``signal:<name>`` (seeded under the Signals category) so it can be muted
    independently; pass ``notify_type`` to route into an existing category instead.
    """
    name = (name or "").strip().lower()
    if not _NAME_RE.match(name):
        raise ValueError(
            "name must be 2-64 chars, lowercase letters/digits/_/- (start alnum)"
        )

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
    secret = _gen_secret()
    c = store.conn()
    with c:
        c.execute(
            "INSERT INTO notify_signal_sources "
            "(name, secret, enabled, notify_type, priority, title_tmpl, body_tmpl, preset, created_at) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?)",
            (name, secret, routing_type, priority, title_tmpl, body_tmpl, preset, store.now_iso()),
        )
    # Seed the per-source matrix row so the deck shows a mutable toggle for it.
    if routing_type.startswith("signal:"):
        prefs.seed_type(routing_type, default_channels)
    row = get_source(name)
    assert row is not None
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


def rotate_secret(name: str) -> str:
    """Generate a new secret for *name*; returns the full secret (shown once)."""
    store.init_db()
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
    """Bump the hit counter + last-seen timestamp. Best-effort."""
    store.init_db()
    c = store.conn()
    with c:
        c.execute(
            "UPDATE notify_signal_sources "
            "SET hit_count = hit_count + 1, last_event_at = ? WHERE name = ?",
            (store.now_iso(), name),
        )
