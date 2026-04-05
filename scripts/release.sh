#!/usr/bin/env bash
# scripts/release.sh — Create and push a signed annotated release tag.
#
# Usage: bash scripts/release.sh <version>
# Example: bash scripts/release.sh 2.5.0
#
# Requirements:
#   - Git Bash / WSL / macOS / Linux
#   - Remote 'origin' must be configured
#   - Must be run from repo root on main branch
#   - No uncommitted working-tree changes
set -euo pipefail

VERSION="${1:-}"
if [[ -z "$VERSION" ]]; then
  echo "Usage: bash scripts/release.sh <version>"
  echo "Example: bash scripts/release.sh 2.5.0"
  exit 1
fi

# Strip leading 'v' if supplied (we normalise to vX.Y.Z)
VERSION="${VERSION#v}"
TAG="v${VERSION}"

# ── Guards ─────────────────────────────────────────────────────────────────────
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "❌ Must be on main to release. Currently on: $CURRENT_BRANCH"
  echo "   Run: git checkout main && git pull origin main"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "❌ Uncommitted changes detected. Commit or stash them first."
  git status --short
  exit 1
fi

# Ensure develop has no unmerged commits that should be in this release
UNMERGED=$(git log main..develop --oneline 2>/dev/null | wc -l | tr -d ' ')
if [[ "$UNMERGED" -gt 0 ]]; then
  echo "⚠️  develop is $UNMERGED commit(s) ahead of main."
  echo "   Merge develop → release/* → main before tagging, or confirm this is intentional."
  read -r -p "   Continue anyway? [y/N] " REPLY
  [[ "$REPLY" =~ ^[Yy]$ ]] || exit 1
fi

if git tag -l "$TAG" | grep -q "$TAG"; then
  echo "❌ Tag $TAG already exists locally."
  exit 1
fi

echo "──────────────────────────────────────────"
echo "  Releasing $TAG from main"
echo "  Commit: $(git rev-parse --short HEAD) — $(git log -1 --format='%s')"
echo "──────────────────────────────────────────"

git pull origin main

# ── Update version manifests ───────────────────────────────────────────────────
echo "Syncing version manifests to $VERSION..."
python scripts/_version_sync.py --version "$VERSION"

# Stage manifests and amend if there are pending changes
if ! git diff --quiet latest.json config/latest.json 2>/dev/null; then
  git add latest.json config/latest.json
  git commit -m "chore(release): sync version manifests to $VERSION" || true
  git push origin main
  echo "✅ Manifest commit pushed to main"
fi

git tag -a "$TAG" -m "Release $TAG"
echo "✅ Tag $TAG created locally"

git push origin "$TAG"
echo "✅ Tag $TAG pushed to origin"

# ── Create GitHub Release with notes from .local/CHANGELOG.md ─────────────────
LOCAL_CHANGELOG=".local/CHANGELOG.md"
NOTES_FILE="/tmp/release_notes_${TAG}.md"

if [[ -f "$LOCAL_CHANGELOG" ]]; then
  # Extract the [VERSION] block (everything between ## [VERSION] and the next ## heading)
  NOTES=$(awk "/^## \[${VERSION}\]/{found=1; next} found && /^## \[/{exit} found{print}" "$LOCAL_CHANGELOG")
  if [[ -n "$NOTES" ]]; then
    printf "%s\n" "$NOTES" > "$NOTES_FILE"
    echo "📝 Extracted release notes from $LOCAL_CHANGELOG"
    HAVE_NOTES=true
  else
    echo "⚠️  No [${VERSION}] section found in $LOCAL_CHANGELOG — using auto-generated notes."
    HAVE_NOTES=false
  fi
else
  echo "⚠️  $LOCAL_CHANGELOG not found — using auto-generated notes."
  HAVE_NOTES=false
fi

if command -v gh &>/dev/null; then
  if [[ "$HAVE_NOTES" == "true" ]]; then
    gh release create "$TAG" \
      --title "$TAG" \
      --notes-file "$NOTES_FILE" \
      2>/dev/null && echo "✅ GitHub Release $TAG created with curated notes." \
      || echo "⚠️  Release may already exist or gh failed. Check https://github.com/navig-run/core/releases"
  else
    gh release create "$TAG" \
      --title "$TAG" \
      --generate-notes \
      2>/dev/null && echo "✅ GitHub Release $TAG created with auto-generated notes." \
      || echo "⚠️  Release may already exist or gh failed. Check https://github.com/navig-run/core/releases"
  fi
else
  echo "ℹ️  gh CLI not found — release will be created by GitHub Actions (auto-generated notes)."
fi

echo ""
echo "──────────────────────────────────────────"
echo "  Build verification"
echo "──────────────────────────────────────────"

# Ensure build and twine are available
if ! python -m build --version &>/dev/null; then
  echo "Installing build..."
  pip install --quiet build twine
fi

# Clean previous dist artifacts, build fresh
rm -rf dist/
python -m build

# Validate the distributions
python -m twine check dist/*

echo ""
echo "✅ Build artifacts validated."
echo ""
echo "PyPI publish triggered automatically by GitHub Actions when Release is published."
echo "  Workflow: .github/workflows/publish.yml"
echo ""
echo "Manual fallback (emergency only): python -m twine upload dist/*"

echo ""
echo "Next steps:"
echo "  1. Confirm PyPI workflow success: https://github.com/navig-run/core/actions"
echo "  2. Add [Unreleased] entries to .local/CHANGELOG.md for next release"
echo "  3. Merge main back into develop: git checkout develop && git merge main"
