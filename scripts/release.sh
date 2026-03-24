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

git tag -a "$TAG" -m "Release $TAG"
echo "✅ Tag $TAG created locally"

git push origin "$TAG"
echo "✅ Tag $TAG pushed to origin"
echo ""
echo "──────────────────────────────────────────"
echo "  Building and publishing to PyPI"
echo "──────────────────────────────────────────"

# Ensure build and twine are available
if ! python -m build --version &>/dev/null; then
  echo "Installing build..."
  pip install --quiet build twine
fi

# Clean previous dist artifacts, build fresh
rm -rf dist/
python -m build

# Validate the distributions before uploading
python -m twine check dist/*

echo ""
echo "Ready to upload to PyPI."
echo "Ensure TWINE_USERNAME=__token__ and TWINE_PASSWORD=<your-api-token> are set,"
echo "or that ~/.pypirc is configured."
echo ""
read -r -p "Upload to PyPI now? [y/N] " PYPI_REPLY
if [[ "$PYPI_REPLY" =~ ^[Yy]$ ]]; then
  python -m twine upload dist/*
  echo "✅ Published navig==$VERSION to PyPI"
  echo "   https://pypi.org/project/navig/$VERSION/"
else
  echo "⚠️  PyPI upload skipped. Run manually: python -m twine upload dist/*"
fi

echo ""
echo "Next steps:"
echo "  1. Create GitHub Release from $TAG in the repository UI"
echo "  2. Update CHANGELOG.md — move [Unreleased] entries under [$VERSION]"
echo "  3. Merge main back into develop: git checkout develop && git merge main"
