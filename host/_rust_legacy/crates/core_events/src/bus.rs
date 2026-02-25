// crates/core_events/src/bus.rs
//! Broadcast-based event bus.

use crate::event::{Event, EventEnvelope};
use std::sync::Arc;
use tokio::sync::broadcast;
use tracing::{debug, warn};

/// Shared handle to the event bus.
///
/// Cheaply cloneable — clone and pass to each subsystem.
#[derive(Clone, Debug)]
pub struct EventBus {
    inner: Arc<BusInner>,
}

#[derive(Debug)]
struct BusInner {
    tx: broadcast::Sender<EventEnvelope>,
}

impl EventBus {
    /// Create a new bus with receiver capacity `cap`.
    ///
    /// When a slow receiver falls `cap` messages behind, older messages are
    /// dropped for it (broadcast semantics — acceptable for UI/status events).
    pub fn new(cap: usize) -> Self {
        let (tx, _) = broadcast::channel(cap);
        Self { inner: Arc::new(BusInner { tx }) }
    }

    /// Publish an event.  Returns the number of active receivers that received it.
    pub fn publish(&self, event: Event) -> usize {
        let envelope = EventEnvelope::new(event);
        match self.inner.tx.send(envelope) {
            Ok(n)  => { debug!(receivers = n, "event published"); n }
            Err(_) => { warn!("no active receivers for event"); 0 }
        }
    }

    /// Subscribe to the bus.  The returned receiver will receive all events
    /// published *after* the call.
    pub fn subscribe(&self) -> broadcast::Receiver<EventEnvelope> {
        self.inner.tx.subscribe()
    }

    /// Number of active subscribers.
    pub fn receiver_count(&self) -> usize {
        self.inner.tx.receiver_count()
    }
}
