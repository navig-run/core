// crates/core_config/src/cli.rs
//! CLI argument definitions via `clap`.

use clap::Parser;
use std::path::PathBuf;

#[derive(Debug, Parser)]
#[command(
    name    = "navig-core-host",
    about   = "NAVIG Core Host — Rust daemon",
    version
)]
pub struct CliArgs {
    /// Path to a TOML config file (overrides default search paths).
    #[arg(long, short = 'c', env = "NAVIG_CONFIG")]
    pub config: Option<PathBuf>,

    /// Runtime environment label (dev / staging / production).
    #[arg(long, short = 'e', default_value = "production", env = "NAVIG_ENV")]
    pub env: Option<String>,

    /// Bind address override, e.g. `0.0.0.0:42424`.
    #[arg(long, env = "NAVIG_BIND")]
    pub bind: Option<String>,

    /// Increase log verbosity (repeat for more: -v, -vv, -vvv).
    #[arg(long = "verbose", short = 'v', action = clap::ArgAction::Count)]
    pub verbosity: u8,
}
