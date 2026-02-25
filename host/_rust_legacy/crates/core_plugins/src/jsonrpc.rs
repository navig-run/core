// crates/core_plugins/src/jsonrpc.rs
//! JSON-RPC 2.0 message types.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcRequest {
    pub jsonrpc: String,
    pub id:      String,
    pub method:  String,
    pub params:  serde_json::Value,
}

impl RpcRequest {
    pub fn new(id: impl Into<String>, method: impl Into<String>, params: serde_json::Value) -> Self {
        Self { jsonrpc: "2.0".into(), id: id.into(), method: method.into(), params }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(untagged)]
pub enum RpcResponse {
    Success { jsonrpc: String, id: String, result: serde_json::Value },
    Error   { jsonrpc: String, id: String, error: RpcError },
    Chunk   { jsonrpc: String, id: String, stream: bool, chunk: String },
    End     { jsonrpc: String, id: String, stream_end: bool },
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RpcError {
    pub code:    i64,
    pub message: String,
    pub data:    Option<serde_json::Value>,
}
