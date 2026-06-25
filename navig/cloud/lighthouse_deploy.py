"""Deploy the Lighthouse Worker to the user's own Cloudflare account.

Pure-Python, **no Node / no wrangler**: navig ships a prebuilt Worker bundle
(``lighthouse_worker/worker.js`` — generated from ``navig-lighthouse`` via
``wrangler deploy --dry-run --outdir dist``) and uploads it through the
Cloudflare REST API using the user's API token. The only stateful binding is
the BrainSocket Durable Object (SQLite-backed, free-plan eligible), so a deploy
is a single script upload — no D1/Queue provisioning required.

Token scopes required (Account):
  • Workers Scripts: Edit   • Workers Durable Objects (implied by Scripts)
  • Account Settings / Workers subdomain: Read

The token never leaves the user's machine except to call api.cloudflare.com.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

CF_API = "https://api.cloudflare.com/client/v4"
WORKER_NAME_DEFAULT = "navig-lighthouse"
COMPAT_DATE = "2026-02-12"
COMPAT_FLAGS = ["nodejs_compat"]
_TIMEOUT = 60


class DeployError(RuntimeError):
    """Raised on any Cloudflare API failure, with a human-readable reason."""


@dataclass
class DeployResult:
    url: str
    account_id: str
    subdomain: str
    worker_name: str
    created: bool  # True on first deploy, False on redeploy


def bundle_path() -> Path:
    """Path to the prebuilt Worker bundle shipped inside the package."""
    return Path(__file__).resolve().parent / "lighthouse_worker" / "worker.js"


# ── low-level Cloudflare REST helpers ───────────────────────────────────────

def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _result(resp: requests.Response, what: str) -> Any:
    """Parse a Cloudflare envelope; raise DeployError with its messages."""
    try:
        data = resp.json()
    except ValueError:
        raise DeployError(f"{what}: HTTP {resp.status_code} (non-JSON response)") from None
    if not data.get("success", False):
        errs = data.get("errors") or []
        msg = "; ".join(str(e.get("message", e)) for e in errs) or f"HTTP {resp.status_code}"
        codes = {e.get("code") for e in errs if isinstance(e, dict)}
        # CF code 10000 / 9109 (and 401/403) on a Workers call = the token
        # authenticated but lacks the scope for this resource — almost always a
        # missing Workers Scripts or Account Settings permission.
        low = msg.lower()
        if (
            resp.status_code in (401, 403)
            or codes & {10000, 9109}
            or "authentication error" in low
            or "permission" in low
        ):
            raise DeployError(
                f"{what}: {msg}.\n"
                "Your Cloudflare token is missing a permission. Re-create it with the "
                "'Edit Cloudflare Workers' template (or a custom token with Account → "
                "Workers Scripts: Edit + Account Settings: Read), then re-run. "
                "Easiest: `navig lighthouse login` (browser, no token to create)."
            )
        raise DeployError(f"{what}: {msg}")
    return data.get("result")


def verify_token(token: str) -> None:
    """Validate the credential.

    Scoped **API tokens** validate via ``/user/tokens/verify``. **OAuth access
    tokens** (from ``navig lighthouse login``) are not API tokens, so that
    endpoint returns 401 for them — fall back to ``/accounts``, which accepts
    both kinds, before declaring the credential rejected.
    """
    resp = requests.get(f"{CF_API}/user/tokens/verify", headers=_headers(token), timeout=_TIMEOUT)
    if resp.status_code == 200:
        _result(resp, "verify token")
        return
    accts = requests.get(f"{CF_API}/accounts", headers=_headers(token), timeout=_TIMEOUT)
    if accts.status_code in (401, 403):
        raise DeployError(
            "Cloudflare credential rejected (401/403). If you ran `navig lighthouse login`, "
            "re-run it and complete the browser authorization; if you used a token, check its scopes."
        )
    _result(accts, "verify token")


def list_accounts(token: str) -> list[dict[str, Any]]:
    resp = requests.get(f"{CF_API}/accounts", headers=_headers(token), timeout=_TIMEOUT)
    return list(_result(resp, "list accounts") or [])


def resolve_account_id(token: str, account_id: str | None) -> str:
    if account_id:
        return account_id
    accounts = list_accounts(token)
    if not accounts:
        raise DeployError("No Cloudflare accounts visible to this token.")
    if len(accounts) > 1:
        names = ", ".join(f"{a.get('name')} ({a.get('id')})" for a in accounts)
        raise DeployError(
            f"Token has multiple accounts; pass --account-id. Available: {names}"
        )
    return str(accounts[0]["id"])


def get_workers_subdomain(token: str, account_id: str) -> str:
    resp = requests.get(
        f"{CF_API}/accounts/{account_id}/workers/subdomain",
        headers=_headers(token),
        timeout=_TIMEOUT,
    )
    result = _result(resp, "get workers.dev subdomain") or {}
    sub = (result.get("subdomain") or "").strip()
    if not sub:
        raise DeployError(
            "This Cloudflare account has no workers.dev subdomain yet. Register one "
            "once at dash.cloudflare.com → Workers & Pages → (choose a subdomain), "
            "then re-run deploy."
        )
    return sub


def script_exists(token: str, account_id: str, name: str) -> bool:
    resp = requests.get(
        f"{CF_API}/accounts/{account_id}/workers/scripts/{name}",
        headers=_headers(token),
        timeout=_TIMEOUT,
    )
    return resp.status_code == 200


def upload_worker(
    token: str, account_id: str, name: str, script: bytes, *, with_migration: bool
) -> None:
    metadata: dict[str, Any] = {
        "main_module": "index.js",
        "compatibility_date": COMPAT_DATE,
        "compatibility_flags": COMPAT_FLAGS,
        "bindings": [
            {
                "type": "durable_object_namespace",
                "name": "BRAIN",
                "class_name": "BrainSocket",
            }
        ],
    }
    if with_migration:
        # First deploy: declare the SQLite-backed DO class.
        metadata["migrations"] = {"new_tag": "v1", "new_sqlite_classes": ["BrainSocket"]}

    files = {
        "metadata": ("metadata.json", json.dumps(metadata), "application/json"),
        # The part filename must match main_module ("index.js").
        "index.js": ("index.js", script, "application/javascript+module"),
    }
    resp = requests.put(
        f"{CF_API}/accounts/{account_id}/workers/scripts/{name}",
        headers=_headers(token),
        files=files,
        timeout=_TIMEOUT,
    )
    _result(resp, "upload worker")


def enable_workers_dev(token: str, account_id: str, name: str) -> None:
    """Flip the workers.dev route on so the script is reachable at its URL."""
    resp = requests.post(
        f"{CF_API}/accounts/{account_id}/workers/scripts/{name}/subdomain",
        headers={**_headers(token), "Content-Type": "application/json"},
        data=json.dumps({"enabled": True}),
        timeout=_TIMEOUT,
    )
    _result(resp, "enable workers.dev route")


def delete_worker(token: str, account_id: str | None, name: str = WORKER_NAME_DEFAULT) -> bool:
    """Tear down the deployed Worker. Returns True if it existed."""
    acc = resolve_account_id(token, account_id)
    resp = requests.delete(
        f"{CF_API}/accounts/{acc}/workers/scripts/{name}",
        headers=_headers(token),
        timeout=_TIMEOUT,
    )
    if resp.status_code == 404:
        return False
    _result(resp, "delete worker")
    return True


# ── the one-shot deploy ──────────────────────────────────────────────────────

def deploy(
    *,
    token: str,
    account_id: str | None = None,
    worker_name: str = WORKER_NAME_DEFAULT,
    fresh: bool = False,
) -> DeployResult:
    """Deploy (or redeploy) the Lighthouse Worker; returns the workers.dev URL.

    Idempotent: on redeploy the DO migration is skipped (it already exists)
    unless ``fresh=True``. The whole thing is one script upload — no Node,
    no wrangler, no custom domain.
    """
    path = bundle_path()
    if not path.is_file():
        raise DeployError(
            f"Worker bundle missing at {path}. Rebuild it from navig-lighthouse "
            "(`wrangler deploy --dry-run --outdir dist`) and copy dist/index.js here."
        )
    script = path.read_bytes()

    verify_token(token)
    acc = resolve_account_id(token, account_id)
    subdomain = get_workers_subdomain(token, acc)
    exists = script_exists(token, acc, worker_name)
    upload_worker(token, acc, worker_name, script, with_migration=(fresh or not exists))
    enable_workers_dev(token, acc, worker_name)
    url = f"https://{worker_name}.{subdomain}.workers.dev"
    logger.info("Lighthouse deployed: %s (account=%s, new=%s)", url, acc, not exists)
    return DeployResult(
        url=url,
        account_id=acc,
        subdomain=subdomain,
        worker_name=worker_name,
        created=not exists,
    )
