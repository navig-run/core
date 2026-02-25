// crates/core_auth/src/scope.rs
//! JWT scope definitions.

use serde::{Deserialize, Serialize};
use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Scope {
    /// Full internal access.
    All,
    /// Can call `/v1/router/complete`.
    Router,
    /// Can call `/v1/inbox/ingest`.
    Inbox,
    /// Can call `/v1/tools/execute`.
    Tools,
    /// Can call `/v1/status`.
    Status,
    /// Cloudflare tunnel — health + status only.
    Tunnel,
}

impl fmt::Display for Scope {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let s = match self {
            Scope::All    => "*",
            Scope::Router => "router",
            Scope::Inbox  => "inbox",
            Scope::Tools  => "tools",
            Scope::Status => "status",
            Scope::Tunnel => "tunnel",
        };
        write!(f, "{s}")
    }
}

impl Scope {
    /// Return `true` if `self` grants access to the `required` scope.
    pub fn grants(&self, required: &Scope) -> bool {
        self == &Scope::All || self == required
    }
}
