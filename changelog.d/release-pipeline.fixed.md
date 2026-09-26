- **`tools/release.sh` is the release pipeline that actually ships — and it could not.** The
  org's GitHub Actions is billing-blocked, so the tag workflow never fires and 3.25.0 went to
  PyPI by hand with no tag at all. Read as the pipeline it is, the script had five defects:
  it never checked that `pyproject.toml` carries the version it was told to release
  (`release.sh 3.26.0` on a 3.25.0 tree tagged v3.26.0 over a `navig-3.25.0` wheel); it built
  AFTER tagging; it synced `latest.json` BEFORE the GitHub Release existed, so the hand path
  always recorded a null `download_url`; its `gh release create` pasted a changelog block
  GitHub refuses over 125,000 characters (3.25.0's is 288 KB) and attached no asset; and it
  said the tag push publishes. Now: guards (main · clean · tag absent locally and on origin ·
  pyproject == version) → changelog rotated → build + `twine check` + the wheel is
  `navig-X.Y.Z` + the real-install smoke → push main, tag → `--publish` uploads with twine
  (said plainly: this is the path) → GitHub Release with the artifacts attached → `latest.json`
  re-synced against the asset that now exists. The release body has ONE writer
  (`changelog_assemble.py --release-notes`: install block + the version's block, or its
  headline digest when oversize), shared with `release.yml`, which also refuses a tag whose
  `pyproject.toml` says another version. `version_bump.py --push` now pushes the release
  commit to main before the tag (a tag whose commit is not on main was half a release), and
  every print in the three release tools is ASCII — `--release` had died on this machine's
  cp1251 console on a check mark, after rotating the changelog. The guards run for real in
  the tests against a throwaway repo; the irreversible order is pinned at source.
