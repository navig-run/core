// crates/core_api/src/server.rs
//! Axum server construction and binding.

use axum::{
    middleware,
    routing::{get, post},
    Router,
};
use std::net::SocketAddr;
use tower_http::{
    compression::CompressionLayer,
    cors::CorsLayer,
    limit::RequestBodyLimitLayer,
    trace::TraceLayer,
};
use tracing::info;
use crate::{routes, state::AppState};

/// Build and start the HTTP server.  Resolves when the server shuts down.
pub async fn serve(state: AppState, bind: &str) -> anyhow::Result<()> {
    let addr: SocketAddr = bind.parse()?;
    let app = build_router(state);
    let listener = tokio::net::TcpListener::bind(addr).await?;
    info!(%addr, "HTTP server listening");
    axum::serve(listener, app).await?;
    Ok(())
}

fn build_router(state: AppState) -> Router {
    let v1 = Router::new()
        .route("/status",          get(routes::status::handler))
        .route("/router/complete", post(routes::router::handler))
        .route("/inbox/ingest",    post(routes::inbox::handler))
        .route("/tools/execute",   post(routes::tools::handler));

    #[cfg(feature = "debug-endpoints")]
    let v1 = v1.route("/debug/config", get(routes::debug::config_handler));

    Router::new()
        .route("/health", get(routes::health::handler))
        .nest("/v1", v1)
        .layer(TraceLayer::new_for_http())
        .layer(CompressionLayer::new())
        .layer(CorsLayer::permissive())
        .layer(RequestBodyLimitLayer::new(4 * 1024 * 1024)) // 4 MiB
        .with_state(state)
}
