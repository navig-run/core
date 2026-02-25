// crates/core_api/src/lib.rs
//! # core_api
//!
//! Axum HTTP server and all route handlers for NAVIG Core Host.
//!
//! ## Routes
//! | Method | Path | Auth | Handler |
//! |---|---|---|---|
//! | GET  | `/health`               | None   | [`routes::health`] |
//! | GET  | `/v1/status`            | Bearer | [`routes::status`] |
//! | POST | `/v1/router/complete`   | Bearer | [`routes::router`] |
//! | POST | `/v1/inbox/ingest`      | Bearer | [`routes::inbox`]  |
//! | POST | `/v1/tools/execute`     | Bearer | [`routes::tools`]  |

pub mod error;
pub mod middleware;
pub mod routes;
pub mod server;
pub mod state;

pub use server::serve;
pub use state::AppState;
