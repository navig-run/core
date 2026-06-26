# Hardcoded Values — Intentional Skips

This file documents literals that were intentionally left unchanged during the
"extract hardcoded values to constants" refactoring pass (PRs 1–7, commits
`5ff7a6e`–`60bf7d0`).  Each entry explains *why* it was not extracted.

---

## 1. `max_tokens=4096` in `ModelDefinition` objects

**Files:** `navig/providers/*.py` (various model catalogue files)  
**Reason:** These are model-capability specs — the maximum context window a given
model supports — not generation-time defaults.  `_DEFAULT_MAX_TOKENS = 4_096`
(from `navig/_llm_defaults.py`) controls what the LLM *generates* by default;
model capability limits are intrinsic to each model and belong in the model
definition itself.

---

## 2. HTTP status code literals (200, 401, 403, 429, 500 …)

**Files:** `navig/gateway/channels/telegram.py`, `navig/remote.py`, and others  
**Reason:** HTTP status codes are protocol constants specified by RFC 9110.  They
are not tuneable values — renaming `429` to `_HTTP_TOO_MANY_REQUESTS` adds no
clarity that the comment beside it does not already provide.

---

## 3. MIME types and protocol path segments (`"v1"`, `"api"`, …)

**Files:** various provider adapters  
**Reason:** API path fragments and MIME type strings are part of the external
protocol contract, not application-internal tuneable values.

---

## 4. `bridge_port = 42070` in `telegram.py` (import-fallback sentinel)

**File:** `navig/gateway/channels/telegram.py` (~L4490)  
**Reason:** This line lives inside a `try / except ImportError` block that
catches a failed import of `navig.bridge`.  When the import fails it is
*impossible* to reference `BRIDGE_DEFAULT_PORT` from the same module, so a
literal fallback is the only option.  The value intentionally matches
`BRIDGE_DEFAULT_PORT = 42070` defined in `navig/bridge/bridge_grid_reader.py`.

---

## 5. `"~/.navig/debug.log"` in `_DAEMON_LOG_CANDIDATES`

**File:** `navig/commands/system.py` (or daemon config)  
**Reason:** This is a *legacy probe path* — the list is searched at runtime to
find an existing log file on systems where NAVIG was previously installed to the
old location.  `debug_log_path()` resolves to a platform-specific path such as
`~/.local/state/navig/logs/debug.log` (Linux XDG) or `%LOCALAPPDATA%\navig\logs\debug.log`
(Windows); replacing the candidate list entry with `debug_log_path()` would drop
the legacy-path probe entirely and break backward compatibility for existing
installations.

---

## 6. `Path.home() / ".navig" / "debug.log"` in `debug_logger.py`

**File:** `navig/debug_logger.py` (~L89)  
**Reason:** This literal is the last-resort fallback that is reached only when
`debug_log_path()` itself raises.  Using `debug_log_path()` here would be
circular (a function that fails calling itself to log the failure), so the
hard-coded fallback is necessary.

---

## 7. `registry.py` probe strings `"127.0.0.1:11434"` / `"127.0.0.1:8080"`

**File:** `navig/providers/registry.py` (local probe entries)  
**Reason:** These are `host:port` strings without a URL scheme, used as
dictionary keys in the provider probe registry.  The constants added in
`navig/providers/_local_defaults.py` are full `http://…` URLs for HTTP client
calls.  Constructing a key of the form `_OLLAMA_BASE_URL.removeprefix("http://")`
would obscure the intent and couple the registry to the URL constants without
benefit; the key format is a separate concern.

---

## 8. Inline model name strings in `telegram_commands.py`

**File:** `navig/gateway/channels/telegram_commands.py`  
**Lines:** calls to `_pick()` containing `"gpt-4o"`, `"gpt-4o-mini"`, `"gpt-3.5"`  
**Reason:** These strings live inside function bodies as runtime routing hints
(preference tuples), not as configuration that users or operators are expected
to change.  They depend on the provider routing logic in `navig/llm_router.py`
and are tightly coupled to that specific call site.  Extracting them to a shared
constant would create an artificial dependency between unrelated modules.

---

## 9. `GatewayConfig.port` vs `defaults.yaml gateway.port` — RESOLVED (2026-06-20)

**Original observation:** the pydantic schema (`core/config_schema.py`) and the
runtime `GatewayConfig` (`gateway/server.py`) both defaulted `gateway.port` to
**8765**, while `config/defaults.yaml` sets **8789**.

**Resolution:** 8789 is canonical for the gateway (defaults.yaml, all docs,
every client/probe, the daemon's embedded gateway). The 8765 fallbacks were a
bug — they made the gateway bind the **daemon-IPC** port (`daemon.port: 8765`),
so on any install without an explicit `gateway.port` the gateway squatted the
daemon's port and every 8789-probing client (doctor, deck, flux, mesh) failed
to reach it. `defaults.yaml` never participates at runtime (`_load_global_config`
reads only `~/.navig/config.yaml`; the cached path uses `validate=False`), so the
inline fallback was the *only* default that executed.

The canonical value now lives in **one place**: `navig._daemon_defaults._GATEWAY_PORT`
(8789), alongside `_DAEMON_PORT` (8765) and `_OAUTH_REDIRECT_PORT` (1455).
`server.py`, `core/config_schema.py`, and `gateway_client.py` reference it, as do
the gateway-port fallbacks in `telegram_mesh`, `mesh/registry`, `oauth_redirect`,
`bridge`, `dashboard`, `doctor`, `flux`, `interactive`, and `lighthouse`. A
regression guard asserts `_GATEWAY_PORT != _DAEMON_PORT`
(`tests/gateway/test_gateway_client.py`, `tests/config/test_config_schema.py`).

---

## 10. `poll_interval_sec = 15` in `_poll_due_reminders()`

**File:** `navig/gateway/channels/telegram.py`  
**Reason:** This value appears exactly once and is a low-level implementation
detail of a single function.  Extracting a constant used in exactly one place
provides no deduplication benefit.  It can be promoted if a second call site
appears.
