#!/usr/bin/env bash
# github-bootstrap.sh — navig-run/core branch protection setup
# Label: READY_TO_RUN_WITH_GH
# Requires: gh auth status with admin or repo-scoped token on navig-run/core
# Idempotent: safe to re-run (PUT replaces existing protection rules)
set -euo pipefail

REPO="navig-run/core"
BRANCH="main"

echo "==> Verifying gh CLI auth..."
if ! gh auth status 2>&1 | grep -q "Logged in"; then
  echo "ERROR: gh CLI is not authenticated. Run: gh auth login"
  exit 1
fi

echo "==> Verifying repo access..."
if ! gh api "repos/${REPO}" --silent 2>/dev/null; then
  echo "ERROR: Cannot access ${REPO}. Check token scope (needs 'repo' or 'admin:repo')."
  exit 1
fi

echo "==> Applying branch protection to ${REPO}:${BRANCH}..."

# Status check names must match exact job names from ci.yml
# The matrix produces: "secret-scan", "test (3.10)", "test (3.11)", "test (3.12)"
# We require all three test variants + the secret scan gate.
gh api \
  --method PUT \
  -H "Accept: application/vnd.github+json" \
  "/repos/${REPO}/branches/${BRANCH}/protection" \
  --input - <<'EOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "secret-scan",
      "test (3.10)",
      "test (3.11)",
      "test (3.12)",
      "test (3.13)",
      "test-go"
    ]
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false,
  "block_creations": false
}
EOF

echo "==> Verifying protection applied..."
STATUS=$(gh api "repos/${REPO}/branches/${BRANCH}/protection" \
  --jq '{
    required_reviews: .required_pull_request_reviews.required_approving_review_count,
    dismiss_stale: .required_pull_request_reviews.dismiss_stale_reviews,
    codeowner_reviews: .required_pull_request_reviews.require_code_owner_reviews,
    status_checks: .required_status_checks.contexts,
    enforce_admins: .enforce_admins.enabled,
    force_push: .allow_force_pushes.enabled,
    deletions: .allow_deletions.enabled
  }')
echo "==> Protection config:"
echo "$STATUS"

echo ""
echo "==> Done. Branch '${BRANCH}' on ${REPO} is protected."
echo "    PRs require:"
echo "    - 1 approving review (stale reviews dismissed, CODEOWNERS respected)"
echo "    - All 6 CI checks to pass: secret-scan, test (3.10), test (3.11), test (3.12), test (3.13), test-go"
echo "    - Branch up-to-date with main before merge"
echo "    Force pushes and deletions are disabled."
echo "    enforce_admins: disabled (maintainer can merge emergency fixes without CI gate)"
