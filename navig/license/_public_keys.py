"""
NAVIG license public keys — the trust root for offline license verification.

WHAT THIS IS
------------
A dictionary of ``key_id -> Ed25519 public key bytes`` that the daemon and
Deck use to verify the cryptographic signature on every license token.

The matching PRIVATE keys live ONLY on the founder's signing machine and
are NEVER shipped in any public package. Compromise of a private key is
handled by:

  1. Generating a new Ed25519 keypair (e.g. ``navig-license-sign rotate``).
  2. Adding the new public key here with a fresh key_id.
  3. Optionally adding the compromised key_id to ``REVOKED_KEY_IDS`` so the
     daemon refuses to validate ANY license signed by that key.
  4. Re-issuing licenses to legitimate customers under the new key.

The old key_id stays in this file until the next major Deck release so that
honest customers on flights / offline keep working with their existing keys.

ROTATION CADENCE
----------------
Plan to rotate every 24 months unless a compromise event forces sooner.

DEVELOPMENT KEY (k0_dev)
------------------------
The dev key below was generated for local testing. Real production keys
must be generated on the founder's signing machine; this file is updated
with the new public bytes before any production release.
"""

from __future__ import annotations

# Map of key_id -> 32-byte Ed25519 public key (raw bytes).
#
# The development key was generated with:
#   from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
#   from cryptography.hazmat.primitives import serialization
#   sk = Ed25519PrivateKey.generate()
#   pk = sk.public_key()
#   pk.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
#
# The matching dev private key lives at tools/license_keys/k0_dev.priv
# (gitignored). Replace before production.
PUBLIC_KEYS: dict[str, bytes] = {
    # Development / first-launch key. Generated 2026-06-04 on the founder's
    # local machine. Replace with `k1_prod` (or similar) before public sale.
    "k0_dev": bytes.fromhex(
        "83a44f0217c20d24720aa8f70b860fdca1f8067af64abd1cac89ca94d82da312"
    ),
    # Cloudflare Worker signing key. The matching private seed lives ONLY in
    # the navig-www Pages secret `LICENSE_SIGN_KEY` (Web Crypto Ed25519 signs
    # license tokens in-Worker — no founder VPS). Rotate by generating a new
    # keypair, adding it here with a fresh id, and updating the Worker secret.
    "k_cf_prod": bytes.fromhex(
        "8030146028338bd815b8cf54f72b07658b6edf486ee625d7a63955783fbd4643"
    ),
}

# Key IDs that have been revoked. Any license carrying signature_key_id in
# this set is rejected with reason="revoked". This is the leak-response
# lever — if a private key escapes, add its id here in the next release.
REVOKED_KEY_IDS: frozenset[str] = frozenset({
    # "k0_legacy",  # example: revoked on 2026-12-15 after laptop theft
})

# Individual licenses can also be revoked by license_id even if their
# signing key is still trusted. This is the per-license-leak lever.
REVOKED_LICENSE_IDS: frozenset[str] = frozenset({
    # "00000000-aaaa-bbbb-cccc-000000000001",  # example
})


def get_public_key(key_id: str) -> bytes | None:
    """Return the raw 32-byte Ed25519 public key for ``key_id``, or None."""
    return PUBLIC_KEYS.get(key_id)


def is_key_revoked(key_id: str) -> bool:
    return key_id in REVOKED_KEY_IDS


def is_license_revoked(license_id: str) -> bool:
    return license_id in REVOKED_LICENSE_IDS
