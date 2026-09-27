- **`navig doctor` → Release now says when the NEXT core wheel cannot be installed.** Core has
  required `navig-contacts>=0.1.0` since #1306 and that package was never uploaded to PyPI, so
  a core release would be uninstallable for everyone — while every Release row stayed green.
  A new **`Release · hard deps`** row checks each `navig-*` package in core's required
  dependencies against PyPI: not published, or published below its floor, is a ✗ naming the
  publish command; PyPI unreachable is a ⚠ "not checked", never an accusation. Extras and
  comment prose are out of scope. `docs/dev/release.md` gains the ordered procedure — the dep
  imports names the published core lacks, so it ships in the same session as core, after the
  bump.
