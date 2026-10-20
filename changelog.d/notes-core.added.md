- **`navig notes` — your web sticky notes on this computer** (new plugin `navig-notes`,
  `pip install "navig[notes]"`). The NAVIG Dock syncs the notes you keep on a page, a site or
  everywhere to `<data_dir>/notes.db` over `/api/deck/notes/*` (this computer only): `list`,
  `search`, `show`, `add` (it appears in the browser on the next sync), `rm`, `export`,
  `status`. Two-way, by revision — the higher one wins, deletes travel as tombstones, an older
  copy never overwrites a newer one.
- **Recall reads web notes** (`navig recall ingest --source notes`): your words as yours,
  lines quoted from the page as page captures, checkboxes as your open / done tasks; a note
  deleted in the browser is forgotten at the next ingest. Only notes the Dock was allowed to
  sync are read, and only "the agent may use them" notes may reach a model.
