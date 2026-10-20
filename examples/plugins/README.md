# Plugin Examples — the registry

Plugin examples live in the registry, which is the source of truth in this repository:

**Model:** [`registry/examples/hello-app/`](../../../registry/examples/hello-app/) (exported to the public
`navig-run/registry`). The SDK is [`registry/sdk/`](../../../registry/sdk/).

The old Typer-based plugin model (which imported from `navig.plugins.*` internals) has been retired.
Use the new decoupled model — `plugin.json` + `handler.py` + plain `handle()` functions.

## New plugin structure

```
my-plugin/
  plugin.json     ← manifest: id, entry, provides, depends, config_defaults
  handler.py      ← lifecycle: on_load(ctx), on_unload(ctx), on_event(event, ctx)
  commands/
    __init__.py   ← COMMANDS dict
    hello.py      ← handle(args: dict, ctx) -> dict
  tests/
    test_hello.py
  README.md
```

## Install the SDK

```bash
pip install navig-sdk     # Python
npm install navig-sdk     # TypeScript / Node.js
```

- [SDK docs](../../sdk/README.md)
- [Registry](../../../registry/README.md)
