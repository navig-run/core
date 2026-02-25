// crates/core_os/src/autostart.rs
//! Platform-specific autostart registration.

use thiserror::Error;

#[derive(Debug, Error)]
pub enum AutostartError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[cfg(target_os = "windows")]
    #[error("registry error: {0}")]
    Registry(#[from] winreg::Error),
    #[error("autostart not supported on this platform")]
    Unsupported,
}

/// Register the current executable to run at system login.
pub fn enable_autostart() -> Result<(), AutostartError> {
    _enable()
}

/// Remove the autostart registration.
pub fn disable_autostart() -> Result<(), AutostartError> {
    _disable()
}

/// Return `true` if autostart is currently registered.
pub fn is_autostart_enabled() -> Result<bool, AutostartError> {
    _is_enabled()
}

// ── Windows ───────────────────────────────────────────────────────────────────

#[cfg(target_os = "windows")]
mod platform {
    use super::AutostartError;
    use std::path::PathBuf;
    use winreg::{enums::*, RegKey};

    const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
    const VALUE_NAME: &str = "NavigCoreHost";

    fn exe_path() -> std::io::Result<PathBuf> {
        std::env::current_exe()
    }

    pub fn enable() -> Result<(), AutostartError> {
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let (key, _) = hkcu.create_subkey(RUN_KEY)?;
        key.set_value(VALUE_NAME, &exe_path()?.to_string_lossy().into_owned())?;
        Ok(())
    }

    pub fn disable() -> Result<(), AutostartError> {
        let hkcu  = RegKey::predef(HKEY_CURRENT_USER);
        let key   = hkcu.open_subkey_with_flags(RUN_KEY, KEY_WRITE)?;
        key.delete_value(VALUE_NAME)?;
        Ok(())
    }

    pub fn is_enabled() -> Result<bool, AutostartError> {
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let key  = hkcu.open_subkey(RUN_KEY)?;
        let val: Result<String, _> = key.get_value(VALUE_NAME);
        Ok(val.is_ok())
    }
}

// ── macOS ─────────────────────────────────────────────────────────────────────

#[cfg(target_os = "macos")]
mod platform {
    use super::AutostartError;

    pub fn enable()     -> Result<(), AutostartError> { Err(AutostartError::Unsupported) }
    pub fn disable()    -> Result<(), AutostartError> { Err(AutostartError::Unsupported) }
    pub fn is_enabled() -> Result<bool, AutostartError> { Ok(false) }
}

// ── Linux / other ─────────────────────────────────────────────────────────────

#[cfg(not(any(target_os = "windows", target_os = "macos")))]
mod platform {
    use super::AutostartError;

    pub fn enable()     -> Result<(), AutostartError> { Err(AutostartError::Unsupported) }
    pub fn disable()    -> Result<(), AutostartError> { Err(AutostartError::Unsupported) }
    pub fn is_enabled() -> Result<bool, AutostartError> { Ok(false) }
}

// ── Dispatch shims ────────────────────────────────────────────────────────────

fn _enable()     -> Result<(), AutostartError> { platform::enable() }
fn _disable()    -> Result<(), AutostartError> { platform::disable() }
fn _is_enabled() -> Result<bool, AutostartError> { platform::is_enabled() }
