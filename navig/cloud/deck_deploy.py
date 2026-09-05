"""Deploy the navig-deck static export to the user's own Cloudflare as a
**Workers Static Assets** site — pure-Python (no Node / no wrangler), mirroring
:mod:`navig.cloud.lighthouse_deploy`.

The deck is built (``next build`` → ``out/``) with Node, but the *upload* uses
the Cloudflare REST API and reuses the **same** Cloudflare credential as
Lighthouse (an API token, or the OAuth token from ``navig lighthouse login``) —
so no ``wrangler``, no separate ``wrangler login``, and no Pages scope (Workers
scope covers it).

Flow (Cloudflare Workers Assets upload):
  1. hash every file in ``out/`` → a manifest {path: {hash, size}}
  2. start an assets-upload-session → get a JWT + the buckets still missing
  3. upload each bucket (base64 multipart) with the session JWT
  4. PUT the Worker script (a tiny ASSETS-serving shim) attaching the assets
  5. enable the ``*.workers.dev`` route

Reuses verify_token / resolve_account_id / get_workers_subdomain /
enable_workers_dev / script_exists from lighthouse_deploy.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from navig.cloud.lighthouse_deploy import (
    _TIMEOUT,
    CF_API,
    COMPAT_DATE,
    DeployError,
    _headers,
    _result,
    enable_workers_dev,
    get_workers_subdomain,
    resolve_account_id,
    script_exists,
    verify_token,
)

logger = logging.getLogger(__name__)

DECK_WORKER_DEFAULT = "navig-deck"

# Files that configure the asset layer rather than being served by it.
# `_headers` is Cloudflare's header-rules format. Uploading it as an ordinary
# asset does NOT apply it -- it just publishes the security policy at
# `/_headers` as application/octet-stream, which is exactly what this deployment
# used to do: the deck shipped with no CSP, no HSTS and no X-Frame-Options at
# all, while the file sat in the repo looking authoritative. We parse it here
# and let the shim Worker apply the headers, so `_headers` stays the single
# source of truth and the policy is not published as a public file.
_ASSET_CONFIG_FILES = {"/_headers"}

# A minimal Worker whose only job is to serve the bound static assets, applying
# the `_headers` rules on the way out. The deck is a static export, so all
# routing is handled by the assets layer.
_ASSETS_WORKER_TEMPLATE = """const HEADER_RULES = __RULES__;

// Cloudflare's `_headers` matching: every rule whose pattern matches applies, in
// file order, so a later rule overrides an earlier one for the same header.
function headersFor(pathname) {
  const out = {};
  for (const [re, headers] of HEADER_RULES) {
    if (new RegExp(re).test(pathname)) Object.assign(out, headers);
  }
  return out;
}

export default {
  async fetch(request, env) {
    const res = await env.ASSETS.fetch(request);
    const extra = headersFor(new URL(request.url).pathname);
    const names = Object.keys(extra);
    if (names.length === 0) return res;
    const headers = new Headers(res.headers);
    for (const name of names) headers.set(name, extra[name]);
    return new Response(res.body, {
      status: res.status,
      statusText: res.statusText,
      headers,
    });
  },
};
"""


def _pattern_to_regex(pattern: str) -> str:
    """Cloudflare `_headers` path pattern -> an anchored JS regex source.

    Supports the forms this repo actually uses -- an exact path (`/connect`) and
    a `*` wildcard (`/*`, `/_next/static/*`). Every other character is escaped,
    so an unfamiliar pattern degrades to "matches only itself" rather than to
    "matches everything", which is the safe direction for a header rule.
    """
    out = ["^"]
    for ch in pattern:
        out.append(".*" if ch == "*" else re.escape(ch))
    out.append("$")
    return "".join(out)


def parse_headers_file(text: str) -> list[tuple[str, dict[str, str]]]:
    """Parse a Cloudflare `_headers` file into ``[(pattern, {name: value})]``.

    Format: a line starting with `/` opens a rule; indented `Name: value` lines
    belong to it. `#` comments and blank lines are ignored. A header line before
    any pattern, or a rule with no headers, is dropped rather than guessed at.
    """
    rules: list[tuple[str, dict[str, str]]] = []
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("/"):
            current = {}
            rules.append((line.split()[0], current))
            continue
        if current is None or ":" not in line:
            continue  # a header before any pattern has nothing to attach to
        name, _, value = line.partition(":")
        name, value = name.strip(), value.strip()
        if name and value:
            current[name] = value
    return [(pat, hdrs) for pat, hdrs in rules if hdrs]


def build_assets_worker(out_dir: Path) -> bytes:
    """The shim Worker, with this build's `_headers` rules compiled in."""
    src = out_dir / "_headers"
    rules: list[tuple[str, dict[str, str]]] = []
    if src.is_file():
        try:
            rules = parse_headers_file(src.read_text(encoding="utf-8", errors="replace"))
        except OSError as exc:  # unreadable is not the same as absent -- say so
            logger.warning("could not read %s: %r -- deploying without header rules", src, exc)
    compiled = [[_pattern_to_regex(pat), hdrs] for pat, hdrs in rules]
    return _ASSETS_WORKER_TEMPLATE.replace("__RULES__", json.dumps(compiled)).encode("utf-8")


# Cloudflare's asset manifest hash: hex SHA-256 of the file, first 32 chars.
_HASH_LEN = 32


@dataclass
class DeckDeployResult:
    url: str
    account_id: str
    created: bool
    files: int


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:_HASH_LEN]


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = "/" + p.relative_to(root).as_posix()
        if rel in _ASSET_CONFIG_FILES:
            # Configuration for the asset layer, not content. Uploading it would
            # publish the security policy at its own URL without applying it --
            # `build_assets_worker` compiles these rules into the Worker instead.
            continue
        yield rel, p


def build_manifest(out_dir: Path) -> tuple[dict[str, dict[str, Any]], dict[str, tuple[str, bytes, str]]]:
    """Return (manifest, files_by_hash).

    manifest: ``{ "/index.html": {"hash": ..., "size": ...}, ... }`` for the session.
    files_by_hash: ``{ hash: (path, bytes, content_type) }`` for the bucket uploads.
    """
    manifest: dict[str, dict[str, Any]] = {}
    files_by_hash: dict[str, tuple[str, bytes, str]] = {}
    for rel, p in _iter_files(out_dir):
        data = p.read_bytes()
        h = _hash_bytes(data)
        manifest[rel] = {"hash": h, "size": len(data)}
        ct = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        files_by_hash[h] = (rel, data, ct)
    return manifest, files_by_hash


def _start_upload_session(token: str, acc: str, name: str, manifest: dict) -> dict:
    resp = requests.post(
        f"{CF_API}/accounts/{acc}/workers/scripts/{name}/assets-upload-session",
        headers={**_headers(token), "Content-Type": "application/json"},
        data=json.dumps({"manifest": manifest}),
        timeout=_TIMEOUT,
    )
    return _result(resp, "start assets upload session") or {}


def _upload_buckets(
    acc: str, buckets: list[list[str]], files_by_hash: dict[str, tuple[str, bytes, str]], session_jwt: str
) -> str:
    """Upload each bucket of files (base64 multipart). Returns the completion JWT."""
    completion = session_jwt
    for bucket in buckets:
        files = {}
        for h in bucket:
            entry = files_by_hash.get(h)
            if not entry:
                raise DeployError(f"asset {h} requested by Cloudflare but not found locally")
            _path, data, ct = entry
            files[h] = (h, base64.b64encode(data), ct)
        resp = requests.post(
            f"{CF_API}/accounts/{acc}/workers/assets/upload?base64=true",
            headers={"Authorization": f"Bearer {session_jwt}"},
            files=files,
            timeout=_TIMEOUT * 3,
        )
        res = _result(resp, "upload assets bucket") or {}
        if isinstance(res, dict) and res.get("jwt"):
            completion = res["jwt"]
    return completion


def _put_assets_worker(
    token: str, acc: str, name: str, completion_jwt: str, *, script: bytes
) -> None:
    metadata = {
        "main_module": "index.js",
        "compatibility_date": COMPAT_DATE,
        "bindings": [{"type": "assets", "name": "ASSETS"}],
        "assets": {
            "jwt": completion_jwt,
            "config": {
                "html_handling": "auto-trailing-slash",
                "not_found_handling": "404-page",
                # Without this the asset layer answers matching requests DIRECTLY and
                # the Worker is never invoked — so the header rules compiled into it
                # applied to nothing anyone actually loads. Measured: a non-asset path
                # carried the full CSP while `/` carried no headers at all.
                "run_worker_first": True,
            },
        },
    }
    files = {
        "metadata": ("metadata.json", json.dumps(metadata), "application/json"),
        "index.js": ("index.js", script, "application/javascript+module"),
    }
    resp = requests.put(
        f"{CF_API}/accounts/{acc}/workers/scripts/{name}",
        headers=_headers(token),
        files=files,
        timeout=_TIMEOUT,
    )
    _result(resp, "deploy deck worker")


def deploy(
    out_dir: str | Path,
    *,
    token: str,
    account_id: str | None = None,
    worker_name: str = DECK_WORKER_DEFAULT,
) -> DeckDeployResult:
    """Upload ``out_dir`` as a Workers Static Assets site. Returns its URL."""
    out = Path(out_dir)
    if not out.is_dir():
        raise DeployError(f"build output not found: {out}")

    verify_token(token)
    acc = resolve_account_id(token, account_id)
    subdomain = get_workers_subdomain(token, acc)
    existed = script_exists(token, acc, worker_name)

    manifest, files_by_hash = build_manifest(out)
    if not manifest:
        raise DeployError(f"no files to deploy in {out}")

    session = _start_upload_session(token, acc, worker_name, manifest)
    jwt = session.get("jwt") or ""
    buckets = session.get("buckets") or []
    completion = _upload_buckets(acc, buckets, files_by_hash, jwt) if buckets else jwt
    if not completion:
        raise DeployError("Cloudflare did not return an assets completion token")

    _put_assets_worker(
        token, acc, worker_name, completion, script=build_assets_worker(out)
    )
    enable_workers_dev(token, acc, worker_name)

    return DeckDeployResult(
        url=f"https://{worker_name}.{subdomain}.workers.dev",
        account_id=acc,
        created=not existed,
        files=len(manifest),
    )
