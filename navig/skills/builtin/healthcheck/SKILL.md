# Healthcheck Skill
**id:** healthcheck
**name:** System Healthcheck
**version:** 1.0.0
**category:** system
**tags:** health, status, monitoring, diagnostics
**platforms:** linux, macos, windows
**tools:** bash_exec, memory_store
**safety:** low
**user_invocable:** true
**description:** Run a lightweight system healthcheck and store a summary snapshot.

---

## Description

Runs a set of non-destructive diagnostic commands to report on:

- Disk usage (top 5 fullest mount points)
- Memory usage
- CPU load average
- Running process count
- Current working directory and hostname

Results are stored in session memory under the key `system.healthcheck.latest`.

---

## System Prompt / Behavior

You are a calm, methodical diagnostics agent.  Your goal is to run the healthcheck
commands one at a time, parse their output for key numbers, and produce a structured
summary in the following JSON shape:

```json
{
  "hostname": "...",
  "disk": [{"mount": "...", "percent_used": 0}],
  "memory_mb": {"total": 0, "used": 0, "free": 0},
  "load": [0.0, 0.0, 0.0],
  "process_count": 0,
  "status": "ok | warning | critical",
  "notes": []
}
```

Set `status` to:
- `"ok"` — all disks < 85% and memory < 90%
- `"warning"` — any disk >= 85% or memory >= 85%
- `"critical"` — any disk >= 95% or memory >= 95%

Store the result in memory with key `"system.healthcheck.latest"`.

---

## Examples

**User:** Run a system healthcheck
**Agent:**
1. `bash_exec(command="df -h")` → parse disk usage
2. `bash_exec(command="free -m")` → parse memory (Linux) or `vm_stat` (macOS)
3. `bash_exec(command="uptime")` → parse load averages
4. `bash_exec(command="hostname")` → get hostname
5. Assemble JSON summary
6. `memory_store(key="system.healthcheck.latest", value=<summary>, tags=["health", "snapshot"])`

**User:** What was the last healthcheck result?
**Agent:**
1. `memory_fetch(key="system.healthcheck.latest")` → return stored summary
