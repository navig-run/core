// crates/core_os/src/lib.rs
//! # core_os
//!
//! OS-specific functionality:
//! - **Keychain / secret store** — DPAPI (Windows), Keychain (macOS), libsecret / kwallet (Linux)
//! - **Autostart** — registry run key (Windows), launchd plist (macOS), systemd unit (Linux)
//! - **Platform paths** — consistent data / log / cache dirs

pub mod autostart;
pub mod keychain;
pub mod paths;

pub use keychain::{KeychainError, get_secret, set_secret, delete_secret};
pub use autostart::{AutostartError, enable_autostart, disable_autostart, is_autostart_enabled};
pub use paths::PlatformPaths;
