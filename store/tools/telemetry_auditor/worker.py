"""
telemetry_auditor — NAVIG Windows Privacy Audit Tool
CLI path: telemetry audit scan | whois | sources | report | cmdref

Conducts a structured, read-only privacy audit of all outbound/inbound
network connections made by VS Code and Windows telemetry services.

Steps implemented:
  cmd_scan    → Steps 1 + 2: capture connections, resolve, match known telemetry
  cmd_whois   → Step 3: RDAP / BGPView / PTR / GeoIP ownership trace
  cmd_sources → Step 5: enumerate local telemetry staging paths + registry
  cmd_report  → Step 6: full classified risk table
  cmd_cmdref  → Reference commands (Steps 1–5) for manual execution

Safety: All operations are passive and read-only. No packets are injected,
        no logs deleted, no network tables flushed.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.request
import urllib.error
import winreg
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parents[1] / "_lib"))
from common import ok, err, emit, run, Timer, current_os, is_admin

TOOL = "telemetry_auditor"
PS   = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]

# ──────────────────────────────────────────────────────────────────────────────
# KNOWLEDGE BASE
# ──────────────────────────────────────────────────────────────────────────────

# Known telemetry FQDNs → (owner, data_category, risk_level, status)
KNOWN_ENDPOINTS: dict[str, tuple[str, str, str, str]] = {
    # VS Code / Application Insights
    "dc.services.visualstudio.com":       ("Microsoft / Azure Monitor", "VS Code telemetry: editor events, extension usage, crash stacks, session metadata", "Low", "Known Telemetry"),
    "dc.applicationinsights.azure.com":   ("Microsoft / Azure Monitor", "VS Code telemetry: Application Insights pipeline events", "Low", "Known Telemetry"),
    "dc.applicationinsights.microsoft.com":("Microsoft / Azure Monitor","VS Code telemetry: Application Insights pipeline events", "Low", "Known Telemetry"),
    "westus-0.in.applicationinsights.azure.com": ("Microsoft / Azure Monitor", "VS Code telemetry: regional AI pipeline", "Low", "Known Telemetry"),
    "eastus-8.in.applicationinsights.azure.com": ("Microsoft / Azure Monitor", "VS Code telemetry: regional AI pipeline", "Low", "Known Telemetry"),
    # Windows DiagTrack / Connected User Experiences
    "vortex.data.microsoft.com":          ("Microsoft / DiagTrack", "Windows diagnostic data (Required/Optional level): device ID, OS info, app usage, crash dumps", "Low", "Known Telemetry"),
    "v10.events.data.microsoft.com":      ("Microsoft / DiagTrack", "Windows events v10 pipeline: structured telemetry events", "Low", "Known Telemetry"),
    "v10.vortex-win.data.microsoft.com":  ("Microsoft / DiagTrack", "Windows v10 Vortex pipeline fallback", "Low", "Known Telemetry"),
    "v20.events.data.microsoft.com":      ("Microsoft / DiagTrack", "Windows events v20 pipeline: structured telemetry events (Win11)", "Low", "Known Telemetry"),
    "mobile.events.data.microsoft.com":   ("Microsoft / DiagTrack", "Mobile/cross-device event pipeline", "Low", "Known Telemetry"),
    "settings-win.data.microsoft.com":    ("Microsoft / DiagTrack", "DiagTrack settings/configuration pull", "Low", "Known Telemetry"),
    "watson.telemetry.microsoft.com":     ("Microsoft / Watson", "Windows Error Reporting (WER): crash dump metadata upload", "Low", "Known Telemetry"),
    "watson.microsoft.com":               ("Microsoft / Watson", "WER crash report upload", "Low", "Known Telemetry"),
    "telecommand.telemetry.microsoft.com":("Microsoft / DiagTrack", "DiagTrack command channel: remote scenario triggers", "Low", "Known Telemetry"),
    "oca.telemetry.microsoft.com":        ("Microsoft / OCA", "Online Crash Analysis pipeline: crash symbols and dump analysis", "Low", "Known Telemetry"),
    "oca.microsoft.com":                  ("Microsoft / OCA", "Online Crash Analysis: legacy endpoint", "Low", "Known Telemetry"),
    "kmwatsonc.events.data.microsoft.com":("Microsoft / Watson", "Kernel-mode Watson crash events", "Low", "Known Telemetry"),
    "ceuswatcab01.blob.core.windows.net": ("Microsoft / Azure Blob", "WER CAB file upload staging", "Low", "Known Telemetry"),
    # Settings / config telemetry
    "geo.settings.live.net":              ("Microsoft", "Settings sync and geo-aware configuration", "Low", "Known Telemetry"),
    "settings.data.microsoft.com":        ("Microsoft", "Windows settings pull (experiment configs)", "Low", "Known Telemetry"),
    # SmartScreen
    "smartscreen.microsoft.com":          ("Microsoft / SmartScreen", "SmartScreen URL reputation checks: visited URLs and file hashes", "Low", "Known Telemetry"),
    "smartscreen-prod.microsoft.com":     ("Microsoft / SmartScreen", "SmartScreen production endpoint", "Low", "Known Telemetry"),
    "nav.smartscreen.microsoft.com":      ("Microsoft / SmartScreen", "SmartScreen navigation checks", "Low", "Known Telemetry"),
    # Windows Update
    "windowsupdate.microsoft.com":        ("Microsoft / Windows Update", "Windows Update metadata and cab downloads", "Low", "Known Telemetry"),
    "update.microsoft.com":               ("Microsoft / Windows Update", "Windows Update legacy endpoint", "Low", "Known Telemetry"),
    "download.windowsupdate.com":         ("Microsoft / Windows Update", "Windows Update download CDN", "Low", "Known Telemetry"),
    "sls.update.microsoft.com":           ("Microsoft / Windows Update", "Software Licensing Service: activation and validation", "Low", "Known Telemetry"),
    # Microsoft Store / OneDrive
    "displaycatalog.mp.microsoft.com":    ("Microsoft / Store", "Microsoft Store catalog requests", "Low", "Known Telemetry"),
    "arc.msn.com":                        ("Microsoft / MSN", "Microsoft News and interest bar telemetry", "Low", "Known Telemetry"),
    "ris.api.iris.microsoft.com":         ("Microsoft / MSN", "Windows Spotlight / news content personalization", "Low", "Known Telemetry"),
    # GitHub Copilot / VS Code extensions
    "copilot-proxy.githubusercontent.com": ("GitHub / Microsoft", "GitHub Copilot inference proxy: code completions, prompt content (opt-in only)", "Medium", "Known Telemetry"),
    "api.github.com":                     ("GitHub / Microsoft", "VS Code GitHub extension: repo data, auth tokens", "Low", "Known Telemetry"),
    "objects.githubusercontent.com":      ("GitHub / Microsoft", "VS Code: GitHub asset downloads", "Low", "Known Telemetry"),
    # Extension telemetry (third-party)
    "o2.mouseflow.com":                   ("Mouseflow (third-party)", "Session recording / heatmap analytics — undocumented in extension", "High", "Unknown — Investigate Further"),
    "sentry.io":                          ("Sentry / Functional Software Inc.", "Error tracking and performance monitoring from extensions using Sentry SDK", "Medium", "Unknown — Investigate Further"),
    "o1151633.ingest.sentry.io":          ("Sentry", "Sentry event ingestion for extension crash reporting", "Medium", "Unknown — Investigate Further"),
    # Azure / Microsoft CDN
    "azureedge.net":                      ("Microsoft / Azure CDN", "CDN delivery — VS Code extensions, updates", "Low", "Known Telemetry"),
    "vsmarketplacebadge.apphb.com":       ("Microsoft / VS Marketplace", "VS Code extension badge service", "Low", "Known Telemetry"),
    "marketplace.visualstudio.com":       ("Microsoft / VS Marketplace", "VS Code extension marketplace queries", "Low", "Known Telemetry"),
    "gallery.vsassets.io":                ("Microsoft / VS Marketplace", "Extension asset downloads", "Low", "Known Telemetry"),
    # NordVPN (auto-start → telephony)
    "api.nordvpn.com":                    ("NordVPN / Nord Security", "NordVPN app telemetry and API calls (user-installed)", "Medium", "Known Telemetry"),
    # Google Drive
    "clients6.google.com":               ("Google", "Google Drive File Stream auth/API", "Low", "Known Telemetry"),
    "www.googleapis.com":                 ("Google", "Google Drive API calls", "Low", "Known Telemetry"),
    # Bitdefender
    "nimbus.bitdefender.net":             ("Bitdefender", "Antivirus cloud scan lookups", "Low", "Known Telemetry"),
}

# VS Code process name patterns
VSCODE_PROCS = {"code", "code - insiders", "antigravity", "cursor", "codium",
                "code.exe", "antigravity.exe"}

# Windows telemetry service process patterns
WIN_TELEMETRY_PROCS = {
    "svchost",        # hosts DiagTrack, dmwappushservice, etc.
    "diagtrack",      # Connected User Experiences and Telemetry
    "dmwappushservice",
    "msdtc",
    "runtimebroker",
    "microsoftedge",
    "wer",            # Windows Error Reporting
    "compattelrunner", # Application Compatibility Telemetry
    "wsqmcons",       # Customer Experience Improvement
}

# Data category reference by telemetry level
VSCODE_LEVELS = {
    "off":         "No telemetry. Disables all event collection and transmission.",
    "crash":       "Crash-only mode. Transmits crash stacks and unhandled exception payloads to Application Insights. No feature usage or session data.",
    "error":       "Error-level. Adds first-party error reports to crash data. Still no feature usage.",
    "all":         "Full telemetry (default). Transmits: editor session UUID, platform/arch, VS Code version, extension IDs+versions, feature activation events (command names), completion/refactor usage counts, error stacks, timing metrics. No file contents or keystrokes.",
}

WINDOWS_LEVELS = {
    "Security (0)":          "Minimal. Only security-related data required to keep Windows Defender up-to-date. Available via Group Policy only.",
    "Required (1)":          "Basic device info: device ID (MachineId), OS version, hardware class, app crash data (no user content), Windows Update status.",
    "Optional (3)":          "Everything in Required plus: app usage patterns, inking/typing improvement data, browsing history summary (Edge), full crash dumps in some scenarios, compatibility data.",
}

# ──────────────────────────────────────────────────────────────────────────────
# STEP 1 + 2: SCAN CONNECTIONS
# ──────────────────────────────────────────────────────────────────────────────

# PowerShell script to enumerate all TCP connections with process names + remote resolution
_SCAN_PS = r"""
$conns = Get-NetTCPConnection -ErrorAction SilentlyContinue |
  Where-Object { $_.RemoteAddress -ne '0.0.0.0' -and $_.RemoteAddress -ne '::' -and
                 $_.RemoteAddress -notmatch '^127\.' -and
                 $_.RemoteAddress -ne '::1' -and
                 $_.RemoteAddress -notmatch '^169\.254\.' }
$procs = @{}
Get-Process -ErrorAction SilentlyContinue | ForEach-Object { $procs[[string]$_.Id] = $_.Name }
$rows = foreach ($c in $conns) {
    [PSCustomObject]@{
        ProcessName  = if($procs.ContainsKey([string]$c.OwningProcess)){$procs[[string]$c.OwningProcess]}else{"<unknown>"}
        PID          = $c.OwningProcess
        LocalAddress = $c.LocalAddress
        LocalPort    = $c.LocalPort
        RemoteAddress= $c.RemoteAddress
        RemotePort   = $c.RemotePort
        State        = $c.State
    }
}
ConvertTo-Json @($rows) -Compress -Depth 3
"""


def _ps(script: str, timeout: int = 60) -> tuple[int, str, str]:
    return run([*PS, script], timeout=timeout)


def _ptr_lookup(ip: str) -> str:
    """Passive reverse DNS PTR lookup. Returns hostname or empty string."""
    try:
        result = socket.gethostbyaddr(ip)
        return result[0]
    except Exception:
        return ""


def _match_known(hostname: str, remote_ip: str) -> dict | None:
    """Return knowledge-base entry if hostname matches any known endpoint."""
    hostname_lower = hostname.lower()
    for fqdn, data in KNOWN_ENDPOINTS.items():
        if fqdn in hostname_lower or hostname_lower.endswith("." + fqdn):
            return {
                "matched_fqdn": fqdn,
                "owner": data[0],
                "data_category": data[1],
                "risk_level": data[2],
                "status": data[3],
            }
    # Check suffix patterns for CDN-like entries
    for fqdn, data in KNOWN_ENDPOINTS.items():
        if remote_ip and fqdn.endswith(".net") or fqdn.endswith(".com"):
            pass  # only exact tail match above
    return None


def _classify_unknown(process_name: str, remote_port: int, hostname: str) -> dict:
    """Heuristic classification for endpoints not in the knowledge base."""
    p = remote_port
    if p in (443, 80, 8443):
        return {
            "matched_fqdn": hostname or "—",
            "owner": "Unknown",
            "data_category": "Undocumented — further research required",
            "risk_level": "Medium",
            "status": "Unknown — Investigate Further",
        }
    return {
        "matched_fqdn": hostname or "—",
        "owner": "Unknown",
        "data_category": "Undocumented — non-standard port, further research required",
        "risk_level": "High",
        "status": "Unknown — Investigate Further",
    }


def cmd_scan(args: dict) -> dict:
    t = Timer()
    if current_os() != "windows":
        return err(TOOL, "scan", ["telemetry_auditor is Windows-only."], ms=t.ms())

    scope   = args.get("filter", "all").lower()
    resolve = args.get("resolve", True)
    timeout = int(args.get("timeout", 60))
    save    = args.get("save")

    rc, out, er = _ps(_SCAN_PS, timeout=timeout)
    if rc != 0 or not out.strip():
        return err(TOOL, "scan", [er or "No output from Get-NetTCPConnection"], ms=t.ms())

    try:
        raw_conns = json.loads(out)
    except json.JSONDecodeError as e:
        return err(TOOL, "scan", [f"JSON parse error: {e}"], ms=t.ms())

    if not isinstance(raw_conns, list):
        raw_conns = [raw_conns]

    rows = []
    warnings = []

    for c in raw_conns:
        proc = (c.get("ProcessName") or "<unknown>").lower()
        pid  = c.get("PID", 0)
        rip  = c.get("RemoteAddress", "")
        rport = int(c.get("RemotePort", 0))

        # Apply scope filter
        is_vscode = any(v in proc for v in VSCODE_PROCS)
        is_win_tel = any(w in proc for w in WIN_TELEMETRY_PROCS)
        if scope == "vscode" and not is_vscode:
            continue
        if scope == "windows" and not is_win_tel:
            continue

        # PTR resolve
        hostname = ""
        if resolve and rip:
            try:
                hostname = _ptr_lookup(rip)
            except Exception as e:
                warnings.append(f"PTR lookup failed for {rip}: {e}")

        # Knowledge-base match
        match = _match_known(hostname, rip) if hostname else None
        if match is None and rip:
            # Try IP-only match against common Microsoft ranges
            match = _classify_unknown(proc, rport, hostname)

        row = {
            "process":        c.get("ProcessName", "<unknown>"),
            "pid":            pid,
            "local_address":  c.get("LocalAddress", ""),
            "local_port":     c.get("LocalPort", 0),
            "remote_address": rip,
            "remote_port":    rport,
            "state":          c.get("State", ""),
            "hostname":       hostname,
            "classification": match or {},
        }
        rows.append(row)

    data = {
        "scope":        scope,
        "total":        len(rows),
        "connections":  rows,
        "scan_ts":      datetime.now(timezone.utc).isoformat(),
        "known_fqdns_reference": list(KNOWN_ENDPOINTS.keys()),
    }

    if save:
        try:
            Path(save).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            warnings.append(f"Save failed: {e}")

    return ok(TOOL, "scan", data, warnings=warnings, ms=t.ms())


# ──────────────────────────────────────────────────────────────────────────────
# STEP 3: WHOIS / ASN / PTR / GeoIP OWNERSHIP TRACE
# ──────────────────────────────────────────────────────────────────────────────

def _http_get(url: str, timeout: int = 10) -> tuple[int, str]:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "navig-telemetry-auditor/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", errors="replace")
    except Exception as e:
        return 0, str(e)


def cmd_whois(args: dict) -> dict:
    t = Timer()
    if current_os() != "windows":
        return err(TOOL, "whois", ["Windows-only."], ms=t.ms())

    ip      = args.get("ip", "").strip()
    timeout = int(args.get("timeout", 15))
    save    = args.get("save")

    if not ip:
        return err(TOOL, "whois", ["--ip is required."], ms=t.ms())

    data: dict[str, Any] = {"ip": ip}
    warnings = []

    # ── PTR (Reverse DNS) ─────────────────────────────────────────────────────
    ptr = _ptr_lookup(ip)
    data["ptr"] = {
        "hostname": ptr or None,
        "note": (
            "PTR matched Microsoft infrastructure" if ptr and "microsoft.com" in ptr
            else "PTR matched Azure CDN" if ptr and "azure" in ptr
            else "No PTR record — investigate manually" if not ptr
            else "Third-party or unknown PTR record"
        ),
    }

    # ── RDAP / WHOIS (ARIN) ───────────────────────────────────────────────────
    rdap_url = f"https://rdap.arin.net/registry/ip/{ip}"
    status, body = _http_get(rdap_url, timeout=timeout)
    if status == 200:
        try:
            rdap = json.loads(body)
            name = rdap.get("name") or rdap.get("handle") or "—"
            org  = ""
            for e in rdap.get("entities", []):
                vcard = e.get("vcardArray", [])
                if isinstance(vcard, list) and len(vcard) > 1:
                    for field in vcard[1]:
                        if isinstance(field, list) and field[0] == "fn":
                            org = field[3]
                            break
            data["rdap"] = {
                "source": rdap_url,
                "name": name,
                "org": org or name,
                "handle": rdap.get("handle"),
                "country": rdap.get("country"),
                "start_address": rdap.get("startAddress"),
                "end_address": rdap.get("endAddress"),
                "raw_truncated": body[:800],
            }
        except Exception as e:
            data["rdap"] = {"source": rdap_url, "error": str(e), "raw_truncated": body[:400]}
            warnings.append(f"RDAP parse error: {e}")
    else:
        data["rdap"] = {"source": rdap_url, "error": f"HTTP {status}"}
        warnings.append(f"RDAP returned HTTP {status}")

    # ── ASN via BGPView ───────────────────────────────────────────────────────
    bgp_url = f"https://api.bgpview.io/ip/{ip}"
    status2, body2 = _http_get(bgp_url, timeout=timeout)
    if status2 == 200:
        try:
            bgp = json.loads(body2)
            prefixes = bgp.get("data", {}).get("prefixes", [])
            asns = []
            for p in prefixes:
                asn_info = p.get("asn", {})
                asns.append({
                    "asn":         asn_info.get("asn"),
                    "name":        asn_info.get("name"),
                    "description": asn_info.get("description"),
                    "country_code": asn_info.get("country_code"),
                    "prefix":      p.get("prefix"),
                })
            data["asn"] = {
                "source":   bgp_url,
                "prefixes": asns,
                "note": "Microsoft-owned ASN (AS8075)" if any(a.get("asn") == 8075 for a in asns) else
                        "Azure-owned ASN (AS8068)" if any(a.get("asn") == 8068 for a in asns) else
                        "Non-Microsoft ASN — review carefully",
            }
        except Exception as e:
            data["asn"] = {"source": bgp_url, "error": str(e)}
            warnings.append(f"BGPView parse error: {e}")
    else:
        data["asn"] = {"source": bgp_url, "error": f"HTTP {status2}"}
        warnings.append(f"BGPView returned HTTP {status2}")

    # ── GeoIP via ipinfo.io ───────────────────────────────────────────────────
    geo_url = f"https://ipinfo.io/{ip}/json"
    status3, body3 = _http_get(geo_url, timeout=timeout)
    if status3 == 200:
        try:
            geo = json.loads(body3)
            data["geoip"] = {
                "source":   geo_url,
                "city":     geo.get("city"),
                "region":   geo.get("region"),
                "country":  geo.get("country"),
                "org":      geo.get("org"),
                "hostname": geo.get("hostname"),
                "note":     "GeoIP is unreliable for Anycast IPs and CDN endpoints. Country may differ from actual data destination.",
            }
        except Exception as e:
            data["geoip"] = {"source": geo_url, "error": str(e)}
    else:
        data["geoip"] = {"source": geo_url, "error": f"HTTP {status3}"}

    # ── Knowledge-base match ──────────────────────────────────────────────────
    kb_match = _match_known(ptr, ip) if ptr else None
    data["knowledge_base"] = kb_match or {
        "matched_fqdn": None,
        "note": "Not in telemetry knowledge base — use RDAP/ASN/PTR results to classify manually",
    }

    if save:
        try:
            Path(save).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            warnings.append(f"Save failed: {e}")

    return ok(TOOL, "whois", data, warnings=warnings, ms=t.ms())


# ──────────────────────────────────────────────────────────────────────────────
# STEP 5: LOCAL TELEMETRY SOURCES
# ──────────────────────────────────────────────────────────────────────────────

def _list_dir_safe(path: Path, max_items: int = 50) -> dict:
    if not path.exists():
        return {"exists": False, "items": []}
    try:
        items = []
        for p in list(path.iterdir())[:max_items]:
            try:
                stat = p.stat()
                items.append({
                    "name":     p.name,
                    "is_dir":   p.is_dir(),
                    "size_kb":  round(stat.st_size / 1024, 1) if not p.is_dir() else None,
                    "modified": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                })
            except Exception:
                items.append({"name": p.name, "error": "stat failed"})
        return {"exists": True, "items": items, "truncated": len(list(path.iterdir())) > max_items}
    except PermissionError:
        return {"exists": True, "items": [], "error": "Access denied — re-run as Administrator"}
    except Exception as e:
        return {"exists": True, "items": [], "error": str(e)}


def _reg_query_safe(hive, path: str) -> dict:
    """Read a registry key's values without modifying them."""
    try:
        key = winreg.OpenKey(hive, path, 0, winreg.KEY_READ)
        values = {}
        i = 0
        while True:
            try:
                name, data, _ = winreg.EnumValue(key, i)
                values[name] = data
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
        return {"exists": True, "values": values}
    except FileNotFoundError:
        return {"exists": False, "values": {}}
    except PermissionError:
        return {"exists": True, "values": {}, "error": "Access denied — re-run as Administrator"}
    except Exception as e:
        return {"exists": True, "values": {}, "error": str(e)}


def cmd_sources(args: dict) -> dict:
    t = Timer()
    if current_os() != "windows":
        return err(TOOL, "sources", ["Windows-only."], ms=t.ms())

    save = args.get("save")
    appdata = Path(os.environ.get("APPDATA", r"C:\Users\Default\AppData\Roaming"))
    localapp = Path(os.environ.get("LOCALAPPDATA", r"C:\Users\Default\AppData\Local"))
    warnings = []

    sources = {

        # ── VS Code local telemetry paths ─────────────────────────────────────
        "vscode_logs": {
            "path":        str(appdata / "Code" / "logs"),
            "description": "VS Code extension host logs, output channel logs, and telemetry event traces. Written by Code.exe. Safe to inspect — reading does not alter state.",
            "process":     "Code.exe",
            "contents":    _list_dir_safe(appdata / "Code" / "logs"),
        },
        "vscode_user_global_storage": {
            "path":        str(appdata / "Code" / "User" / "globalStorage"),
            "description": "Extension persistent state, analytics caches, and VS Code platform telemetry opt-out state. Written by each extension's host context.",
            "process":     "Code.exe + extension hosts",
            "contents":    _list_dir_safe(appdata / "Code" / "User" / "globalStorage"),
        },
        "vscode_user_settings": {
            "path":        str(appdata / "Code" / "User" / "settings.json"),
            "description": "User settings file. Contains telemetry.telemetryLevel. Inspect to verify opt-out status.",
            "process":     "Code.exe",
            "contents":    _read_file_safe(appdata / "Code" / "User" / "settings.json"),
        },
        "vscode_crash_reporter": {
            "path":        str(localapp / "Code" / "CrashPad"),
            "description": "Crashpad-format crash dumps staged for upload to Application Insights. Written by Code.exe on unhandled crash.",
            "process":     "Code.exe (crashpad_handler)",
            "contents":    _list_dir_safe(localapp / "Code" / "CrashPad"),
        },
        "antigravity_logs": {
            "path":        str(appdata / "Antigravity" / "logs"),
            "description": "Logs for Antigravity (Google's VS Code fork) — same AI pipeline as Code. Inspect for AI query and telemetry records.",
            "process":     "Antigravity.exe",
            "contents":    _list_dir_safe(appdata / "Antigravity" / "logs"),
        },

        # ── Windows DiagTrack / ETL staging ──────────────────────────────────
        "diagtrack_etl_autologger": {
            "path":        r"C:\ProgramData\Microsoft\Diagnosis\ETLLogs\AutoLogger",
            "description": "DiagTrack AutoLogger ETL binary traces. Written continuously by svchost/DiagTrack. These are queued payloads awaiting upload to vortex.data.microsoft.com. Binary ETL format — decode with Windows Performance Analyzer (WPA).",
            "process":     "svchost.exe -k utcsvc (DiagTrack)",
            "contents":    _list_dir_safe(Path(r"C:\ProgramData\Microsoft\Diagnosis\ETLLogs\AutoLogger")),
        },
        "diagtrack_etl_shutdown": {
            "path":        r"C:\ProgramData\Microsoft\Diagnosis\ETLLogs\ShutdownLogger",
            "description": "DiagTrack shutdown-phase ETL traces. Captures events during Windows shutdown. Same binary ETL format.",
            "process":     "svchost.exe -k utcsvc (DiagTrack)",
            "contents":    _list_dir_safe(Path(r"C:\ProgramData\Microsoft\Diagnosis\ETLLogs\ShutdownLogger")),
        },
        "diagnosis_wer_queue": {
            "path":        r"C:\ProgramData\Microsoft\Diagnosis",
            "description": "Watson Error Reporting queue: staging area for crash CAB files, supplemental data, and WER metadata. Written by WerSvc / WerFault.exe.",
            "process":     "WerSvc / WerFault.exe",
            "contents":    _list_dir_safe(Path(r"C:\ProgramData\Microsoft\Diagnosis")),
        },
        "wer_report_queue": {
            "path":        r"C:\ProgramData\Microsoft\Windows\WER\ReportQueue",
            "description": "WER report queue: full crash reports pending upload to watson.microsoft.com. Each subdirectory is one crash event, containing WER metadata and optionally a mini dump.",
            "process":     "WerSvc",
            "contents":    _list_dir_safe(Path(r"C:\ProgramData\Microsoft\Windows\WER\ReportQueue")),
        },
        "wer_report_archive": {
            "path":        r"C:\ProgramData\Microsoft\Windows\WER\ReportArchive",
            "description": "WER archived reports (already uploaded or older than retention window).",
            "process":     "WerSvc",
            "contents":    _list_dir_safe(Path(r"C:\ProgramData\Microsoft\Windows\WER\ReportArchive")),
        },

        # ── Registry: DiagTrack control ───────────────────────────────────────
        "registry_diagtrack_policy": {
            "path":        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Diagnostics\DiagTrack",
            "description": "DiagTrack behaviour registry key: controls upload frequency, endpoint overrides, and feature flags.",
            "process":     "svchost.exe -k utcsvc",
            "values":      _reg_query_safe(winreg.HKEY_LOCAL_MACHINE,
                                           r"SOFTWARE\Microsoft\Windows\CurrentVersion\Diagnostics\DiagTrack"),
        },
        "registry_diagtrack_telemetry_allowed": {
            "path":        r"HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection",
            "description": "AllowTelemetry GPO value: 0=Security, 1=Required, 3=Optional. Controls maximum data level sent to Microsoft.",
            "process":     "DiagTrack / Group Policy",
            "values":      _reg_query_safe(winreg.HKEY_LOCAL_MACHINE,
                                           r"SOFTWARE\Policies\Microsoft\Windows\DataCollection"),
        },
        "registry_wer_consent": {
            "path":        r"HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting",
            "description": "WER consent and reporting mode flags. Disabled=1 suppresses all crash reporting.",
            "process":     "WerSvc",
            "values":      _reg_query_safe(winreg.HKEY_LOCAL_MACHINE,
                                           r"SOFTWARE\Microsoft\Windows\Windows Error Reporting"),
        },
        "registry_ceip": {
            "path":        r"HKLM\SOFTWARE\Microsoft\SQMClient",
            "description": "SQM/CEIP (Customer Experience Improvement Program) legacy key. CEIPEnable=0 disables legacy telemetry collection.",
            "process":     "wsqmcons.exe",
            "values":      _reg_query_safe(winreg.HKEY_LOCAL_MACHINE,
                                           r"SOFTWARE\Microsoft\SQMClient"),
        },
        "registry_vscode_telemetry_flag": {
            "path":        r"HKCU\SOFTWARE\Microsoft\VSCode",
            "description": "VS Code registry keys (if present). Most VS Code settings live in settings.json, not registry.",
            "process":     "Code.exe",
            "values":      _reg_query_safe(winreg.HKEY_CURRENT_USER,
                                           r"SOFTWARE\Microsoft\VSCode"),
        },

        # ── ETW Sessions ──────────────────────────────────────────────────────
        "etw_sessions": {
            "description": "Active ETW (Event Tracing for Windows) sessions. DiagTrack uses 'Diagtrack-Listener' and 'AutoLogger-Diagtrack-Listener' sessions. Use 'logman query -ets' to enumerate.",
            "note":        "Run: logman query -ets  (no admin required to list; admin required to stop)",
            "query":       _query_etw_sessions(),
        },
    }

    data = {"sources": sources, "audit_ts": datetime.now(timezone.utc).isoformat()}

    if save:
        try:
            Path(save).write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            warnings.append(f"Save failed: {e}")

    return ok(TOOL, "sources", data, warnings=warnings, ms=t.ms())


def _read_file_safe(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        # Check telemetry level setting
        note = "telemetry.telemetryLevel not found — defaults to 'all'"
        m = re.search(r'"telemetry\.telemetryLevel"\s*:\s*"([^"]+)"', content)
        if m:
            level = m.group(1)
            note = f"telemetry.telemetryLevel = \"{level}\" → {VSCODE_LEVELS.get(level, 'unknown level')}"
        return {
            "exists": True,
            "size_kb": round(path.stat().st_size / 1024, 1),
            "telemetry_level_note": note,
        }
    except PermissionError:
        return {"exists": True, "error": "Access denied"}
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _query_etw_sessions() -> dict:
    rc, out, er = run(["logman", "query", "-ets"], timeout=15)
    if rc != 0:
        return {"error": er or "logman failed"}
    # Extract DiagTrack-related sessions
    diagtrack_lines = [l for l in out.splitlines() if "diagtrack" in l.lower() or "autologger" in l.lower()]
    return {
        "diagtrack_sessions_found": len(diagtrack_lines),
        "diagtrack_lines": diagtrack_lines,
        "full_output_truncated": out[:1000],
    }


# ──────────────────────────────────────────────────────────────────────────────
# STEP 6: FULL REPORT WITH RISK TABLE
# ──────────────────────────────────────────────────────────────────────────────

def _build_report_table(connections: list[dict]) -> list[dict]:
    rows = []
    seen = set()
    for c in connections:
        remote = c.get("hostname") or c.get("remote_address", "")
        key = (c.get("process", ""), remote)
        if key in seen:
            continue
        seen.add(key)
        clf = c.get("classification", {})
        rows.append({
            "Process":       c.get("process", "<unknown>"),
            "Remote Host":   remote or c.get("remote_address", ""),
            "Remote IP":     c.get("remote_address", ""),
            "Remote Port":   c.get("remote_port", ""),
            "State":         c.get("state", ""),
            "Owner":         clf.get("owner", "Unknown"),
            "Country":       "US",  # default for Microsoft infra; whois provides accurate data
            "Data Category": clf.get("data_category", "Undocumented — further research required"),
            "Risk Level":    clf.get("risk_level", "Medium"),
            "Status":        clf.get("status", "Unknown — Investigate Further"),
        })
    return rows


def _render_markdown_table(rows: list[dict]) -> str:
    if not rows:
        return "_No connections captured._\n"
    headers = ["Process", "Remote Host", "Remote IP", "Port", "Owner", "Country",
               "Data Category", "Risk Level", "Status"]
    lines = []
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for r in rows:
        row_vals = [
            r.get("Process", ""),
            r.get("Remote Host", ""),
            r.get("Remote IP", ""),
            str(r.get("Remote Port", "")),
            r.get("Owner", ""),
            r.get("Country", ""),
            r.get("Data Category", ""),
            r.get("Risk Level", ""),
            r.get("Status", ""),
        ]
        lines.append("| " + " | ".join(v.replace("|", "\\|") for v in row_vals) + " |")
    return "\n".join(lines) + "\n"


def cmd_report(args: dict) -> dict:
    t = Timer()
    if current_os() != "windows":
        return err(TOOL, "report", ["Windows-only."], ms=t.ms())

    save    = args.get("save")
    warnings = []

    # Run scan
    scan_result = cmd_scan({
        "filter":  args.get("filter", "all"),
        "resolve": args.get("resolve", True),
        "timeout": int(args.get("timeout", 90)),
    })
    if not scan_result["ok"]:
        return err(TOOL, "report", scan_result["errors"], ms=t.ms())

    connections = scan_result["data"].get("connections", [])
    warnings.extend(scan_result.get("warnings", []))

    # Run sources
    sources_result = cmd_sources({})
    if not sources_result["ok"]:
        warnings.append("sources scan partially failed — continuing with connection data only")
        sources_data = {}
    else:
        sources_data = sources_result["data"]

    # Build risk table
    table_rows = _build_report_table(connections)
    markdown_table = _render_markdown_table(table_rows)

    # Risk summary
    risk_counts = {"Low": 0, "Medium": 0, "High": 0}
    status_counts = {"Known Telemetry": 0, "Unknown — Investigate Further": 0, "Suspicious": 0}
    for r in table_rows:
        rl = r.get("Risk Level", "Medium")
        st = r.get("Status", "Unknown — Investigate Further")
        risk_counts[rl] = risk_counts.get(rl, 0) + 1
        status_counts[st] = status_counts.get(st, 0) + 1

    # Telemetry level check (from sources)
    vscode_tl_note = ""
    vsc_settings = sources_data.get("sources", {}).get("vscode_user_settings", {})
    if isinstance(vsc_settings, dict):
        vscode_tl_note = vsc_settings.get("contents", {}).get("telemetry_level_note", "")

    data = {
        "report_ts":     datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_unique_connections": len(table_rows),
            "risk_counts":             risk_counts,
            "status_counts":           status_counts,
            "vscode_telemetry_level":  vscode_tl_note or "Could not read settings.json",
            "windows_allow_telemetry": (sources_data.get("sources", {})
                                        .get("registry_diagtrack_telemetry_allowed", {})
                                        .get("values", {})
                                        .get("AllowTelemetry", "not set (defaults to Required level)")),
        },
        "risk_table":    table_rows,
        "markdown_table": markdown_table,
        "sources":        sources_data,
        "vscode_telemetry_levels_reference": VSCODE_LEVELS,
        "windows_telemetry_levels_reference": WINDOWS_LEVELS,
    }

    if save:
        try:
            base = Path(save).with_suffix("")
            Path(save).write_text(json.dumps(data, indent=2), encoding="utf-8")
            md_path = base.with_suffix(".md")
            md_path.write_text(
                f"# Telemetry Audit Report\n\n"
                f"Generated: {data['report_ts']}\n\n"
                f"## Risk Summary\n\n"
                f"- Low: {risk_counts.get('Low',0)}  Medium: {risk_counts.get('Medium',0)}  High: {risk_counts.get('High',0)}\n"
                f"- VS Code telemetry: {vscode_tl_note}\n"
                f"- Windows AllowTelemetry: {data['summary']['windows_allow_telemetry']}\n\n"
                f"## Risk Table\n\n"
                f"{markdown_table}\n",
                encoding="utf-8"
            )
            warnings.append(f"Markdown report saved: {md_path}")
        except Exception as e:
            warnings.append(f"Save failed: {e}")

    return ok(TOOL, "report", data, warnings=warnings, ms=t.ms())


# ──────────────────────────────────────────────────────────────────────────────
# CMDREF — Copy-pasteable command reference (Steps 1–5)
# ──────────────────────────────────────────────────────────────────────────────

CMDREF = {
    "step1_powershell": {
        "title": "Step 1A — PowerShell: All TCP connections with process names (admin not required)",
        "privilege": "Standard user",
        "copy_paste": (
            "Get-NetTCPConnection -ErrorAction SilentlyContinue | "
            "Where-Object { $_.RemoteAddress -ne '0.0.0.0' -and $_.RemoteAddress -notmatch '^127\\.' } | "
            "ForEach-Object { "
            "$proc = (Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).Name; "
            "[PSCustomObject]@{ Process=$proc; PID=$_.OwningProcess; "
            "LocalPort=$_.LocalPort; RemoteAddress=$_.RemoteAddress; "
            "RemotePort=$_.RemotePort; State=$_.State } } | "
            "Sort-Object Process | Format-Table -AutoSize"
        ),
        "vscode_only_variant": (
            "$vsPIDs = (Get-Process | Where-Object { $_.Name -match 'code|antigravity|cursor' }).Id; "
            "Get-NetTCPConnection | Where-Object { $vsPIDs -contains $_.OwningProcess -and $_.RemoteAddress -ne '0.0.0.0' } | "
            "Select-Object @{N='Process';E={(Get-Process -Id $_.OwningProcess -EA 0).Name}}, "
            "OwningProcess, LocalPort, RemoteAddress, RemotePort, State | Format-Table -AutoSize"
        ),
        "expected_output": "Table: Process | PID | LocalPort | RemoteAddress | RemotePort | State",
        "what_to_look_for": "RemotePort 443 to non-Microsoft IPs; ESTABLISHED connections from 'Code' or 'svchost' to known telemetry addresses",
    },
    "step1_netstat": {
        "title": "Step 1B — netstat: All connections with PIDs (then join to tasklist)",
        "privilege": "Standard user",
        "copy_paste": "netstat -ano | findstr ESTABLISHED",
        "join_command": (
            "netstat -ano > %TEMP%\\netstat_out.txt && "
            "tasklist /FO CSV > %TEMP%\\tasklist_out.txt && "
            "echo 'Now cross-reference PID column in netstat_out.txt with PID in tasklist_out.txt'"
        ),
        "expected_output": "Proto | Local Address:Port | Foreign Address:Port | State | PID",
        "what_to_look_for": "Foreign addresses on port 443 matching known telemetry FQDNs (after PTR lookup)",
    },
    "step1_tcpview": {
        "title": "Step 1C — TCPView (Sysinternals): Live visual connection inspector",
        "privilege": "Standard user (admin for full process list)",
        "download": "https://learn.microsoft.com/en-us/sysinternals/downloads/tcpview",
        "steps": [
            "1. Download TCPView.zip from above URL, extract, run tcpview64.exe",
            "2. Accept EULA. Live table appears: Process | PID | Protocol | Local | Remote | State",
            "3. Enable: Options → Resolve Addresses (DNS names for remote IPs)",
            "4. Enable: Options → Always on Top (optional)",
            "5. Filter to VS Code: Edit → Find (Ctrl+F) → type 'Code'",
            "6. Export snapshot: File → Save → tcpview_snapshot.txt",
            "7. Look for Code.exe connections to *.visualstudio.com, *.microsoft.com, *.github.com",
        ],
    },
    "step1_wireshark": {
        "title": "Step 1D — Wireshark: Packet-level capture filtered to VS Code",
        "privilege": "Admin required for live capture",
        "note": "Wireshark with Npcap installed. TLS will show as encrypted — this captures metadata (IP/port/SNI), not plaintext content.",
        "capture_filter_by_port": "tcp port 443",
        "display_filter_vscode_known_ips": (
            "# After resolving VS Code PID, use editcap/tshark to filter by PID (via npcap process tracking):\n"
            "tshark -i Ethernet -f 'tcp port 443' -Y 'frame.time_relative < 60' -T fields "
            "-e frame.time_relative -e ip.dst -e tcp.dstport -e tls.handshake.extensions_server_name"
        ),
        "display_filter_ms_telemetry_subnets": (
            "# Known Microsoft telemetry ASN subnets (AS8075):\n"
            "ip.dst == 13.107.4.0/22 or ip.dst == 20.34.0.0/14 or ip.dst == 20.38.96.0/19 or ip.dst == 52.96.0.0/14"
        ),
        "note2": "To correlate packets to a PID, use Microsoft Message Analyzer (legacy) or Network Monitor 3.4 — both are EOL but available from MSFT.",
    },
    "step1_etw": {
        "title": "Step 1E — ETW: netsh trace capture (30 seconds, no disruption)",
        "privilege": "Admin required",
        "copy_paste": (
            "netsh trace start capture=yes tracefile=%TEMP%\\telemetry_trace.etl "
            "maxsize=50 overwrite=yes provider=Microsoft-Windows-TCPIP "
            "provider=Microsoft-Windows-WFP keywords=0xffffffff"
        ),
        "stop_command": "netsh trace stop",
        "convert_command": (
            "netsh trace convert input=%TEMP%\\telemetry_trace.etl "
            "output=%TEMP%\\telemetry_trace.txt dump=txt"
        ),
        "expected_output": "ETL binary trace → convert to .txt. Look for TCP connect events to remote IPs.",
        "what_to_look_for": "TCPConnectionAttempt events with remote IP in Microsoft AS8075 ranges",
    },
    "step2_resolve_hostname": {
        "title": "Step 2 — Resolve a remote IP to FQDN via PTR",
        "copy_paste": "Resolve-DnsName <IP_ADDRESS> -Type PTR",
        "example":    "Resolve-DnsName 13.107.42.16 -Type PTR",
        "expected_output": "NameHost: vortex.data.microsoft.com",
    },
    "step3_rdap_example": {
        "title": "Step 3 — RDAP ownership lookup (PowerShell, no third-party tools)",
        "copy_paste": (
            "$ip = (Resolve-DnsName 'vortex.data.microsoft.com' -Type A | Select-Object -First 1 -ExpandProperty IPAddress); "
            "Invoke-RestMethod \"https://rdap.arin.net/registry/ip/$ip\" | ConvertTo-Json -Depth 3"
        ),
        "expected_fields": "name, handle, country, startAddress, endAddress, entities[].vcardArray (contains org name)",
        "expected_org":    "MICROSOFT-1-S or MSFT — confirming Microsoft ownership",
    },
    "step5_local_paths": {
        "title": "Step 5 — Enumerate local telemetry staging paths (PowerShell, read-only)",
        "commands": {
            "vscode_logs":    "Get-ChildItem \"$env:APPDATA\\Code\\logs\" -Recurse -ErrorAction SilentlyContinue | Select Name, Length, LastWriteTime",
            "vscode_storage": "Get-ChildItem \"$env:APPDATA\\Code\\User\\globalStorage\" -ErrorAction SilentlyContinue | Select Name",
            "diagtrack_etl":  "Get-ChildItem \"C:\\ProgramData\\Microsoft\\Diagnosis\\ETLLogs\\AutoLogger\" -ErrorAction SilentlyContinue | Select Name, Length, LastWriteTime",
            "wer_queue":      "Get-ChildItem \"C:\\ProgramData\\Microsoft\\Windows\\WER\\ReportQueue\" -ErrorAction SilentlyContinue | Select Name",
            "diagtrack_reg":  "Get-ItemProperty -Path \"HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection\" -ErrorAction SilentlyContinue",
            "etw_sessions":   "logman query -ets | Select-String -Pattern 'diagtrack|autologger' -CaseSensitive:$false",
            "allow_telemetry":"(Get-ItemProperty -Path 'HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\DataCollection' -ErrorAction SilentlyContinue).AllowTelemetry",
        },
    },
}


def cmd_cmdref(args: dict) -> dict:
    t = Timer()
    return ok(TOOL, "cmdref", CMDREF, ms=t.ms())


# ──────────────────────────────────────────────────────────────────────────────
# WORKER LOOP
# ──────────────────────────────────────────────────────────────────────────────

HANDLERS = {
    "scan":    cmd_scan,
    "whois":   cmd_whois,
    "sources": cmd_sources,
    "report":  cmd_report,
    "cmdref":  cmd_cmdref,
}


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req     = json.loads(line)
            command = req.get("command", "")
            handler = HANDLERS.get(command)
            emit(
                handler(req.get("args", {}))
                if handler
                else err(TOOL, command or "?", [f"Unknown command: '{command}'. Valid: {list(HANDLERS)}"])
            )
        except json.JSONDecodeError as e:
            emit(err(TOOL, "?", [f"Invalid JSON input: {e}"]))
        except Exception as e:
            emit(err(TOOL, "?", [str(e)]))


if __name__ == "__main__":
    main()
