# Contribution Pipeline — NAVIG Self-Heal & Hive Mind Protocol

> **Opt-in.** Nothing runs without explicit user consent at every step.

---

## Architecture Overview

```
navig contribute scan
│
├─ 1. Fork           POST /repos/navig-run/core/forks  (one-time)
├─ 2. Clone/Sync     git clone ~/.navig/core-repo/  OR  fetch + rebase
├─ 3. Branch         navig-selfheal/{YYYYMMDD}-{8-char-hash}
├─ 4. Scan           LLM reads local .py files → ScanFinding[]
│                    (vault/, *.env, token/secret files are NEVER sent)
├─ 5. Review         CLI confirm prompts  OR  Telegram approval flow
│                    (user can reject any group of findings)
├─ 6. Patch          difflib unified diff applied via `git apply`
├─ 7. Commit+Push    git commit + push to fork branch
└─ 8. PR             POST /repos/navig-run/core/pulls → returns URL
```

---

## Requirements

| Requirement | Details |
|---|---|
| GitHub PAT | Scopes: `repo`, `workflow` — stored in NAVIG vault as `github_contribute` |
| Git | Must be on `PATH` |
| Python | >=3.10 |
| Opt-in config | `contribute.enabled: true` in `.navig/config.yaml` |

---

## Enable

### During `navig init`

At the end of the onboarding flow, NAVIG will prompt:

```
Enable Contribution Mode? [y/N]
```

Choosing `y` writes the config block and prompts for an alias (used in PR author credits).

### Manual Configuration

Add to `.navig/config.yaml`:

```yaml
contribute:
  enabled: true
  alias: your-alias          # shown in PR body as author credit
  min_confidence: 0.85       # minimum LLM confidence to include a finding (0-1)
  upstream_repo: navig-run/core
  clone_path: ~/.navig/core-repo
```

Store your PAT:

```bash
navig vault set github_contribute
# Paste your token when prompted — it is never logged
```

---

## Running a Scan

```bash
navig contribute scan             # full interactive pipeline
navig contribute scan --dry-run   # preview findings only, no PR created
navig contribute status           # show fork URL, config, and last-run info
```

---

## Approval Flow

After the scan, each severity group (`critical`, `high`) is shown with a summary table.

**CLI mode** (default):

```
┌─ critical findings (2) ──────────────────────────────────────────┐
│  navig/commands/run.py:47 — bare except clause                   │
│  navig/selfheal/scanner.py:112 — mutable default argument        │
└──────────────────────────────────────────────────────────────────┘
Approve and patch this group? [y/N]:
```

**Telegram mode** (activated when `TELEGRAM_BOT_TOKEN` is set):

The bot sends each group as an inline-keyboard message.  Tap **✅ Approve** or **❌ Skip** per group.  After reviewing all groups tap **🚀 Submit PR**.

---

## What Gets Patched

| Severity | Patched | Notes |
|---|---|---|
| `critical` | ✅ | Bug, security, or data-loss risk |
| `high` | ✅ | Correctness or significant quality issue |
| `medium` | ❌ | Reported only, never patched |
| `low` | ❌ | Informational |

Every patched line carries a `# NAVIG-HEAL: <reason>` inline annotation in the diff.

---

## Security Model

- **No secrets sent to LLM**: Files matching `vault/`, `secret`, `token`, `key`, `.env` are excluded before the LLM call.
- **PAT isolation**: `NAVIG_GITHUB_TOKEN` (vault provider `github_contribute`) is separate from `GITHUB_TOKEN` (used only for GitHub Models LLM access).
- **No auto-commit**: Git operations only happen after user approval.
- **No WAN mesh ops**: Contribution pipeline is local-only; findings never leave the machine except via the explicitly approved PR.
- **Opt-out is instant**: Set `contribute.enabled: false` in config — no background processes, no residual state.

---

## Branch & PR Conventions

- **Branch**: `navig-selfheal/{YYYYMMDD}-{8-char-hash}` — always short-lived, never to `main`.
- **PR title**: `fix: self-heal — N critical, M high issue(s) [alias]`
- **PR body**: Includes contributing alias, NAVIG version, finding table, and confidence scores.
- **Labels** (applied if they exist): `automated`, `self-heal`

---

## Opt-Out

```bash
# Permanent (config file)
navig config set contribute.enabled false

# One-time dry run (never creates a PR)
navig contribute scan --dry-run
```

Deleting `~/.navig/core-repo/` removes the local clone.  No data is retained on NAVIG servers.
