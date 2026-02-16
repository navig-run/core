---
name: github-cli
description: "Interact with GitHub: Manage PRs, Issues, Actions, and Releases."
metadata:
  navig:
    emoji: 🐙
    requires:
      bins: [gh]
---

# GitHub CLI Skill

Official NAVIG interface for GitHub using the `gh` CLI. This skill allows you to manage the full development lifecycle from the terminal.

**Requirement**: User must be authenticated (`gh auth login`).

## Pull Requests

### Listing & Review
```bash
# List open PRs
gh pr list --limit 10

# View specific PR details
gh pr view <pr_number>

# Checkout a PR locally to test
gh pr checkout <pr_number>
```

### Action
```bash
# Approve a PR
gh pr review <pr_number> --approve -b "lgtm"

# Merge a PR (Squash is recommended default)
gh pr merge <pr_number> --squash --delete-branch
```

## Issues

### Management
```bash
# List issues assign to me
gh issue list --assignee "@me"

# Create a new issue
gh issue create --title "Bug: X is broken" --body "Steps to reproduce..."

# Close an issue
gh issue close <issue_number>
```

## CI/CD (GitHub Actions)

### Monitoring
```bash
# List recent workflow runs
gh run list --limit 5

# View failure details for a specific run
gh run view <run_id> --log-failed
```

### Triggering
```bash
# Manually trigger a workflow (e.g., Deploy)
gh workflow run deploy.yml -f environment=staging
```

## Releases

### Create Release
```bash
# Create a new release from a tag
gh release create v1.0.0 --title "v1.0.0" --notes "Release notes here..."
```

## Scripting Tips
Use `--json` and `jq` for powerful automation.
```bash
# Get URL of the latest release
gh release view --json url --jq .url
```



