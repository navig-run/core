# Distributed missions — who runs a mission, and who decides

A Mission carries a `node_id`. Until 2026-10-28 nothing read it: `missions/executor.py` runs a
mission **locally** through the agent loop, and `node_id` was recorded metadata. A node registry
with no consumer is an inventory, not a scheduler. `missions/claimer.py` is the consumer.

## The protocol

```
node → brain   GET  /runtime/missions?node_id=<self>&status=queued
node → brain   POST /runtime/missions/{id}/advance  {"action": "start"}     ← the claim
node           run the mission
node → brain   POST /runtime/missions/{id}/complete {"succeeded", "result"|"error"}
                                                           ← signs an ExecutionReceipt
```

Three properties are load-bearing, and each is pinned by a test:

| Property | Why it matters |
|---|---|
| A lost claim is never executed | claiming is a compare-and-set on the mission's own state machine (`queued` → `running`), so the loser is told, not guessed at |
| An empty queue is idle, not broken | a node polling all night must not log an error per poll |
| Every claimed mission closes with a receipt | a mission left in `running` is invisible work; a failure closes as a *failed* mission with its reason attached |

`409` and `404` both mean "not ours" — another node won it, or it is gone. The claimer moves to
the next queued mission rather than failing.

## Running a worker

```bash
navig missions work --once                  # one poll against this install's store (the brain)
navig missions work --url http://brain:8789 --token … --loop --interval 30
```

`--url` empty means *this process holds the store* — the brain's own case, where the transport is
`StoreTransport`. Otherwise `HttpTransport` drives the brain over the runtime API it already
serves. `--node-id` defaults to the hostname. `--json` prints one object per poll, for a supervisor.

The store transport flushes after every mutation. Without that, a claim and its signed receipt
live only in the worker process, and the next `RuntimeStore()` loads the mission still `queued` —
work that happened and left no trace.

## Capabilities a node admits

`missions/claimer.py` keeps an explicit registry (`_EXECUTORS`) and refuses anything else with
`UnknownCapability`, which closes the mission as **failed** with the reason. Refusing loudly is the
point: a node that reports success for work it cannot do is worse than one that reports it cannot.

| Capability | What it does |
|---|---|
| `readiness.probe` | runs `navig.readiness.remote_dev.assess` on this machine and returns the six capability verdicts that `navig doctor remote-dev` prints |

A new capability is a function in `_EXECUTORS`; it gets the claim protocol, the receipt and the
CLI for free.

## What this does not do yet

- **No lease renewal.** A claimed mission has no lease, so a worker that dies mid-run leaves it
  `running` for ever. `Mission.timeout_secs` and the store's `timeout` action are the existing
  hooks for a reaper; nothing runs one.
- **No streaming.** The executor's SSE `mission_state` emission is not wired into this path.
- **One mission per poll.** Deliberate for the first slice: concurrency is a decision that needs
  its own fencing design, not a loop variable.

## Two things that bit, and are now pinned

**The completion route does not speak the store's language.** `POST /runtime/missions/{id}/complete`
takes `{outcome, output, error}` — not the store's `{succeeded, result}`. Sending the store's
spelling is silently lossy rather than obviously wrong: `outcome` defaults to `"success"`, so the
mission reports success while `output` is dropped and the receipt's `result_digest` covers nothing.
Measured against the live gateway on 2026-10-28: a mission came back `succeeded` with
`result: null`. `test_http_complete_speaks_the_route_contract_not_the_store_spelling` pins it.

**Node identity is asserted, not proven.** `--node-id` is whatever the caller says, and the bearer
token is installation-wide, so a worker can claim a mission addressed to *another* node and its
result is then attributed to that host. Measured: a mission addressed to `outpost` was claimed from
the workstation and reported the workstation's readiness. The probe is honest about the machine it
ran on — the *attribution* is the lie. Until per-node credentials exist, run a worker only on the
machine it claims for, and treat the token as the fleet credential it is. A later increment:
bind `node_id` to the authenticated principal, or mint one token per node.
