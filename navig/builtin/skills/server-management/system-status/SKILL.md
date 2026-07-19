---
name: system-status
description: Check server health - uptime, memory, CPU load, running processes, and general system info
user-invocable: true
navig-commands:
  - navig host use {host}
  - navig run "uptime"
  - navig run "free -h"
  - navig run "top -bn1 | head -20"
  - navig run "uname -a"
examples:
  - "How is my server doing?"
  - "Check memory on production"
  - "What's the server load?"
  - "Show running processes"
  - "Is the server overloaded?"
---

# System Status Check

When the user asks about server health, load, memory, uptime, or processes.

## Steps

1. **Identify the host**: Extract server name from query
2. **Switch host**: `navig host use {host}`
3. **Run diagnostics**: Choose relevant commands based on what was asked

## Commands by Topic

### General Health (default - run all)

```bash
navig run "uptime && free -h && df -h / | tail -1"
```

### Memory Usage

```bash
navig run "free -h"
```

**Response format:**
```
🧠 Memory on {host}:

RAM:  3.2GB used / 8GB total (40%)
Swap: 128MB used / 2GB total (6%)

✅ Memory looks healthy
```

### CPU / Load Average

```bash
navig run "uptime"
navig run "nproc"
```

**Response format:**
```
⚡ CPU Load on {host}:

Load average: 0.45, 0.38, 0.42 (4 cores)
Uptime: 45 days, 3 hours

✅ Load is normal (0.45 / 4 cores = 11%)
```

**Thresholds** (per core):
- < 0.7: ✅ Normal
- 0.7 - 1.0: ⚠️ Moderate
- > 1.0: 🔴 High load

### Running Processes

```bash
navig run "ps aux --sort=-%mem | head -15"
```

**Response format:**
```
📊 Top Processes on {host}:

1. mysql      - 1.2GB RAM (15%)
2. nginx      - 256MB RAM (3%)
3. php-fpm    - 512MB RAM (6%)
4. node       - 384MB RAM (5%)
```

### System Info

```bash
navig run "uname -a"
navig run "cat /etc/os-release | head -4"
navig run "nproc && free -h | head -2"
```

**Response format:**
```
🖥️ System Info for {host}:

OS: Ubuntu 22.04 LTS
Kernel: 5.15.0-91-generic
CPU: 4 cores
RAM: 8GB total
Uptime: 45 days
```

## Full Health Check

When user says "how is my server" or "server health" — run everything:

```bash
navig run "echo '=== UPTIME ===' && uptime && echo '=== MEMORY ===' && free -h && echo '=== DISK ===' && df -h / | tail -1 && echo '=== LOAD ===' && cat /proc/loadavg"
```

**Response format:**
```
🏥 Health Check for {host}:

⏱️ Uptime: 45 days
⚡ Load: 0.45 (4 cores) ✅
🧠 Memory: 3.2GB / 8GB (40%) ✅
💾 Disk: 45GB / 100GB (55%) ✅

Overall: All systems healthy! 🚀
```

## Proactive Suggestions

- High memory (>80%): "⚠️ Memory is running low. Want me to check which processes are using the most?"
- High load (>1.0/core): "⚠️ CPU load is elevated. Want me to find the culprit?"
- High swap usage: "⚠️ Server is swapping heavily. This slows everything down. Consider adding more RAM."
- Long uptime (>90 days): "💡 Server has been up for 90+ days. Consider scheduling a reboot for pending kernel updates."

## Error Handling

- **Host not found**: "I don't have a server called '{name}'. Available hosts: {list}"
- **Connection failed**: "Can't reach {host}. Is it online? Try: `navig host test`"
- **Timeout**: "Commands are timing out. Server might be overloaded."
