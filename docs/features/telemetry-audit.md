# Telemetry Audit — Feature Docs

> Tool: `telemetry_auditor` · CLI: `navig telemetry audit <cmd>` · OS: Windows only

Passive, read-only privacy audit of VS Code and Windows telemetry connections.
Traces every active TCP connection to its owner, maps it to a data category, locates
local staging files, and produces a classified risk report. Zero data is modified.

---

## Commands

| Command | CLI path | What it does |
|---------|----------|--------------|
| `scan` | `navig telemetry audit scan` | Enumerate active TCP connections + classify them |
| `whois` | `navig telemetry audit whois --ip <IP>` | 4-source ownership trace |
| `sources` | `navig telemetry audit sources` | Inspect local telemetry staging files + registry |
| `report` | `navig telemetry audit report` | Full risk table (scan + sources combined) |
| `cmdref` | `navig telemetry audit cmdref` | Copy-pasteable manual verification commands |

---

## Audit Methodology

The tool implements six structured steps:

| Step | Method | Tool command |
|------|--------|-------------|
| 1A | `Get-NetTCPConnection` — enumerate all active/time-wait connections | `scan` |
| 1B-1E | netstat / TCPView / Wireshark / ETW | `cmdref` (reference only) |
| 2 | PTR reverse-DNS on every remote IP | `scan` (built-in) |
| 3 | RDAP + ASN + GeoIP ownership trace | `whois` |
| 4 | Check VS Code + Windows telemetry level settings | `sources` |
| 5 | Inspect local telemetry files and ETL queues | `sources` |
| 6 | Full classified risk report | `report` |

---

## Step 1 — Scanning Connections (`scan`)

### Quick examples

```bash
navig telemetry audit scan                      # all processes
navig telemetry audit scan --filter vscode      # Code / Antigravity only
navig telemetry audit scan --filter windows     # svchost / DiagTrack only
navig telemetry audit scan --no-resolve         # skip PTR, faster
navig telemetry audit scan --save out.json
```

### Implementation

PowerShell `Get-NetTCPConnection` joins TCP socket table against `Get-Process` by PID.
For each connection whose remote IP is routable (not 127.x / ::1 / 0.0.0.0):

1. PTR reverse-DNS via `socket.gethostbyaddr()` (capped at `--timeout` seconds total)
2. FQDN matched against `KNOWN_ENDPOINTS` knowledge base (50+ entries)
3. If no KB match: `_classify_unknown()` — marks as `High` risk / `Unknown — Investigate Further`

### Manual equivalent

```powershell
# PowerShell — Step 1A (run in admin terminal for full visibility)
Get-NetTCPConnection -State Established,TimeWait,CloseWait |
  Select-Object -Property LocalAddress,LocalPort,RemoteAddress,RemotePort,State,
    @{Name='Process';Expression={(Get-Process -Id $_.OwningProcess -EA SilentlyContinue).Name}},
    OwningProcess |
  Where-Object { $_.RemoteAddress -ne '0.0.0.0' -and $_.RemoteAddress -ne '::' } |
  Sort-Object Process |
  Format-Table -AutoSize
```

---

## Step 3 — Ownership Trace (`whois`)

```bash
navig telemetry audit whois --ip 13.107.42.16
```

Four passive lookups performed per IP:

| Source | URL | Returns |
|--------|-----|---------|
| PTR | `socket.gethostbyaddr()` | FQDN (e.g., `vortex.data.microsoft.com`) |
| RDAP | `https://rdap.arin.net/registry/ip/<IP>` | Registered org, CIDR block, network name |
| ASN | `https://api.bgpview.io/ip/<IP>` | ASN number, AS name, announced prefix |
| GeoIP | `https://ipinfo.io/<IP>/json` | Country, city, org description |

### Worked example — `13.107.42.16`

```json
{
  "ip": "13.107.42.16",
  "ptr": "vortex.data.microsoft.com",
  "rdap": {
    "org": "MICROSOFT-CORP-MSN-AS-BLOCK",
    "cidr": "13.64.0.0/11",
    "network_name": "MSFT"
  },
  "asn": {
    "asn": "AS8075",
    "name": "MICROSOFT-CORP-MSN-AS-BLOCK",
    "prefix": "13.107.0.0/17"
  },
  "geoip": {
    "country": "US",
    "org": "AS8075 Microsoft Corporation"
  }
}
```

**Verdict:** `Low` risk — owned by Microsoft MSFT, PTR resolves to `vortex.data.microsoft.com`
(Windows DiagTrack pipeline), documented by `AllowTelemetry` policy.

### Key Microsoft ASNs

| ASN | Name | Usage |
|-----|------|-------|
| AS8075 | MICROSOFT-CORP-MSN-AS-BLOCK | DiagTrack, Watson, Azure Monitor |
| AS8068 | MICROSOFT-CORP-MSN-AS-BLOCK | Azure East US, Office 365 |
| AS3598 | MICROSOFT-CORP-MSN-AS-BLOCK | Older Azure IP ranges |

Non-Microsoft ASN receiving telemetry data = **Medium/High risk** by default.

---

## Step 4 — Telemetry Settings

### VS Code telemetry levels

Configured in `%APPDATA%\Code\User\settings.json`:

```json
{
  "telemetry.telemetryLevel": "off"
}
```

| Level | What is sent |
|-------|-------------|
| `"off"` | Nothing |
| `"crash"` | Unhandled exceptions + crash stacks |
| `"error"` | Above + first-party errors |
| `"all"` *(default)* | Session UUID, platform, extension IDs+versions, feature activation, command names, usage counts, error stacks, timing. **No file contents. No keystrokes.** |

To verify current level without changing it:
```bash
navig telemetry audit sources
# Check: data.sources.vscode_user_settings.contents.telemetry_level_note
```

Official docs: https://code.visualstudio.com/docs/getstarted/telemetry

---

### Windows telemetry levels

Registry key: `HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection` → `AllowTelemetry`

| DWORD value | Level | What is sent |
|-------------|-------|-------------|
| `0` | Security | Defender telemetry only — requires GPO / Enterprise policy |
| `1` | Required *(Win11 default)* | Device class ID, OS version/build, hardware class, app crash metadata, Windows Update outcomes. No user content. |
| `3` | Optional | Everything in Required + app usage patterns, inking/typing improvement, Edge browsing summary, basic location, in some configurations — full crash dumps |

To verify current level:
```bash
navig telemetry audit sources
# Check: data.registry.AllowTelemetry
```

**Set Required level (admin required):**
```powershell
reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\DataCollection" /v AllowTelemetry /t REG_DWORD /d 1 /f
```

Official docs: https://learn.microsoft.com/en-us/windows/privacy/configure-windows-diagnostic-data-in-your-organization

---

## Step 5 — Local Files (`sources`)

```bash
navig telemetry audit sources
navig telemetry audit sources --save sources_result.json
```

Inspects 15 local paths (read-only, never modifies contents):

### VS Code paths

| Path | Contents |
|------|----------|
| `%APPDATA%\Code\logs\` | Timestamped log folders — output channels, extension host logs |
| `%APPDATA%\Code\User\globalStorage\` | Extension state databases (SQLite), analytics caches |
| `%APPDATA%\Code\User\settings.json` | Reads `telemetry.telemetryLevel` setting |
| `%LOCALAPPDATA%\Code\CrashPad\` | Minidumps awaiting upload to `dc.services.visualstudio.com` |

### Windows DiagTrack / WER paths

| Path | Contents |
|------|----------|
| `C:\ProgramData\Microsoft\Diagnosis\ETLLogs\AutoLogger\` | DiagTrack binary ETL queue — events built up since last upload |
| `C:\ProgramData\Microsoft\Diagnosis\ETLLogs\ShutdownLogger\` | DiagTrack ETL written at shutdown |
| `C:\ProgramData\Microsoft\Diagnosis\` | Watson metadata, intermediate diagnostics |
| `C:\ProgramData\Microsoft\Windows\WER\ReportQueue\` | Windows Error Reports pending upload |
| `C:\ProgramData\Microsoft\Windows\WER\ReportArchive\` | WER reports uploaded / not sent (kept for 30 days) |

### ETW session status

The tool queries `logman query -ets` to list active Event Tracing for Windows sessions.
DiagTrack-related sessions (`DiagLog`, `EventLog-Application`, `WiFiSession`) indicate
active background telemetry capture even when DiagTrack service is stopped.

---

## Step 6 — Risk Report (`report`)

```bash
navig telemetry audit report
navig telemetry audit report --filter vscode --save vscode_audit.json
```

Runs `scan` + `sources` internally, deduplicates connections by `(process, remote_host)`,
then emits the complete JSON envelope. Markdown risk table is printed to stdout.

### Example risk table

| Process | Remote Host | Owner | Data Category | Risk | Status |
|---------|------------|-------|---------------|------|--------|
| Code | vortex.data.microsoft.com | Microsoft / DiagTrack | Windows diagnostic data | Low | Known Telemetry |
| Code | dc.services.visualstudio.com | Microsoft / Azure Monitor | VS Code editor events | Low | Known Telemetry |
| Code | copilot-proxy.githubusercontent.com | GitHub / Microsoft | Copilot completions | Medium | Known Telemetry |
| Code | marketplace.visualstudio.com | Microsoft | Extension marketplace | Low | Known Telemetry |
| svchost | v10.events.data.microsoft.com | Microsoft / DiagTrack | Structured telemetry v10 | Low | Known Telemetry |
| svchost | watson.telemetry.microsoft.com | Microsoft / Watson | Windows Error Reporting | Low | Known Telemetry |
| some.exe | 203.0.113.45 | Unknown | — | High | Unknown — Investigate Further |

---

## Known Endpoints Reference

Full knowledge base embedded in `worker.py → KNOWN_ENDPOINTS`. Key entries:

### Windows OS Telemetry

| FQDN | Service | Data Sent |
|------|---------|-----------|
| `vortex.data.microsoft.com` | DiagTrack | Device ID, OS info, app crashes, Windows Update outcomes |
| `v10.events.data.microsoft.com` | DiagTrack | Structured telemetry events — v10 pipeline |
| `v20.events.data.microsoft.com` | DiagTrack | Structured telemetry events — v20 pipeline (Win 11) |
| `watson.telemetry.microsoft.com` | Watson WER | Crash reports, minidump metadata |
| `watson.microsoft.com` | Watson WER | Legacy crash upload endpoint |
| `ceuswatcab01.blob.core.windows.net` | Watson | Crash dump blob upload |
| `telecommand.telemetry.microsoft.com` | DiagTrack | Remote configuration triggers |
| `settings-win.data.microsoft.com` | DiagTrack | DiagTrack settings endpoint |
| `smartscreen.microsoft.com` | SmartScreen | URL + file hash reputation queries |
| `urs.smartscreen.microsoft.com` | SmartScreen | SmartScreen URL reputation service |

### VS Code / Azure Monitor

| FQDN | Service | Data Sent |
|------|---------|-----------|
| `dc.services.visualstudio.com` | Azure Monitor | Editor session events, extension usage, crash stacks |
| `dc.applicationinsights.azure.com` | Azure Monitor | Application Insights custom events |
| `dc.applicationinsights.microsoft.com` | Azure Monitor | Azure Monitor telemetry pipeline |
| `vscodeexperiments.azureedge.net` | VS Code A/B | A/B experiment assignments |
| `default.exp-tas.com` | VS Code A/B | Experimentation service |
| `marketplace.visualstudio.com` | VSIX Marketplace | Extension downloads and metadata |
| `*.gallerycdn.vsassets.io` | VSIX CDN | Extension package downloads |
| `copilot-proxy.githubusercontent.com` | GitHub Copilot | Prompt context + completions (opt-in) |
| `api.github.com` | GitHub API | Copilot session tokens, Codespaces |

### Windows Update / Auth (Low Risk — non-telemetry)

| FQDN | Service |
|------|---------|
| `update.microsoft.com` | Windows Update CDN |
| `windowsupdate.microsoft.com` | Windows Update downloads |
| `login.microsoftonline.com` | Azure AD / Microsoft account auth |
| `graph.microsoft.com` | Microsoft Graph API |
| `office.net` | Microsoft 365 services |

### Third-Party Analytics (Medium Risk)

| FQDN | Owner | Used by |
|------|-------|---------|
| `sentry.io` | Sentry / Functional Software | Extension crash reporting |
| `o*.ingest.sentry.io` | Sentry | Crash reports from VS Code extensions |
| `amplitude.com` | Amplitude | Usage analytics for some extensions |
| `api.segment.io` | Segment (Twilio) | Analytics SDK used by some extensions |
| `fullstory.com` | FullStory | Session replay SDKs |

---

## Heartbeat Integration

To run periodic telemetry audits, add to `docs/heartbeat.md` schedule:

```yaml
- id: telemetry-scan-daily
  schedule: "0 09 * * 1"          # Monday 09:00
  command: navig telemetry audit report --save /var/log/navig/telemetry_{{date}}.json
  threshold:
    high_risk_connections: 0       # alert if any High risk connections found
    unknown_connections: 3         # alert if more than 3 Unknown — Investigate Further
  auto_fix: false                  # scan is passive — no auto-fix possible
  notify: true
```

---

## Privacy Resources

| Resource | URL |
|----------|-----|
| VS Code telemetry docs | https://code.visualstudio.com/docs/getstarted/telemetry |
| Windows diagnostic data | https://learn.microsoft.com/en-us/windows/privacy/configure-windows-diagnostic-data-in-your-organization |
| Windows diagnostic data events | https://learn.microsoft.com/en-us/windows/privacy/windows-diagnostic-data |
| Watson / WER docs | https://learn.microsoft.com/en-us/windows/win32/wer/windows-error-reporting |
| Azure Monitor IP ranges | https://www.microsoft.com/en-us/download/details.aspx?id=56519 |
| DiagTrack endpoints (MSFT doc) | https://learn.microsoft.com/en-us/windows/privacy/manage-windows-1903-endpoints |
| RDAP bootstrap | https://rdap.arin.net/ |
| BGPView IP lookup | https://bgpview.io/ |

---

## File Locations

| File | Purpose |
|------|---------|
| `scripts/telemetry_auditor/manifest.json` | Tool registration + CLI command graph |
| `scripts/telemetry_auditor/worker.py` | Full implementation, KNOWN_ENDPOINTS KB |
| `scripts/telemetry_auditor/tool.py` | argparse CLI fallback |
| `skills/system/telemetry-audit/SKILL.md` | NAVIG Skill definition |
| `docs/features/telemetry-audit.md` | This file |
