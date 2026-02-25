// crates/core_api/src/middleware.rs
//! Axum middleware: auth extraction, request ID, rate limiting stubs.

use axum::{
    extract::{Request, State},
    http::{header::AUTHORIZATION, StatusCode},
    middleware::Next,
    response::Response,
};
use core_auth::{validate_token, Scope};
use crate::{error::ApiError, state::AppState};

/// Bearer token extractor — validates JWT and checks scope.
pub async fn require_scope(
    State(state): State<AppState>,
    required:     Scope,
    mut req:      Request,
    next:         Next,
) -> Result<Response, ApiError> {
    let token = req
        .headers()
        .get(AUTHORIZATION)
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .ok_or_else(|| ApiError::unauthorized("missing bearer token"))?;

    let secret = state.config.auth.jwt_secret.as_bytes();
    let claims = validate_token(token, secret).map_err(|e| ApiError::unauthorized(e.to_string()))?;

    if state.token_store.is_revoked(&claims.jti).unwrap_or(false) {
        return Err(ApiError::unauthorized("token revoked"));
    }
    if !claims.scope.grants(&required) {
        return Err(ApiError::unauthorized(format!(
            "insufficient scope: required {required}, got {}",
            claims.scope
        )));
    }

    req.extensions_mut().insert(claims);
    Ok(next.run(req).await)
}
