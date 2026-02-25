// crates/core_api/src/routes/router.rs
//! POST /v1/router/complete — LLM routing.

use axum::{extract::State, Json};
use serde::{Deserialize, Serialize};
use crate::{error::ApiError, state::AppState};

#[derive(Debug, Deserialize)]
pub struct RouterCompleteRequest {
    pub prompt:    String,
    pub model:     Option<String>,
    pub stream:    Option<bool>,
    pub max_tokens: Option<u32>,
}

#[derive(Debug, Serialize)]
pub struct RouterCompleteResponse {
    pub request_id: String,
    pub provider:   String,
    pub model:      String,
    pub content:    String,
    pub usage:      TokenUsage,
}

#[derive(Debug, Serialize)]
pub struct TokenUsage {
    pub prompt_tokens:     u32,
    pub completion_tokens: u32,
    pub total_tokens:      u32,
}

pub async fn handler(
    State(_state): State<AppState>,
    Json(_req):    Json<RouterCompleteRequest>,
) -> Result<Json<RouterCompleteResponse>, ApiError> {
    // TODO: delegate to core_plugins Python subprocess
    Err(ApiError::internal("router not yet wired"))
}
