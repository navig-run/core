"""
OsDeviceStore — phones and browsers paired to drive NAVIG OS remotely.

Remote NAVIG OS (os.navig.run → the user's Lighthouse edge → this brain → the NAVIG OS
server) authenticates each device with its OWN token, never with ``deck.api_key``: the
deck key is also the Telegram bot's address, so handing it to a phone would make "lose
the phone" mean "re-key the bot". A device token is 256-bit, stored here only as its
sha256, and revoked one device at a time.

A device is born from a pairing code: the desktop mints one (local requests only), shows
it as a QR, and the phone redeems it once through the edge. Codes are 128-bit, single
use, and expire in two minutes; only their hash is stored either.

A device can also be born from an ACCESS REQUEST: someone signed in to the navig.run
account this computer is linked to asks from a new browser, and the owner approves it
here or in Telegram. The request holds no key. Approval only flips its status; the
device and its token are created when the waiting browser next polls with the request's
secret (itself stored only as a hash), so the token exists in exactly one place — the
reply to that poll.

A device is one of two KINDS. ``remote`` is a phone or browser driving this computer's
NAVIG OS through the relay (it can only speak to the NAVIG OS server, through
``navig.cloud.os_relay``). ``brain`` is ANOTHER NAVIG OS — a second machine — that uses
this computer as its brain: its token is accepted by the deck API itself, through the
relay (``navig.gateway.deck.auth``). A code minted for one kind can never be redeemed as
the other, and a token of one kind never authenticates as the other, so pairing a phone
can never hand out deck access by accident.

Usage is counted per device per day, because Cloudflare bills the messages this channel
pushes into the user's Durable Object, and the budget has to be measured to be kept.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from navig.store.base import BaseStore, _utcnow

PAIR_CODE_TTL_S = 120
# A brain-link code is typed or pasted on ANOTHER machine, not scanned next to the
# screen showing it, so it lives longer than a QR code does.
LINK_CODE_TTL_S = 600
DEVICE_KINDS = ("remote", "brain")
# An access request waits this long for the owner. Long enough to walk to the computer
# or find the Telegram message; short enough that a forgotten one does not linger.
ACCESS_REQUEST_TTL_S = 600
MAX_PENDING_REQUESTS = 5
# Outstanding codes at once. The desktop dialog holds one; a few spare cover a user who
# opens it twice. Minting past this drops the oldest rather than refusing.
MAX_OPEN_CODES = 5
MAX_DEVICE_NAME = 80


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _today() -> str:
    """The usage day, in UTC on purpose: the budget meters Cloudflare's daily allowance,
    which resets at 00:00 UTC. Written and read only by this store."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class OsDeviceStore(BaseStore):
    SCHEMA_VERSION = 3
    PRAGMAS = {"cache_size": -1000}

    def __init__(self, db_path: Path | None = None):
        if db_path is None:
            db_path = _default_db_path()
        super().__init__(db_path)

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS devices (
                id          TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                token_hash  TEXT NOT NULL UNIQUE,
                ua          TEXT NOT NULL DEFAULT '',
                created_at  TEXT NOT NULL,
                last_seen   TEXT,
                revoked_at  TEXT,
                kind        TEXT NOT NULL DEFAULT 'remote'
            );
            CREATE TABLE IF NOT EXISTS pair_codes (
                code_hash   TEXT PRIMARY KEY,
                expires     REAL NOT NULL,
                created_at  TEXT NOT NULL,
                kind        TEXT NOT NULL DEFAULT 'remote'
            );
            CREATE TABLE IF NOT EXISTS device_usage (
                device_id   TEXT NOT NULL,
                day         TEXT NOT NULL,
                msgs_in     INTEGER NOT NULL DEFAULT 0,
                msgs_out    INTEGER NOT NULL DEFAULT 0,
                bytes_in    INTEGER NOT NULL DEFAULT 0,
                bytes_out   INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (device_id, day)
            );
            -- v2: sign-in from a new device, waiting for the owner. status:
            -- pending → approved → done (device minted), or denied / expired.
            CREATE TABLE IF NOT EXISTS access_requests (
                id           TEXT PRIMARY KEY,
                secret_hash  TEXT NOT NULL,
                ticket_nonce TEXT NOT NULL UNIQUE,
                email        TEXT NOT NULL,
                name         TEXT NOT NULL,
                ua           TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'pending',
                device_id    TEXT,
                created_at   TEXT NOT NULL,
                expires      REAL NOT NULL,
                decided_by   TEXT
            );
        """)

    def _migrate(self, conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
        # v2 only adds access_requests, which _create_schema creates IF NOT EXISTS on
        # every open — so an existing v1 database already has it by the time we get here.
        # v3 adds `kind` to devices and pair_codes. Every row that exists before v3 was
        # minted by the phone flow, so the column default ('remote') is the truth for it.
        for table in ("devices", "pair_codes"):
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "kind" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN kind TEXT NOT NULL DEFAULT 'remote'")

    # ── Pairing codes ─────────────────────────────────────────

    def mint_pair_code(self, *, ttl_s: float = PAIR_CODE_TTL_S, kind: str = "remote") -> tuple[str, float]:
        """A fresh single-use code and its expiry (epoch seconds). Only the hash is kept."""
        _check_kind(kind)
        now = time.time()
        code = secrets.token_urlsafe(16)  # 128 bits
        expires = now + ttl_s
        self._write("DELETE FROM pair_codes WHERE expires <= ?", (now,))
        self._write(
            "INSERT INTO pair_codes (code_hash, expires, created_at, kind) VALUES (?, ?, ?, ?)",
            (_sha256(code), expires, _utcnow(), kind),
        )
        # Per kind: a QR dialog opened five times must not push out a link code the owner
        # is still typing on the other machine.
        self._write(
            """DELETE FROM pair_codes WHERE code_hash IN (
                   SELECT code_hash FROM pair_codes WHERE kind = ?
                   ORDER BY expires DESC LIMIT -1 OFFSET ?
               )""",
            (kind, MAX_OPEN_CODES),
        )
        return code, expires

    def take_pair_code(self, code: str, *, kind: str = "remote") -> bool:
        """Consume *code* if it is live AND was minted for *kind*. True exactly once per
        code, never after expiry. A code presented as the wrong kind is left untouched."""
        _check_kind(kind)
        if not code or len(code) > 64:
            return False
        cur = self._write(
            "DELETE FROM pair_codes WHERE code_hash = ? AND expires > ? AND kind = ?",
            (_sha256(code), time.time(), kind),
        )
        return cur.rowcount == 1

    def cancel_pair_code(self, code: str) -> None:
        """The desktop closed the QR dialog: the code it showed must stop working now."""
        if code:
            self._write("DELETE FROM pair_codes WHERE code_hash = ?", (_sha256(code),))

    # ── Access requests (sign-in from a new device) ───────────

    def add_access_request(self, *, email: str, name: str, ua: str, ticket_nonce: str) -> tuple[str, str] | None:
        """Record a request; returns ``(request_id, poll_secret)``.

        ``None`` when *ticket_nonce* was already used: one navig.run ticket buys one request.
        """
        now = time.time()
        self._write(
            "UPDATE access_requests SET status = 'expired' WHERE status IN ('pending', 'approved') AND expires <= ?",
            (now,),
        )
        request_id = uuid.uuid4().hex[:12]
        secret = secrets.token_urlsafe(32)
        try:
            self._write(
                """INSERT INTO access_requests
                       (id, secret_hash, ticket_nonce, email, name, ua, status, created_at, expires)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
                (
                    request_id,
                    _sha256(secret),
                    ticket_nonce,
                    email.strip().lower(),
                    (name or "Unnamed device").strip()[:MAX_DEVICE_NAME],
                    (ua or "")[:200],
                    _utcnow(),
                    now + ACCESS_REQUEST_TTL_S,
                ),
            )
        except sqlite3.IntegrityError:
            return None
        # Keep only the newest few waiting: a flood of sign-ins cannot bury the real one
        # under hundreds of prompts, and cannot grow this table.
        self._write(
            """UPDATE access_requests SET status = 'expired' WHERE id IN (
                   SELECT id FROM access_requests WHERE status = 'pending'
                   ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?
               )""",
            (MAX_PENDING_REQUESTS,),
        )
        return request_id, secret

    def get_access_request(self, request_id: str) -> dict[str, Any] | None:
        row = self._read_one(
            "SELECT id, email, name, ua, status, device_id, created_at, expires, decided_by"
            " FROM access_requests WHERE id = ?",
            (request_id,),
        )
        if row is None:
            return None
        out = dict(row)
        if out["status"] in ("pending", "approved") and out["expires"] <= time.time():
            out["status"] = "expired"
        return out

    def list_pending_requests(self) -> list[dict[str, Any]]:
        rows = self._read_all(
            "SELECT id, email, name, ua, created_at, expires FROM access_requests"
            " WHERE status = 'pending' AND expires > ? ORDER BY created_at DESC",
            (time.time(),),
        )
        return [dict(r) for r in rows]

    def decide_access_request(self, request_id: str, *, approve: bool, by: str = "") -> bool:
        """Approve or deny a live pending request. True only for the decision that took."""
        cur = self._write(
            "UPDATE access_requests SET status = ?, decided_by = ?"
            " WHERE id = ? AND status = 'pending' AND expires > ?",
            ("approved" if approve else "denied", by[:40], request_id, time.time()),
        )
        return cur.rowcount == 1

    def poll_access_request(self, request_id: str, secret: str) -> dict[str, Any]:
        """What the waiting browser learns: its status, and — exactly once, right after
        approval — the new device and its token.

        Returns ``{"status": "unknown"}`` for a wrong id OR a wrong secret, so a guess
        learns nothing about which requests exist.
        """
        if not request_id or not secret or len(secret) > 128:
            return {"status": "unknown"}
        row = self._read_one(
            "SELECT id, name, ua, status, expires, device_id FROM access_requests"
            " WHERE id = ? AND secret_hash = ?",
            (request_id, _sha256(secret)),
        )
        if row is None:
            return {"status": "unknown"}
        status = row["status"]
        if status in ("pending", "approved") and row["expires"] <= time.time():
            return {"status": "expired"}
        if status != "approved":
            # 'done' means the token was already handed out; it is never shown twice.
            return {"status": "used" if status == "done" else status}
        # Claim the approved request first (one row, one winner), then mint.
        claimed = self._write(
            "UPDATE access_requests SET status = 'done' WHERE id = ? AND status = 'approved'",
            (request_id,),
        )
        if claimed.rowcount != 1:
            return {"status": "used"}
        device_id, token = self.add_device(name=row["name"], ua=row["ua"])
        self._write("UPDATE access_requests SET device_id = ? WHERE id = ?", (device_id, request_id))
        return {"status": "approved", "deviceId": device_id, "deviceToken": token, "name": row["name"]}

    # ── Devices ───────────────────────────────────────────────

    def add_device(self, *, name: str, ua: str = "", kind: str = "remote") -> tuple[str, str]:
        """Register a device; returns ``(device_id, token)``. The token is never stored."""
        _check_kind(kind)
        device_id = uuid.uuid4().hex[:12]
        token = secrets.token_urlsafe(32)  # 256 bits
        self._write(
            "INSERT INTO devices (id, name, token_hash, ua, created_at, last_seen, kind)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                device_id,
                (name or "Unnamed device").strip()[:MAX_DEVICE_NAME],
                _sha256(token),
                (ua or "")[:200],
                _utcnow(),
                _utcnow(),
                kind,
            ),
        )
        return device_id, token

    def device_for_token(self, token: str, *, kind: str = "remote") -> dict[str, Any] | None:
        """The live device of *kind* that holds *token*, or None (unknown, revoked, or a
        token of the other kind — a phone's token is never a brain link, and back)."""
        _check_kind(kind)
        if not token or len(token) > 128:
            return None
        row = self._read_one(
            "SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL AND kind = ?",
            (_sha256(token), kind),
        )
        return dict(row) if row else None

    def get(self, device_id: str) -> dict[str, Any] | None:
        row = self._read_one("SELECT * FROM devices WHERE id = ?", (device_id,))
        return dict(row) if row else None

    def list_devices(self, *, include_revoked: bool = False) -> list[dict[str, Any]]:
        where = "" if include_revoked else "WHERE d.revoked_at IS NULL"
        rows = self._read_all(
            f"""SELECT d.id, d.name, d.ua, d.created_at, d.last_seen, d.revoked_at, d.kind,
                       COALESCE(u.msgs_in, 0) + COALESCE(u.msgs_out, 0) AS msgs_today,
                       COALESCE(u.bytes_in, 0) + COALESCE(u.bytes_out, 0) AS bytes_today
                FROM devices d
                LEFT JOIN device_usage u ON u.device_id = d.id AND u.day = ?
                {where}
                ORDER BY d.created_at DESC""",  # noqa: S608 — `where` is one of two literals
            (_today(),),
        )
        return [dict(r) for r in rows]

    def revoke(self, device_id: str) -> bool:
        cur = self._write(
            "UPDATE devices SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
            (_utcnow(), device_id),
        )
        return cur.rowcount == 1

    def touch(self, device_id: str) -> None:
        self._write("UPDATE devices SET last_seen = ? WHERE id = ?", (_utcnow(), device_id))

    # ── Usage ─────────────────────────────────────────────────

    def add_usage(
        self, device_id: str, *, msgs_in: int = 0, msgs_out: int = 0, bytes_in: int = 0, bytes_out: int = 0
    ) -> None:
        if not (msgs_in or msgs_out or bytes_in or bytes_out):
            return
        self._write(
            """INSERT INTO device_usage (device_id, day, msgs_in, msgs_out, bytes_in, bytes_out)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(device_id, day) DO UPDATE SET
                   msgs_in = msgs_in + excluded.msgs_in,
                   msgs_out = msgs_out + excluded.msgs_out,
                   bytes_in = bytes_in + excluded.bytes_in,
                   bytes_out = bytes_out + excluded.bytes_out""",
            (device_id, _today(), msgs_in, msgs_out, bytes_in, bytes_out),
        )

    def usage_today(self) -> dict[str, int]:
        """All devices, today (UTC): messages both ways and bytes both ways."""
        row = self._read_one(
            """SELECT COALESCE(SUM(msgs_in), 0) AS msgs_in, COALESCE(SUM(msgs_out), 0) AS msgs_out,
                      COALESCE(SUM(bytes_in), 0) AS bytes_in, COALESCE(SUM(bytes_out), 0) AS bytes_out
               FROM device_usage WHERE day = ?""",
            (_today(),),
        )
        return {k: int(row[k]) for k in ("msgs_in", "msgs_out", "bytes_in", "bytes_out")} if row else {
            "msgs_in": 0, "msgs_out": 0, "bytes_in": 0, "bytes_out": 0,
        }


def _check_kind(kind: str) -> None:
    if kind not in DEVICE_KINDS:
        raise ValueError(f"unknown device kind: {kind!r}")


# ── Singleton ─────────────────────────────────────────────────

_store: OsDeviceStore | None = None


def _default_db_path() -> Path:
    from navig.platform import paths

    return paths.data_dir() / "os_remote.db"


def get_os_device_store() -> OsDeviceStore:
    global _store
    if _store is None:
        _store = OsDeviceStore()
    return _store


def reset_os_device_store() -> None:
    """Tests only: drop the singleton so the next call re-reads NAVIG_DATA_DIR."""
    global _store
    if _store is not None:
        try:
            _store.close()
        except Exception:  # noqa: BLE001
            pass
    _store = None
