// crates/core_events/src/event.rs
//! All typed events flowing through the bus.

use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use ulid::Ulid;

/// Opaque wrapper around any event, adding a ULID + timestamp.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EventEnvelope {
    pub id:        String,
    pub timestamp: DateTime<Utc>,
    pub payload:   Event,
}

impl EventEnvelope {
    pub fn new(payload: Event) -> Self {
        Self {
            id:        Ulid::new().to_string(),
            timestamp: Utc::now(),
            payload,
        }
    }
}

/// All events published on the bus.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Event {
    // ── System lifecycle ───────────────────────────────────────────────────
    DaemonStarted   { version: String },
    DaemonStopping  { reason: String },

    // ── Module status ──────────────────────────────────────────────────────
    StatusChanged   { module: String, healthy: bool, message: Option<String> },
    ModuleRestarted { module: String },

    // ── Auth ───────────────────────────────────────────────────────────────
    TokenIssued  { scope: String, expires_at: DateTime<Utc> },
    TokenRevoked { jti: String },
    AuthDenied   { path: String, reason: String },

    // ── Router ────────────────────────────────────────────────────────────
    RouterRequestStarted  { request_id: String, model: Option<String> },
    RouterRequestComplete { request_id: String, provider: String, latency_ms: u64 },
    RouterRequestFailed   { request_id: String, error: String },
    RouterFallback        { request_id: String, from: String, to: String },

    // ── Inbox ─────────────────────────────────────────────────────────────
    InboxItemIngested    { item_id: String, source: String },
    InboxItemProcessed   { item_id: String },
    InboxItemFailed      { item_id: String, error: String },
    InboxQueueDepth      { depth: u64 },

    // ── Tools ─────────────────────────────────────────────────────────────
    ToolExecutionStarted  { execution_id: String, tool_name: String },
    ToolExecutionComplete { execution_id: String, success: bool, latency_ms: u64 },
    ToolExecutionFailed   { execution_id: String, error: String },

    // ── Plugins (Python subprocess) ───────────────────────────────────────
    PluginStarted   { name: String, pid: u32 },
    PluginStopped   { name: String, exit_code: Option<i32> },
    PluginCrashed   { name: String, stderr: String },
    PluginRestarted { name: String, attempt: u32 },

    // ── Tray ──────────────────────────────────────────────────────────────
    TrayMenuItemSelected { id: String },
    TrayTooltipUpdated   { text: String },

    // ── Config ────────────────────────────────────────────────────────────
    ConfigReloaded { changed_keys: Vec<String> },
}
