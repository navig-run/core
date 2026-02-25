// crates/core_api/src/routes/mod.rs
//! Route modules.

pub mod health;
pub mod inbox;
pub mod router;
pub mod status;
pub mod tools;

#[cfg(feature = "debug-endpoints")]
pub mod debug;
