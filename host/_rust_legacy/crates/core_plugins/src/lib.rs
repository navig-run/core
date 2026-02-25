// crates/core_plugins/src/lib.rs
//! # core_plugins
//!
//! Manages Python subprocess(es) communicating via stdin/stdout JSON-RPC 2.0.
//!
//! ## Protocol
//! Each line on stdin/stdout is a JSON object:
//! - **Request** (host → Python): `{ "jsonrpc": "2.0", "id": "ulid", "method": "router.complete", "params": {...} }`
//! - **Response** (Python → host): `{ "jsonrpc": "2.0", "id": "ulid", "result": {...} }` or `{ ..., "error": {...} }`
//! - **Stream chunk** (Python → host): `{ "jsonrpc": "2.0", "id": "ulid", "stream": true, "chunk": "..." }`
//! - **Stream end**: `{ "jsonrpc": "2.0", "id": "ulid", "stream_end": true }`
//!
//! No TCP ports are opened.

pub mod jsonrpc;
pub mod plugin;
pub mod registry;

pub use plugin::{Plugin, PluginConfig, PluginError};
pub use registry::PluginRegistry;
pub use jsonrpc::{RpcRequest, RpcResponse, RpcError};
