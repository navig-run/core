// crates/core_config/src/loader.rs
//! Config loading and merging logic.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use thiserror::Error;

// ── Errors ────────────────────────────────────────────────────────────────────

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("config read error: {0}")]
    Read(#[from] config::ConfigError),
    #[error("logging init error: {0}")]
    LogInit(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

// ── Sub-structs ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ServerConfig {
    pub bind:     String,
    pub log_reqs: bool,
}

impl Default for ServerConfig {
    fn default() -> Self {
        Self { bind: "127.0.0.1:42424".into(), log_reqs: true }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LogConfig {
    /// `trace` | `debug` | `info` | `warn` | `error`
    pub level: String,
    /// `json` | `pretty` | `compact`
    pub format: String,
    /// Directory for rolling log files; `None` → stdout only.
    pub dir: Option<PathBuf>,
}

impl Default for LogConfig {
    fn default() -> Self {
        Self {
            level:  "info".into(),
            format: "json".into(),
            dir:    None,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AuthConfig {
    /// JWT signing secret — MUST be overridden via env / secrets.
    pub jwt_secret:     String,
    /// Token TTL in seconds.
    pub token_ttl_secs: u64,
}

impl Default for AuthConfig {
    fn default() -> Self {
        Self {
            jwt_secret:     "CHANGEME".into(),
            token_ttl_secs: 3600,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginsConfig {
    /// Path to the Python interpreter.
    pub python_bin: PathBuf,
    /// Command + args for the router subprocess.
    pub router_cmd: Vec<String>,
    /// Seconds before subprocess I/O is considered hung.
    pub timeout_secs: u64,
}

impl Default for PluginsConfig {
    fn default() -> Self {
        Self {
            python_bin:   PathBuf::from("python"),
            router_cmd:   vec!["navig-core".into(), "rpc-serve".into()],
            timeout_secs: 30,
        }
    }
}

// ── Root config ───────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Config {
    pub server:  ServerConfig,
    pub log:     LogConfig,
    pub auth:    AuthConfig,
    pub plugins: PluginsConfig,
}

// ── Loader ────────────────────────────────────────────────────────────────────

/// Resolve and merge config from all layers (see module doc).
pub fn load(
    explicit_path: Option<&Path>,
    env_label:     Option<&str>,
) -> Result<Config, ConfigError> {
    use config::{Config as RawConfig, Environment, File, FileFormat};

    let mut builder = RawConfig::builder()
        .add_source(File::with_name("config/default").format(FileFormat::Toml).required(false));

    // Environment-specific override file
    let env_name = env_label.unwrap_or("production");
    builder = builder.add_source(
        File::with_name(&format!("config/{}", env_name))
            .format(FileFormat::Toml)
            .required(false),
    );

    // User override in APPDATA / XDG
    if let Some(user_dir) = user_config_dir() {
        let user_cfg = user_dir.join("core.toml");
        builder = builder.add_source(
            File::from(user_cfg).format(FileFormat::Toml).required(false),
        );
    }

    // Explicit path (highest file priority)
    if let Some(path) = explicit_path {
        builder = builder.add_source(File::from(path).format(FileFormat::Toml).required(true));
    }

    // Environment variables: NAVIG_SERVER__BIND, NAVIG_LOG__LEVEL, …
    builder = builder.add_source(
        Environment::with_prefix("NAVIG").separator("__").try_parsing(true),
    );

    let raw = builder.build()?;
    Ok(raw.try_deserialize()?)
}

fn user_config_dir() -> Option<PathBuf> {
    dirs::config_dir().map(|d| d.join("Navig").join("Core").join("config"))
}
