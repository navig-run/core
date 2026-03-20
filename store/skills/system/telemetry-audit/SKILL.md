```skill
---
name: telemetry-audit
description: Privacy audit — captures all outbound/inbound TCP connections from VS Code and Windows telemetry services, traces each remote endpoint to its owner (WHOIS/ASN/PTR/GeoIP), maps data categories, locates local staging files, and generates a classified risk report. Passive and read-only.
user-invocable: true
navig-commands:
  - navig telemetry audit scan [--filter vscode|windows|all] [--no-resolve] [--save FILE]
  - navig telemetry audit whois --ip <IP> [--save FILE]
  - navig telemetry audit sources [--save FILE]
  - navig telemetry audit report [--filter all] [--save FILE]
  - navig telemetry audit cmdref
requires:
  - Windows 10 22H2 / Windows 11 23H2 or later
  - Standard user for scan/sources/cmdref
  - Admin recommended for sources (ETL paths, registry)
  - Outbound HTTPS for whois (RDAP, BGPView, ipinfo.io)
os: [windows]
tool_id: telemetry_auditor
examples:
  - "What is VS Code sending to Microsoft?"
  - "Show me all telemetry connections from my machine"
  - "Who owns the IP that vortex.data.microsoft.com resolves to?"
  - "Where does Windows store DiagTrack data before uploading?"
  - "Is my telemetry turned off in VS Code?"
  - "Generate a full privacy audit report"
  - "Show me the copy-paste commands to audit telemetry manually"
  - "What data does Windows send to Microsoft at the Required level?"
---

# Telemetry Audit Skill

Structured privacy audit of VS Code and Windows OS telemetry.
All commands are **read-only** — no packets injected, no logs deleted, no tables flushed.

---

## Quick Start

```bash
# Full audit (runs scan + sources + builds risk table)
navig telemetry audit report --save audit_report.json

# VS Code only, with Markdown risk table printed to terminal
navig telemetry audit scan --filter vscode

# Trace who owns a specific IP
navig telemetry audit whois --ip 13.107.42.16

# Inspect local telemetry staging paths
navig telemetry audit sources

# Get copy-pasteable commands for manual verification
navig telemetry audit cmdref
```

---

## Commands

### `scan` — Step 1 + 2: Enumerate Connections

Enumerates all active TCP connections via PowerShell `Get-NetTCPConnection`, resolves PIDs
to process names, performs PTR reverse-DNS on each remote IP, and matches FQDNs against
a built-in knowledge base of 50+ known telemetry endpoints.

```bash
navig telemetry audit scan
navig telemetry audit scan --filter vscode           # VS Code / Antigravity only
navig telemetry audit scan --filter windows          # DiagTrack / svchost only
navig telemetry audit scan --no-resolve              # skip PTR (faster)
navig telemetry audit scan --save connections.json
```

**Output fields per connection:**
- `process` — process name (e.g., `Code`, `svchost`, `Antigravity`)
- `pid` — OS process ID
- `local_address` / `local_port`
- `remote_address` / `remote_port`
- `state` — TCP state (`ESTABLISHED`, `TIME_WAIT`, `CLOSE_WAIT`, etc.)
- `hostname` — PTR-resolved FQDN (if `--resolve` enabled)
- `classification.owner` — registered organisation from knowledge base
- `classification.data_category` — what data category Microsoft's docs describe
- `classification.risk_level` — `Low` / `Medium` / `High`
- `classification.status` — `Known Telemetry` / `Unknown — Investigate Further` / `Suspicious`

**Privilege:** Standard user sufficient. Admin recommended to see all system processes.

---

### `whois` — Step 3: Ownership Trace

Performs four passive, read-only lookups against a single IP:
1. **PTR** — `socket.gethostbyaddr()` reverse DNS
2. **RDAP** — `rdap.arin.net/registry/ip/<IP>` (registered org, CIDR block)
3. **ASN** — `api.bgpview.io/ip/<IP>` (ASN number, name, prefix)
4. **GeoIP** — `ipinfo.io/<IP>/json` (country, org, city)

```bash
navig telemetry audit whois --ip 13.107.42.16
navig telemetry audit whois --ip 20.209.4.0 --save whois_result.json
```

**Note:** GeoIP country is unreliable for Anycast and Azure CDN IPs.
Microsoft primarily uses **AS8075** (MICROSOFT-CORP-MSN-AS-BLOCK) and **AS8068**.

---

### `sources` — Step 5: Local Telemetry Data

Lists contents of every path where Windows and VS Code stage telemetry before upload.
Reads registry keys controlling DiagTrack and WER behaviour.

**Paths inspected (read-only, never modified):**

| Path | Description |
|------|-------------|
| `%APPDATA%\Code\logs\` | VS Code logs, output channel traces |
| `%APPDATA%\Code\User\globalStorage\` | Extension state, analytics caches |
| `%APPDATA%\Code\User\settings.json` | Reads `telemetry.telemetryLevel` value |
| `%LOCALAPPDATA%\Code\CrashPad\` | Crashpad dumps awaiting upload |
| `C:\ProgramData\Microsoft\Diagnosis\ETLLogs\AutoLogger\` | DiagTrack ETL binary queue |
| `C:\ProgramData\Microsoft\Diagnosis\ETLLogs\ShutdownLogger\` | DiagTrack shutdown ETL |
| `C:\ProgramData\Microsoft\Diagnosis\` | Watson crash queue |
| `C:\ProgramData\Microsoft\Windows\WER\ReportQueue\` | WER reports pending upload |
| `C:\ProgramData\Microsoft\Windows\WER\ReportArchive\` | WER archived reports |

**Registry keys read (read-only):**

| Key | Value Checked |
|-----|---------------|
| `HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection` | `AllowTelemetry` (0=off, 1=Required, 3=Optional) |
| `HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting` | `Disabled` flag |
| `HKLM\SOFTWARE\Microsoft\SQMClient` | `CEIPEnable` |
| `HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Diagnostics\DiagTrack` | DiagTrack behaviour flags |

**Privilege:** Standard user for VS Code paths. Admin recommended for `C:\ProgramData\` paths.

---

### `report` — Step 6: Full Risk Table

Runs `scan` + `sources` and produces the complete classified risk table.
JSON + Markdown both written when `--save` is specified.

```bash
navig telemetry audit report
navig telemetry audit report --filter vscode --save vscode_audit.json
```

**Risk Level definitions:**
- `Low` — documented Microsoft first-party telemetry with opt-out available
- `Medium` — third-party analytics, extension telemetry, or undocumented but plausibly legitimate
- `High` — unknown destination, no PTR, non-Microsoft ASN receiving telemetry-like traffic, no opt-out

**Status classifications:**
- `Known Telemetry` — matches knowledge base with documented data category
- `Unknown — Investigate Further` — not in knowledge base, needs manual research
- `Suspicious` — non-Microsoft ASN, no PTR, unrecognised process

---

### `cmdref` — Reference Commands

Prints the complete copy-pasteable command reference for running each audit step
manually with native Windows tools: PowerShell, netstat, TCPView, Wireshark, ETW.

```bash
navig telemetry audit cmdref
```

---

## Inputs Schema

| Flag | Command | Type | Default | Description |
|------|---------|------|---------|-------------|
| `--filter` | scan, report | string | `all` | `vscode` \| `windows` \| `all` |
| `--resolve` / `--no-resolve` | scan, report | bool | `true` | PTR reverse-DNS resolution |
| `--ip` | whois | string | required | IP address to look up |
| `--timeout` | scan, whois, report | int (sec) | 60/15/90 | Per-operation timeout |
| `--save` | all | string | null | Output file path |

---

## JSON Envelope

All commands return the standard NAVIG envelope:

```json
{
  "ok": true,
  "tool": "telemetry_auditor",
  "command": "scan",
  "ts": "2026-02-20T19:00:00Z",
  "data": { ... },
  "warnings": [],
  "errors": [],
  "metrics": { "ms": 1842, "backend": "worker" }
}
```

Exit codes: `0` success · `1` validation error · `2` runtime failure

---

## Known Telemetry Endpoints (Reference)

Partial list built into the knowledge base:

| FQDN | Owner | Data Category | Risk |
|------|-------|---------------|------|
| `vortex.data.microsoft.com` | Microsoft / DiagTrack | Windows diagnostic data (device ID, OS info, crashes) | Low |
| `v10.events.data.microsoft.com` | Microsoft / DiagTrack | Structured telemetry events v10 pipeline | Low |
| `v20.events.data.microsoft.com` | Microsoft / DiagTrack | Structured telemetry events v20 pipeline (Win11) | Low |
| `dc.services.visualstudio.com` | Microsoft / Azure Monitor | VS Code: editor events, extension usage, crash stacks | Low |
| `watson.telemetry.microsoft.com` | Microsoft / Watson | Windows Error Reporting crash metadata | Low |
| `smartscreen.microsoft.com` | Microsoft / SmartScreen | URL reputation checks (visited URLs + file hashes) | Low |
| `telecommand.telemetry.microsoft.com` | Microsoft / DiagTrack | Remote scenario triggers (command channel) | Low |
| `settings-win.data.microsoft.com` | Microsoft / DiagTrack | DiagTrack settings pull | Low |
| `copilot-proxy.githubusercontent.com` | GitHub / Microsoft | Copilot completions (opt-in, contains prompt context) | Medium |
| `sentry.io` | Sentry / Functional Software | Extension crash reporting | Medium |

Full list: 50+ entries in `worker.py → KNOWN_ENDPOINTS`.

---

## VS Code Telemetry Levels

Set in `settings.json` via `"telemetry.telemetryLevel"`:

| Level | What is sent |
|-------|-------------|
| `"off"` | Nothing |
| `"crash"` | Crash stacks and unhandled exceptions only |
| `"error"` | Crash + first-party errors |
| `"all"` *(default)* | Editor session UUID, platform/arch, extension IDs+versions, feature activation events, command names, usage counts, error stacks, timing metrics. **No file contents, no keystrokes.** |

Official docs: https://code.visualstudio.com/docs/getstarted/telemetry

**To verify or change:**
```bash
navig telemetry audit sources
# Look at: data.sources.vscode_user_settings.contents.telemetry_level_note
```

---

## Windows Telemetry Levels

Controlled by `HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection → AllowTelemetry`:

| Value | Level | What is sent |
|-------|-------|-------------|
| `0` | Security | Defender updates only (GPO/Enterprise policy only) |
| `1` | Required *(Win11 default)* | Device ID, OS version, hardware class, app crash data (no user content), Windows Update status |
| `3` | Optional | Everything in Required + app usage patterns, inking/typing improvement, Edge browsing summary, full crash dumps (in some scenarios) |

Official docs: https://learn.microsoft.com/en-us/windows/privacy/configure-windows-diagnostic-data-in-your-organization

---

## Safety Rules

- All operations are **passive and read-only** — no packets injected
- No telemetry is disabled, blocked, or modified by this skill
- No kernel drivers, no WFP filters, no hosts file changes
- `whois` makes outbound HTTPS calls to `rdap.arin.net`, `api.bgpview.io`, `ipinfo.io` — these are passive public registry lookups

---

## Related Skills

- `defender-exclusion-manage` — modify Defender exclusions
- `procmon-capture` — detailed per-process file/registry/network trace (requires Procmon64.exe on USB)
```
