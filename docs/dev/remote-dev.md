# Remote development readiness — `navig doctor remote-dev`

`navig doctor remote-dev` answers one question with evidence: **can NAVIG develop, test, build
and release while the owner's Windows workstation is powered off?** It is read-only — it never
writes config, never touches a repository, never publishes.

```
navig doctor remote-dev            # the six capabilities, with evidence
navig doctor remote-dev --verbose  # every requirement behind them
navig doctor remote-dev --json     # machine-readable, same data
navig doctor remote-dev --no-remote-probe   # skip the SSH round-trip
```

Exit code is `0` only when every capability is READY. Anything less exits `1`.

## Three states, never two

| State | Meaning |
|---|---|
| `READY` | every requirement behind the capability was **verified** |
| `BLOCKED` | at least one requirement was verified as **failing** |
| `UNVERIFIED` | at least one requirement could not be checked (and nothing failed) |

`UNVERIFIED` is not a soft pass. A capability with no evidence at all is `UNVERIFIED`, and an
unreadable input never becomes green — the same rule `navig doctor` enforces everywhere
(`tests/cli/test_doctor_honesty.py`).

## The six capabilities

| Key | What it claims |
|---|---|
| `REMOTE_EDIT_READY` | a browser reaches the persistent editor without the workstation |
| `REMOTE_AGENT_READY` | a coding agent can be dispatched to another machine |
| `REMOTE_CI_READY` | required checks run where the workstation is not their power source |
| `REMOTE_BUILD_READY` | a remote host holds the source and toolchain to build and verify a release |
| `REMOTE_RELEASE_READY` | a verified artifact can be published under exactly one authority |
| `NEURON_OFF_READY` | the composite: all five above hold with the workstation off |

`NEURON_OFF_READY` is **derived**, never asserted. It reports as `BLOCKED` and names every
capability that is not READY, so a single unverified credential is enough to hold the whole
claim back.

## What it reads

- the editor URL over HTTP (`remote_dev.editor_url`, default `https://code.miztizm.com`) — a
  3xx with `Www-Authenticate: Cloudflare-Access` is a pass *and* names the Access layer
- the existing node registry via `navig.contracts.store.RuntimeStore` — an empty registry is a
  verified failure, not an unknown: nothing can be dispatched anywhere but the local process
- the same registry over the brain's gateway API (`GET /runtime/nodes`) when `remote_dev.brain_url`
  and `gateway.auth.token` are both configured — the registry that decides placement lives on the
  brain, so a store read on a client machine is reported as *that machine's* store and the fleet
  registry stays `UNKNOWN` rather than being silently equated with it
- the local process table for CI runners — a runner process here is reported as a fact and kept
  `UNKNOWN`, because the same process is the answer on the brain and the dependency on a
  workstation; runner *provisioning* elsewhere is not locally observable, so it too is reported
  `UNKNOWN` together with the exact `gh api` command that can answer it
- `~/.navig/hosts/*.yaml` for `metadata.role: brain`, then one read-only SSH round-trip
  (`hostname`, checkout present, `git`/`node`/`python3` on PATH)
- `core/tools/release.sh` and `.github/workflows/release.yml` for publication markers — two
  live publishers is a failure, because a version can be published twice or the wrong one can win

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `remote_dev.editor_url` | `https://code.miztizm.com` | the editor surface to probe |
| `remote_dev.remote_repo_path` | `~/projects/navig` | where the brain host should hold the checkout |
| `remote_dev.ssh_alias` | `outpost` | SSH alias used when the registry address does not answer |
| `remote_dev.brain_url` | *(unset)* | the brain's gateway base URL, for reading the fleet registry |
| `gateway.auth.token` | *(unset)* | the gateway token that read is authorised with; never printed |

The SSH alias is a fallback that **names the drift** instead of silently working around it:
a pass reached through the alias reports that the registry address is stale.

Without `remote_dev.brain_url` and a gateway token, the fleet registry is out of reach from a
client machine — so run this command **on the brain** when the question is about the fleet, and
on a workstation when the question is about that workstation.

## Interpretation rule

A stale address and an unverifiable credential are different problems with different fixes.
Because a verified fact is never hidden behind an unknown, `exactly one publication path;
could not verify: credentials are not readable from here` tells you the authority question is
settled and only the credential is open.
