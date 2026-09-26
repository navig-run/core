- **A release bump now rotates the changelog — `[Unreleased]` under `## [X.Y.Z] — date`,
  fragments folded, all in the release commit.** The 3.25.0 release did this by hand;
  `version_bump.py` — the documented `npm run release:*` path — touched only the version
  manifests, so the first bump after changelog fragments landed would have shipped a tag whose
  changelog still said "Unreleased" and left every fragment on disk. `changelog_assemble.py
  --release X.Y.Z` is the rotation as a tool (byte-identical heading shape, fresh template whose
  `git log v<X.Y.Z>..HEAD` hint names the version just released, idempotent on re-run); the
  bump runs it FIRST, so its refusals — an empty release, a version already present, a malformed
  fragment — fire before `pyproject.toml` is rewritten, and `--no-changelog` is the one deliberate
  hatch. `tools/release.sh` runs the same step before it tags, and no longer names a `develop`
  branch or a `publish.yml` workflow this repo never had. Both entry points are gated for the
  first time (`tests/release/test_version_bump.py`, `test_release_script.py`).
