"""`navig.notify.stripe_connect` — reuse-or-create a project's Stripe endpoint.

Offline: the Stripe API is replaced, and the remote path is proven by actually
running the generated program (argv handling + the result line) and parsing the
shell line the host would execute.
"""

from __future__ import annotations

import json
import shlex
import subprocess
import sys

import pytest

URL = "https://edge.example.workers.dev/ingest/abc/gaze"
EVENTS = ["checkout.session.completed", "charge.refunded"]
LIVE = "sk_live_" + "x" * 24


@pytest.fixture
def sc():
    from navig.notify import stripe_connect

    return stripe_connect


def _env(tmp_path, text: str):
    p = tmp_path / ".dev.vars"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _fake_api(monkeypatch, sc, *, endpoints=(), create=None):
    calls: list[tuple] = []

    def api(key, method, path, form=None):
        calls.append((method, path, form))
        assert key == LIVE
        if path == "account":
            return {"id": "acct_1", "business_profile": {"name": "Gaze"}}
        if path.startswith("webhook_endpoints?"):
            return {"data": list(endpoints)}
        if method == "POST" and path == "webhook_endpoints":
            return create or {"id": "we_new", "secret": "whsec_fresh_123456789",
                              "livemode": True, "enabled_events": EVENTS}
        raise AssertionError(f"unexpected call {method} {path}")

    monkeypatch.setattr(sc, "_api", api)
    return calls


def test_read_env_key_handles_export_and_quotes(tmp_path, sc):
    path = _env(tmp_path, f'# c\nexport A="{LIVE}"\nB=\'two\'\nC = nope\n')
    assert sc.read_env_key(path, "A") == LIVE
    assert sc.read_env_key(path, "B") == "two"
    assert sc.read_env_key(path, "MISSING") == ""


def test_creates_the_endpoint_with_the_profile_events(tmp_path, monkeypatch, sc):
    calls = _fake_api(monkeypatch, sc)
    res = sc.connect(_env(tmp_path, f"K={LIVE}\n"), "K", URL, EVENTS)
    assert res["status"] == "created"
    assert res["endpoint_id"] == "we_new" and res["secret"] == "whsec_fresh_123456789"
    assert res["account_id"] == "acct_1" and res["account_name"] == "Gaze"
    form = dict(calls[-1][2][:2])
    assert form["url"] == URL
    assert [v for k, v in calls[-1][2] if k == "enabled_events[]"] == EVENTS


def test_an_existing_endpoint_is_reused_never_duplicated(tmp_path, monkeypatch, sc):
    calls = _fake_api(monkeypatch, sc, endpoints=[{"id": "we_old", "url": URL}])
    res = sc.connect(_env(tmp_path, f"K={LIVE}\n"), "K", URL, EVENTS)
    assert res == {"account_id": "acct_1", "account_name": "Gaze", "status": "exists", "endpoint_id": "we_old"}
    assert not [c for c in calls if c[0] == "POST"]


def test_a_test_key_or_missing_var_never_reaches_stripe(tmp_path, monkeypatch, sc):
    calls = _fake_api(monkeypatch, sc)
    assert sc.connect(_env(tmp_path, "K=sk_test_abc\n"), "K", URL, EVENTS)["status"] == "error"
    assert "is not set" in sc.connect(_env(tmp_path, "OTHER=1\n"), "K", URL, EVENTS)["error"]
    missing = sc.connect(str(tmp_path / "nope.env"), "K", URL, EVENTS)
    assert missing["status"] == "error" and "cannot read" in missing["error"]
    assert calls == []


def test_a_stripe_error_is_reported_not_raised(tmp_path, monkeypatch, sc):
    monkeypatch.setattr(sc, "_api", lambda *_a, **_k: {"error": {"message": "Stripe HTTP 401 Invalid API Key"}})
    res = sc.connect(_env(tmp_path, f"K={LIVE}\n"), "K", URL, EVENTS)
    assert res == {"status": "error", "error": "Stripe HTTP 401 Invalid API Key"}


def test_parse_output_finds_the_result_among_shell_noise(sc):
    out = "ℹ Executing…\nsome banner\n" + sc.MARK + json.dumps({"status": "exists", "endpoint_id": "we_1"}) + "\n"
    assert sc.parse_output(out)["endpoint_id"] == "we_1"
    assert sc.parse_output("nothing here")["status"] == "error"
    assert sc.parse_output(sc.MARK + "{not json")["status"] == "error"


def test_remote_script_quotes_args_the_shell_will_hand_python(sc):
    """A path with a single quote must survive bash quoting byte-for-byte."""
    script = sc.remote_script("/srv/o'brien/.env", "STRIPE_SECRET_KEY", URL, EVENTS)
    line = next(ln for ln in script.splitlines() if ln.startswith("python3 - "))
    argv = shlex.split(line.split(" <<", 1)[0])
    assert argv[:2] == ["python3", "-"]
    assert json.loads(argv[2]) == {"env_path": "/srv/o'brien/.env", "var": "STRIPE_SECRET_KEY",
                                   "url": URL, "events": EVENTS}
    # The heredoc carries this module's source and closes on its own line.
    assert "def connect(" in script
    assert script.rstrip().endswith("NAVIG_STRIPE_CONNECT_PY")


def test_the_shipped_program_runs_and_prints_one_result_line(tmp_path, sc):
    """Run the module the way the host does (`python - <json>` with the source on
    stdin) against an env file WITHOUT the key: no network, and the result line
    must still come back — proving argv, __main__ and MARK end to end."""
    env = _env(tmp_path, "OTHER=1\n")
    args = json.dumps({"env_path": env, "var": "STRIPE_SECRET_KEY", "url": URL, "events": EVENTS})
    with open(sc.__file__, encoding="utf-8") as fh:
        source = fh.read()
    proc = subprocess.run([sys.executable, "-", args], input=source, capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    res = sc.parse_output(proc.stdout)
    assert res["status"] == "error" and "is not set" in res["error"]
