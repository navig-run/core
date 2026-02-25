// crates/core_config/src/logging.rs
//! Structured logging initialisation using `tracing-subscriber` + rolling file writer.

use crate::loader::{ConfigError, LogConfig};
use once_cell::sync::OnceCell;
use tracing::level_filters::LevelFilter;
use tracing_subscriber::{
    fmt::{self, writer::MakeWriterExt},
    layer::SubscriberExt,
    EnvFilter, Registry,
};

static LOG_GUARD: OnceCell<tracing_appender::non_blocking::WorkerGuard> = OnceCell::new();

/// Initialise global tracing subscriber.  Must be called **once**.
pub fn init_logging(cfg: &LogConfig) -> Result<(), ConfigError> {
    let level_filter: LevelFilter = cfg
        .level
        .parse()
        .map_err(|_| ConfigError::LogInit(format!("bad log level: {}", cfg.level)))?;

    let env_filter = EnvFilter::builder()
        .with_default_directive(level_filter.into())
        .from_env_lossy();

    match cfg.dir.as_ref() {
        Some(dir) => {
            std::fs::create_dir_all(dir)?;
            let appender = tracing_appender::rolling::daily(dir, "navig-core-host.log");
            let (nb_writer, guard) = tracing_appender::non_blocking(appender);
            LOG_GUARD.set(guard).ok();

            let subscriber = Registry::default()
                .with(env_filter)
                .with(fmt::layer().json().with_writer(nb_writer.and(std::io::stderr)));

            tracing::subscriber::set_global_default(subscriber)
                .map_err(|e| ConfigError::LogInit(e.to_string()))
        }
        None => {
            let subscriber = Registry::default()
                .with(env_filter)
                .with(fmt::layer().pretty().with_writer(std::io::stderr));

            tracing::subscriber::set_global_default(subscriber)
                .map_err(|e| ConfigError::LogInit(e.to_string()))
        }
    }
}
