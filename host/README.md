# NAVIG Core — Host Supervisor

This folder contains the **host supervisor** component of NAVIG.

## Current State

The host supervisor is being **rewritten from Rust to Go**.

| Subfolder | Purpose |
|---|---|
| `_rust_legacy/` | Original Rust scaffolding (frozen, reference) |
| *(root files)* | Future Go implementation lives here |

## Roadmap

1. ✅ Consolidate `navig-core/host/_rust_legacy/` → `navig-core/host/_rust_legacy/`
2. 🔲 Scaffold Go module in `navig-core/host/`
3. 🔲 Port supervisor logic from Rust → Go
4. 🔲 Delete `_rust_legacy/` once Go reaches parity

## Building

### Go (future)
```bash
cd navig-core/host
go build ./...
```

### Rust legacy (reference only)
```bash
cd navig-core/host/_rust_legacy
cargo check
```
