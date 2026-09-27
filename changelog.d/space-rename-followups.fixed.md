- **A renamed root space kept its old id as a live alias — `navig space rename --move-folder`
  finishes the job.** `resolve_space()` returns `~/.navig/spaces/<name>` the moment that
  directory exists, *before* it consults the registry or the manifest, so a space under the
  spaces root that was re-identified in place still answered to its old name and squatted that
  id for whatever space was given it next. `rename` now says so plainly when it leaves a folder
  behind (naming the collision and the command that fixes it), and `--move-folder` renames the
  folder too: the junctions it created (`plans`, `.inbox`, the five `.claude/*` links) store
  ABSOLUTE targets, so they are removed before the move and rebuilt after, the process cwd is
  carried across (`main.py` chdir's into the active space, so renaming the space you stand in
  would otherwise fail with the directory in use), and the registry row is carried by
  `registry.repath` — which preserves `enabled`, `trusted` and `last_active`, the columns that
  are decisions rather than derivations. A destination that exists, or a space outside the
  spaces root, is refused with the reason.
- **`rename` called the active space inactive whenever the ids had drifted — the very case it
  exists to repair.** It compared the active-space pointer against the manifest-derived id, but
  a manifest edited by hand leaves the registry row and the pointer on the FORMER id; the
  registry was then re-keyed correctly while `active_space.txt` was left naming an id nothing
  resolves. Identity is now decided by PATH (manifest id, then the registry row for this
  folder, then what the pointer actually resolves to — which also covers a pointer naming a
  conventional `~/.navig/spaces/<folder>`).
- **A pending global config write could be replayed onto a different install, and a reset
  parent could be undone by an older child.** The unsaved-write ledger added in #1543 is
  replayed onto a freshly read copy, and this singleton resolves its config path live — so a
  `NAVIG_CONFIG_DIR` that moved for good laid one directory's unsaved values over another's
  file, and `save()` would have persisted them there; the ledger now records the path it
  belongs to and discards itself when that path changes. And because a dict does not reorder a
  key on reassignment, `set("a", {})` → `set("a.b", 1)` → `set("a", {})` replayed as
  `a, a.b` and RESURRECTED a value the final parent reset had removed: re-setting a key now
  moves it to the end, and a write to a parent drops every pending write beneath it.
