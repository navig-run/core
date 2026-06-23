#!/usr/bin/env python3
"""
NAVIG license signer — founder-only tool.

THIS FILE MUST NEVER SHIP IN ANY PUBLIC PACKAGE.

It lives under ``tools/`` which is excluded from the navig PyPI wheel via
the project's MANIFEST/pyproject ``packages`` discovery. The matching
private key lives at ``tools/license_keys/k0_dev.priv`` (gitignored).

Usage
-----
    # Generate a fresh keypair (first time only / rotation)
    python tools/license_sign.py keygen --key-id k1_prod

    # Sign a license for a buyer
    python tools/license_sign.py sign \\
        --key-id k1_prod \\
        --email customer@example.com \\
        --tier pro \\
        --billing annual \\
        --hosts 10 \\
        --capabilities core_ops,business_ops,ai_operator \\
        --subscription-until 2027-06-04 \\
        --perpetual-modules security_ops \\
        --output /tmp/customer-license.key

    # Verify a token (sanity check)
    python tools/license_sign.py verify --token "NAVIG-LICENSE-v1:..."

After signing, drop the signed token into the buyer's email + the
post-purchase /activate page. Never store signed tokens server-side longer
than the delivery window.
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import json
import sys
import uuid
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (
        Ed25519PrivateKey,
        Ed25519PublicKey,
    )
    from cryptography.hazmat.primitives import serialization
except ImportError:
    sys.exit("install: pip install cryptography")


_KEYS_DIR = Path(__file__).parent / "license_keys"
_TOKEN_PREFIX = "NAVIG-LICENSE-v1:"


def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _load_private(key_id: str) -> bytes:
    p = _KEYS_DIR / f"{key_id}.priv"
    if not p.is_file():
        sys.exit(f"private key not found: {p}\nrun: python tools/license_sign.py keygen --key-id {key_id}")
    return p.read_bytes()


def _sign(payload: dict, private_bytes: bytes) -> str:
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    payload_b64 = _b64url_encode(payload_bytes)
    sk = Ed25519PrivateKey.from_private_bytes(private_bytes)
    sig = sk.sign(payload_b64.encode("ascii"))
    return f"{_TOKEN_PREFIX}{payload_b64}.{_b64url_encode(sig)}"


def cmd_keygen(args: argparse.Namespace) -> None:
    _KEYS_DIR.mkdir(parents=True, exist_ok=True)
    sk = Ed25519PrivateKey.generate()
    pk = sk.public_key()
    sk_bytes = sk.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    pk_bytes = pk.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    priv_path = _KEYS_DIR / f"{args.key_id}.priv"
    pub_path = _KEYS_DIR / f"{args.key_id}.pub.hex"
    priv_path.write_bytes(sk_bytes)
    pub_path.write_text(pk_bytes.hex())
    try:
        import os, stat as _stat
        os.chmod(priv_path, _stat.S_IRUSR | _stat.S_IWUSR)
    except OSError:
        pass
    print(f"private key:  {priv_path}")
    print(f"public key hex (paste into navig/license/_public_keys.py under PUBLIC_KEYS):")
    print(f"  '{args.key_id}': bytes.fromhex(")
    print(f"      \"{pk_bytes.hex()}\"")
    print(f"  ),")


def cmd_sign(args: argparse.Namespace) -> None:
    if args.billing not in ("annual", "monthly", "one_time"):
        sys.exit("--billing must be one of: annual, monthly, one_time")

    if args.billing == "one_time":
        subscription_until = None
    elif args.subscription_until:
        # Allow YYYY-MM-DD or full ISO.
        dt = _dt.datetime.fromisoformat(args.subscription_until)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        subscription_until = dt.isoformat()
    else:
        # Default: now + 1 year for annual / 1 month for monthly.
        now = _dt.datetime.now(tz=_dt.timezone.utc)
        delta = _dt.timedelta(days=365 if args.billing == "annual" else 31)
        subscription_until = (now + delta).isoformat()

    perpetual_modules = []
    if args.perpetual_modules:
        perpetual_modules = [m.strip() for m in args.perpetual_modules.split(",") if m.strip()]

    capabilities = []
    if args.capabilities:
        capabilities = [c.strip() for c in args.capabilities.split(",") if c.strip()]

    branding = None
    if args.branding:
        branding = json.loads(args.branding)

    payload = {
        "license_version": 1,
        "license_id": args.license_id or str(uuid.uuid4()),
        "tier": args.tier,
        "hosts": args.hosts,
        "capabilities": capabilities,
        "commercial_use": bool(args.commercial),
        "billing_period": args.billing,
        "issued_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "subscription_until": subscription_until,
        "entitlements_perpetual": (
            {"modules": perpetual_modules, "tier": args.perpetual_tier}
            if getattr(args, "perpetual_tier", "")
            else {"modules": perpetual_modules}
        ),
        "fallback_major": args.fallback_major or "v1",
        "branding": branding,
        "signature_key_id": args.key_id,
        "buyer_email": args.email or "",
    }

    token = _sign(payload, _load_private(args.key_id))
    if args.output:
        Path(args.output).write_text(token + "\n", encoding="utf-8")
        print(f"wrote: {args.output}")
    else:
        print(token)


def cmd_verify(args: argparse.Namespace) -> None:
    # Use the same verifier the daemon uses, against the bundled public keys.
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from navig.license.keys import verify_license

    status = verify_license(args.token)
    print(json.dumps(status.as_dict(), indent=2, default=str))


def main() -> None:
    ap = argparse.ArgumentParser(description="NAVIG license signer (founder-only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_keygen = sub.add_parser("keygen", help="generate a fresh Ed25519 keypair")
    p_keygen.add_argument("--key-id", required=True, help="e.g. k1_prod")
    p_keygen.set_defaults(func=cmd_keygen)

    p_sign = sub.add_parser("sign", help="sign a license token")
    p_sign.add_argument("--key-id", required=True)
    p_sign.add_argument("--email", default="")
    p_sign.add_argument("--license-id", default="")
    p_sign.add_argument("--tier", required=True,
                        choices=["free", "plus", "max", "team", "enterprise",
                                 "solo", "personal", "pro", "business", "fleet"])
    p_sign.add_argument("--billing", required=True,
                        choices=["annual", "monthly", "one_time"])
    p_sign.add_argument("--hosts", type=int, default=0,
                        help="(informational; the verifier uses tier→host_limit)")
    p_sign.add_argument("--capabilities", default="",
                        help="comma-separated module list (informational)")
    p_sign.add_argument("--subscription-until", default="",
                        help="ISO date or datetime; default = now + 1yr (annual) / 1mo (monthly)")
    p_sign.add_argument("--perpetual-modules", default="",
                        help="comma-separated modules the buyer owns forever (e.g. ai_operator,security_ops)")
    p_sign.add_argument("--perpetual-tier", default="",
                        choices=["", "personal", "pro", "business", "fleet", "enterprise"],
                        help="Perpetual fallback tier — the verifier drops to this on subscription lapse. "
                             "Set when a prior one-time tier purchase should survive a later sub.")
    p_sign.add_argument("--commercial", action="store_true")
    p_sign.add_argument("--fallback-major", default="v1")
    p_sign.add_argument("--branding", default="",
                        help="JSON blob: {\"logo_url\":\"...\",\"product_name\":\"...\"}")
    p_sign.add_argument("--output", default="", help="write token to file instead of stdout")
    p_sign.set_defaults(func=cmd_sign)

    p_verify = sub.add_parser("verify", help="verify a token against bundled public keys")
    p_verify.add_argument("--token", required=True)
    p_verify.set_defaults(func=cmd_verify)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
