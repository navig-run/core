// crates/core_api/src/error.rs
//! Standard JSON error envelope returned on all non-2xx responses.

use axum::{http::StatusCode, response::{IntoResponse, Response}, Json};
use serde::{Deserialize, Serialize};

#[derive(Debug, Serialize, Deserialize)]
pub struct ErrorBody {
    pub code:    String,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub detail:  Option<serde_json::Value>,
}

/// Typed API error that serialises to the standard envelope.
#[derive(Debug)]
pub struct ApiError {
    pub status:  StatusCode,
    pub code:    &'static str,
    pub message: String,
    pub detail:  Option<serde_json::Value>,
}

impl ApiError {
    pub fn internal(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::INTERNAL_SERVER_ERROR, code: "internal_error", message: msg.into(), detail: None }
    }
    pub fn unauthorized(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::UNAUTHORIZED, code: "unauthorized", message: msg.into(), detail: None }
    }
    pub fn bad_request(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::BAD_REQUEST, code: "bad_request", message: msg.into(), detail: None }
    }
    pub fn not_found(msg: impl Into<String>) -> Self {
        Self { status: StatusCode::NOT_FOUND, code: "not_found", message: msg.into(), detail: None }
    }
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let body = ErrorBody { code: self.code.to_owned(), message: self.message, detail: self.detail };
        (self.status, Json(body)).into_response()
    }
}
