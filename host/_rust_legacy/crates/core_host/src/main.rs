// crates/core_host/src/main.rs
//! # navig-core-host
//!
//! Entry point.  Boots all subsystems in dependency order, then drives the
//! tokio runtime until a shutdown signal is received.
//!
//! ## Boot sequence
//! 1. Parse CLI + load config + init structured logging (`core_config::bootstrap`)
//! 2. Resolve platform paths and ensure dirs exist (`core_os`)
//! 3. Create the typed event bus (`core_events`)
//! 4. Load auth secrets and build token store (`core_auth`)
//! 5. Start plugin registry + register Python subprocess (`core_plugins`)
//! 6. Start Axum HTTP server (`core_api`) — blocks until shutdown
//! 7. Graceful shutdown: stop plugins → publish DaemonStopping

use core_api::{serve, AppState};
use core_auth::{store::TokenStore, token::issue_token, Scope};
use core_config::bootstrap;
use core_events::{Event, EventBus};
use core_os::paths::PlatformPaths;
use core_plugins::{PluginConfig, PluginRegistry};
use eyre::Result;
use std::sync::Arc;
use tracing::{error, info};

#[tokio::main]
async fn main() -> Result<()> {
    // ── 1. Config + logging ───────────────────────────────────────────────────
    let (config, args) = bootstrap().map_err(|e| eyre::eyre!("config error: {e}"))?;

    info!(
        version = env!("CARGO_PKG_VERSION"),
        env     = args.env.as_deref().unwrap_or("production"),
        "navig-core-host starting"
    );

    // ── 2. Platform paths ──────────────────────────────────────────────────────
    let paths = PlatformPaths::resolve();
    paths.ensure_dirs().map_err(|e| eyre::eyre!("dir creation error: {e}"))?;

    // ── 3. Event bus ──────────────────────────────────────────────────────────
    let events = EventBus::new(512);
    events.publish(Event::DaemonStarted { version: env!("CARGO_PKG_VERSION").to_owned() });

    // ── 4. Auth ───────────────────────────────────────────────────────────────
    let token_store = TokenStore::new();
    // Emit a startup internal token for diagnostics (debug builds only)
    #[cfg(feature = "debug-endpoints")]
    {
        let startup_token = issue_token(
            "internal/startup",
            Scope::All,
            config.auth.token_ttl_secs,
            config.auth.jwt_secret.as_bytes(),
        )?;
        tracing::debug!(%startup_token, "debug: startup token issued");
    }

    // ── 5. Plugins ────────────────────────────────────────────────────────────
    let plugin_registry = Arc::new(PluginRegistry::new(events.clone()));
    {
        let pcfg = config.plugins.clone();
        let router_cfg = PluginConfig {
            name:         "router".into(),
            command:      pcfg.router_cmd.clone(),
            python_bin:   pcfg.python_bin.clone(),
            timeout_secs: pcfg.timeout_secs,
            max_restarts: 5,
        };
        if let Err(e) = plugin_registry.register(router_cfg).await {
            error!(error = %e, "failed to start router plugin; continuing without it");
        }
    }

    // ── 6. HTTP server ────────────────────────────────────────────────────────
    let bind  = args.bind.as_deref().unwrap_or(&config.server.bind).to_owned();
    let state = AppState::new(config.clone(), events.clone(), token_store);

    // Install Ctrl-C handler and serve
    let server = tokio::spawn(async move {
        if let Err(e) = serve(state, &bind).await {
            error!(error = %e, "HTTP server error");
        }
    });

    // Wait for shutdown signal
    tokio::select! {
        _ = tokio::signal::ctrl_c() => {
            info!("received Ctrl-C — shutting down");
        }
        _ = server => {
            info!("HTTP server exited");
        }
    }

    // ── 7. Graceful shutdown ──────────────────────────────────────────────────
    events.publish(Event::DaemonStopping { reason: "shutdown signal".into() });
    plugin_registry.stop_all().await;
    info!("navig-core-host stopped");
    Ok(())
}
