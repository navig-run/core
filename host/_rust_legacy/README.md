# NAVIG Core Host

Production-grade cross-platform Rust daemon replacing `navig-core` (Python).

## Workspace Crates

| Crate | Type | Role |
|---|---|---|
| `core_host` | bin | Entry point — wires all subsystems, lifecycle, graceful shutdown |
| `core_config` | lib | Layered config (TOML → env → CLI) + logging init |
| `core_auth` | lib | JWT/token generation, scope enforcement, secret store |
| `core_api` | lib | Axum HTTP server, routes, middleware, health/metrics |
| `core_events` | lib | Typed async pub/sub event bus |
| `core_plugins` | lib | Python subprocess manager (stdin/stdout JSON-RPC) |
| `core_os` | lib | OS-specific: autostart, keychain, platform paths |
| `core_tray` | lib | System tray icon + menu (Windows/macOS; feature-gated) |

## Quick Start

```bash
# Debug build (all features)
cargo build

# Release build (no debug endpoints)
cargo build --release

# With tray + debug endpoints (dev)
cargo build --features "tray debug-endpoints"

# Headless server build (no tray)
cargo build --release --no-default-features --features "headless"

# Run
cargo run -- --config config/dev.toml
```

## Feature Flags

| Flag | Default | Description |
|---|---|---|
| `tray` | on | System tray UI (requires native libs) |
| `debug-endpoints` | off | `/debug/config` route (dev builds only) |
| `headless` | off | Strips tray; CI/server builds |

## HTTP API

Base URL: `http://127.0.0.1:42424`

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | None | Liveness + provider status |
| GET | `/v1/status` | Bearer | Extended status + module map |
| POST | `/v1/router/complete` | Bearer | LLM routing + prompt transform |
| POST | `/v1/inbox/ingest` | Bearer | Inbox item ingestion |
| POST | `/v1/tools/execute` | Bearer | Tool execution |

Full contract: [`docs/architecture/CORE_HOST_API_CONTRACT.md`](../docs/architecture/CORE_HOST_API_CONTRACT.md)

## File Paths

| Artifact | Windows | Linux |
|---|---|---|
| Config | `%APPDATA%\Navig\Core\config\core.toml` | `~/.config/navig/core.toml` |
| Logs | `%LOCALAPPDATA%\Navig\Core\logs\` | `~/.local/share/navig/logs/` |
| Cache | `%LOCALAPPDATA%\Navig\Core\cache\` | `~/.local/share/navig/cache/` |
| Secrets | `%APPDATA%\Navig\Core\secrets.dat` (DPAPI) | libsecret / `~/.local/share/navig/secrets.enc` |
| IPC | `\\.\pipe\navig-core` | `/tmp/navig-core.sock` |

## Migration Plan

See [`plan/README.md`](../plan/README.md).
