// crates/core_api/src/routes/inbox.rs
//! POST /v1/inbox/ingest — item ingestion.

use axum::{extract::State, Json};
use serde::{Deserialize, Serialize};
use crate::{error::ApiError, state::AppState};

#[derive(Debug, Deserialize)]
pub struct InboxIngestRequest {
    pub source:   String,
    pub content:  serde_json::Value,
    pub priority: Option<u8>,
    pub tags:     Option<Vec<String>>,
}

#[derive(Debug, Serialize)]
pub struct InboxIngestResponse {
    pub item_id:    String,
    pub enqueued:   bool,
    pub queue_depth: u64,
}

pub async fn handler(
    State(_state): State<AppState>,
    Json(_req):    Json<InboxIngestRequest>,
) -> Result<Json<InboxIngestResponse>, ApiError> {
    // TODO: enqueue item, publish InboxItemIngested event
    Err(ApiError::internal("inbox not yet wired"))
}
