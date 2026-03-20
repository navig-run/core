# Plugin Examples — Moved to navig-community

Plugin examples now live in the community repository:

**New model:** [`navig-community/examples/hello-app/`](https://github.com/navig-run/community/tree/main/examples/hello-app)

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
- [Community repo](https://github.com/navig-run/community)
