- **`navig repo gates` now shows the whole CI fleet, and `--prs` shows one repository.**
  The default view adds the self-hosted GitHub runners (online / idle / busy / offline, with the
  free-worker count and the local service state), the Actions jobs running and queued at
  GitHub with how long each waited for a runner, the **bottleneck** (lane-starved, cpu-bound,
  io-bound with `--deep`, github-queued, or a step running past twice its usual time), a
  typical-gate-time estimate that is shown **only** when at least five comparable runs agree
  closely (otherwise `—` and why), and the month's GitHub Actions spend against
  `github.ci_budget_usd` (default $10) — hosted minutes against the included quota, the billed
  net, and what GitHub's postponed per-minute self-hosted fee would add *if it were enacted*.
  `navig repo gates --prs` lists every open PR with its `gate-result` check and fingerprint,
  the async land queue, the last landed commit and, for a PR the lander refused, why. `--no-github`
  keeps it offline. `navig doctor` gains a **CI Runners** section on a runner host: services
  running, the shared lane directory writable by both accounts, the free-space floor, and a
  warning when every local account can modify the repository.
- **Self-hosted Windows runners, a PR gate, and an asynchronous lander.** `npm run land -- --queue`
  pushes the branch, opens the PR if needed, labels it `land` and returns at once; the lander
  rebases, reuses the PR's `gate-result` only on an exact fingerprint match (tree, base, tool
  versions, workflow) or gates the candidate itself, then fast-forwards `main` (a moved main
  rejects the push and the candidate is re-validated). `scripts/runners/install.ps1` provisions
  the runners under a dedicated non-admin `navig-ci` account — preview it with `-WhatIf`.
  See `docs/dev/ci-local.md` ("Runners, tiers & lander").
- **`navig vm push <vm> <files> --to <dir>`** copies files into a VM over its own key and
  known_hosts — what the clean-machine install tier needs to put a wheel into a reverted guest.
