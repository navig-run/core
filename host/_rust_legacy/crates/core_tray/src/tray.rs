// crates/core_tray/src/tray.rs
//! Tray implementation (Windows / macOS via `tray-icon` + `muda`).

use core_events::{Event, EventBus};
use std::thread;
use tracing::{error, info};

/// Tray icon states.
pub enum TrayState {
    /// All modules healthy — green icon.
    Healthy,
    /// One or more modules degraded — amber icon.
    Degraded,
    /// Core daemon stopped — grey icon.
    Stopped,
}

/// Manages the system tray icon and reacts to event bus messages.
pub struct TrayApp {
    events: EventBus,
}

impl TrayApp {
    pub fn new(events: EventBus) -> Self {
        Self { events }
    }

    /// Spawn tray on a dedicated thread.  Returns immediately.
    ///
    /// The thread will live until the tray icon is quit or the process exits.
    pub fn spawn(self) {
        thread::Builder::new()
            .name("navig-tray".to_owned())
            .spawn(move || {
                if let Err(e) = self.run_loop() {
                    error!(error = %e, "tray thread exited with error");
                }
            })
            .expect("failed to spawn tray thread");
    }

    fn run_loop(self) -> Result<(), Box<dyn std::error::Error>> {
        info!("tray event loop starting");
        // TODO: create tray-icon TrayIcon + muda Menu
        // Subscribe to EventBus and update icon state on StatusChanged events
        let mut rx = self.events.subscribe();
        loop {
            match rx.try_recv() {
                Ok(env) => match env.payload {
                    Event::DaemonStopping { .. } => break,
                    Event::StatusChanged { healthy, .. } => {
                        let _state = if healthy { TrayState::Healthy } else { TrayState::Degraded };
                        // TODO: update tray icon
                    }
                    Event::TrayMenuItemSelected { id } => {
                        info!(item = %id, "tray menu item selected");
                        // TODO: dispatch menu actions
                    }
                    _ => {}
                },
                Err(tokio::sync::broadcast::error::TryRecvError::Empty)   => {
                    thread::sleep(std::time::Duration::from_millis(100));
                }
                Err(tokio::sync::broadcast::error::TryRecvError::Closed)  => break,
                Err(tokio::sync::broadcast::error::TryRecvError::Lagged(n)) => {
                    tracing::warn!(dropped = n, "tray receiver lagged");
                }
            }
        }
        info!("tray event loop exiting");
        Ok(())
    }
}
