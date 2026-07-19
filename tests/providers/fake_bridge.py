#!/usr/bin/env python3
"""A minimal stand-in for core-providers-bridge: speaks the same JSON-RPC 2.0 /
ndjson protocol so BridgeClient + PiDriver can be tested without Node/Pi."""

import json
import sys


def handle(method, params):
    if method == "handshake":
        return {"protocol": "1.0", "version": "fake-0.1",
                "methods": ["catalog", "detect", "startAuth", "authStatus",
                            "cancelAuth", "validate", "listModels", "refresh", "shutdown"]}
    if method == "catalog":
        return {"providers": [{"template_id": "codex", "label": "Codex"},
                              {"template_id": "github-copilot", "label": "Copilot"}]}
    if method == "detect":
        return {"detected": []}
    if method == "startAuth":
        return {"flow": "device_code", "handle": "h1", "user_code": "WXYZ-1234",
                "verification_uri": "https://example.test/device", "poll_interval_s": 0}
    if method == "authStatus":
        return {"state": "connected", "secret_ref": "bridge/codex/abc"}
    if method == "validate":
        return {"ok": True, "health": "healthy",
                "models": [{"id": "gpt-4o"}, {"id": "o4-mini"}]}
    if method == "listModels":
        return {"models": [{"id": "gpt-4o"}]}
    if method == "refresh":
        return {"state": "connected", "secret_ref": params.get("secret_ref")}
    if method == "shutdown":
        return {"ok": True}
    return None


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid, method, params = req.get("id"), req.get("method"), req.get("params") or {}
        try:
            resp = {"jsonrpc": "2.0", "id": rid, "result": handle(method, params)}
        except Exception as exc:  # noqa: BLE001
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": "fake_error", "message": str(exc)}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()
        if method == "shutdown":
            break


if __name__ == "__main__":
    main()
