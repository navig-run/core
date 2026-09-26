# changelog.d — one fragment per change

**Do not edit `core/CHANGELOG.md`'s `[Unreleased]` section directly. Drop a file here.**
The pre-push gate enforces it: a branch that adds an entry to `[Unreleased]` by hand is
refused with the line and this file named (`scripts/check-changelog-fragments.mjs`). To recover:
write the fragment, then `git checkout origin/main -- core/CHANGELOG.md`. Assemblies and release
rotations — what the tools write — pass.

`core/CHANGELOG.md` is the hottest file in the repo (189 commits in 60 days), and every
change used to insert its entry at the same line — the top of `[Unreleased] > ### Added`.
Two branches doing that collide on every rebase, and GitHub's server-side merge does not
run merge drivers, so even with `merge=union` a PR showed as *conflicting* and cost a
rebase plus a full pre-push gate per collision (measured: four in three hours). A fragment
is a **new file per change**, so no two changes touch the same path and GitHub merges them
without a rebase.

## The file

```
core/changelog.d/<slug>.<kind>.md
```

- `slug` — your branch slug (`a-z 0-9 -`), e.g. `repo-add-timeout`.
- `kind` — one of `added` `changed` `deprecated` `removed` `fixed` `security`
  (Keep a Changelog's headings).
- body — the entry **exactly as it will appear** in the changelog: a Markdown list entry
  starting with `- **`, continuation lines indented two spaces, wrapped at ~95 columns.
  One file may hold several entries of the same kind.

Example — `core/changelog.d/repo-add-timeout.fixed.md`:

```markdown
- **`navig repo new` reported "timed out after 15s" over a checkout that then FINISHED.**
  `git worktree add` is not a query — it checks out the whole tree — but it ran under the
  15 s query budget, and the timeout only killed `git` itself …
```

## Assembling

```
npm run changelog:check       # validate every fragment (the gate runs this too)
npm run changelog:assemble    # fold them into CHANGELOG.md [Unreleased], delete the files
```

You rarely run `assemble` yourself: **the release bump does it** — `npm run release:*` (or
`python tools/changelog_assemble.py --release X.Y.Z`) folds every fragment and rotates
`[Unreleased]` under `## [X.Y.Z] — <date>` in the release commit, refusing an empty release.
Run `assemble` by hand only when the Unreleased section should be readable in one place
before a release. It is deterministic (entries ordered by slug, newest section first) and idempotent
(no fragments → the changelog is left byte-identical). An assembly is an ordinary PR that
touches `CHANGELOG.md` and deletes fragments — deleting fragment A never conflicts with
adding fragment B, so assemblies are conflict-free too.

Other packages (`plugins/*`, `apps/*`) keep writing dated entries straight into their own
`CHANGELOG.md` — their traffic (38 commits across all plugins in the same 60 days) never
earned the machinery.
