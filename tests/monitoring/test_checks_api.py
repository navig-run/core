r"""Gate 1b — the structured health-check API (``navig.monitoring.checks``).

What this file holds, and why:

1. **One data path.** The CLI printers and ``collect()`` must yield the *same*
   parsed metrics from the same fake SSH output, and every shell recipe must
   stay byte-identical to the one it was extracted from — otherwise the Gate 1
   alert pipeline and ``navig host monitor`` end up reading one host through two
   parsers. The fake transport routes on command *literals*, so a recipe that
   drifts stops being answered at all (rc=1 → the assertions below fail).
2. **A failed fetch is never a reading.** An SSH exception, a non-zero exit, or
   an unknown host produces no samples plus an explicit ``CheckFailure`` — and
   ``collect()`` still does not raise.
3. **Honest provenance.** ``source`` is ``"ssh"`` / ``"local"`` / ``"http"`` and
   says which transport produced the value.

Local sampling is real psutil (no mocks). The remote path fakes the *transport*
only — parsing is the production parser. ``tests/commands/test_monitoring.py``
keeps pinning the CLI's own rendering; the two single-path tests here pin that
both surfaces still agree.
"""

from __future__ import annotations

import json
import socket
import subprocess
import threading
import time
from dataclasses import FrozenInstanceError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest.mock import MagicMock

import pytest

from navig.commands import monitoring as mon
from navig.monitoring import checks
from navig.monitoring.checks import (
    CheckFailure,
    Sample,
    collect,
    collect_http,
    collect_with_errors,
)

# ── The recipes, written out INDEPENDENTLY of the code under test ─────────────
# If checks.py changes one of these strings, the fake stops recognising the
# command, the command exits 1, and the assertions below fail. That is the point:
# these are byte-stability pins for what was extracted from commands/monitoring.py.

_RESOURCES_CMD = (
    "echo '===CPU==='; top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1; "
    "echo '===MEM==='; free | grep Mem | awk '{print ($3/$2) * 100.0, $3, $2}'; "
    "echo '===DISK==='; df -h / | tail -1 | awk '{print $5, $3, $2, $4}' | sed 's/%//'; "
    "echo '===LOAD==='; uptime | awk -F'load average:' '{print $2}' | xargs; "
    "echo '===CONN==='; ss -s 2>/dev/null | grep 'TCP:' | awk '{print $2}'; "
    "echo '===UPTIME==='; uptime -p"
)
_DISK_CMD = "df -h | grep -E '^/dev/'"
_NETWORK_CMD = (
    "echo '===CONN==='; ss -s; "
    "echo '===LISTEN==='; ss -tuln 2>/dev/null | grep -c LISTEN || echo 0; "
    "echo '===ESTAB==='; ss -tn 2>/dev/null | grep -c ESTAB || echo 0; "
    "echo '===IFACE==='; ip -s link show | grep -E '^[0-9]+:' | awk '{print $2}' | sed 's/:$//' | head -5"
)
_CPU_CMD = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"
_MEM_CMD = "free | grep Mem | awk '{print ($3/$2) * 100.0, $3, $2}'"
_LOAD_CMD = "uptime | awk -F'load average:' '{print $2}' | xargs"
_ESTAB_CMD = "ss -tn | grep ESTAB | wc -l"

_SERVICES = (
    "nginx",
    "apache2",
    "mysql",
    "mariadb",
    "postgresql",
    "php8.3-fpm",
    "php8.2-fpm",
    "php8.1-fpm",
    "php-fpm",
    "hestia",
    "fail2ban",
    "ufw",
    "ssh",
    "sshd",
    "redis-server",
    "memcached",
)
_REPORT_SERVICES = ("nginx", "apache2", "mysql", "mariadb", "postgresql", "php-fpm", "hestia", "fail2ban")


def _service_status_cmd(service: str) -> str:
    """The per-unit recipe generate_report runs (literal — checked by the fake)."""
    return f"systemctl is-active {service} 2>/dev/null || echo 'not-installed'"


def _services_cmd(services: tuple[str, ...] = _SERVICES) -> str:
    return "; ".join(f"echo '===SVC:{s}==='; {_service_status_cmd(s)}" for s in services)


_SERVICES_CMD = _services_cmd()

# ── Canned remote output (crafted; the parser under test does the reading) ────

_BATCH = (
    "===CPU===\n"
    "12.5\n"
    "===MEM===\n"
    "41.0 3300 8000\n"
    "===DISK===\n"
    "55 20G 40G 18G\n"
    "===LOAD===\n"
    "0.10, 0.20, 0.30\n"
    "===CONN===\n"
    "42\n"
    "===UPTIME===\n"
    "up 3 days\n"
)

_DF = "/dev/sda1 40G 20G 18G 55% /\n/dev/sdb1 100G 95G 5G 95% /data\n"

_SERVICE_STATUS = {
    "nginx": "active",
    "apache2": "not-installed",
    "mysql": "inactive",
    "mariadb": "not-installed",
    "postgresql": "not-installed",
    "php8.3-fpm": "not-installed",
    "php8.2-fpm": "not-installed",
    "php8.1-fpm": "not-installed",
    "php-fpm": "not-installed",
    "hestia": "not-installed",
    "fail2ban": "active",
    "ufw": "active",
    "ssh": "active",
    "sshd": "active",
    "redis-server": "not-installed",
    "memcached": "not-installed",
}
_REPORT_STATUS = {
    "nginx": "active",
    "apache2": "not-installed",
    "mysql": "inactive",
    "mariadb": "inactive",
    "postgresql": "inactive",
    "php-fpm": "inactive",
    "hestia": "not-installed",
    "fail2ban": "active",
}


def _services_batch_output(services: tuple[str, ...] = _SERVICES) -> str:
    return "".join(f"===SVC:{name}===\n{_SERVICE_STATUS.get(name, 'unknown')}\n" for name in services)


_NET = (
    "===CONN===\n"
    "Total 42 segments\n"
    "===LISTEN===\n"
    "7\n"
    "===ESTAB===\n"
    "12\n"
    "===IFACE===\n"
    "eth0\n"
    "lo\n"
)


def _all_outputs() -> dict[str, tuple[str, int]]:
    """Everything a run of all four host checks + generate_report asks for."""
    outputs: dict[str, tuple[str, int]] = {
        _RESOURCES_CMD: (_BATCH, 0),
        _DISK_CMD: (_DF, 0),
        _SERVICES_CMD: (_services_batch_output(), 0),
        _NETWORK_CMD: (_NET, 0),
        _CPU_CMD: ("12.5\n", 0),
        _MEM_CMD: ("41.0 3300 8000\n", 0),
        _LOAD_CMD: ("0.10, 0.20, 0.30\n", 0),
        _ESTAB_CMD: ("12\n", 0),
    }
    for name in _REPORT_SERVICES:
        outputs[_service_status_cmd(name)] = (_REPORT_STATUS.get(name, "unknown") + "\n", 0)
    return outputs


class _FakeRemote:
    """Transport only: exact-command routing, canned stdout, no parsing.

    A command it does not recognise exits 1 (a real SSH session's answer for a
    recipe that no longer matches), so recipe drift fails tests instead of
    silently feeding the parser something else.
    """

    def __init__(self, outputs: dict[str, tuple[str, int]]) -> None:
        self.outputs = outputs
        self.commands: list[str] = []

    def execute_command(
        self, command: str, server_config: dict[str, Any], **kwargs: Any
    ) -> subprocess.CompletedProcess:
        self.commands.append(command)
        if command in self.outputs:
            stdout, returncode = self.outputs[command]
            return subprocess.CompletedProcess(command, returncode, stdout, "")
        return subprocess.CompletedProcess(command, 1, "", f"unexpected command: {command[:80]}")


def _patch_remote(monkeypatch: pytest.MonkeyPatch, outputs: dict[str, tuple[str, int]]) -> _FakeRemote:
    """Point BOTH surfaces (CLI printers + collect) at one fake transport."""
    fake = _FakeRemote(outputs)

    def _factory(_config: Any) -> _FakeRemote:
        return fake

    monkeypatch.setattr(checks, "RemoteOperations", _factory)
    monkeypatch.setattr(mon, "RemoteOperations", _factory)
    return fake


def _config() -> Any:
    """A MagicMock config shaped like the one the CLI resolves a named host with."""
    cfg = MagicMock()
    cfg.load_host_config.return_value = {"host": "example.com", "type": "ssh", "user": "root"}
    cfg.load_server_config.return_value = {"host": "example.com", "type": "ssh", "user": "root"}
    return cfg


def _wire_cli(monkeypatch: pytest.MonkeyPatch, cfg: Any) -> None:
    """Drive ``commands.monitoring`` at a named host without touching real config.

    The transport itself is patched by ``_patch_remote`` (which points both
    surfaces at one fake).
    """
    monkeypatch.setattr(mon, "get_config_manager", lambda: cfg)
    monkeypatch.setattr(mon, "require_active_server", lambda *_a, **_k: "web-1")


def _json_payload(captured: str) -> Any:
    """The CLI prints a prose header before its JSON — parse from the first brace."""
    return json.loads(captured[captured.index("{") :])


def _by_metric(samples: list[Sample]) -> dict[str, Sample]:
    return {sample.metric: sample for sample in samples}


class _RealShapedConfig:
    """Only attributes ConfigManager really has.

    ``get_app_config`` is deliberately absent — ``ConfigManager`` has no such
    method (``tests/security/test_security_fixes.py`` pins its absence), so a
    printer that calls it raises AttributeError on every real run. The
    ``MagicMock`` other tests hand the commands invents it, which is exactly how
    three printers shipped broken.
    """

    def __init__(self, base_dir: Any) -> None:
        self.base_dir = base_dir

    def load_server_config(self, name: str) -> dict[str, Any]:
        return {"host": "example.com", "type": "ssh", "user": "root"}

    def load_host_config(self, name: str) -> dict[str, Any]:
        return {"host": "example.com", "type": "ssh", "user": "root"}


# ── Local path: REAL psutil, no mocks ──────────────────────────────────────────


async def test_local_collect_samples_this_machine_with_real_psutil() -> None:
    from navig.commands import monitor

    if not monitor._psutil_available():
        pytest.skip("psutil not installed")

    samples = await collect(None, ["resources", "disk"])

    assert samples, "local sampling returned no readings"
    now = time.time()
    assert all(isinstance(s, Sample) for s in samples)
    assert all(s.source == "local" for s in samples)
    assert all(isinstance(s.host, str) and s.host for s in samples)
    assert all(0.0 < s.ts <= now + 5.0 for s in samples)

    by = _by_metric(samples)
    assert "cpu_pct" in by, [s.metric for s in samples]
    assert 0.0 <= float(by["cpu_pct"].value) <= 100.0
    assert by["cpu_pct"].unit == "%"
    assert "mem_pct" in by and 0.0 <= float(by["mem_pct"].value) <= 100.0
    assert "disk_pct" in by and float(by["disk_pct"].value) >= 0.0

    per_mount = [s for s in samples if s.metric.startswith("disk.")]
    assert per_mount, "the disk check must report at least the system drive"
    for sample in per_mount:
        assert sample.unit == "%"
        assert 0.0 <= float(sample.value) <= 100.0
        # disk.<mount>.usage_pct — mount embedded, never two rows on one metric
        assert sample.metric.endswith(".usage_pct")


async def test_local_checks_report_honest_failures_when_collectors_have_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty collector answer is a failure, not a row of zeros."""
    from navig.commands import monitor

    monkeypatch.setattr(monitor, "get_disk_info", lambda *a, **k: [])
    samples, errors = await collect_with_errors(None, ["disk"])
    assert samples == []
    assert [e.check for e in errors] == ["disk"]
    assert errors[0].reason


# ── Remote path: fake transport, REAL parser ──────────────────────────────────


async def test_remote_resources_become_exact_samples_with_ssh_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_remote(monkeypatch, {_RESOURCES_CMD: (_BATCH, 0)})

    samples = await collect("web-1", ["resources"], config=_config())

    assert fake.commands == [_RESOURCES_CMD]
    by = _by_metric(samples)
    assert set(by) == {
        "cpu_pct",
        "mem_pct",
        "mem_used_kb",
        "mem_total_kb",
        "disk_pct",
        "load_avg_1m",
        "load_avg_5m",
        "load_avg_15m",
        "tcp_connections",
        "uptime",
    }
    assert by["cpu_pct"].value == 12.5
    assert by["cpu_pct"].unit == "%"
    assert by["mem_pct"].value == 41.0
    assert by["mem_used_kb"].value == 3300
    assert by["mem_total_kb"].value == 8000
    assert by["disk_pct"].value == 55
    assert by["load_avg_1m"].value == 0.10
    assert by["load_avg_5m"].value == 0.20
    assert by["load_avg_15m"].value == 0.30
    assert by["tcp_connections"].value == 42
    assert by["uptime"].value == "up 3 days"
    assert by["uptime"].unit == ""
    assert all(s.host == "web-1" for s in samples)
    assert all(s.source == "ssh" for s in samples)
    assert all(0.0 < s.ts <= time.time() + 5.0 for s in samples)


async def test_remote_disk_rows_map_one_sample_per_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_remote(monkeypatch, {_DISK_CMD: (_DF, 0)})

    samples = await collect("web-1", ["disk"], config=_config())

    by = _by_metric(samples)
    assert set(by) == {"disk./.usage_pct", "disk./data.usage_pct"}
    assert by["disk./.usage_pct"].value == 55
    assert by["disk./data.usage_pct"].value == 95
    assert by["disk./.usage_pct"].unit == "%"
    assert by["disk./.usage_pct"].source == "ssh"


async def test_remote_services_keep_the_systemd_vocabulary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_remote(monkeypatch, {_SERVICES_CMD: (_services_batch_output(), 0)})

    samples = await collect("web-1", ["services"], config=_config())

    by = _by_metric(samples)
    assert len(by) == len(_SERVICES)
    assert by["service.nginx.status"].value == "active"
    assert by["service.mysql.status"].value == "inactive"
    assert by["service.ufw.status"].value == "active"
    assert by["service.redis-server.status"].value == "not-installed"
    assert all(s.unit == "state" and s.source == "ssh" for s in samples)


async def test_remote_network_emits_the_scalar_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_remote(monkeypatch, {_NETWORK_CMD: (_NET, 0)})

    samples = await collect("web-1", ["network"], config=_config())

    by = _by_metric(samples)
    assert set(by) == {"listening_ports", "established_connections"}
    assert by["listening_ports"].value == 7
    assert by["established_connections"].value == 12
    assert all(s.unit == "count" and s.source == "ssh" for s in samples)


async def test_all_four_host_checks_run_in_one_collect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_remote(monkeypatch, _all_outputs())

    samples, errors = await collect_with_errors("web-1", list(checks.HOST_CHECKS), config=_config())

    assert errors == []
    by = _by_metric(samples)
    assert {"cpu_pct", "disk./.usage_pct", "service.nginx.status", "listening_ports"} <= set(by)
    assert all(s.source == "ssh" for s in samples)


# ── Failure channel: no sample for a failed fetch ─────────────────────────────


async def test_a_raising_transport_is_surfaced_and_never_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Boom:
        def __init__(self, config: Any) -> None:
            self.config = config

        def execute_command(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("ssh is down")

    monkeypatch.setattr(checks, "RemoteOperations", _Boom)
    cfg = _config()

    samples, errors = await collect_with_errors("web-1", ["resources", "disk"], config=cfg)

    assert samples == []
    assert [e.check for e in errors] == ["resources", "disk"]
    assert all("ssh is down" in e.reason for e in errors)
    assert all(e.host == "web-1" for e in errors)
    assert all(isinstance(e, CheckFailure) for e in errors)
    # the samples-only wrapper must swallow the same failure
    assert await collect("web-1", ["resources"], config=cfg) == []


async def test_a_nonzero_ssh_exit_is_a_failure_not_a_reading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _patch_remote(monkeypatch, {_RESOURCES_CMD: ("", 255)})

    samples, errors = await collect_with_errors("web-1", ["resources"], config=_config())

    assert fake.commands == [_RESOURCES_CMD]
    assert samples == []
    assert len(errors) == 1
    assert errors[0].check == "resources"
    assert "255" in errors[0].reason


async def test_malformed_remote_metrics_yield_no_sample_and_do_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    garbage = "===CPU===\nnot-a-number\n===MEM===\nx\n"
    _patch_remote(monkeypatch, {_RESOURCES_CMD: (garbage, 0)})

    samples, errors = await collect_with_errors("web-1", ["resources"], config=_config())

    assert errors == []  # the transport succeeded — only the fields are unreadable
    assert "cpu_pct" not in _by_metric(samples)
    assert "mem_pct" not in _by_metric(samples)


async def test_an_unknown_host_never_falls_back_to_another_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _config()
    cfg.load_host_config.side_effect = FileNotFoundError("no such host: ghost-1")

    samples, errors = await collect_with_errors("ghost-1", ["resources", "disk"], config=cfg)

    assert samples == []
    assert [e.check for e in errors] == ["resources", "disk"]
    assert all("not configured" in e.reason for e in errors)
    # the name the CALLER passed is the name that was looked up — no active-server state
    cfg.load_host_config.assert_called_once_with("ghost-1")
    assert not cfg.get_active_server.called


async def test_an_unknown_check_name_is_a_caller_error() -> None:
    with pytest.raises(ValueError, match="unknown check"):
        await collect(None, ["resourcs"])


# ── HTTP / cert path (SiteCheckTool) ──────────────────────────────────────────


class _SiteHandler(BaseHTTPRequestHandler):
    """Minimal 200-OK site — stdlib, not aiohttp (the repo avoids aiohttp in
    tests: its import hangs on Windows / Python 3.14 — see fixtures/mock_llm_server.py)."""

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        pass

    def do_HEAD(self) -> None:  # noqa: N802
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", "2")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        self.do_HEAD()
        self.wfile.write(b"ok")


@pytest.fixture
def site_url() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SiteHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


def _allow_local_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """SiteCheckTool's SSRF guard blocks loopback by default (secure default).

    This test probes a local server on purpose — the same patch the site_check
    tests use; the guard itself is covered by ``tests/tools/test_tools_site_check.py``.
    """
    monkeypatch.setattr("navig.tools.site_check.check_url", lambda *_a, **_k: None)


def _closed_port_url() -> str:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return f"http://127.0.0.1:{port}/"


async def test_http_probe_yields_status_and_latency(
    monkeypatch: pytest.MonkeyPatch, site_url: str
) -> None:
    _allow_local_urls(monkeypatch)

    samples, errors = await collect_http([site_url], host="web-1")

    assert errors == []
    by = _by_metric(samples)
    assert by["http.status_code"].value == 200
    assert by["http.status_code"].unit == ""
    assert by["http.status_code"].source == "http"
    assert by["http.status_code"].host == "web-1"
    assert by["http.latency_ms"].unit == "ms"
    assert float(by["http.latency_ms"].value) >= 0.0
    assert by["http.online"].value == 1
    # plain HTTP has no TLS certificate — absence, not a fabricated reading
    assert "http.cert_expiry" not in by


async def test_collect_runs_the_http_check_through_the_same_tool(
    monkeypatch: pytest.MonkeyPatch, site_url: str
) -> None:
    _allow_local_urls(monkeypatch)

    samples = await collect(None, ["http"], urls=[site_url])

    by = _by_metric(samples)
    assert by["http.status_code"].value == 200
    assert all(s.source == "http" for s in samples)
    assert all(s.host for s in samples)


async def test_a_url_bearing_mapping_config_feeds_the_http_check(
    monkeypatch: pytest.MonkeyPatch, site_url: str
) -> None:
    _allow_local_urls(monkeypatch)

    samples = await collect(None, ["http"], config={"urls": [site_url]})

    assert any(s.metric == "http.status_code" and s.value == 200 for s in samples)


async def test_the_http_check_without_urls_is_a_failure_not_a_silent_empty() -> None:
    samples, errors = await collect_with_errors(None, ["http"])

    assert samples == []
    assert len(errors) == 1
    assert "no URLs" in errors[0].reason


async def test_an_unreachable_url_is_a_failure_never_a_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _allow_local_urls(monkeypatch)

    samples, errors = await collect_http([_closed_port_url()])

    assert samples == []
    assert len(errors) == 1
    assert errors[0].check == "http"
    assert errors[0].reason


# ── Single path: the CLI printer and collect must agree ───────────────────────


async def test_cli_and_collect_read_the_same_resource_metrics(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config()
    fake = _patch_remote(monkeypatch, _all_outputs())
    _wire_cli(monkeypatch, cfg)

    mon.monitor_resources({"json_output": True})
    cli = _json_payload(capsys.readouterr().out)

    monkeypatch.setattr(checks, "RemoteOperations", lambda _cfg: fake)
    samples = await collect("web-1", ["resources"], config=cfg)
    values = {s.metric: s.value for s in samples}

    # the JSON shape the CLI has always emitted
    assert set(cli) == {"timestamp", "server", "metrics", "alerts"}
    assert cli["server"] == "web-1"
    assert set(cli["metrics"]) == {
        "cpu",
        "memory",
        "disk",
        "load_average",
        "tcp_connections",
        "uptime",
    }
    assert cli["alerts"] == []

    metrics = cli["metrics"]
    assert metrics["cpu"] == values["cpu_pct"]
    assert metrics["memory"]["usage_percent"] == values["mem_pct"]
    assert metrics["memory"]["used_kb"] == values["mem_used_kb"]
    assert metrics["memory"]["total_kb"] == values["mem_total_kb"]
    assert metrics["memory"]["used_mb"] == round(3300 / 1024, 1)
    assert metrics["memory"]["total_mb"] == round(8000 / 1024, 1)
    assert metrics["disk"]["usage_percent"] == values["disk_pct"]
    assert int(metrics["tcp_connections"]) == values["tcp_connections"]
    assert metrics["uptime"] == values["uptime"]
    assert metrics["load_average"] == "0.10, 0.20, 0.30"
    assert values["load_avg_1m"] == 0.10
    assert values["load_avg_5m"] == 0.20
    assert values["load_avg_15m"] == 0.30


async def test_cli_and_collect_read_the_same_disk_rows(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config()
    fake = _patch_remote(monkeypatch, _all_outputs())
    _wire_cli(monkeypatch, cfg)

    mon.monitor_disk(80, {"json_output": True})
    cli = _json_payload(capsys.readouterr().out)

    monkeypatch.setattr(checks, "RemoteOperations", lambda _cfg: fake)
    samples = await collect("web-1", ["disk"], config=cfg)
    values = {s.metric: s.value for s in samples}

    assert set(cli) == {"timestamp", "server", "threshold", "disks", "alerts"}
    assert cli["threshold"] == 80
    assert cli["alerts"] == ["/data is 95% full (threshold: 80%)"]
    assert [d["mount"] for d in cli["disks"]] == ["/", "/data"]
    for disk in cli["disks"]:
        assert values[f"disk.{disk['mount']}.usage_pct"] == disk["usage_percent"]
        assert set(disk) == {"device", "size", "used", "available", "usage_percent", "mount"}


async def test_cli_services_and_collect_agree_on_every_status(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = _config()
    fake = _patch_remote(monkeypatch, _all_outputs())
    _wire_cli(monkeypatch, cfg)

    mon.monitor_services({"json_output": True})
    cli = _json_payload(capsys.readouterr().out)

    monkeypatch.setattr(checks, "RemoteOperations", lambda _cfg: fake)
    samples = await collect("web-1", ["services"], config=cfg)
    values = {s.metric: s.value for s in samples}

    assert set(cli) == {"timestamp", "server", "services", "inactive_count"}
    for row in cli["services"]:
        assert values[f"service.{row['name']}.status"] == row["status"]
    assert cli["inactive_count"] == 1  # mysql is the one inactive unit


# ── The printers must not call APIs that do not exist ─────────────────────────


def test_printers_survive_a_config_without_get_app_config(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    """``monitor_services`` / ``monitor_network`` / ``generate_report`` read their
    server config through ``config.get_app_config`` — a method ConfigManager does
    not have, so every real run died with AttributeError while the MagicMock in
    the old tests happily invented it.
    """
    cfg = _RealShapedConfig(tmp_path)
    fake = _patch_remote(monkeypatch, _all_outputs())
    _wire_cli(monkeypatch, cfg)

    mon.monitor_services({})
    mon.monitor_network({})
    mon.generate_report({})

    captured = capsys.readouterr().out
    assert "Service Status" in captured
    assert "Connection Summary" in captured
    assert "Report generated" in captured
    reports = list((tmp_path / "reports").glob("health-report_web-1_*.json"))
    assert len(reports) == 1
    assert fake.commands.count(_SERVICES_CMD) == 1


def test_generate_report_keeps_its_json_shape(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    cfg = _RealShapedConfig(tmp_path)
    fake = _patch_remote(monkeypatch, _all_outputs())
    _wire_cli(monkeypatch, cfg)

    mon.generate_report({})

    report = json.loads(
        next((tmp_path / "reports").glob("health-report_web-1_*.json")).read_text(encoding="utf-8")
    )
    assert list(report) == ["timestamp", "server", "metrics", "services", "disks", "network", "alerts"]
    assert report["server"] == "web-1"
    assert list(report["metrics"]) == ["cpu", "memory", "load_average"]
    assert report["metrics"]["cpu"] == 12.5
    assert report["metrics"]["memory"] == {"usage_percent": 41.0, "used_mb": 3.2, "total_mb": 7.8}
    assert report["metrics"]["load_average"] == "0.10, 0.20, 0.30"
    assert list(report["disks"][0]) == ["device", "mount", "size", "used", "usage_percent"]
    assert report["disks"][1] == {
        "device": "/dev/sdb1",
        "mount": "/data",
        "size": "100G",
        "used": "95G",
        "usage_percent": 95,
    }
    assert report["services"][0] == {"name": "nginx", "status": "active"}
    assert report["network"]["established_connections"] == 12
    # the report's own alert wording (no threshold suffix — that is monitor_disk's)
    assert report["alerts"] == [
        "/data is 95% full",
        "Service mysql is inactive",
        "Service mariadb is inactive",
        "Service postgresql is inactive",
        "Service php-fpm is inactive",
    ]
    # the report reads through the shared recipes, not private copies
    for command in (_CPU_CMD, _MEM_CMD, _DISK_CMD, _LOAD_CMD, _ESTAB_CMD):
        assert command in fake.commands


# ── Byte-stability of what was extracted ──────────────────────────────────────


def test_the_shell_recipes_are_the_originals_byte_for_byte() -> None:
    assert checks.RESOURCES_COMMAND == _RESOURCES_CMD
    assert checks.DISK_COMMAND == _DISK_CMD
    assert checks.NETWORK_COMMAND == _NETWORK_CMD
    assert checks.CPU_COMMAND == _CPU_CMD
    assert checks.MEM_COMMAND == _MEM_CMD
    assert checks.LOAD_COMMAND == _LOAD_CMD
    assert checks.CRITICAL_SERVICES == _SERVICES
    assert checks.services_command() == _SERVICES_CMD
    assert checks.service_status_command("nginx") == (
        "systemctl is-active nginx 2>/dev/null || echo 'not-installed'"
    )


def test_the_check_names_the_api_accepts() -> None:
    assert checks.VALID_CHECKS == ("resources", "disk", "services", "network", "http", "cert")
    assert checks.HOST_CHECKS == ("resources", "disk", "services", "network")
    assert checks.DEFAULT_CHECKS == checks.HOST_CHECKS


def test_sample_is_an_immutable_row_with_the_documented_fields() -> None:
    sample = Sample(host="web-1", metric="cpu_pct", value=12.5, unit="%", ts=1.0, source="ssh")
    assert (sample.host, sample.metric, sample.value, sample.unit, sample.ts, sample.source) == (
        "web-1",
        "cpu_pct",
        12.5,
        "%",
        1.0,
        "ssh",
    )
    with pytest.raises(FrozenInstanceError):  # a reading is not mutable after the fact
        sample.value = 99.0  # type: ignore[misc]
