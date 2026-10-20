"""Gateway latency budgets: in-process route handler cost + live loopback probe.

Companion to `test_latency_budgets.py` (CLI side). Together they answer the
"how fast does NAVIG OS reach core" question with numbers a regression can fail:

  measured 2026-10-15, operator box, live gateway on 127.0.0.1:8790:
    keep-alive GET /health                p50 0.4 ms   (min 0.4, max 15.3)
    new-connection GET /health            p50 ~10–13 ms (min 1.3, max 19.5)
    new-connection GET /api/deck/status   p50 7.9 ms    (min 1.4, max 14.0)
    curl.exe round-trip (spawn overhead)  65–280 ms     ← client cost, not gateway

  The handler is sub-millisecond; budgets below are set ~15–30x above the
  measured p50 so loaded-box noise never flakes a gate, while a sync blocker
  creeping into a hot route (the documented psutil-in-daemon trap: a walk that
  takes 0.12 s from a shell took 6–11 s inside the gateway) fails loudly.

Tests marked `live` need the running daemon: they skip with an explicit reason
when no gateway answers, and run when invoked as `-m live`. No secret is ever
printed — the bearer token is read, sent, and dropped.
"""

import json
import statistics
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


def _gateway_port() -> int | None:
    """Port from ~/.navig/gateway.json discovery (self-healed binds land here)."""
    p = Path.home() / ".navig" / "gateway.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        port = int(data.get("port") or 0)
        return port or None
    except (OSError, ValueError, TypeError):
        return None


def _deck_bearer() -> str:
    """deck.api_key — the credential /api/deck/* expects. Never logged."""
    try:
        import yaml

        cfg = (
            yaml.safe_load((Path.home() / ".navig" / "config.yaml").read_text(encoding="utf-8"))
            or {}
        )
        return str((cfg.get("deck") or {}).get("api_key") or "")
    except Exception:  # noqa: BLE001 — a missing/broken config must not crash a probe
        return ""


def _get(port: int, path: str, token: str = "", timeout: float = 20.0) -> tuple[int, float, int]:
    """One new-connection GET. Returns (status, elapsed ms, body bytes)."""
    import http.client

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    t0 = time.perf_counter()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        return resp.status, (time.perf_counter() - t0) * 1000, len(body)
    finally:
        conn.close()


def _require_gateway() -> tuple[int, str]:
    port = _gateway_port()
    if not port:
        pytest.skip("no ~/.navig/gateway.json discovery file — gateway not bound")
    try:
        status, _, _ = _get(port, "/health", timeout=2.0)
    except OSError as exc:
        pytest.skip(f"gateway not reachable on 127.0.0.1:{port}: {exc}")
    assert status == 200, f"/health returned {status} on port {port}"
    return port, _deck_bearer()


def test_live_gateway_health_loopback_budget():
    """New-connection loopback GET /health: measured p50 ~10 ms, budget 250 ms."""
    port, _ = _require_gateway()
    runs = [_get(port, "/health")[1] for _ in range(6)]
    med = statistics.median(runs)
    assert med < 250, (
        f"live /health p50 {med:.1f} ms exceeds the 250 ms budget "
        f"(runs: {[round(r, 1) for r in runs]} ms; baseline p50 ~10 ms new-conn / "
        f"0.4 ms keep-alive). Something blocks the gateway event loop?"
    )


def test_live_deck_status_loopback_budget():
    """Authenticated deck route: measured p50 7.9 ms, budget 250 ms."""
    port, token = _require_gateway()
    assert token, "deck.api_key missing from config — cannot auth /api/deck/*"
    runs = []
    status_seen = None
    for _ in range(6):
        status, ms, _ = _get(port, "/api/deck/status", token=token)
        status_seen = status
        runs.append(ms)
    assert status_seen == 200, (
        f"/api/deck/status returned {status_seen} (401 = deck.api_key mismatch)"
    )
    med = statistics.median(runs)
    assert med < 250, (
        f"live /api/deck/status p50 {med:.1f} ms exceeds the 250 ms budget "
        f"(runs: {[round(r, 1) for r in runs]} ms; baseline p50 7.9 ms). "
        f"Auth/handler path regressed?"
    )


def test_live_monitor_cpu_route_and_cache():
    """The psutil-heavy route a process manager would ride (/monitor/cpu ships
    top_processes): cold budget 15 s (documented 6–11 s in-daemon wart in
    monitor.py's own comment), then the 8 s TTL cache must answer a warm
    repeat in under 1 s."""
    port, token = _require_gateway()
    assert token, "deck.api_key missing from config"

    status, cold_ms, _ = _get(port, "/api/deck/monitor/cpu", token=token, timeout=30.0)
    assert status == 200, f"/api/deck/monitor/cpu returned {status}"

    status, warm_ms, _ = _get(port, "/api/deck/monitor/cpu", token=token, timeout=30.0)
    assert status == 200, f"warm /api/deck/monitor/cpu returned {status}"

    assert cold_ms < 15_000, (
        f"cold /monitor/cpu {cold_ms:.0f} ms exceeds 15000 ms (documented "
        f"6–11 s psutil-in-daemon baseline; 15 s is where decks started "
        f"crossing the proxy timeout)"
    )
    assert warm_ms < 1000, (
        f"warm (within 8 s TTL) /monitor/cpu {warm_ms:.0f} ms — cache not "
        f"serving? cold was {cold_ms:.0f} ms"
    )
