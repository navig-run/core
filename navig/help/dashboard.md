# navig dashboard

A live operator console: this install's identity, its services, its safety
state, its hosts and its recent operations on one screen.

## Usage

```bash
navig dashboard              # live view (full-screen, 2 Hz)
navig dashboard --no-live    # one snapshot — also what you get when piped
navig dashboard -r 10        # re-read services every 10 s (default 5)
```

## Panels

| Panel | Shows | Same source as |
|-------|-------|----------------|
| Identity | Your sigil (the one `navig whoami` draws), active space + plan progress, version | `navig whoami` · `navig space` |
| Services | Daemon (pid, uptime, heartbeat), gateway health, bot, cron, reach, default AI | `navig service status` · `navig status` |
| Safety | Ledger chain, pending approvals, host locks, in-flight operations, store wiring | `navig ledger verify` · `navig approve list` · `navig store status` |
| Hosts | Each host's SSH port reachability + latency (TCP, not ICMP) | `navig host test` |
| Activity | The last operations from the hash-chained ledger | `navig ledger show` |

The header counts everything that needs attention (a broken chain, waiting
approvals, a held lock, an unreachable host, a stale daemon heartbeat, a source
that could not be read).

## Keys

| Key | Action |
|-----|--------|
| `q` | Quit |
| `r` | Re-read every source now |
| `d` | Run `navig doctor` and show the problems |
| `g` | Open the deck in your browser |
| `?` | Help |

## See also

- `navig status` — one-line status
- `navig doctor` — full health report
- `navig whoami` — the identity card
