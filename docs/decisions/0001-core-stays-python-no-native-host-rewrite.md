# ADR-0001 — `navig-core` stays Python; no native (Rust/Go) host rewrite

- **Status:** Accepted
- **Date:** 2026-06-19
- **Deciders:** project owner
- **Supersedes:** the frozen `host/_rust_legacy/` Rust supervisor and the stalled
  `host/` Go supervisor (both deleted in the change that introduced this ADR)

## Context

A recurring proposal is to split NAVIG into a **trusted native core** ("Layer 1 — Rust
Core": policy engine, secrets broker, process runner, PTY/SSH/session manager, filesystem
guard, audit log, permission store, plugin verifier, sandbox manager, local gateway) with a
**Python agent runtime** on top ("Layer 2").

Three facts make this proposal a re-run of a road already travelled:

1. **The Rust core was already built and abandoned.** `host/_rust_legacy/` described itself
   as *"Production-grade cross-platform Rust daemon replacing navig-core (Python)"* with the
   exact crates being re-proposed: `core_auth` (JWT/scopes/secret store), `core_api`
   (Axum gateway), `core_plugins` (Python subprocess manager), `core_os` (keychain/paths),
   `core_events`. It was frozen ("DO NOT DEVELOP HERE").

2. **A Rust → Go rewrite was then attempted and also stalled.** `host/README.md`:
   *"The host supervisor is being rewritten from Rust to Go."* The Go module
   (`host/cmd/navig-host`, `host/internal/*`) never reached parity and was never wired into
   the product.

3. **Pure-Python `navig-core` is what shipped and got hardened.** ~87K LOC across ~1,060
   files, ~975 test files, and 8 rounds of production-security audits
   (`docs/PRODUCTION_AUDIT.md`, monorepo root). Every box in the proposed "Layer 1" already
   exists in Python and is exercised in production:

   | Proposed native crate | Existing Python implementation |
   |---|---|
   | policy engine     | `navig/approval/policies.py`, `navig/gateway/policy_gate.py`, `navig/permissions/rules.py` |
   | secrets broker    | `navig/vault/*` (crypto, manager, resolver, session; DPAPI/keychain) |
   | filesystem guard  | `navig/core/file_permissions.py`, `navig/safety_guard.py` |
   | audit log         | `navig/gateway/audit_log.py`, `navig/store/audit.py` |
   | permission store  | `navig/permissions/*` (loader, rules, rule_parser) |
   | sandbox manager   | `navig/tools/sandbox.py`, `navig/tools/code_exec_sandbox.py` |
   | local gateway     | `navig/gateway/server.py` (binds 127.0.0.1, `auth_guard`) |

### Why a rewrite would not address NAVIG's actual risks

Rust's headline benefit is **memory safety**. The 8 audit rounds found **zero** memory-safety
bugs. Every finding was a policy/logic/config error that ports 1:1 into any language:

- R1 — prototype pollution, `pickle.load` RCE, swallowed exceptions
- R4 (**CRITICAL**) — tunnel auth bypass (`Origin: localhost` trusted through cloudflared)
- R3 — timing-unsafe `==` on an API key; wildcard CORS
- R6 — event loop blocked by synchronous `psutil` (fixed with a 1-line `asyncio.to_thread`)

The security of this system lives in the **correctness of the policy/auth boundary**, which is
language-independent. A rewrite would discard 8 rounds of hardening and re-introduce the same
class of bugs in a language where the last two attempts are sitting frozen.

## Decision

1. **`navig-core` remains a pure-Python daemon.** It is the trusted core and the agent runtime.
   No native (Rust/Go) host supervisor.
2. **The trust boundary is hardened in place**, not rewritten: funnel every side-effecting
   action (process exec, fs write, secret read, network egress) through the existing
   `gateway/policy_gate.py` chokepoint + audit log; close the standing audit proposals
   (`P-A` masked TS errors, `P-B` wildcard CORS, `P-C` `subprocess(shell=True)` paths).
3. **OS-level isolation is achieved with OS features, not a new language** — restricted
   subprocesses (Windows Job Objects / Linux seccomp+namespaces or bubblewrap), dropped
   privileges, and an explicit fs/network allowlist enforced by the policy gate.
4. **The dead scaffolding is deleted** (`host/_rust_legacy/` + the Go `host/`) so the repo
   stops implying an in-flight migration. The stale `navig-browser-agent` probe in
   `navig/commands/doctor.py` is removed; the live browser implementation is the Python
   `navig/browser/` stack (CDP), which nothing in the daemon delegates to the Go binary.

## When to revisit (native is in-scope only if one of these is true)

- A **hard memory-safety boundary** is genuinely needed — e.g. parsing untrusted *binary*
  input, or running untrusted *native* plugin code in-process. (Today plugins run as
  Python subprocesses / MCP — already an OS-process boundary.)
- A **sustained CPU-bound hot path** Python provably cannot meet. (The one perf incident, R6,
  was a blocking syscall, not a Python-speed problem.)
- **Single-binary distribution** becomes a hard product requirement *and* PyInstaller/Nuitka
  are proven insufficient.

If a criterion fires, the response is **one surgical native primitive** (e.g. a sandbox
launcher or syscall filter, ~hundreds of lines) that the Python core shells out to (subprocess
or PyO3) — **not** a rewrite of policy/audit/vault/gateway. That is a scalpel, not a new body.

## Consequences

- **Positive:** keeps 8 rounds of hardening; no multi-month rewrite; removes 640 MB of dead
  build artifacts and ~171 tracked legacy files; eliminates the "migration in progress"
  ambiguity that has invited three restart attempts.
- **Negative / accepted:** NAVIG stays subject to Python's deployment model (interpreter +
  deps) and GIL; mitigated by `asyncio.to_thread`/process offload where measured.
- **Follow-up:** the hardening work (decision points 2–3) is tracked separately, starting with
  the single-policy-chokepoint pass over `gateway/policy_gate.py` and the `approval` package.

## Alternatives considered

- **Full Rust core rewrite** — *rejected.* Already built once (`_rust_legacy/`) and abandoned;
  addresses no observed bug class; highest cost, lowest return.
- **Go host supervisor** — *rejected.* Already attempted (`host/`), never reached parity, never
  wired in.
- **Harden the existing Python boundary + OS-level isolation** — **chosen.** Highest security
  ROI, no language migration, preserves the audited surface.
