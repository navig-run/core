// crates/core_plugins/src/plugin.rs
//! Python subprocess lifecycle management.

use crate::jsonrpc::{RpcError, RpcRequest, RpcResponse};
use core_events::{Event, EventBus};
use serde::{Deserialize, Serialize};
use std::{path::PathBuf, process::Stdio, time::Duration};
use thiserror::Error;
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, ChildStdout, Command},
    sync::Mutex,
    time::timeout,
};
use tracing::{error, info, warn};
use ulid::Ulid;

#[derive(Debug, Error)]
pub enum PluginError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("json error: {0}")]
    Json(#[from] serde_json::Error),
    #[error("rpc error {code}: {message}")]
    Rpc { code: i64, message: String },
    #[error("subprocess timeout after {0}ms")]
    Timeout(u64),
    #[error("subprocess not running")]
    NotRunning,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PluginConfig {
    pub name:         String,
    pub command:      Vec<String>,
    pub python_bin:   PathBuf,
    pub timeout_secs: u64,
    pub max_restarts: u32,
}

/// Manages a single long-lived Python subprocess.
pub struct Plugin {
    pub config: PluginConfig,
    events:     EventBus,
    child:      Mutex<Option<Child>>,
    stdin:      Mutex<Option<ChildStdin>>,
    stdout:     Mutex<Option<BufReader<ChildStdout>>>,
    restarts:   std::sync::atomic::AtomicU32,
}

impl Plugin {
    pub fn new(config: PluginConfig, events: EventBus) -> Self {
        Self {
            config,
            events,
            child:    Mutex::new(None),
            stdin:    Mutex::new(None),
            stdout:   Mutex::new(None),
            restarts: std::sync::atomic::AtomicU32::new(0),
        }
    }

    /// Spawn the Python subprocess.
    pub async fn start(&self) -> Result<(), PluginError> {
        let mut args = self.config.command.iter().skip(1).collect::<Vec<_>>();
        let mut cmd = Command::new(&self.config.python_bin);
        cmd.args(&self.config.command[..])
           .stdin(Stdio::piped())
           .stdout(Stdio::piped())
           .stderr(Stdio::piped())
           .kill_on_drop(true);

        let mut child = cmd.spawn()?;
        let pid   = child.id().unwrap_or(0);
        let stdin  = child.stdin.take().expect("stdin");
        let stdout = child.stdout.take().expect("stdout");

        *self.stdin.lock().await  = Some(stdin);
        *self.stdout.lock().await = Some(BufReader::new(stdout));
        *self.child.lock().await  = Some(child);

        info!(name = %self.config.name, pid, "plugin started");
        self.events.publish(Event::PluginStarted { name: self.config.name.clone(), pid });
        Ok(())
    }

    /// Send a JSON-RPC request and wait for the response line.
    pub async fn call(
        &self,
        method: &str,
        params: serde_json::Value,
    ) -> Result<serde_json::Value, PluginError> {
        let id  = Ulid::new().to_string();
        let req = RpcRequest::new(id.clone(), method, params);
        let mut line = serde_json::to_string(&req)? + "\n";

        {
            let mut stdin = self.stdin.lock().await;
            let writer = stdin.as_mut().ok_or(PluginError::NotRunning)?;
            writer.write_all(line.as_bytes()).await?;
            writer.flush().await?;
        }

        let deadline = Duration::from_secs(self.config.timeout_secs);
        let response_line = {
            let mut stdout = self.stdout.lock().await;
            let reader = stdout.as_mut().ok_or(PluginError::NotRunning)?;
            let mut buf = String::new();
            timeout(deadline, reader.read_line(&mut buf))
                .await
                .map_err(|_| PluginError::Timeout(self.config.timeout_secs * 1000))??;
            buf
        };

        let resp: RpcResponse = serde_json::from_str(response_line.trim())?;
        match resp {
            RpcResponse::Success { result, .. } => Ok(result),
            RpcResponse::Error   { error, .. }  => Err(PluginError::Rpc { code: error.code, message: error.message }),
            _                                   => Err(PluginError::Rpc { code: -1, message: "unexpected response type".into() }),
        }
    }

    /// Stop the subprocess.
    pub async fn stop(&self) {
        if let Some(mut child) = self.child.lock().await.take() {
            let _ = child.kill().await;
            let exit_code = child.wait().await.ok().and_then(|s| s.code());
            info!(name = %self.config.name, ?exit_code, "plugin stopped");
            self.events.publish(Event::PluginStopped { name: self.config.name.clone(), exit_code });
        }
    }
}
