- **`navig repo stale` says which unmerged branches carry user-facing work.** "3 ahead" says
  nothing about what a branch holds; a `core/changelog.d/` fragment is the cheapest honest
  signal — every merged change leaves one, so an unmerged branch holding one is a release note
  nobody can read yet. Each branch row now carries its fragment count (three-dot, added-only,
  scoped to the fragments dir, README excluded; also in `--json` as `fragments`), rendered as
  `N changelog fragment(s) — user-facing work waiting`. A branch with none says nothing.
