- **`navig space rename <space> <new-id>` — one command for an id that lives in three places.**
  A space's id is written in `.navig/space.json`, in the registry row in `spaces.json`, and in
  the active-space pointer when it is the space you stand in; editing one by hand left the other
  two answering to a name that no longer existed. `rename` takes an id or a path, writes the
  manifest FIRST (discovery re-derives the registry from it, so a failed registry write heals on
  the next tick rather than the reverse), re-keys the registry row, moves the active pointer only
  when it is this space, and follows labels that were DERIVED from the old id — the
  `display_name` that `space init` writes and `NAVIG.md`'s `space:` frontmatter — while leaving
  a label someone chose alone. It never moves the folder and never adds or drops a registry row;
  `--dry-run` prints the plan, and a target id another space already holds is refused with that
  space named. `navig space register` was the one register site that skipped the
  one-id-one-space check (and filed every folder as `external`); it now refuses the duplicate
  and files a `~/.navig/spaces` resident as `root`.
