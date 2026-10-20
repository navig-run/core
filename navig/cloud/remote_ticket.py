"""Verify a navig.run remote-access ticket — "this email is signed in, for this computer".

Signing in at os.navig.run on a new device ends with navig.run handing the page a ticket
for one of the account's computers. The page passes it to that computer through the
user's own relay; this module decides whether it is genuine. A genuine ticket still grants
nothing: it only lets the device ASK, and the owner approves or denies on the computer or
in Telegram (``navig.store.os_devices`` pending requests).

Format (mirrors ``services/api/src/lib/remote-access.ts``)::

    navig-rt1.<payload b64url>.<Ed25519 signature b64url>

The signature covers ``navig-remote-ticket/v1.`` + the payload text — a prefix no other
NAVIG signature uses, so a ticket can never be replayed as a license or the reverse. The
payload is JSON ``{v, kid, e, t, x, n}``: version, key id, email, target tenant, expiry
(unix seconds), nonce. The key is SEPARATE from the license keys, by design.
"""

from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass

TICKET_PREFIX = "navig-rt1."
SIGNED_PREFIX = b"navig-remote-ticket/v1."
MAX_TICKET_LEN = 2048
# Clock skew we tolerate between api.navig.run and this computer.
SKEW_S = 120

# key_id -> raw 32-byte Ed25519 public key. The private seed lives only in the
# api.navig.run Worker secret REMOTE_TICKET_KEY (and the owner's vault). Rotate by adding
# a new id here, shipping it, then switching REMOTE_TICKET_KEY_ID.
PUBLIC_KEYS: dict[str, bytes] = {
    "rt_prod_1": bytes.fromhex("beff79d3584da0bbf9f0fc5d4cc29521e6cbdc27da9b94c47620351fa8b3289a"),
}
REVOKED_KEY_IDS: frozenset[str] = frozenset()


class TicketError(ValueError):
    """Why a ticket was refused. ``code`` is stable; the message is for people."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Ticket:
    email: str
    tenant: str
    expires: int
    nonce: str
    key_id: str


def _b64url(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def verify_ticket(
    ticket: str,
    *,
    tenant: str,
    email: str,
    now: float | None = None,
    keys: dict[str, bytes] | None = None,
) -> Ticket:
    """Return the ticket's claims, or raise :class:`TicketError`.

    *tenant* is this computer's relay tenant and *email* the account it was linked to
    (``os.remote.account_email``). A ticket for another computer, or signed in as another
    email, is refused even though its signature is valid: navig.run is trusted to say who
    signed in, never to decide who gets in.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(ticket, str) or not ticket.startswith(TICKET_PREFIX) or len(ticket) > MAX_TICKET_LEN:
        raise TicketError("malformed", "That sign-in ticket is not valid.")
    body = ticket[len(TICKET_PREFIX):]
    parts = body.split(".")
    if len(parts) != 2:
        raise TicketError("malformed", "That sign-in ticket is not valid.")
    payload_b64, sig_b64 = parts
    try:
        payload = json.loads(_b64url(payload_b64).decode("utf-8"))
        sig = _b64url(sig_b64)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        raise TicketError("malformed", "That sign-in ticket is not valid.") from None
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise TicketError("malformed", "That sign-in ticket is not valid.")

    kid = str(payload.get("kid") or "")
    table = PUBLIC_KEYS if keys is None else keys
    raw = table.get(kid)
    if raw is None or kid in REVOKED_KEY_IDS:
        raise TicketError("unknown_key", "That sign-in ticket was signed by a key this NAVIG does not trust. Update NAVIG.")
    try:
        Ed25519PublicKey.from_public_bytes(raw).verify(sig, SIGNED_PREFIX + payload_b64.encode("ascii"))
    except (InvalidSignature, ValueError):
        raise TicketError("bad_signature", "That sign-in ticket is not genuine.") from None

    claims_email = str(payload.get("e") or "").strip().lower()
    claims_tenant = str(payload.get("t") or "")
    try:
        expires = int(payload.get("x"))
    except (TypeError, ValueError):
        raise TicketError("malformed", "That sign-in ticket is not valid.") from None
    current = time.time() if now is None else now
    if expires + SKEW_S < current:
        raise TicketError("expired", "That sign-in took too long. Sign in again.")
    if claims_tenant != tenant:
        raise TicketError("wrong_computer", "That sign-in was for a different computer.")
    want = (email or "").strip().lower()
    if not want:
        raise TicketError("not_linked", "This computer is not linked to a NAVIG account.")
    if claims_email != want:
        raise TicketError("wrong_account", "This computer is linked to a different NAVIG account.")
    return Ticket(email=claims_email, tenant=claims_tenant, expires=expires, nonce=str(payload.get("n") or ""), key_id=kid)
