// crates/core_api/src/routes/tools.rs
//! POST /v1/tools/execute — tool dispatch.

use axum::{extract::State, Json};
use serde::{Deserialize, Serialize};
use crate::{error::ApiError, state::AppState};

#[derive(Debug, Deserialize)]
pub struct ToolsExecuteRequest {
    pub tool_name:  String,
    pub parameters: serde_json::Value,
    pub timeout_ms: Option<u64>,
}

#[derive(Debug, Serialize)]
pub struct ToolsExecuteResponse {
    pub execution_id: String,
    pub success:      bool,
    pub output:       serde_json::Value,
    pub latency_ms:   u64,
}

pub async fn handler(
    State(_state): State<AppState>,
    Json(_req):    Json<ToolsExecuteRequest>,
) -> Result<Json<ToolsExecuteResponse>, ApiError> {
    // TODO: route to core_plugins tool executor
    Err(ApiError::internal("tools not yet wired"))
}
