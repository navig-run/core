// crates/core_plugins/src/registry.rs
//! Registry mapping plugin names to running Plugin instances.

use crate::plugin::{Plugin, PluginConfig, PluginError};
use core_events::EventBus;
use std::{collections::HashMap, sync::Arc};
use tokio::sync::RwLock;

pub struct PluginRegistry {
    plugins: RwLock<HashMap<String, Arc<Plugin>>>,
    events:  EventBus,
}

impl PluginRegistry {
    pub fn new(events: EventBus) -> Self {
        Self { plugins: RwLock::new(HashMap::new()), events }
    }

    /// Register and start a plugin.
    pub async fn register(&self, config: PluginConfig) -> Result<(), PluginError> {
        let plugin = Arc::new(Plugin::new(config.clone(), self.events.clone()));
        plugin.start().await?;
        self.plugins.write().await.insert(config.name.clone(), plugin);
        Ok(())
    }

    /// Get a running plugin by name.
    pub async fn get(&self, name: &str) -> Option<Arc<Plugin>> {
        self.plugins.read().await.get(name).cloned()
    }

    /// Stop all plugins.
    pub async fn stop_all(&self) {
        let plugins = self.plugins.read().await;
        for plugin in plugins.values() {
            plugin.stop().await;
        }
    }
}
