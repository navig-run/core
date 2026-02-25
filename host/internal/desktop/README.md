# navig-desktop-agent

Windows UI Automation and AutoHotkey integration for NAVIG.

---

## 1. Architecture

```
┌──────────────────────────────────────────────────────────┐
│                  User / AI Assistant                     │
└─────────────────────┬────────────────────────────────────┘
                      │
          ┌───────────┴───────────┐
          │                       │
   navig desktop CLI        MCP tools (mcp_server.py)
   (commands/desktop.py)    desktop_find / desktop_tree
          │                 desktop_click / desktop_set_value
          │                 desktop_ahk
          │                       │
          └───────────┬───────────┘
                      │ spawns subprocess
              ┌───────▼────────────┐
              │  client.go (Go)    │  ← IPC client (navig-host)
              │  desktop.py (CLI)  │  ← IPC client (CLI path)
              └───────┬────────────┘
                      │ stdin/stdout NDJSON
              ┌───────▼────────────┐
              │    agent.py        │  ← JSON-RPC sidecar
              └───────┬────────────┘
                      │
            ┌─────────┴─────────┐
            │                   │
     Windows UIA           AutoHotkey.exe
     (uiautomation)        (AHK scripts)
```

---

## 2. Method Reference

| Method | Go export | Parameters | Return type | Permission required |
|---|---|---|---|---|
| `ping` | `Ping()` | — | `PingResult` (`{"ok": true}`) | None |
| `find_element` | `FindElement(params)` | `name?`, `class_name?`, `control_type?`, `depth` (default 5) | `[]ElementInfo` | None |
| `get_window_tree` | `GetWindowTree(depth)` | `depth` (default 3) | `WindowTreeNode` | None |
| `click` | `Click(handle)` | `handle: int` | `ClickResult` (`{"clicked": true}`) | `permissionGranted == true` |
| `set_value` | `SetValue(handle, value)` | `handle: int`, `value: str` | `SetValueResult` (`{"method": "ValuePattern"\|"SendKeys"}`) | `permissionGranted == true` |
| `ahk_run` | `AHKRun(script)` | `script: str` | `AHKRunResult` (`{"stdout", "stderr", "exit_code"}`) | `permissionGranted == true` |

---

## 3. Guardrail Policy

| Guardrail | Enforcement location | Behaviour on violation |
|---|---|---|
| Non-Windows OS | `client.go` (runtime GOOS check) and `agent.py` (platform.system check) | Go: return `ErrWindowsOnly`; Python: `sys.exit(1)` with stderr message |
| `click` permission gate | `client.go` `Click()` method | Return `ErrPermissionDenied` without calling agent |
| `set_value` permission gate | `client.go` `SetValue()` method | Return `ErrPermissionDenied` without calling agent |
| `ahk_run` permission gate | `client.go` `AHKRun()` method | Return `ErrPermissionDenied` without calling agent |
| CLI destructive op confirmation | `desktop.py` `click`, `set`, `ahk` subcommands | Print `"error: --confirm flag required for destructive operations"` to stderr; exit code 1 |
| MCP permission gate | `mcp_server.py` `desktop_click`, `desktop_set_value`, `desktop_ahk` | Return `{"error": "permission_denied", "reason": "...", "tool": "<name>"}` |
| Audit log initialisation | `client.go` `callAndAudit()` (called by every exported method) | Return `ErrAuditLog` and abort method without calling agent |
| Audit log append-only | `client.go` `audit()` helper | Opens file with `O_APPEND\|O_CREATE\|O_WRONLY`; never truncates |

---

## 4. AHK Manifest Reference

`ahk/manifest.json` describes every bundled AHK script.

### Schema

```json
{
  "scripts": [
    {
      "name": "<filename.ahk>",
      "description": "<what the script automates>",
      "trigger_method": "ahk_run | desktop_ahk CLI | desktop_ahk MCP",
      "uiautomation_replacement": "<tool_name or null>",
      "is_fallback_only": true | false
    }
  ]
}
```

### Fields

| Field | Type | Description |
|---|---|---|
| `name` | string | Filename of the `.ahk` script (no path) |
| `description` | string | Human-readable description of what the script does |
| `trigger_method` | string | How to run: via `ahk_run` JSON-RPC, `navig desktop ahk` CLI, or `desktop_ahk` MCP tool |
| `uiautomation_replacement` | string\|null | Name of the `uiautomation`-native equivalent, or `null` if no UIA replacement exists |
| `is_fallback_only` | bool | `true` = AHK is the only way; `false` = UIA alternative is available |

### Adding a new script

1. Drop the `.ahk` file into `navig-core/host/internal/desktop/ahk/`.
2. Add an entry to `manifest.json` following the schema above.
3. Test via: `navig desktop ahk ./ahk/my_script.ahk --confirm`

---

## 5. Quickstart

1. **Install Python dependencies** (Windows only):
   ```powershell
   pip install uiautomation comtypes
   ```

2. **Install AutoHotkey v2** (optional, only needed for `ahk_run`/`desktop_ahk`):
   Download from https://www.autohotkey.com and ensure `AutoHotkey64.exe` is on `PATH`.

3. **Configure the audit log path** (optional — defaults to `~/.navig/logs/desktop_audit.jsonl`):
   ```powershell
   $env:NAVIG_DESKTOP_AUDIT_LOG = "C:\navig-logs\desktop_audit.jsonl"
   ```

4. **Verify Python path** (optional — defaults to the current Python executable):
   ```powershell
   $env:NAVIG_PYTHON_PATH = "C:\Python312\python.exe"
   ```

5. **Run the health check**:
   ```powershell
   navig desktop ping
   ```
   Expected output: `{"ok": true}`
