# NAVIG Execution Engine — Architecture

## Overview

The `navig.engine` package provides three composable primitives that sit above the
existing `navig.tools.registry` contract developed in prior releases:

| Module | Purpose |
|--------|---------|
| `engine.hooks` | Observable pub/sub lifecycle events for every tool run |
| `engine.queue` | Per-lane serialized asyncio command queue with cancellation |
| `engine.pipeline` | Composable tool-chaining with context passing |

These three primitives are independent — each can be imported and used without
the others.

---

## 1. `engine.hooks` — Observable Execution Events

### Design rationale
The existing `ToolRegistry.run_tool()` already isolates tool failures and fires
`on_status` callbacks.  Hooks add a *cross-cutting, decoupled* observation layer
so that logging, metrics, auditing, and UI updates do not require changes inside
individual tool implementations.

### Key types

```
HookPhase  (Enum)       BEFORE | AFTER | ERROR | STATUS
ExecutionEvent          Immutable snapshot: tool_name, phase, args, output,
                        error, elapsed_ms, status_step/detail/progress, meta
ExecutionHooks          Registry: .on(phase) decorator, .register(), .emit(),
                        .emit_sync(), .instrument(), .clear()
global_hooks            Module-level singleton used by integrations
```

### Usage pattern

```python
from navig.engine.hooks import global_hooks, HookPhase

@global_hooks.on(HookPhase.AFTER)
async def audit(event):
    if not event.success:
        logger.warning("Tool %s failed: %s", event.tool_name, event.error)
```

### Safety guarantee
Failing observer callbacks are caught and logged as `DEBUG`.  They can never
abort or delay a tool run.

---

## 2. `engine.queue` — Lane-Based Command Queue

### Design rationale
Certain workloads (interactive chat, background cron, file uploads) must not
block each other, but within each workload type commands must be serialized.
Lanes provide this isolation without global coordination.

### Key types

```
TaskHandle          Returned by enqueue(); has .wait(), .cancel(), .state
LaneClearedError    Raised from TaskHandle.wait() when lane was cleared
QueueShutdownError  Raised when enqueue() is called after shutdown()
CommandQueue        .enqueue(lane, coro, timeout?) → TaskHandle
                    .clear_lane(lane) → int (cancelled count)
                    .drain(lane?)
                    .shutdown()
                    .status() → dict
```

### Lane model
- Each lane has one serial worker (asyncio.Task draining an asyncio.Queue).
- Lanes are created on first use and removed when cleared.
- Default lane: `"main"`.  Background cron should use `"cron"`.
- `max_workers_per_lane` is configurable (default 1 = fully serial).

### Timeout
Every enqueued task gets a `default_timeout` wrapping (default 120s).  Override
per-task with the `timeout` kwarg.

---

## 3. `engine.pipeline` — Composable Tool Chaining

### Design rationale
Complex agent tasks often require multi-tool sequences:
`fetch → extract → store → summarize`.  The pipeline eliminates the boilerplate
of manually threading outputs between calls.

### Key types

```
PipelineStep    tool_name, args, input_key, output_key, transform, required
StepResult      step_index, tool_name, success, output, error, elapsed_ms
PipelineResult  steps, context, total_elapsed_ms, aborted_at
                .succeeded, .final_output
ToolPipeline    .add(step) → self
                .run(initial_input?, on_step?) → PipelineResult
```

### Context propagation
Each step receives a merged dict of:
1. All accumulated `output_key` values from prior steps
2. The step's own `args` dict (overrides context)
3. The previous step's output injected under `input_key` (with optional `transform`)

### Failure handling
- `required=True` (default): a failed step aborts the pipeline and sets
  `PipelineResult.aborted_at`.
- `required=False`: the pipeline continues to the next step.

---

## 4. New Tools

### `navig.tools.bash_exec` — `BashExecTool`
Safe subprocess execution:
- `shlex.split()` + `asyncio.create_subprocess_exec(shell=False)` — shell injection impossible.
- Default timeout 30s; `max_output` caps at 8 000 chars.
- `requires_approval=True` flag blocks execution unless `NAVIG_ALLOW_ALL_COMMANDS=1`.

### `navig.tools.memory` — `MemoryStoreTool` + `MemoryFetchTool`
In-process key/value store:
- Thread-safe (`RLock`), optional JSON-file persistence.
- Similarity search via keyword TF-IDF cosine (no external ML dependencies).
- `memory_store` → store/update values; `memory_fetch` → exact key, fuzzy search, or full list.

---

## 5. Builtin Skills

| Skill | Location | Purpose |
|-------|----------|---------|
| `healthcheck` | `navig/skills/builtin/healthcheck/SKILL.md` | Run non-destructive system diagnostics |
| `summarize` | `navig/skills/builtin/summarize/SKILL.md` | Fetch + summarize URL or memory text |

Skills are discovered by `navig.skills.loader` and are invocable via
`navig ai "run healthcheck"` or referenced as tool-chains in agent plans.

---

## 6. Integration Points

### Wire hooks into ToolRegistry
```python
# navig/tools/registry.py  — inside run_tool() after the call
from navig.engine.hooks import global_hooks, ExecutionEvent, HookPhase

await global_hooks.emit(
    ExecutionEvent.after(name, args, success=result.success,
                         output=result.output, error=result.error,
                         elapsed_ms=result.elapsed_ms)
)
```

### Use CommandQueue in the daemon
```python
from navig.engine.queue import CommandQueue

_queue = CommandQueue(default_timeout=60.0)

async def handle_user_request(coro):
    handle = await _queue.enqueue("main", coro)
    return await handle.wait()
```

### Chain tools in an agent step
```python
from navig.engine.pipeline import ToolPipeline, PipelineStep
from navig.tools.registry import registry

pipe = (
    ToolPipeline(registry)
    .add(PipelineStep("web_fetch",   args={"url": url}))
    .add(PipelineStep("summarize",   input_key="text"))
    .add(PipelineStep("memory_store", args={"key": "latest_summary"}))
)
result = await pipe.run()
```

---

## 7. Dependency Graph

```
navig.engine.hooks      ← no navig deps (stdlib only)
navig.engine.queue      ← no navig deps (stdlib only)
navig.engine.pipeline   ← navig.tools.registry (ToolRegistry protocol)
navig.tools.bash_exec   ← navig.tools.registry (BaseTool)
navig.tools.memory      ← navig.tools.registry (BaseTool)
```

All new modules are pure Python standard-library only (no new dependencies).


---

## 8. Tool Infrastructure Update (2026-03)

### New modules

| Module | Role |
|--------|------|
| `navig.tools.hooks` | `HookRegistry` — sync pub/sub for `BEFORE_EXECUTE`, `AFTER_EXECUTE`, `DENIED`, `ERROR`, `NOT_FOUND` |
| `navig.tools.domains.exec_pack` | `bash_exec` — sandboxed shell execution (DANGEROUS, 50k output cap) |
| `navig.tools.bridge` | `bridge_all(base_reg, router_reg)` — adapts BaseTool to ToolRouter without modifying either registry |
| `navig.skills.eligibility` | `SkillEligibilityContext`, `filter_skills()` — enforce platform/safety/user_invocable gates at injection time |

### ToolRouter changes

- Hook firing integrated at all return paths (`BEFORE_EXECUTE` before handler, `AFTER_EXECUTE`/`ERROR`/`NOT_FOUND`/`DENIED` at respective exits)
- `exec_pack` added to `_load_builtin_packs()`

### Agent changes

- `TaskExecutor._execute_step()` falls through to `ToolRouter.async_execute()` after exhausting the 12 hardcoded action types
- `NOT_FOUND` re-raises `ValueError` (preserves caller contract); `ERROR`/`DENIED` raise `RuntimeError`
- `TaskExecutor.execute_multi_step_action(MultiStepAction)` added — chains `ToolCallAction` steps in sequence

### Telegram fixes (same session)

- `telegram.py`: added `import re` (was missing; broke `_sanitize_response_for_telegram`)
- `telegram.py`: `_send_response` now sanitizes at entry (strips search reflection tags before sending to user)
- `telegram.py`: webhook mode no longer pre-loads `telegram_offset` from `RuntimeStore` (avoids stale-offset false-duplicate rejection after process restart)
- `telegram_keyboards.py`: fixed unmatched `]` syntax error in `_build_response_rows()`

### Dependency graph (additions)

```
navig.tools.hooks            <- no navig deps (stdlib only)
navig.tools.domains.exec_pack  <- navig.tools.router (ToolMeta, ToolDomain, SafetyLevel)
navig.tools.bridge           <- navig.tools.registry + navig.tools.router
navig.skills.eligibility     <- navig.skills.loader (Skill)
navig.tools.router           <- navig.tools.hooks [new]
navig.agent.conv.executor    <- navig.tools.router + navig.tools.schemas [new]
```

---

## 9. Migration to Typed Execution Pipeline (Uncoupled Pipeline)

As part of the execution engine upgrade, the infrastructure transitions to strictly declarative interfaces and an uncoupled pipeline architecture. This aligns the execution logic with more robust, programmatic capabilities without sacrificing the existing packs/skills abstraction.

### 9.1 Core Domain Models (`navig.tools.interfaces`)

The existing typed classes (`ToolResult`, `BaseTool`, `ToolMeta`) are being superseded by the uncoupled domains:

- **`SkillSpec`**: A strict definition of an overarching capability grouping, containing environment constraints and execution directives.
- **`ToolSpec`**: Replaces `BaseTool`. A declarative schema detailing a tool’s inputs, outputs, required contexts, and domain grouping.
- **`ExecutionRequest`**: Represents a bundled invocation, wrapping the raw payload, associated credentials, cancellation token, timeout constraint, and execution metadata (like lane requirements).
- **`ExecutionEvent`**: Replaces the unstructured `on_status` callback. It uses structured payloads (e.g., `StreamChunk`, `StreamStatus`, `StreamFinal`) allowing complex streaming telemetry back to the agent or UI.
- **`ExecutionResult`**: The strict, state-machine output of the execution loop (replaces `ToolResult`).

### 9.2 The Uncoupled Pipeline Lifecycle

Execution of any tool is governed by a strict, state-machine pipeline (replaces monolithic `.run` methods):

1. **`dispatch`**: The gateway or agent triggers an execution request. The job is queued based on its priority and parallelization rules (lanes).
2. **`validate`**: Pre-flight checks. The system resolves the `ExecutionRequest` against `SkillSpec` and `ToolSpec`, handling schema coercion, injecting `ExecutionContext`, and enforcing safety/approval gates.
3. **`execute`**: The isolated invocation worker attempts the execution block, respecting cancellation signals, retries, and timeout boundaries from the request.
4. **`stream`**: During execution, the tool yields `ExecutionEvent` streams, allowing immediate, chunked consumption by upstream layers without waiting for final completion.
5. **`finalize`**: State machine exit. The final output is bundled into an `ExecutionResult` (whether success, timeout, cancellation, or structured failure) and all execution telemetry is purged from the active session pool.

### 9.3 Extensibility Rules
Despite adopting this structured pipeline:
- We retain the concepts of **Packs** and **Skills** rather than shifting to a new heavy-weight plugin manifest loader.
- Native UI bridges (TypeScript, VS Code Forge) are unaffected by the deep Python abstraction shift—the bridge remains compatible with the serializing endpoints.


---

## 10. Security & Observability Layer (2026-03)

Seven new modules harden the execution pipeline with credential rotation,
trust tagging, approval gates, session tracking, dual-hook bridging, output
validation, and tool introspection.

---

### 10.1 `navig.agent.auth_profiles` -- Credential Pool

Round-robin credential rotation with exponential back-off for failed keys.

```
AuthProfile         name, api_key, provider, weight
ProfileCooldown     failure_count, last_failure_ts, cooldown_seconds
AuthProfilePool     next_available() -> AuthProfile | None
                    mark_failure(name)   # backoff: base=5s x 2^(failures-1), cap=300s
                    mark_good(name)      # resets cooldown
                    status() -> dict     # monitoring snapshot
get_profile_pool()  -> singleton
```

Config key: `auth.profiles[]` -- list of `{name, api_key, provider, weight}` objects.

---

### 10.2 `navig.tools.trust_boundary` -- External Content Tagging

Wraps any LLM-bound external content in sentinel tags before injection,
preventing prompt-injection from untrusted sources.

```
wrap_external(content, source) -> str     # idempotent
unwrap_external(content) -> str           # raises TrustBoundaryError if not wrapped
is_externally_wrapped(content) -> bool
extract_source(content) -> str | None
```

Sentinels: `[EXTERNAL CONTENT: {source}]...[/EXTERNAL CONTENT]`
`source` labels are sanitised -- `[` and `]` are stripped.

---

### 10.3 `navig.tools.approval` -- Operator Safety Interlock

Single-operator gate evaluated inside `ToolRouter._raw_async_execute()` before
any `DANGEROUS` tool handler is invoked.

```
ApprovalGate        .check(meta, args) -> bool
                    .backend            # async callable, injectable
get_approval_gate() -> singleton
```

Behaviour by safety level:
- `SAFE` / `MODERATE` -- always approved; gate is a no-op.
- `DANGEROUS` -- delegated to `backend`. Default backend logs WARNING and approves
  (non-blocking, operator watches terminal). Override `gate.backend` with an async
  callable (e.g. Telegram confirmation prompt) to require explicit approval.

Bypass: `NAVIG_ALLOW_ALL_COMMANDS=1` env var skips the gate entirely.

---

### 10.4 `navig.gateway.session_store` -- Per-Channel Operator Context

Tracks per-channel state for the single operator across simultaneous gateway
sessions (Telegram, VS Code, REST, etc.).

```
SessionKey(channel_type, thread_id)    # frozen, hashable
OperatorContext                        # .get/set/unset(key), .increment_turn(),
                                       # .is_idle(threshold), .to_dict()
SessionStore                           # .get_or_create(key), .touch(key),
                                       # .update(key, meta), .remove(key),
                                       # .active_contexts(), .expire_idle(threshold)
get_session_store() -> singleton
```

Persistence: optional JSON file via `gateway.session_store_path` config key.

---

### 10.5 `navig.tools.hook_bridge` -- Dual-Hook Topology

Bridges the two independent hook systems so all engine `global_hooks` observers
receive `ToolRouter` events without coupling the two registries directly.

```
ToolHookBridge.wire()   # subscribes 4 handlers on get_hook_registry():
                        #   BEFORE_EXECUTE -> ExecutionEvent(phase=BEFORE)
                        #   AFTER_EXECUTE  -> ExecutionEvent(phase=AFTER, success=True)
                        #   ERROR/DENIED   -> ExecutionEvent(phase=ERROR, success=False)
ToolHookBridge.unwire() # resets _WIRED flag (test teardown)
```

Wire once at daemon start. Double-wiring is guarded by a `_WIRED` flag.

```
navig.tools.hooks (sync HookRegistry)
       | hook_bridge
navig.engine.hooks (async ExecutionHooks / global_hooks)
```

---

### 10.6 `navig.tools.output_validator` -- Soft JSON Schema Validation

Post-execution output validation gate wired into `ToolRouter._raw_async_execute()`
after a successful tool call.

```
validate_output(output, schema, *, strict=False) -> (bool, str | None)
OutputValidationError   # raised only when strict=True
```

Strategy: `jsonschema` library when available; naive fallback checks
`type`, `required`, `enum`, `items` (first array element).
Always soft-fail by default -- returns (False, reason) and attaches
`metadata={"output_schema_warning": msg}` to the ToolResult; never aborts
the pipeline.

---

### 10.7 `ToolMeta` -- Output Schema & Autodoc

`ToolMeta` gains one new field and two new methods:

| Addition | Purpose |
|----------|---------|
| `output_schema: dict or None` | JSON Schema describing the tool's return value |
| `to_openapi_schema()` | Returns {operationId, summary, tags, x-safety, requestBody, responses?} |
| `to_markdown_summary()` (ToolRegistry) | Markdown table: tool / domain / safety / description |
| `to_openapi_schema()` (ToolRegistry) | OpenAPI 3.0 paths dict wrapping all registered tools |

`to_dict()` conditionally includes `output_schema` only when set -- backward-compatible.

---

### 10.8 `navig.skills.loader` -- Install-Spec Security

Two additions protect against malicious `install:` blocks in SKILL.md files:

```
SkillSecurityError(field_name, value, reason)   # ValueError subclass
_validate_install_spec(spec: dict)              # raises on first violation
```

Validation rules:

| Manager | Rule |
|---------|------|
| brew, apt | ^[A-Za-z0-9][A-Za-z0-9@+._/-]*$ |
| pip, npm, cargo | Same + PEP 440 / semver version specifiers allowed |
| go | Must not contain :// |
| download.url | Must start with https:// |

On violation: `parse_skill_file()` logs WARNING and returns None (skill silently
rejected). Never raises into the caller.

---

### 10.9 `navig.commands.tools` -- Tool Introspection CLI

```
navig tools list   [--domain X] [--detailed] [--available/--all]
                   [--format table|json|markdown]
navig tools schema [--output FILE] [--available/--all]
navig tools show   <name>
```

Registered in `navig/main.py` under the `_PLUGIN_FREE` fast-path set --
responds instantly without daemon contact.

---

### 10.10 Updated Dependency Graph

```
navig.agent.auth_profiles    <- navig.config
navig.tools.trust_boundary   <- stdlib only
navig.tools.approval         <- navig.tools.router (SafetyLevel, ToolMeta)
navig.tools.output_validator <- jsonschema (optional) | stdlib fallback
navig.gateway.session_store  <- navig.config
navig.tools.hook_bridge      <- navig.tools.hooks + navig.engine.hooks
navig.commands.tools         <- navig.tools.router (ToolRegistry)

Call path for a DANGEROUS tool:
  ToolRouter._raw_async_execute()
    -> ApprovalGate.check()          [10.3]
    -> handler()
    -> validate_output()             [10.6]
    -> HookRegistry (sync)           [Section 8]
       | ToolHookBridge              [10.5]
    -> ExecutionHooks / global_hooks [Section 1]
```
