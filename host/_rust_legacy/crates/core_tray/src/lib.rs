// crates/core_tray/src/lib.rs
//! # core_tray
//!
//! System tray icon + context menu for NAVIG Core Host.
//!
//! Enabled on Windows and macOS via the `tray` feature flag.
//! On headless Linux builds this module compiles to a no-op.
//!
//! The tray runs on its own thread (event loop requirement on some platforms).

#[cfg(feature = "tray")]
pub mod tray;
#[cfg(feature = "tray")]
pub use tray::TrayApp;

#[cfg(not(feature = "tray"))]
/// Stub when compiled without the `tray` feature.
pub struct TrayApp;

#[cfg(not(feature = "tray"))]
impl TrayApp {
    pub fn run(_events: core_events::EventBus) -> Result<(), String> {
        tracing::info!("tray feature disabled — running headless");
        Ok(())
    }
}
