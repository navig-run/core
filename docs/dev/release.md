# Release Checklist

This checklist is for NAVIG maintainers preparing an official release.

## Pre-Release

- [ ] **Update version number** — the bump scripts handle this automatically:
  - `pyproject.toml` (bumped by `version_bump.py`)
  - `latest.json` (root — canonical, auto-synced by `tools/_version_sync.py`)
  - `navig-www/public/latest.json` (auto-synced when `NAVIG_DEV_SYNC=1` or sibling dir detected)
- [ ] **Changelog** — nothing to write by hand at release time. Every merged change left a
  fragment in `changelog.d/`; the bump folds them and rotates `[Unreleased]` under the new
  version in the release commit. Preview with `npm run release:dry`; validate the fragments
  with `npm run changelog:check`. An empty release refuses the bump (`--no-changelog` to
  override, only when there truly are no user-facing entries).
- [ ] **Run full test suite**:
  ```bash
  pytest tests/ -v
  ```
- [ ] **Run linters**:
  ```bash
  ruff check navig tests
  ruff format --check navig tests
  ```
- [ ] **Test installation** on clean environment:
  ```bash
  pip install -e .
  navig --version
  ```
- [ ] **Review security** considerations (credentials, input validation)

## Before you release: first-party packages core cannot install without

`navig doctor` → **Release · hard deps** checks every `navig-*` package in core's REQUIRED
`dependencies`. When one is not on PyPI, the next core wheel cannot be installed by anyone,
and `release.sh`'s install gate refuses to ship it. As of 2026-09-27 that is
**`navig-contacts`** (a hard dependency since #1306, never uploaded).

It cannot simply be published first: it imports core names the published 3.25.0 does not
have (`scripts/check_plugin_core_floor.py` lists them), so its own floor must name the NEW
core. The order, in one session:

1. `python tools/version_bump.py bump minor --commit` — core becomes X.Y.Z.
2. Raise `navig>=` in the hard dep's `pyproject.toml` to X.Y.Z, commit (PR to `main`).
3. `node scripts/publish-plugins.mjs navig-contacts --publish` — for the minutes until
   step 4 lands it asks for a core PyPI does not have yet; nothing depends on it standalone.
4. `bash tools/release.sh X.Y.Z --publish` — the install gate now resolves it.

The same floor rule applies to every plugin `check_plugin_core_floor.py` flags (cabinet,
email, github, pipeline, audio): raise each floor to X.Y.Z after core ships, then publish.

## Build, tag, publish, release — one script

```bash
cd core
python tools/version_bump.py bump minor --commit   # pyproject + changelog rotated, one commit
bash tools/release.sh 3.26.0 --publish            # everything below
```

`tools/release.sh` does, in order — each step a precondition of the next:

1. **Guards** — on `main`, clean tree, tag absent locally AND on origin, and
   `pyproject.toml` carries exactly the version you named (else it points you at
   `version_bump.py`; a tag over a wheel of another version is the one mistake you cannot
   take back).
2. **Changelog** rotated under `## [X.Y.Z]` (a no-op after `version_bump`) + manifests, committed.
3. **Build** wheel + sdist, `twine check`, prove the wheel is `navig-X.Y.Z-*.whl`, then the
   **real-install smoke** (`scripts/verify-install.mjs`: install into a clean venv and drive
   it — the gate every repo-side check cannot be). `--skip-verify` skips only that.
4. **Push main, tag, push the tag.**
5. **Publish to PyPI** with `--publish` (`python -m twine upload dist/*`; twine reads
   `TWINE_USERNAME=__token__` / `TWINE_PASSWORD` or `~/.pypirc`). The org's GitHub Actions is
   **billing-blocked**, so the tag workflow (`.github/workflows/release.yml`) does not run —
   this flag IS how a release reaches PyPI. Without it the exact command is printed.
6. **GitHub Release** with the wheel and sdist **attached**, body from the one release-notes
   writer (`python tools/changelog_assemble.py --release-notes X.Y.Z`: install block + that
   version's changelog block, or its headline digest when the block is over GitHub's
   125,000-character limit), with GitHub's "What's Changed" list appended.
7. **`latest.json` re-synced** now that the release asset exists — `download_url` is verified,
   never fabricated — committed and pushed (the second commit `release.yml` would have made).

If Actions can run again, the same tag push runs `release.yml`, which composes the release
body from the same writer and refuses a tag whose `pyproject.toml` says another version.

### Quick bump commands (maintainers)

Use the helper script to bump `pyproject.toml` and create/push a tag in one command:

```bash
python tools/version_bump.py bump patch --commit --tag --push
python tools/version_bump.py bump minor --commit --tag --push
python tools/version_bump.py bump major --commit --tag --push
```

Optional npm-style shortcuts are available at repo root:

```bash
npm run release:dry
npm run release:normal
npm run release:minor
npm run release:big
```

Command mapping:

- `release:normal` → patch bump (`X.Y.Z` -> `X.Y.(Z+1)`) — folds `changelog.d/` fragments, rotates `[Unreleased]` under the new version, bumps `pyproject.toml`, syncs `latest.json`, commits (one commit, all of it), tags, pushes main **and** the tag. Then `bash tools/release.sh X.Y.Z --publish` builds, verifies, publishes and creates the release.
- `release:minor` → minor bump (`X.Y.Z` -> `X.(Y+1).0`)
- `release:big` → major bump (`X.Y.Z` -> `(X+1).0.0`)
- `release:dry` → preview next patch version only (no file or git changes)
- `version:sync` → manually re-sync `latest.json` files from current `pyproject.toml` version

To sync manifests manually without bumping (e.g. after a manual pyproject edit):

```bash
python tools/_version_sync.py
# or
npm run version:sync
```

## Post-Release

- [ ] **Announce release**:
  - GitHub Discussions
  - Discord (if available)
  - Social media (if applicable)
- [ ] **Monitor for issues**:
  - Check GitHub Issues for installation problems
  - Watch for security reports
- [ ] **Update documentation** if needed:
  - Installation instructions
  - Breaking change migration guides
- [ ] **Refresh the site's published snapshot** — `cd web/www && npm run sync:site`, then
  commit `content/cli-reference.generated.json`, `content/bay-catalog.generated.json`,
  `public/latest.json` (and `core/navig/data/bay-catalog.json`). These are what
  navig.run documents; they must describe the **released** CLI, so this is the one
  moment to regenerate and commit them. Run between releases, the builders overwrite
  the committed files and print a ⚠ saying so — `git checkout -- <file>` puts the site
  back on the shipped version.

## Hotfix Process

For critical bug fixes or security patches:

1. Create hotfix branch from release tag: `git checkout -b hotfix/v2.x.y v2.x.x`
2. Apply minimal fix
3. Update version to patch increment (e.g., `2.1.0` → `2.1.1`)
4. Follow full release checklist above
5. Merge hotfix back to `main`

---

**Next Release**: TBD
