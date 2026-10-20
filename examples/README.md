# NAVIG CLI — Examples

This directory contains usage examples for the NAVIG CLI.

## App & host configuration templates

App and host YAML config templates live in this repository under `registry/library/` — the registry is
the single source of truth, and the public `navig-run/registry` repo is its export:

| Content                 | Location                                     |
|-------------------------|----------------------------------------------|
| App configs             | `registry/library/apps/`                     |
| Host configs            | `registry/library/hosts/`                    |
| Formations              | `registry/library/formations/`               |
| Prompt templates        | `registry/library/prompts/`                  |

## Plugin examples

Plugin examples live in `registry/library/` and in each plugin's own `examples/`; see
[`examples/plugins/README.md`](plugins/README.md) for the current model.

## Shell examples

| File                         | Description                                         |
|------------------------------|-----------------------------------------------------|
| [run-complex-commands.sh](run-complex-commands.sh) | Examples of `navig run` with complex shell commands |
