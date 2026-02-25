// crates/core_config/src/lib.rs
//! # core_config
//!
//! Layered configuration loader, CLI argument parser, and structured logging
//! initialiser for NAVIG Core Host.
//!
//! ## Layer resolution order (last wins):
//! 1. Built-in defaults (`config/default.toml`)
//! 2. Environment-specific file (`config/{env}.toml`)
//! 3. User override file (`$APPDATA/Navig/Core/config/core.toml`)
//! 4. Environment variables prefixed `NAVIG_`
//! 5. CLI flags (highest priority)

pub mod cli;
pub mod loader;
pub mod logging;

pub use cli::CliArgs;
pub use loader::{Config, ConfigError, ServerConfig, LogConfig, AuthConfig, PluginsConfig};
pub use logging::init_logging;

/// Convenience initialiser: parse CLI → load config → init logging.
///
/// Returns the resolved [`Config`] and parsed [`CliArgs`].
pub fn bootstrap() -> Result<(Config, CliArgs), ConfigError> {
    use clap::Parser;
    let args = CliArgs::parse();
    let cfg  = loader::load(args.config.as_deref(), args.env.as_deref())?;
    logging::init_logging(&cfg.log)?;
    Ok((cfg, args))
}
