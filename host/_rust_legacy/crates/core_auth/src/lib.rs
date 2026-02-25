// crates/core_auth/src/lib.rs
//! # core_auth
//!
//! JWT token issuance and validation, cryptographic scope enforcement,
//! and an in-process token store backed by the OS keychain.
//!
//! ## Token scopes
//! | Scope | Grants |
//! |---|---|
//! | `*` | Full access (internal / management) |
//! | `router` | POST `/v1/router/complete` |
//! | `inbox` | POST `/v1/inbox/ingest` |
//! | `tools` | POST `/v1/tools/execute` |
//! | `status` | GET `/v1/status` |
//! | `tunnel` | GET `/health` + GET `/v1/status` (Cloudflare) |

pub mod scope;
pub mod store;
pub mod token;

pub use scope::Scope;
pub use store::{TokenStore, TokenStoreError};
pub use token::{Claims, TokenError, issue_token, validate_token};
