# tools/ — maintainer tooling

Scripts the **maintainer** runs. Nothing here is meant for users installing or running
NAVIG — for that, see [`../installers/`](../installers/README.md).

This directory is deliberately **excluded from every published artifact**, by three
independent mechanisms:

1. `pyproject.toml` `[tool.setuptools.packages.find] include = ["navig*"]` — only `navig*`
   packages are collected, and `tools/` is not a package;
2. `[tool.setuptools.package-data]` covers only `navig`;
3. `MANIFEST.in` has no `tools` include, so it is absent from the sdist too.

That exclusion is **load-bearing**, not incidental: `navig/license/__init__.py` uses the
presence of `tools/dev_license.py` to tell a source checkout from an installed wheel.
Do not add a `tools` entry to packaging config.

| Script | Purpose | Invoked by |
|---|---|---|
| `_version_sync.py` | Propagate the pyproject version into `latest.json` (and the website) | `npm run version:sync` |
| `version_bump.py` | Bump / commit / tag / push a release | `npm run version:show`, `release:*` |
| `release.sh` | Full release sequence | manual |
| `build.py` | Build entry point | manual |
| `precommit_navig_security.sh` | Secret / security gate | `.pre-commit-config.yaml` |
| `sync-instructions.ps1` | Propagate agent instructions to tool-specific config | manual |
| `migrate_navig_config.py` | One-off config migration helper | probed by `navig migrate` |
| `seed_demo.py` | Seed demo data for local development | manual |
| `test-clean-install.ps1` | Verify a clean install on Windows | manual |
| `dev_license.py` | Switch Harbor license tiers locally | `npm run license:*` |
| `audit_home_reads.py` | Report which tests read the operator's REAL `~/.navig` (opt-in pytest plugin) | manual |
| `license_sign.py` | Sign license keys | manual (private keys required) |
| `export_registry.py` | Regenerate `generated/` command manifests | CI + `npm run ci` |
| `verify_install.py` | Drive an installed wheel end-to-end | `npm run ci:install` |
