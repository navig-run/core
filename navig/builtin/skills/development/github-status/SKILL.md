---
name: github-status
description: Check GitHub repos, CI status, PRs, and issues using the gh CLI
user-invocable: true
navig-commands:
  - navig run "gh pr list --repo {owner}/{repo}"
  - navig run "gh run list --repo {owner}/{repo} --limit 5"
  - navig run "gh issue list --repo {owner}/{repo}"
requires:
  - gh (GitHub CLI, installed on local machine or remote server)
examples:
  - "Check CI status on my repo"
  - "Are there any open PRs?"
  - "Show recent GitHub Actions runs"
  - "List open issues"
  - "Did the last deploy pass?"
---

# GitHub Status

Check GitHub repositories, CI pipelines, pull requests, and issues. Works locally (if `gh` is installed) or on remote servers.

## Prerequisites

- `gh` CLI must be installed and authenticated
- Run locally: just use `gh` commands directly
- Run on server: `navig run "gh ..."`

## Common Tasks

### Check CI/CD Status

**User says:** "Did the build pass?" / "Check CI status"

```bash
gh run list --repo {owner}/{repo} --limit 5
```

**Response format:**
```
🔄 Recent CI Runs for {owner}/{repo}:

✅ Build & Test (#234) - 2m ago - success
✅ Deploy Staging (#233) - 1h ago - success
❌ Build & Test (#232) - 3h ago - failure
✅ Build & Test (#231) - 5h ago - success
```

### View Failed Run Details

```bash
gh run view {run-id} --repo {owner}/{repo} --log-failed
```

**Response format:**
```
❌ Failed Run #232 Details:

Step: "Run tests" failed
Error: AssertionError in test_auth.py:45
  Expected 200, got 401

💡 Looks like an auth test failure. Want me to check the code?
```

### List Pull Requests

**User says:** "Any open PRs?" / "Show pull requests"

```bash
gh pr list --repo {owner}/{repo} --json number,title,author,createdAt
```

**Response format:**
```
📋 Open PRs for {owner}/{repo}:

#55 - Fix auth middleware (by alice, 2 days ago)
#53 - Add dark mode (by bob, 5 days ago)
#51 - Update dependencies (by dependabot, 1 week ago)
```

### Check PR CI Status

```bash
gh pr checks {pr-number} --repo {owner}/{repo}
```

### List Issues

```bash
gh issue list --repo {owner}/{repo} --json number,title,labels,assignees --limit 10
```

**Response format:**
```
🐛 Open Issues for {owner}/{repo}:

#42 - Login fails on mobile [bug] (assigned: alice)
#38 - Add export feature [enhancement] (unassigned)
#35 - Update docs [docs] (assigned: bob)
```

### Create an Issue

```bash
gh issue create --repo {owner}/{repo} --title "Bug: ..." --body "Description..."
```

## Advanced Queries

### API Access for Custom Data

```bash
gh api repos/{owner}/{repo}/pulls/55 --jq '.title, .state, .user.login'
```

### JSON Output for Parsing

```bash
gh pr list --repo {owner}/{repo} --json number,title --jq '.[] | "\(.number): \(.title)"'
```

## Local vs Remote

- **Local machine**: Run `gh` commands directly (faster, uses your auth)
- **Remote server**: `navig run "gh ..."` (if gh is installed on server)
- **Tip**: For CI checks on deploy servers, `gh` on the server is handy

## Error Handling

- **gh not installed**: "GitHub CLI (gh) is not installed. Install: https://cli.github.com"
- **Not authenticated**: "Run `gh auth login` to authenticate with GitHub"
- **Repo not found**: "Repository '{repo}' not found. Check the name or your permissions."
