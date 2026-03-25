# NAVIG Roadmap

> The canonical strategy document is [`.navig/plans/ROADMAP_MASTER.md`](.navig/plans/ROADMAP_MASTER.md).
> This file is the community-facing summary. Timelines are estimates.

---

## Shipped in v2.4.x

- ✅ Python 3.10+ requirement enforced and documented
- ✅ `tomli` dependency pinned for Python < 3.11 compatibility
- ✅ `pydantic>=2.0` promoted to core dependency
- ✅ Encrypted vault with per-host credential injection at runtime
- ✅ SSH MITM prevention and credential security hardening
- ✅ Self-healing daemon with exponential backoff restart
- ✅ Telegram gateway — resilient reconnection, improved command parsing
- ✅ MCP server — tool and resource exposure for AI editors
- ✅ `NAVIG_CONFIG_DIR` environment variable override for config root
- ✅ LAN mesh peer discovery — Phase 1 (UDP multicast, local-only)

---

## Current focus

- 🔄 **Mesh Phase 1 stabilisation** — peer registration, proxy routing, graceful degradation to local
- 🔄 **Learning system** — improved error pattern detection, personalised recommendations
- 🔄 **Matrix gateway** — bridge stability, better event handling
- 🔄 **Packages system** — extensibility framework for custom commands and integrations
- 🔄 **Store system** — community-contributed runbooks, checklists, skills, workflow templates
- 🔄 **Test coverage** — maintain ≥65% coverage as surface area grows

---

## Next (3–6 months)

- **Multi-user support** — shared configurations for small teams
- **Backup improvements** — encrypted backups, remote storage backends (S3, Backblaze B2)
- **Health dashboard** — lightweight UI for multi-server monitoring
- **Enhanced AI context** — better project awareness, improved command suggestions
- **Docker Compose management** — multi-container orchestration workflows
- **Mesh Phase 2** — WAN-capable with secure mesh token auth

---

## Future (6+ months)

- **Cross-server orchestration** — coordinate deployments across multiple hosts
- **Workflow automation triggers** — event-driven actions from health checks or schedules
- **Integration marketplace** — community plugins and packs directory
- **Metrics retention** — long-term storage and visualisation of system metrics
- **Incident response automation** — automated runbook execution during detected outages

---

## How to influence the roadmap

- Open an issue or start a discussion on [GitHub](https://github.com/navig-run/core/discussions)
- Sponsor development via [GitHub Sponsors](https://github.com/sponsors/miztizm) to accelerate specific areas
- Contribute directly — see [CONTRIBUTING.md](CONTRIBUTING.md)

---

<!-- Last updated: March 2026 · NAVIG v2.4.13 -->
