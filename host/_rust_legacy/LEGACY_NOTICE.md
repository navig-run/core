# ⚠️  RUST LEGACY SCAFFOLDING — DO NOT DEVELOP HERE

This directory contains the **original Rust-based host supervisor** code
that was located at `navig-core-host/` before the monorepo consolidation
on 2026-02-18.

## Status

- **FROZEN** — no new features or fixes.
- Retained for reference during the Go rewrite.
- Will be deleted once the Go host in `navig-core/host/` reaches parity.

## Building (reference only)

```bash
cd navig-core/host/_rust_legacy
cargo check
```

## Crate Map

| Crate | Role |
|---|---|
| `core_host` | Binary entry point — lifecycle, graceful shutdown |
| `core_config` | Layered TOML/env/CLI config + structured logging init |
| `core_auth` | JWT issuance, scope enforcement, token revocation store |
| `core_api` | Axum HTTP server — all routes and middleware |
| `core_events` | Typed async pub/sub event bus (tokio::broadcast) |
| `core_plugins` | Python subprocess manager (stdin/stdout JSON-RPC 2.0) |
| `core_os` | OS-specific: keychain, autostart, platform paths |
| `core_tray` | System tray icon + menu (Windows/macOS, feature-gated) |
