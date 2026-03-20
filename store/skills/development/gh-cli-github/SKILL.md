```skill
---
name: gh-cli-github
description: List/create PRs, issues, releases, and CI run status via the gh CLI
user-invocable: true
navig-commands:
  - navig dev gh pr-list --repo {owner/repo}
  - navig dev gh pr-create --title {title} --base main
  - navig dev gh issue-list --repo {owner/repo}
  - navig dev gh release-list --repo {owner/repo}
  - navig dev gh status
  - navig dev gh run --repo {owner/repo}
requires:
  - gh CLI in PATH (winget install GitHub.cli / brew install gh / apt install gh)
  - gh must be authenticated (gh auth login)
os: [windows, linux, mac]
examples:
  - "List open pull requests"
  - "Create a draft PR for my current branch"
  - "Show recent GitHub Actions runs"
  - "List open issues assigned to me"
  - "What releases exist for this repo?"
  - "Show my GitHub status"
---

# GitHub CLI

Interact with GitHub repositories, pull requests, issues, releases, and Actions CI via the `gh` CLI.

## Prerequisites

- `gh` CLI installed and authenticated: `gh auth login`
- Infer `--repo` from current `git remote` if not specified

## Common Tasks

### List open pull requests

**User says:** "Any open PRs?"

```bash
navig dev gh pr-list
```

Or for a specific repo:

```bash
navig dev gh pr-list --repo owner/repo --state open --limit 20
```

### Create a pull request

```bash
navig dev gh pr-create --title "Fix auth middleware" --base main
```

Create as draft:

```bash
navig dev gh pr-create --title "WIP: new feature" --base main --draft
```

### List issues

```bash
navig dev gh issue-list --state open --assignee @me
```

### List releases

```bash
navig dev gh release-list --repo owner/repo --limit 5
```

### Show GitHub Actions runs

**User says:** "Did the CI pass?"

```bash
navig dev gh run --repo owner/repo --limit 5
```

Filter by workflow:

```bash
navig dev gh run --workflow "CI" --status failure
```

### Repository status overview

```bash
navig dev gh status
```

Returns open PRs, issues, and recent run summary for the current repo.

## Safety Notes

- `pr-create` supports `--dry-run` — shows what would be submitted without creating
- All list commands are read-only
- Authentication is checked early; exits with code `3` if not logged in
```
