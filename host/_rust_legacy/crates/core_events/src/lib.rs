// crates/core_events/src/lib.rs
//! # core_events
//!
//! Typed, async pub/sub event bus for NAVIG Core Host.
//!
//! ## Channel types
//! * **Broadcast** (`tokio::broadcast`) — status/UI events; receivers may lag/drop.
//! * **Durable** (pending: SQLite queue) — inbox/router events that must not be lost.
//!
//! ## Usage
//! ```rust,ignore
//! let bus = EventBus::new(256);
//! let mut rx = bus.subscribe();
//!
//! bus.publish(Event::StatusChanged { module: "router".into(), healthy: true });
//!
//! while let Ok(event) = rx.recv().await {
//!     println!("{event:?}");
//! }
//! ```

pub mod bus;
pub mod event;

pub use bus::EventBus;
pub use event::{Event, EventEnvelope};
