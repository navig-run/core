// crates/core_os/src/keychain.rs
//! Cross-platform secret storage using the `keyring` crate.
//!
//! Backends:
//! - Windows  → Windows Credential Manager (DPAPI-backed)
//! - macOS    → Keychain
//! - Linux    → libsecret / kwallet (via `keyring`)

use keyring::Entry;
use thiserror::Error;

const SERVICE_NAME: &str = "navig-core-host";

#[derive(Debug, Error)]
pub enum KeychainError {
    #[error("keychain error: {0}")]
    Keyring(#[from] keyring::Error),
}

/// Store `value` under `key` in the OS keychain.
pub fn set_secret(key: &str, value: &str) -> Result<(), KeychainError> {
    Entry::new(SERVICE_NAME, key)?.set_password(value)?;
    Ok(())
}

/// Retrieve the value for `key` from the OS keychain.
///
/// Returns `None` if the entry does not exist.
pub fn get_secret(key: &str) -> Result<Option<String>, KeychainError> {
    let entry = Entry::new(SERVICE_NAME, key)?;
    match entry.get_password() {
        Ok(v)                                   => Ok(Some(v)),
        Err(keyring::Error::NoEntry)             => Ok(None),
        Err(e)                                   => Err(e.into()),
    }
}

/// Remove a secret from the OS keychain.
pub fn delete_secret(key: &str) -> Result<(), KeychainError> {
    Entry::new(SERVICE_NAME, key)?.delete_credential()?;
    Ok(())
}
