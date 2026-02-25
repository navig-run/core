# NAVIG System Heartbeat

Defines which tools run automatically, when, what they check, and what fixes they apply.
The heartbeat is a **periodic health loop** — not a one-shot scan.

---

## Concept

```
every N minutes → run checks → evaluate thresholds → apply fixes → log result
```

Each check is a NAVIG tool invocation. If a threshold is breached, the fix action runs automatically. Everything outputs JSON and is logged.

---

## Heartbeat Schedule

| Interval | Tool | Command | What It Watches | Auto-Fix |
|----------|------|---------|----------------|----------|
| 5 min | `mem_compression` | `scan` | MemCompression WS, memory load % | — (alert only) |
| 5 min | `memory_doctor` | `scan` | Commit %, Available MB, Modified pages | Flush standby if Modified > 4 GB |
| 15 min | `process_cleaner` | `scan` | Handle counts per process | Restart explorer if handles > 15k |
| 15 min | `wsl_docker_fix` | `scan` | Docker/WSL handle drain | Kill drain if found |
| 30 min | `nonpaged_pool_doctor` | `scan` | NonPaged Pool MB | Alert if > 4 GB; disable known bad drivers |
| 60 min | `disk_scanner` | `scan` | Disk free % | Alert if < 10% free |
| 60 min | `app_manager` | `list` | Running tray apps | — (report only) |
| on-event | `mem_compression` | `top --n 20` | Top compression feeders | Triggered when load > 80% |
| on-event | `memory_doctor` | `optimize` | Working set trim | Triggered when Available < 8 GB |
| on-event | `defender_exclusion` | `exclude list` | Verify USB+dev exclusions present | Triggered on file-lock events from MsMpEng |
| on-demand | `nvidia_updater` | `check` | NVIDIA driver version vs latest | — (manual or 30-day reminder) |

---

## Thresholds & Actions

### Memory Pressure

| Metric | WARNING | CRITICAL | Auto-Fix |
|--------|---------|----------|----------|
| Commit % | > 75% | > 88% | Run `memory_doctor optimize` |
| Available RAM | < 12 GB | < 6 GB | Trim working sets + alert |
| MemCompression WS | > 15 GB | > 22 GB | Alert + log top feeders |
| NonPaged Pool | > 3.5 GB | > 5 GB | Run `nonpaged_pool_doctor scan` + alert |
| Paged Pool | > 8 GB | > 15 GB | Restart ctfmon if it's the source |

### Handle Leaks

| Process | WARNING | CRITICAL | Auto-Fix |
|---------|---------|----------|----------|
| `explorer.exe` | > 8 000 | > 15 000 | `Stop-Process explorer` then restart |
| `msedge` renderer | > 10 000 | > 16 000 | Alert (restart Edge) |
| `chrome` | > 8 000 | > 12 000 | Alert |
| Any single process | > 5 000 | > 10 000 | Log + alert |
| WSL/Docker drain | detected | — | `wsl --shutdown` + kill vmmem |

### Disk

| Metric | WARNING | CRITICAL | Auto-Fix |
|--------|---------|----------|----------|
| Drive free % | < 15% | < 5% | Alert — manual cleanup required |

---

## Tool Inventory

All tools live in `scripts/<tool_id>/tool.py`.

### `memory_doctor`
```
scan       → memory health overview (commit, available, modified, standby)
optimize   → trim working sets, flush modified, flush DNS  [requires admin for full effect]
```
**Heartbeat use:** Every 5 min. Auto-optimize when Available < 8 GB.

---

### `mem_compression`
```
scan       → MemCompression PID, compressed store MB, expanded estimate, pressure level
top --n N  → top N processes feeding compression by paged memory
watch      → live JSON stream of compression metrics (every N sec)
report     → full diagnostic dump
```
**Heartbeat use:** Every 5 min. Run `top` when pressure level is HIGH or CRITICAL.

> `Memory Compression` is a **Windows system process** — cannot be killed. Analyze only.

---

### `process_cleaner`
```
scan       → all processes with handle counts, memory, CPU
leaks      → processes with anomalous handle counts
```
**Heartbeat use:** Every 15 min. Restart `explorer.exe` if handles > 15k.

---

### `wsl_docker_fix`
```
scan       → Docker/WSL handle drain detection, vmmem memory usage
apply      → kill drain, wsl --shutdown, flush
```
**Heartbeat use:** Every 15 min. Auto-apply if drain detected.

---

### `nonpaged_pool_doctor`
```
scan       → NonPaged Pool MB, top driver pool consumers
```
**Heartbeat use:** Every 30 min. Alert if > 4 GB. Known bad drivers:
- `cbfs6` — orphaned ExpanDrive driver → **DISABLED**
- `vpnpbus` — orphaned EldoS VPN bus → **DISABLED**
- `dokan1` — Google Drive File Stream FS driver → **DISABLED**
- `VfpExt` — Hyper-V virtual filtering (WSL2 side effect)
- `npcap` — Npcap packet capture (NordVPN stack)

---

### `disk_scanner`
```
scan       → all drives, free space, largest folders
```
**Heartbeat use:** Every 60 min.

---

### `app_manager`
```
list       → installed + running apps
```
**Heartbeat use:** Every 60 min (passive inventory).

---

## Known Process Notes

| Process | Owner | Notes |
|---------|-------|-------|
| `Memory Compression` | Windows NT KERNEL | Cannot kill. 9–23 GB = normal under load. |
| `language_server_windows_x64` | **Antigravity** (Google's VS Code fork) | 4 instances, ~10 GB paged combined. Restart Antigravity to reclaim. |
| `Antigravity.exe` | Google's VS Code fork | `C:\Users\subdose\AppData\Local\Programs\Antigravity\` |
| `msedge` PID 64928 | Microsoft Edge | `--type=renderer --extension-process`, 18k handles, 9 GB paged — restart Edge |
| `ctfmon.exe` | Windows Text Services Framework | Paged pool grows after long uptimes — restart safely |
| `vmmem` | WSL2 / Hyper-V | Grows when Docker/WSL active — `wsl --shutdown` reclaims |
| `explorer.exe` | Windows Shell | Handle leak after 200h+ uptime — restart recovers 20k handles |

---

## USB / Dev / Media Tools

These tools do not participate in the automated heartbeat loop — they are **on-demand** or **triggered by events**.

### `iperf3`
```
client  → TCP/UDP speed test to a remote host
server  → listen mode (runs until killed)
```
**When:** Manual — run when network performance degrades or before/after upgrades.

---

### `rclone`
```
remotes  → list configured cloud remotes
ls       → browse a remote path
sync     → mirror source to destination (destructive)
copy     → copy without deleting destination extras
```
**When:** Manual or scheduled backup jobs.

---

### `nssm`
```
install  → register any exe as a Windows service
start    → start a service
stop     → stop a service
status   → query service state
remove   → deregister a service
```
**When:** On-demand service management. Admin required.

---

### `vivetool`
```
enable   → enable Windows feature flag IDs
disable  → disable feature flag IDs
query    → check state of a specific feature ID
```
**When:** On-demand Windows build experimentation. Changes take effect after reboot.

---

### `procmon`
```
capture  → record file/registry/network events to a PML file for N seconds
stop     → terminate any running Procmon instance
```
**When:** On-demand diagnostics — triggered when investigating installer behavior, file lock root-cause, or registry changes.

---

### `nvidia_updater`
```
check   → compare installed NVIDIA driver to latest available
```
**When:** Manual or every 30 days. Read-only, no download.

---

### `defender_exclusion`
```
path     → add a folder/file exclusion to Windows Defender
process  → add a process exclusion
remove   → remove an exclusion
list     → list all active exclusions
```
**When:** On-demand.  
**Triggered by:** File-lock errors from `MsMpEng.exe` (MsMpEng holding USB files).  
**Safe defaults:** `C:\USB`, `C:\Server`, `C:\dev`, `python.exe`.  
**Audit:** Run `defender exclude list` after any OS reinstall to verify exclusions survived.

---

### `screenshot`
```
take      → capture full screen / monitor / region to PNG
monitors  → list available monitors
```
**When:** On-demand, or triggered by vision/diagnostics workflows.

---

### `yt_dlp`
```
download  → download video or audio from 1000+ sites
info      → fetch metadata without downloading
formats   → list available formats for a URL
```
**When:** Manual media acquisition.

---

### `gh_cli`
```
pr-list      → list pull requests
pr-create    → create a pull request
issue-list   → list issues
release-list → list releases
status       → repo status summary
run          → list GitHub Actions runs
```
**When:** On-demand dev workflow — triggered by CI status queries, PR reviews, release checks.

---

## Heartbeat Runner (planned)

Future script: `scripts/heartbeat/tool.py`

```
heartbeat run     → start the loop (daemon mode, writes to heartbeat.log)
heartbeat status  → last check results per tool
heartbeat report  → full JSON of last N cycles
heartbeat once    → run all checks once and exit
```

Each cycle outputs:
```json
{
  "cycle": 42,
  "ts": "2026-02-18T11:30:00+01:00",
  "checks": [
    { "tool": "mem_compression", "command": "scan", "pressure": "NORMAL", "action": null },
    { "tool": "memory_doctor",   "command": "scan", "pressure": "HIGH",   "action": "optimize" }
  ],
  "fixes_applied": ["memory_doctor optimize"]
}
```

---

## Reboot Checklist

After long uptime (> 7 days) or commit charge > 88%:

- [ ] `wsl --shutdown` first
- [ ] Run `memory_doctor optimize`
- [ ] Note commit % before and after reboot
- [ ] Post-reboot: verify `cbfs6`, `vpnpbus`, `dokan1` are not loaded (`sc.exe query`)
- [ ] Check NonPaged Pool is < 2 GB within 10 min of boot
