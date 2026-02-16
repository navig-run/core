# NAVIG Core

NAVIG Core is the Python runtime and CLI that powers the NAVIG ecosystem.
It provides an automation-first control plane for infrastructure operations, agent workflows, and MCP-based integrations.

## What NAVIG Core Includes

- Cross-platform CLI for hosts, apps, databases, tunnels, workflows, and automation
- Agent runtime components (goals, remediation, learning, service management)
- MCP server/client surfaces for tool/resource integration
- Credential and vault support
- Task queue and gateway foundations for multi-surface operation

## Project Status

- Python support: **3.10+**
- License: **Apache-2.0**
- Open-source model: code is open, NAVIG trademarks remain protected (see `TRADEMARK.md`)

## Installation

### From source (recommended for contributors)

```bash
git clone https://github.com/navigrun/navig-core.git
cd navigrun/navig-core
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e .[dev]
```

### Verify installation

```bash
navig --help
navig --version
```

## Quickstart

1. Initialize user workspace/config:

```bash
navig init
```

2. Add a host:

```bash
navig host add
```

3. Validate configuration:

```bash
navig config validate
```

4. Run a safe command:

```bash
navig run "uname -a"
```

5. Inspect MCP tools/resources (if using MCP workflow):

```bash
navig mcp serve --transport stdio
```

## Configuration

- Main config lives in `~/.navig/`
- Personal/state workspace files are owned by `~/.navig/workspace/` (see `docs/WORKSPACE_OWNERSHIP.md`)
- Use examples under `examples/hosts/` and docs under `docs/`
- Never commit real secrets or `.env` files

## Development

### Lint and format

```bash
ruff check navig tests
ruff format --check navig tests
```

### Run tests

```bash
pytest
```

Coverage gate is enforced at **65%** minimum in repository test config.

### Build package

```bash
python -m build
```

## Repository Documentation

- `docs/` - user and architecture docs
- `CONTRIBUTING.md` - contribution workflow
- `SECURITY.md` - vulnerability reporting and SLA
- `OFFICIAL_BUILDS.md` - release verification and provenance policy
- `GOVERNANCE.md` - project decision model and maintainer process

## Open-Source and Commercial Strategy

NAVIG Core code is open-source under Apache-2.0.
Brand usage, official build identity, and hosted ecosystem layers are protected and governed separately:

- Trademarks: `TRADEMARK.md`
- Official release policy: `OFFICIAL_BUILDS.md`
- Governance and contribution model: `GOVERNANCE.md`, `CONTRIBUTING.md`

## Support

- Security issues: see `SECURITY.md` (private reporting only)
- Bugs/features: GitHub Issues
- Community discussion: GitHub Discussions (or community channels listed in `.github/ISSUE_TEMPLATE/config.yml`)
