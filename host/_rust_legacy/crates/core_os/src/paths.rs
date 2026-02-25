// crates/core_os/src/paths.rs
//! Consistent platform paths for config, data, logs, and cache.

use std::path::PathBuf;

/// All relevant filesystem paths for NAVIG Core Host.
#[derive(Debug, Clone)]
pub struct PlatformPaths {
    /// `%APPDATA%/Navig/Core/config/` or `~/.config/navig/`
    pub config_dir: PathBuf,
    /// `%LOCALAPPDATA%/Navig/Core/logs/` or `~/.local/share/navig/logs/`
    pub log_dir: PathBuf,
    /// `%LOCALAPPDATA%/Navig/Core/cache/` or `~/.local/share/navig/cache/`
    pub cache_dir: PathBuf,
    /// `%APPDATA%/Navig/Core/data/` or `~/.local/share/navig/data/`
    pub data_dir: PathBuf,
}

impl PlatformPaths {
    /// Resolve paths using the system dirs crate.
    pub fn resolve() -> Self {
        #[cfg(target_os = "windows")]
        let (cfg_base, local_base) = (
            dirs::config_dir().unwrap_or_else(|| PathBuf::from(".")),
            dirs::data_local_dir().unwrap_or_else(|| PathBuf::from(".")),
        );
        #[cfg(not(target_os = "windows"))]
        let (cfg_base, local_base) = (
            dirs::config_dir().unwrap_or_else(|| PathBuf::from(".")),
            dirs::data_local_dir().unwrap_or_else(|| PathBuf::from(".")),
        );

        Self {
            config_dir: cfg_base.join("Navig").join("Core").join("config"),
            log_dir:    local_base.join("Navig").join("Core").join("logs"),
            cache_dir:  local_base.join("Navig").join("Core").join("cache"),
            data_dir:   local_base.join("Navig").join("Core").join("data"),
        }
    }

    /// Create all directories (idempotent).
    pub fn ensure_dirs(&self) -> std::io::Result<()> {
        for dir in [&self.config_dir, &self.log_dir, &self.cache_dir, &self.data_dir] {
            std::fs::create_dir_all(dir)?;
        }
        Ok(())
    }
}
