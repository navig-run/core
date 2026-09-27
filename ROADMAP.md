# NAVIG Roadmap

The public summary of where NAVIG is and where it is heading. Timelines are estimates;
[CHANGELOG.md](CHANGELOG.md) is the record of what actually shipped.

---

## Shipped (3.x)

- ✅ **Spaces** — a folder with `.navig/` holding skills, agents, plans and memory; `navig space init / doctor`
- ✅ **Blocks** — installable, verifiable outcomes: `navig apply <block>` proves the result with a verified receipt
- ✅ **Operations ledger** — every action hash-chained (`navig ledger verify`), interrupted runs reaped, `navig undo` for reversible ones
- ✅ **Safety rails** — per-host locks between agents, an approval gate for dangerous commands, destructive-command confirmation
- ✅ **Lighthouse** — reach your machine from Telegram / the Deck through your own Cloudflare edge, no tunnel and no open port
- ✅ **Telegram extensions** — one switch per bot feature, and "off" really means off
- ✅ **Self-healing daemon** — supervised gateway and bot, heartbeat, scoped restarts that never touch another install
- ✅ **Plugins** — first-party plugins that extend `navig` and also run standalone ([navig-run/plugins](https://github.com/navig-run/plugins))
- ✅ **MCP server** — navig's tools and resources exposed to AI editors
- ✅ **Identity** — every install derives its own sigil (`navig whoami`)

## Current focus

- 🔄 **Cross-platform parity** — launchd autostart on macOS, systemd routing on Linux, CI on all three OSes
- 🔄 **Live dashboard** — `navig dashboard` rebuilt on the same readers as the CLI
- 🔄 **Standalone plugins** — every plugin installable and documented on its own
- 🔄 **Harbor Bay** — the marketplace for Skills, Personas, Spaces and Blocks
- 🔄 **LAN mesh (phase 1)** — peer discovery and proxying, always degrading to local

## Next (3–6 months)

- **Team spaces** — shared configuration and approvals for small teams
- **Backups** — encrypted, with remote storage backends (S3, Backblaze B2)
- **Container workflows** — Docker Compose operations as Blocks with receipts
- **Mesh phase 2** — WAN-capable peers with mesh-token auth

## Later

- **Cross-server orchestration** — one outcome coordinated across many hosts
- **Event-driven Blocks** — apply an outcome when a check or schedule fires
- **Metrics retention** — long-term storage and visualisation
- **Incident runbooks** — detected outage → proposed Block → approved fix, all on the ledger

---

## How to influence the roadmap

- Open an issue or a discussion on [GitHub](https://github.com/navig-run/core/discussions)
- Sponsor development via [GitHub Sponsors](https://github.com/sponsors/navig-run)
- Contribute directly — see [CONTRIBUTING.md](CONTRIBUTING.md)

<!-- Last updated: September 2026 · NAVIG 3.25 -->
