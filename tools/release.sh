#!/usr/bin/env bash
# tools/release.sh — finish a release: verify the build, tag, publish, GitHub Release, manifest.
#
# Usage: bash tools/release.sh <version> [--publish] [--skip-verify]
# Example:
#   python tools/version_bump.py bump minor --commit      # bumps pyproject, folds changelog fragments,
#                                                         # rotates [Unreleased] -> ## [X.Y.Z], commits
#   bash tools/release.sh 3.26.0 --publish                # everything below
#
# What it does, in order — each step is a precondition of the next:
#   1. guards: on main, clean tree, tag absent (local + origin), pyproject.toml == <version>
#   2. changelog rotated under ## [<version>] (idempotent after version_bump) + manifests, committed
#   3. build wheel + sdist, `twine check`, and the REAL-INSTALL smoke (scripts/verify-install.mjs)
#      — the gate that catches what pytest against the repo cannot; skip with --skip-verify
#   4. push main, tag, push the tag
#   5. --publish: upload to PyPI with twine (TWINE_USERNAME/TWINE_PASSWORD or ~/.pypirc).
#      The org's GitHub Actions is billing-blocked, so the tag workflow does NOT run here —
#      this IS the publish path. Without --publish the exact command is printed.
#   6. GitHub Release with the wheel + sdist ATTACHED, body from the one release-notes writer
#      (`changelog_assemble.py --release-notes`, shared with release.yml)
#   7. re-sync latest.json now that the asset exists (so download_url is real, not null),
#      commit + push — the second commit release.yml would have made
#
# Requirements: Git Bash / WSL / macOS / Linux; remote 'origin'; python; gh (for step 6).
set -euo pipefail

VERSION="${1:-}"
PUBLISH=false
SKIP_VERIFY=false
for arg in "${@:2}"; do
  case "$arg" in
    --publish) PUBLISH=true ;;
    --skip-verify) SKIP_VERIFY=true ;;
    *) echo "Unknown option: $arg"; exit 1 ;;
  esac
done
if [[ -z "$VERSION" ]]; then
  echo "Usage: bash tools/release.sh <version> [--publish] [--skip-verify]"
  echo "Example: bash tools/release.sh 3.26.0 --publish"
  exit 1
fi

# Strip leading 'v' if supplied (we normalise to vX.Y.Z)
VERSION="${VERSION#v}"
TAG="v${VERSION}"
CORE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$CORE_DIR"

# ── Guards ─────────────────────────────────────────────────────────────────────
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
if [[ "$CURRENT_BRANCH" != "main" ]]; then
  echo "ERROR: must be on main to release. Currently on: $CURRENT_BRANCH"
  echo "       Run: git checkout main && git pull origin main"
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "ERROR: uncommitted changes detected. Commit them first."
  git status --short
  exit 1
fi

# Every branch merges to main through a PR here (there is no develop); a release is cut
# from main and nowhere else. Unmerged work lives on feature branches — `navig repo stale`.

if git tag -l "$TAG" | grep -qx "$TAG"; then
  echo "ERROR: tag $TAG already exists locally."
  exit 1
fi
if git ls-remote --tags origin "refs/tags/$TAG" | grep -q .; then
  echo "ERROR: tag $TAG already exists on origin."
  exit 1
fi

# The version this script is told to release must be the version the tree BUILDS. This
# tool only ever synced latest.json; pyproject.toml is bumped by version_bump.py. Without
# this check `release.sh 3.26.0` on a 3.25.0 tree tagged v3.26.0 over a navig-3.25.0 wheel.
PYPROJECT_VERSION=$(python -c "import re,pathlib; print(re.search(r'(?m)^version\s*=\s*\"(\d+\.\d+\.\d+)\"', pathlib.Path('pyproject.toml').read_text(encoding='utf-8')).group(1))")
if [[ "$PYPROJECT_VERSION" != "$VERSION" ]]; then
  echo "ERROR: pyproject.toml says $PYPROJECT_VERSION, not $VERSION - the wheel would carry the wrong version."
  echo "       Bump first: python tools/version_bump.py bump <patch|minor|major> --commit"
  exit 1
fi

echo "──────────────────────────────────────────"
echo "  Releasing $TAG from main"
echo "  Commit: $(git rev-parse --short HEAD) — $(git log -1 --format='%s')"
echo "──────────────────────────────────────────"

git pull --ff-only origin main

# ── Changelog: fold fragments, rotate [Unreleased] under this version ──────────
# Idempotent: when version_bump.py already rotated it (the normal path), this is a
# no-op. It refuses an EMPTY release — the release body below would be empty too, so a
# tag with nothing to say is stopped here, not discovered on the Releases page.
echo "Rotating CHANGELOG.md [Unreleased] under [$VERSION]..."
python tools/changelog_assemble.py --release "$VERSION"

# ── Update version manifests ───────────────────────────────────────────────────
# download_url is null at this point — the asset it points at does not exist yet. Step 7
# re-syncs after the GitHub Release is created, which is when the URL becomes real.
echo "Syncing version manifests to $VERSION..."
python tools/_version_sync.py --version "$VERSION"

git add latest.json CHANGELOG.md
git add -A changelog.d 2>/dev/null || true   # the fragments the rotation consumed
if ! git diff --cached --quiet; then
  git commit -m "chore(release): sync version manifests and changelog to $VERSION"
  echo "Release commit created."
fi

# The body is composed ONCE, before anything irreversible, so a changelog with no block
# for this version stops the release here.
NOTES_FILE="$(mktemp -t "release_notes_${TAG}.XXXXXX")"
python tools/changelog_assemble.py --release-notes "$VERSION" > "$NOTES_FILE"

# ── Build + verify BEFORE the tag: never tag something that does not build ───────
echo ""
echo "──────────────────────────────────────────"
echo "  Build + verification"
echo "──────────────────────────────────────────"
if ! python -m build --version &>/dev/null; then
  echo "Installing build + twine..."
  pip install --quiet build twine
fi
rm -rf dist/
python -m build
python -m twine check dist/*

# The artifact must carry THIS version — the guard above makes it so; this proves it.
if ! ls dist/navig-"$VERSION"-*.whl >/dev/null 2>&1; then
  echo "ERROR: no dist/navig-$VERSION-*.whl was built:"; ls dist/; exit 1
fi

if [[ "$SKIP_VERIFY" == "true" ]]; then
  echo "WARN: --skip-verify: the real-install smoke was NOT run."
else
  # Installs the wheel into a throwaway venv and drives it (space init -> space doctor,
  # the builtin store, prompts, skills...). ~3.5 min. Every other check inspects the
  # repo, where every asset is on disk no matter what the wheel contains.
  node "$CORE_DIR/../scripts/verify-install.mjs" --wheel "$(ls dist/navig-"$VERSION"-*.whl | head -1)"
fi
echo "Build artifacts validated."

# ── Push main, tag, push the tag ───────────────────────────────────────────────
git push origin main
git tag -a "$TAG" -m "Release $TAG"
git push origin "$TAG"
echo "Tag $TAG pushed to origin."

# ── Publish to PyPI ───────────────────────────────────────────────────────────
# The org's GitHub Actions is billing-blocked: the tag workflow (release.yml) does not
# run, so nothing publishes unless this does. 3.25.0 went out this way.
if [[ "$PUBLISH" == "true" ]]; then
  python -m twine upload dist/*
  echo "Published $VERSION to PyPI."
else
  echo ""
  echo "NOT published. Upload the artifacts that were just verified and tagged with:"
  echo "  cd core && python -m twine upload dist/*"
fi

# ── GitHub Release: the one body, with the artifacts attached ─────────────────
# Assets attached so latest.json's download_url (step 7) points at something that exists.
# --generate-notes appends GitHub's "What's Changed" PR list after our body, exactly as
# release.yml's action does — one shape either way.
if command -v gh &>/dev/null; then
  if gh release create "$TAG" --title "NAVIG $TAG" --notes-file "$NOTES_FILE" --generate-notes dist/*; then
    echo "GitHub Release $TAG created with the changelog notes and $(ls dist | wc -l | tr -d ' ') asset(s)."
  else
    echo "WARN: gh release create failed - create it by hand: gh release create $TAG --notes-file $NOTES_FILE dist/*"
    exit 1
  fi
else
  echo "ERROR: gh CLI not found - the GitHub Release (and the asset latest.json links to) needs it."
  echo "       Install gh, then: gh release create $TAG --title 'NAVIG $TAG' --notes-file $NOTES_FILE --generate-notes dist/*"
  exit 1
fi

# ── Record the release in the manifest, now that its asset exists ──────────────
# The tool HEADs the asset and writes its URL only when it is really there; before this
# point that is null. Same second commit release.yml makes, done here because it cannot.
python tools/_version_sync.py --version "$VERSION" --released-at "$(date -u +%Y-%m-%d)"
git add latest.json
for www_rel in ../web/www/content/copy.ts ../web/www/public/latest.json; do
  [[ -f "$www_rel" ]] && git add "$www_rel"
done
if ! git diff --cached --quiet; then
  git commit -m "chore(release): record $TAG asset in latest.json"
  git push origin main
  echo "latest.json now records the verified $TAG asset."
fi

PUBLISHED_NOTE=""
if [[ "$PUBLISH" == "true" ]]; then PUBLISHED_NOTE=", published to PyPI"; fi
echo ""
echo "Done: $TAG tagged, released${PUBLISHED_NOTE}, manifest recorded."
echo "Next release's entries go in as fragments: changelog.d/<slug>.<kind>.md"
