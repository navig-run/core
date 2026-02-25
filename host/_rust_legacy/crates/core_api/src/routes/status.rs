// crates/core_api/src/routes/status.rs
//! GET /v1/status — authenticated extended status.

use axum::{extract::State, Json};
use chrono::Utc;
use serde::Serialize;
use crate::state::AppState;

#[derive(Debug, Serialize)]
pub struct StatusResponse {
    pub status:    &'static str,
    pub timestamp: String,
    pub version:   String,
    pub uptime_ms: u64,
    pub modules:   Vec<ModuleStatus>,
}

#[derive(Debug, Serialize)]
pub struct ModuleStatus {
    pub name:    String,
    pub healthy: bool,
    pub message: Option<String>,
}

pub async fn handler(State(_state): State<AppState>) -> Json<StatusResponse> {
    // TODO: derive from EventBus / module registry
    Json(StatusResponse {
        status:    "ok",
        timestamp: Utc::now().to_rfc3339(),
        version:   env!("CARGO_PKG_VERSION").to_owned(),
        uptime_ms: 0,
        modules:   vec![],
    })
}
