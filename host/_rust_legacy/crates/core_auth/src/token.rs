// crates/core_auth/src/token.rs
//! JWT issuance and validation.

use crate::scope::Scope;
use chrono::{Duration, Utc};
use jsonwebtoken::{decode, encode, Algorithm, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};
use thiserror::Error;
use ulid::Ulid;

#[derive(Debug, Error)]
pub enum TokenError {
    #[error("jwt error: {0}")]
    Jwt(#[from] jsonwebtoken::errors::Error),
    #[error("insufficient scope: required {required}, got {got}")]
    InsufficientScope { required: String, got: String },
    #[error("token expired")]
    Expired,
}

/// JWT claims payload.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Claims {
    /// Subject (e.g. client name / integration ID).
    pub sub: String,
    /// JWT ID (ULID) — used for revocation.
    pub jti: String,
    /// Granted scope.
    pub scope: Scope,
    /// Issued-at (Unix seconds).
    pub iat: i64,
    /// Expiry (Unix seconds).
    pub exp: i64,
}

/// Issue a new signed JWT.
pub fn issue_token(
    subject:    &str,
    scope:      Scope,
    ttl_secs:   u64,
    secret:     &[u8],
) -> Result<String, TokenError> {
    let now  = Utc::now();
    let exp  = now + Duration::seconds(ttl_secs as i64);
    let claims = Claims {
        sub:   subject.to_owned(),
        jti:   Ulid::new().to_string(),
        scope,
        iat:   now.timestamp(),
        exp:   exp.timestamp(),
    };
    let key = EncodingKey::from_secret(secret);
    Ok(encode(&Header::new(Algorithm::HS256), &claims, &key)?)
}

/// Validate a JWT and return its claims.
pub fn validate_token(token: &str, secret: &[u8]) -> Result<Claims, TokenError> {
    let key  = DecodingKey::from_secret(secret);
    let mut validation = Validation::new(Algorithm::HS256);
    validation.validate_exp = true;
    let data = decode::<Claims>(token, &key, &validation)?;
    Ok(data.claims)
}
