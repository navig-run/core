"""Point a project's Stripe account at a NAVIG Signals source — the endpoint, created for you.

``navig signals add --provider stripe`` asks you to create the webhook endpoint in
the Stripe dashboard and paste back its ``whsec_`` secret. When the project's own
Stripe key is on disk, NAVIG can do that step itself: list the account's
endpoints, reuse ours if it already exists, otherwise create it with the right
events — Stripe returns the signing secret only on create, so it is captured then.

The key can be on THIS machine (an env file in the project) or on a SERVER (the
production env file). For a server, this very file is shipped over SSH and run
there with ``python3``, so the account key never leaves the box: only the
endpoint's signing secret comes back, on one marked stdout line.

Stdlib only — no NAVIG imports — precisely so it can run on a host with no NAVIG.
Never prints a key or a secret; the caller stores the secret and prints ids.
"""

from __future__ import annotations

import base64
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

MARK = "NAVIGCONNECT "
DESCRIPTION = "NAVIG — sale notifications (Signals)"
_API = "https://api.stripe.com/v1/"
_LIVE = ("sk_live_", "rk_live_")


def read_env_key(path: str, var: str) -> str:
    """The value of ``VAR=`` in an env file (``export`` and quotes tolerated); '' if absent."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            s = line.strip()
            if s.startswith("export "):
                s = s[len("export ") :].lstrip()
            if s.startswith(var + "="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _api(key: str, method: str, path: str, form: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    req = urllib.request.Request(
        _API + path,
        data=urllib.parse.urlencode(form).encode() if form else None,
        method=method,
        headers={"Authorization": "Basic " + base64.b64encode((key + ":").encode()).decode()},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        try:
            msg = (json.load(exc).get("error") or {}).get("message", "")
        except (ValueError, OSError):
            msg = ""
        return {"error": {"message": f"Stripe HTTP {exc.code} {msg}".strip()}}
    except (urllib.error.URLError, OSError) as exc:
        return {"error": {"message": f"cannot reach Stripe: {exc}"}}


def connect(
    env_path: str,
    var: str,
    url: str,
    events: list[str],
    *,
    description: str = DESCRIPTION,
    allow_test: bool = False,
) -> dict[str, Any]:
    """Reuse or create the Stripe endpoint for *url*; returns a result dict.

    ``status`` is ``created`` (carries ``secret``), ``exists`` (no secret — Stripe
    shows it only on create) or ``error``. Ids are safe to print; ``secret`` is not.
    """
    try:
        key = read_env_key(env_path, var)
    except OSError as exc:
        return {"status": "error", "error": f"cannot read {env_path}: {exc.strerror or exc}"}
    if not key:
        return {"status": "error", "error": f"{var} is not set in {env_path}"}
    if not key.startswith(_LIVE) and not (allow_test and key.startswith(("sk_test_", "rk_test_"))):
        return {"status": "error", "error": f"{var} in {env_path} is not a live Stripe key"}

    account = _api(key, "GET", "account")
    if "error" in account:
        return {"status": "error", "error": account["error"]["message"]}
    listing = _api(key, "GET", "webhook_endpoints?limit=100")
    if "error" in listing:
        return {"status": "error", "error": listing["error"]["message"]}
    base = {"account_id": account.get("id", ""), "account_name": _account_name(account)}
    for ep in listing.get("data") or []:
        if ep.get("url") == url:
            return {**base, "status": "exists", "endpoint_id": ep.get("id", "")}

    form = [("url", url), ("description", description)] + [("enabled_events[]", e) for e in events]
    created = _api(key, "POST", "webhook_endpoints", form)
    if "error" in created:
        return {**base, "status": "error", "error": created["error"]["message"]}
    return {
        **base,
        "status": "created",
        "endpoint_id": created.get("id", ""),
        "secret": created.get("secret", ""),
        "livemode": created.get("livemode"),
        "events": len(created.get("enabled_events") or []),
    }


def _account_name(account: dict[str, Any]) -> str:
    profile = account.get("business_profile") or {}
    dashboard = (account.get("settings") or {}).get("dashboard") or {}
    return str(profile.get("name") or dashboard.get("display_name") or "")


def remote_script(env_path: str, var: str, url: str, events: list[str]) -> str:
    """A bash command that runs this module on a host and prints one ``MARK`` line."""
    with open(__file__, encoding="utf-8") as fh:
        source = fh.read()
    args = json.dumps({"env_path": env_path, "var": var, "url": url, "events": list(events)})
    quoted = "'" + args.replace("'", "'\"'\"'") + "'"
    # A quoted heredoc delimiter: the shell expands nothing inside the source.
    return (
        "command -v python3 >/dev/null 2>&1 || "
        f"{{ echo '{MARK}{{\"status\":\"error\",\"error\":\"python3 not found on host\"}}'; exit 0; }}\n"
        f"python3 - {quoted} <<'NAVIG_STRIPE_CONNECT_PY'\n{source}\nNAVIG_STRIPE_CONNECT_PY\n"
    )


def parse_output(stdout: str) -> dict[str, Any]:
    """The result line a remote run printed, or an error result if there is none."""
    for line in (stdout or "").splitlines():
        if line.startswith(MARK):
            try:
                result = json.loads(line[len(MARK) :])
            except ValueError:
                break
            if isinstance(result, dict):
                return result
    return {"status": "error", "error": "the host returned no result"}


def _main(argv: list[str]) -> int:
    args = json.loads(argv[1])
    result = connect(args["env_path"], args["var"], args["url"], list(args["events"]))
    print(MARK + json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
