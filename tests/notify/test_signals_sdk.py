"""Guard: the published navig-signals (Python) SDK must sign exactly the way the
server's verify_and_render expects — otherwise every event would 401."""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest

_SDK = (
    pathlib.Path(__file__).resolve().parents[3]
    / "navig-signals" / "py" / "navig_signals" / "__init__.py"
)


@pytest.mark.skipif(not _SDK.exists(), reason="navig-signals SDK not in this checkout")
def test_sdk_signature_is_accepted_by_server(tmp_path, monkeypatch):
    # Load the standalone SDK module by path (it isn't on navig-core's import path).
    spec = importlib.util.spec_from_file_location("navig_signals_under_test", _SDK)
    sdk = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(sdk)

    # A real source in an isolated notify.db.
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    from navig.notify import store

    monkeypatch.setattr(store, "_initialised", False)
    store.init_db()
    from navig.notify import signals

    src = signals.add_source("sdktest", preset="user_signup")

    # The SDK signs the exact bytes; reproduce its headers and feed the server.
    body = json.dumps({"name": "Ada", "email": "ada@acme.com"}).encode()
    ts = "1000000"
    headers = {
        "X-Navig-Timestamp": ts,
        "X-Navig-Signature": sdk._signature(src["secret"], body, ts),
    }
    res = signals.verify_and_render(src, headers, body, now=1000005)
    assert res.ok is True
    assert res.notify_type == "signal:sdktest"
    assert "Ada" in res.body
