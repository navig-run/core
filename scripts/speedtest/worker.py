"""
speedtest worker — persistent process, handles commands via stdin JSON lines.
CLI path: net speedtest

Also importable as a library:
  from worker import run_speedtest_cli, run_iperf3
"""
from __future__ import annotations

import json
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, Timer  # noqa: E402

TOOL = "speedtest"

# ──────────────────────────────────────────────────────────────────────────────
# Platform helpers
# ──────────────────────────────────────────────────────────────────────────────

OS = platform.system()  # "Linux", "Darwin", "Windows"

_INSTALL_HINTS: dict[str, dict[str, str]] = {
    "speedtest-cli": {
        "Linux":   "pip install speedtest-cli  OR  sudo apt install speedtest-cli",
        "Darwin":  "pip install speedtest-cli  OR  brew install speedtest-cli",
        "Windows": "pip install speedtest-cli",
    },
    "iperf3": {
        "Linux":   "sudo apt install iperf3",
        "Darwin":  "brew install iperf3",
        "Windows": "Download from https://iperf.fr/iperf-download.php — add to PATH",
    },
}


def _install_hint(binary: str) -> str:
    return _INSTALL_HINTS.get(binary, {}).get(OS, f"Install '{binary}' for your platform")


def _find_binary(name: str) -> str | None:
    # Check standard PATH first
    found = shutil.which(name)
    if found:
        return found
    # Windows USB fallback for iperf3
    if name == "iperf3" and OS == "Windows":
        candidates = [
            Path("C:/USB/network/iperf3/iperf3.exe"),
            Path("C:/Server/tools/iperf3/iperf3.exe"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Core runner — prints raw output when silent=False
# ──────────────────────────────────────────────────────────────────────────────

def _run(cmd: list[str], label: str, silent: bool = False) -> subprocess.CompletedProcess:
    cmd_str = " ".join(str(c) for c in cmd)
    if not silent:
        print(f"\n[Method: {label}] Command: {cmd_str}")
        print("-" * 72)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if not silent:
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        print("-" * 72)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# speedtest-cli
# ──────────────────────────────────────────────────────────────────────────────

def run_speedtest_cli(silent: bool = False) -> dict:
    """Run speedtest-cli --json and return parsed result dict."""
    binary = _find_binary("speedtest-cli")
    if binary is None:
        # Try the module entrypoint (installed via pip)
        binary = _find_binary("speedtest")
    if binary is None:
        return {
            "error": "speedtest-cli not found",
            "install": _install_hint("speedtest-cli"),
        }

    result = _run([binary, "--json"], "speedtest-cli", silent=silent)

    if result.returncode != 0:
        return {
            "error": f"speedtest-cli exited with code {result.returncode}",
            "stderr": result.stderr.strip(),
        }

    try:
        data = json.loads(result.stdout)
        return {
            "download_mbps": round(data.get("download", 0) / 1_000_000, 2),
            "upload_mbps":   round(data.get("upload",   0) / 1_000_000, 2),
            "ping_ms":       round(data.get("ping", 0), 2),
            "jitter_ms":     None,  # not exposed in speedtest-cli JSON
            "server":        data.get("server", {}).get("host", "unknown"),
            "timestamp":     data.get("timestamp", ""),
        }
    except json.JSONDecodeError as exc:
        return {"error": f"Failed to parse speedtest-cli output: {exc}"}


# ──────────────────────────────────────────────────────────────────────────────
# Ping helper (latency + jitter approximation)
# ──────────────────────────────────────────────────────────────────────────────

def _ping_stats(host: str, count: int = 5) -> tuple[float | None, float | None]:
    """Return (avg_ms, jitter_ms) from system ping. Both may be None on failure."""
    if OS == "Windows":
        cmd = ["ping", "-n", str(count), host]
    else:
        cmd = ["ping", "-c", str(count), host]

    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = r.stdout + r.stderr

    if r.returncode != 0:
        return None, None

    avg_ms: float | None = None
    jitter_ms: float | None = None

    if OS == "Windows":
        for line in out.splitlines():
            ln = line.strip()
            if "Average" in ln:
                try:
                    avg_ms = float(ln.split("=")[-1].strip().replace("ms", "").strip())
                except ValueError:
                    pass  # malformed value; skip
            if "Minimum" in ln and "Maximum" in ln:
                try:
                    parts = ln.split(",")
                    lo = float(parts[0].split("=")[-1].strip().replace("ms", ""))
                    hi = float(parts[1].split("=")[-1].strip().replace("ms", ""))
                    jitter_ms = round((hi - lo) / 2, 2)
                except (ValueError, IndexError):
                    pass  # malformed value; skip
    else:
        for line in out.splitlines():
            if "min/avg/max" in line:
                try:
                    stats = line.split("=")[-1].strip().split("/")
                    min_ms  = float(stats[0])
                    avg_ms  = float(stats[1])
                    max_ms  = float(stats[2].split(" ")[0])
                    jitter_ms = round((max_ms - min_ms) / 2, 2)
                except (ValueError, IndexError):
                    pass  # malformed value; skip

    return (round(avg_ms, 2) if avg_ms is not None else None, jitter_ms)


# ──────────────────────────────────────────────────────────────────────────────
# iperf3 helpers
# ──────────────────────────────────────────────────────────────────────────────

def _parse_iperf3(raw: str, direction: str) -> tuple[float | None, float | None]:
    """Parse iperf3 --json output. Returns (speed_mbps, jitter_ms_or_None)."""
    try:
        data = json.loads(raw)
        end = data.get("end", {})
        bps: float
        if direction == "send":
            bps = end.get("sum_sent", {}).get("bits_per_second", 0)
        else:
            bps = end.get("sum_received", {}).get("bits_per_second", 0)
        jitter = end.get("sum", {}).get("jitter_ms")
        speed = round(bps / 1_000_000, 2) if bps else None
        return speed, (round(jitter, 2) if jitter is not None else None)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None, None


def run_iperf3(server: str, port: int = 5201, silent: bool = False) -> dict:
    """Run iperf3 upload, download, and UDP jitter tests; return parsed dict."""
    binary = _find_binary("iperf3")
    if binary is None:
        return {"error": "iperf3 not found", "install": _install_hint("iperf3")}

    base = [binary, "-c", server, "-p", str(port), "-J"]

    # Upload
    up_res = _run(base, "iperf3 [upload/TCP]", silent=silent)
    upload_mbps: float | None = None
    upload_err: str | None = None
    if up_res.returncode == 0:
        upload_mbps, _ = _parse_iperf3(up_res.stdout, "send")
    else:
        upload_err = (up_res.stderr or up_res.stdout).strip()

    time.sleep(1)

    # Download (reverse)
    dn_res = _run(base + ["-R"], "iperf3 [download/TCP]", silent=silent)
    download_mbps: float | None = None
    download_err: str | None = None
    if dn_res.returncode == 0:
        download_mbps, _ = _parse_iperf3(dn_res.stdout, "receive")
    else:
        download_err = (dn_res.stderr or dn_res.stdout).strip()

    time.sleep(1)

    # Jitter (UDP)
    udp_res = _run(base + ["-u", "-b", "100M", "-t", "5"], "iperf3 [jitter/UDP]", silent=silent)
    jitter_ms: float | None = None
    if udp_res.returncode == 0:
        _, jitter_ms = _parse_iperf3(udp_res.stdout, "send")

    # Latency via system ping
    ping_ms, ping_jitter = _ping_stats(server)
    effective_jitter = jitter_ms if jitter_ms is not None else ping_jitter

    result: dict = {
        "server":        f"{server}:{port}",
        "download_mbps": download_mbps,
        "upload_mbps":   upload_mbps,
        "ping_ms":       ping_ms,
        "jitter_ms":     effective_jitter,
    }
    if upload_err:
        result["upload_error"] = upload_err
    if download_err:
        result["download_error"] = download_err

    return result


# ──────────────────────────────────────────────────────────────────────────────
# Worker command handlers (navig scripts protocol)
# ──────────────────────────────────────────────────────────────────────────────

def cmd_run(args: dict) -> dict:
    t = Timer()
    server = args.get("iperf3_server")
    port = int(args.get("iperf3_port", 5201))
    skip_st = args.get("skip_speedtest", False)
    skip_ip = args.get("skip_iperf3", False)
    silent = args.get("silent", True)

    if not skip_ip and not server:
        return err(TOOL, "run", ["iperf3_server is required unless skip_iperf3=true"], ms=t.ms())

    summary: dict = {}

    if not skip_st:
        summary["speedtest_cli"] = run_speedtest_cli(silent=silent)
    else:
        summary["speedtest_cli"] = {"skipped": True}

    if not skip_ip:
        summary["iperf3"] = run_iperf3(server, port, silent=silent)
    else:
        summary["iperf3"] = {"skipped": True}

    return ok(TOOL, "run", summary, ms=t.ms())


HANDLERS = {"run": cmd_run}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            command = req.get("command", "")
            handler = HANDLERS.get(command)
            if not handler:
                emit(err(TOOL, command, [f"Unknown command: {command}"]))
            else:
                emit(handler(req.get("args", {})))
        except Exception as exc:
            emit(err(TOOL, "?", [f"Parse error: {exc}"]))


if __name__ == "__main__":
    main()
