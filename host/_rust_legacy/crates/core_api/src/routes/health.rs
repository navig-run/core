// crates/core_api/src/routes/health.rs
//! GET /health — unauthenticated liveness probe.

use axum::{extract::State, Json};
use chrono::Utc;
use serde::Serialize;
use crate::state::AppState;

#[derive(Debug, Serialize)]
pub struct HealthResponse {
    pub status:    &'static str,
    pub timestamp: String,
    pub version:   &'static str,
}

pub async fn handler(_state: State<AppState>) -> Json<HealthResponse> {
    Json(HealthResponse {
        status:    "ok",
        timestamp: Utc::now().to_rfc3339(),
        version:   env!("CARGO_PKG_VERSION"),
    })
}
