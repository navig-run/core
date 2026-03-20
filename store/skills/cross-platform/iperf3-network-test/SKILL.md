```skill
---
name: iperf3-network-test
description: Run iperf3 network speed tests — client or server mode
user-invocable: true
navig-commands:
  - navig net iperf3 client --host {host} --duration {seconds}
  - navig net iperf3 server
  - navig net iperf3 client --host {host} --udp --duration 10
requires:
  - iperf3 (from C:\USB\network\iperf3\ on Windows; system iperf3 on Linux/Mac)
examples:
  - "Run a speed test to 10.0.0.1"
  - "Start an iperf3 server on this machine"
  - "Test UDP throughput to my server"
  - "Run a 30-second bandwidth test"
os: [windows, linux, mac]
---

# iperf3 Network Speed Test

Measure TCP/UDP bandwidth between two machines using iperf3.

## Prerequisites

- On Windows: USB binary at `C:\USB\network\iperf3\iperf3.exe` (auto-discovered)
- On Linux/Mac: `iperf3` must be in PATH (`apt install iperf3` / `brew install iperf3`)
- One machine must run server mode, the other client mode

## Common Tasks

### Measure TCP throughput (client → server)

**User says:** "Test my network speed to 10.0.0.10"

```bash
navig net iperf3 client --host 10.0.0.10 --duration 10
```

**Response:** JSON with `bits_per_second`, `retransmits`, direction.

### Run as server

**User says:** "Start iperf3 server"

```bash
navig net iperf3 server --port 5201
```

Listens until killed. Returns server port on start.

### UDP test

```bash
navig net iperf3 client --host 10.0.0.10 --udp --duration 10
```

### Bidirectional (reverse) test

```bash
navig net iperf3 client --host 10.0.0.10 --reverse
```

## Safety Notes

- `--dry-run` prints the command that would be executed without running it
- Server mode runs indefinitely until interrupted (NAVIG will capture termination signal)
```
