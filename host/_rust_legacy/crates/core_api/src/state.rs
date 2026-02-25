// crates/core_api/src/state.rs
//! Shared application state injected into every route handler.

use core_auth::TokenStore;
use core_config::Config;
use core_events::EventBus;
use std::sync::Arc;

#[derive(Clone, Debug)]
pub struct AppState {
    pub config:      Arc<Config>,
    pub events:      EventBus,
    pub token_store: Arc<TokenStore>,
}

impl AppState {
    pub fn new(config: Config, events: EventBus, token_store: TokenStore) -> Self {
        Self {
            config:      Arc::new(config),
            events,
            token_store: Arc::new(token_store),
        }
    }
}
