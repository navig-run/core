- **You can now see what was deleted — outside the one DM that fires.** The catalog has always
  kept soft-deleted rows (they are the only surviving record that a message existed) and no read
  path returned them, so "what was deleted" was answerable solely by a Telegram alert you could
  miss, mute, or scroll past. Now: **`navig telegram business deleted`** (a table — `--chat`,
  `-n`, `--json`), a **Recently deleted** panel in the deck's Business tab, `GET
  /api/deck/telegram/deleted` across rooms, and `?deleted=only|include` on a room's messages.
  One rendering (`business.media_label` / `content_label`) backs all of them, so the DM and the
  table cannot describe the same message differently.
