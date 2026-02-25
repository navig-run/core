// crates/core_auth/src/store.rs
//! In-process revocation store + token registry.

use std::collections::HashSet;
use std::sync::{Arc, RwLock};
use thiserror::Error;

#[derive(Debug, Error)]
pub enum TokenStoreError {
    #[error("lock poisoned")]
    Poison,
}

/// Thread-safe JWT revocation store.
///
/// Holds revoked JTIs and clears them on TTL.  For extended deployments,
/// replace with a SQLite-backed store.
#[derive(Debug, Clone, Default)]
pub struct TokenStore {
    revoked: Arc<RwLock<HashSet<String>>>,
}

impl TokenStore {
    pub fn new() -> Self {
        Self::default()
    }

    /// Revoke a JWT by its `jti`.
    pub fn revoke(&self, jti: impl Into<String>) -> Result<(), TokenStoreError> {
        self.revoked.write().map_err(|_| TokenStoreError::Poison)?.insert(jti.into());
        Ok(())
    }

    /// Return `true` if `jti` has been revoked.
    pub fn is_revoked(&self, jti: &str) -> Result<bool, TokenStoreError> {
        Ok(self.revoked.read().map_err(|_| TokenStoreError::Poison)?.contains(jti))
    }

    /// Clear all revocations (e.g. after process restart; tokens expire naturally).
    pub fn clear(&self) -> Result<(), TokenStoreError> {
        self.revoked.write().map_err(|_| TokenStoreError::Poison)?.clear();
        Ok(())
    }
}
