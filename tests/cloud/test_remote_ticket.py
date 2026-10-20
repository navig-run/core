"""The navig.run sign-in ticket verifier — and that it agrees with the Worker that signs.

GOLDEN is a ticket minted by the REAL ``services/api/src/lib/remote-access.ts`` signer
(RFC 8032 test key 1, so the key is public knowledge and the vector reproducible). If the
Worker's byte layout and this verifier ever drift apart, this is the test that says so —
instead of every sign-in failing in production with "not genuine".
"""

from __future__ import annotations

import base64
import json

import pytest

from navig.cloud import remote_ticket
from navig.cloud.remote_ticket import TicketError, verify_ticket

# RFC 8032 §7.1 test 1: secret 9d61b1…7f60, public d75a98…511a.
TEST_PUB = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
TEST_SEED = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
KEYS = {"rt_test_1": TEST_PUB}
TENANT = "a" * 64
GOLDEN = (
    "navig-rt1.eyJlIjoib3duZXJAZXhhbXBsZS5jb20iLCJraWQiOiJydF90ZXN0XzEiLCJuIjoiZGE5Zjk1MjJmMDBiZmJiOTg4"
    "ZDk2NTZiIiwidCI6ImFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFhYWFh"
    "YWFhYWEiLCJ2IjoxLCJ4IjoxNzkwNzI4MzcyfQ.SAaw-tVVu68qsUtrz5Ay7H46ZJWG2XJLrEfPVD7kJ76rEWe_CRMw4ArYMv"
    "DIEeyg3-F3OO_SDIXXyLamGH5FDA"
)
MINTED_AT = 1790728072  # the Worker's clock when GOLDEN was signed (expiry = +300 s)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _sign(claims: dict, *, prefix: bytes = remote_ticket.SIGNED_PREFIX) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    payload = _b64(json.dumps(claims, separators=(",", ":"), sort_keys=True).encode())
    sig = Ed25519PrivateKey.from_private_bytes(TEST_SEED).sign(prefix + payload.encode())
    return f"{remote_ticket.TICKET_PREFIX}{payload}.{_b64(sig)}"


def _claims(**over) -> dict:
    return {"v": 1, "kid": "rt_test_1", "e": "owner@example.com", "t": TENANT, "x": MINTED_AT + 300, "n": "abc", **over}


def test_a_ticket_signed_by_the_worker_verifies_here() -> None:
    t = verify_ticket(GOLDEN, tenant=TENANT, email="Owner@Example.com", now=MINTED_AT, keys=KEYS)
    assert (t.email, t.tenant, t.key_id) == ("owner@example.com", TENANT, "rt_test_1")
    assert t.expires == MINTED_AT + 300


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"tenant": "b" * 64, "email": "owner@example.com"}, "wrong_computer"),
        ({"tenant": TENANT, "email": "someone@else.com"}, "wrong_account"),
        ({"tenant": TENANT, "email": ""}, "not_linked"),
    ],
)
def test_a_genuine_ticket_for_someone_else_is_refused(kwargs, code) -> None:
    with pytest.raises(TicketError) as err:
        verify_ticket(GOLDEN, now=MINTED_AT, keys=KEYS, **kwargs)
    assert err.value.code == code


def test_an_expired_ticket_is_refused_after_the_skew_allowance() -> None:
    verify_ticket(GOLDEN, tenant=TENANT, email="owner@example.com", now=MINTED_AT + 300 + 60, keys=KEYS)
    with pytest.raises(TicketError) as err:
        verify_ticket(
            GOLDEN, tenant=TENANT, email="owner@example.com",
            now=MINTED_AT + 300 + remote_ticket.SKEW_S + 1, keys=KEYS,
        )
    assert err.value.code == "expired"


def test_editing_the_payload_breaks_the_signature() -> None:
    body = GOLDEN[len(remote_ticket.TICKET_PREFIX):]
    payload, sig = body.split(".")
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    claims["e"] = "attacker@example.com"
    forged = f"{remote_ticket.TICKET_PREFIX}{_b64(json.dumps(claims).encode())}.{sig}"
    with pytest.raises(TicketError) as err:
        verify_ticket(forged, tenant=TENANT, email="attacker@example.com", now=MINTED_AT, keys=KEYS)
    assert err.value.code == "bad_signature"


def test_a_signature_without_the_domain_prefix_is_refused() -> None:
    """A signature over the bare payload — what any other Ed25519 use of the key would
    produce — must not pass for a ticket."""
    with pytest.raises(TicketError) as err:
        verify_ticket(_sign(_claims(), prefix=b""), tenant=TENANT, email="owner@example.com", now=MINTED_AT, keys=KEYS)
    assert err.value.code == "bad_signature"


def test_unknown_and_revoked_keys_are_refused(monkeypatch) -> None:
    with pytest.raises(TicketError) as err:
        verify_ticket(GOLDEN, tenant=TENANT, email="owner@example.com", now=MINTED_AT, keys={})
    assert err.value.code == "unknown_key"
    monkeypatch.setattr(remote_ticket, "REVOKED_KEY_IDS", frozenset({"rt_test_1"}))
    with pytest.raises(TicketError) as err:
        verify_ticket(GOLDEN, tenant=TENANT, email="owner@example.com", now=MINTED_AT, keys=KEYS)
    assert err.value.code == "unknown_key"


@pytest.mark.parametrize(
    "bad",
    ["", "navig-rt1.", "navig-rt1.a.b.c", "license.abc.def", "navig-rt1.!!!.???", "navig-rt1." + "a" * 3000],
)
def test_malformed_tickets_are_refused_not_crashed(bad) -> None:
    with pytest.raises(TicketError):
        verify_ticket(bad, tenant=TENANT, email="owner@example.com", now=MINTED_AT, keys=KEYS)


def test_the_shipped_trust_root_is_a_real_ed25519_key_and_not_a_license_key() -> None:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from navig.license._public_keys import PUBLIC_KEYS as LICENSE_KEYS

    assert remote_ticket.PUBLIC_KEYS, "no ticket key shipped: every sign-in would be refused"
    for raw in remote_ticket.PUBLIC_KEYS.values():
        Ed25519PublicKey.from_public_bytes(raw)
        assert raw not in LICENSE_KEYS.values(), "tickets must use a key of their own"
