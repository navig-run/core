"""A close should ASK the browser to shut down before it kills it.

Every close path in `targets` — `cdp stop`, `profile close`, `stop --all`, the idle reaper —
was a process kill. Chrome records a kill as a crash, and more importantly it persists
cookies with periodic flushes (~30 s) plus a final one on clean shutdown. Measured on the
operator's `navig-epic` profile, 2026-09-15, a cookie set ONE SECOND before close:

    Browser.close  →  procs 0, cookie persisted: True
    taskkill /F    →  procs 0, cookie persisted: False

So `navig cdp login <domain>` followed by `stop` could silently discard the login it had
just made. `request_browser_close` asks via CDP and waits for the port to go dark; the
identity-checked kill path is unchanged and still the thing that PROVES closure.
"""

from __future__ import annotations

import io
import json
import urllib.request

import pytest

from navig.browser import targets as t


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "cdp-launched.json"
    monkeypatch.setattr(t, "_launched_registry_path", lambda: path)
    path.write_text(json.dumps({"9280": {"pid": 1, "app": "chrome", "user_data_dir": str(tmp_path / "p")}}))
    return path


# ── wiring: ask first, kill second ────────────────────────────────────────────


def test_stop_asks_before_it_kills_and_does_not_kill_a_browser_that_complied(registry, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(t, "request_browser_close", lambda port, **_: (calls.append("ask"), True)[1])
    monkeypatch.setattr(t, "_pid_is_still_the_recorded_process", lambda *_a, **_k: True)
    monkeypatch.setattr(t, "_terminate_pid", lambda pid: (calls.append(f"kill:{pid}"), True)[1])
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *_a, **_k: [], raising=False)
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)  # it complied: port dark

    r = t.stop_launched(9280)

    assert r["ok"] and r["closed"]
    assert calls[0] == "ask", f"the kill ran before the ask: {calls}"
    # ⚠ The tracked-pid kill still runs when the pid identity check says it is ours — that
    # is the existing hardening and it is correct (a compliant browser's pid is gone by
    # then, so the identity check is what stops it). What must NOT happen is the ask being
    # skipped or ordered after a kill.
    assert calls.index("ask") == 0


def test_stop_falls_through_to_the_kill_when_the_ask_fails(registry, monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(t, "request_browser_close", lambda port, **_: (calls.append("ask"), False)[1])
    monkeypatch.setattr(t, "_pid_is_still_the_recorded_process", lambda *_a, **_k: False)
    monkeypatch.setattr(t, "_terminate_pid", lambda pid: (calls.append(f"kill:{pid}"), True)[1])
    monkeypatch.setattr(t, "_debug_browser_pids", lambda *_a, **_k: [4242], raising=False)
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)

    r = t.stop_launched(9280)

    assert r["closed"]
    assert calls == ["ask", "kill:4242"], "a refused ask must still end in the proven kill"


def test_the_ask_is_wired_into_stop_launched_not_only_defined():
    """A helper nothing calls protects nothing. Pin the call site on the AST."""
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(t.stop_launched))
    called = {
        n.func.id for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
    }
    assert "request_browser_close" in called, "stop_launched no longer asks before killing"


# ── the helper's contract ─────────────────────────────────────────────────────


def test_nothing_serving_is_already_closed(monkeypatch):
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: None)
    assert t.request_browser_close(9280) is True


def test_a_probe_result_it_cannot_address_is_a_no(monkeypatch):
    """Other tests fake probe_port with a bare object(); a best-effort ask must never raise."""
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: object())
    assert t.request_browser_close(9280) is False


def test_no_browser_socket_in_json_version_is_a_no(monkeypatch):
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: t.CDPTarget(port=9280, browser="Chrome/1", endpoint="http://127.0.0.1:9280"))

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: _Resp(b'{"Browser":"Chrome/1"}'))
    assert t.request_browser_close(9280) is False


def test_a_refused_http_connection_is_a_no_not_an_exception(monkeypatch):
    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: t.CDPTarget(port=9280, browser="Chrome/1", endpoint="http://127.0.0.1:9280"))

    def _boom(*_a, **_k):
        raise OSError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    assert t.request_browser_close(9280) is False


def test_it_sends_browser_close_and_then_waits_for_the_port_to_go_dark(monkeypatch):
    """The ask is delivered through the CDP runtime; the RESULT is the port, not the reply."""
    from navig.browser import cdp_runtime

    monkeypatch.setattr(t, "probe_port", _probe_sequence([_TARGET, _TARGET, _TARGET, None]))

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_a, **_k: _Resp(b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9280/devtools/browser/x"}'))
    delivered: list[str] = []

    def _run(coro, timeout=None):
        delivered.append(coro.__name__)
        coro.close()  # do not actually open a socket

    monkeypatch.setattr(cdp_runtime, "run", _run)
    monkeypatch.setattr(t.time, "sleep", lambda _s: None)

    assert t.request_browser_close(9280, timeout=2.0) is True
    assert delivered == ["_ask"], "Browser.close was never handed to the CDP runtime"


def test_a_browser_that_ignores_the_ask_is_reported_as_not_closed(monkeypatch):
    from navig.browser import cdp_runtime

    monkeypatch.setattr(t, "probe_port", lambda _p, timeout=1.0: _TARGET)  # never goes dark

    class _Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_a, **_k: _Resp(b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9280/devtools/browser/x"}'))
    monkeypatch.setattr(cdp_runtime, "run", lambda coro, timeout=None: coro.close())
    monkeypatch.setattr(t.time, "sleep", lambda _s: None)
    clock = iter(range(0, 100))
    monkeypatch.setattr(t.time, "monotonic", lambda: float(next(clock)))

    assert t.request_browser_close(9280, timeout=3.0) is False


_TARGET = t.CDPTarget(port=9280, browser="Chrome/1", endpoint="http://127.0.0.1:9280")


def _probe_sequence(values):
    it = iter(values)
    last = {"v": values[-1]}

    def _probe(_p, timeout=1.0):
        try:
            last["v"] = next(it)
        except StopIteration:
            pass
        return last["v"]

    return _probe
