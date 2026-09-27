- **A muted chat still showed up in the deletion digest.** `deletions mute <chat>` promises to stop
  announcing one chat's deletions while keeping the record. The instant path honoured it; the digest
  counted every deleted row since its watermark, so a muted chat still landed in the card's count,
  under **Show**, and could arm a resume entirely on its own. Every digest read — the count, resume,
  and Show — now goes through one scope (`deletions._digest_scope`), so the number on the card and
  the rows under Show cannot disagree.
- **The business digest counted deletions the operator made.** The deck's delete route marks catalog
  rows deleted too, and since `deleted_at` exists that stamp is set for ANY caller — so a message
  deleted in a group through the deck would have been reported as a business deletion (the live
  catalog already holds two such rows). The digest, `navig telegram business deleted`, and the
  Business tab's panel now read business rows only; the unfiltered `/telegram/deleted` route and a
  room's own Deleted tab keep the whole record. Muted chats stay visible in the browse surfaces:
  mute stops the announcing, not the record.
- `count_deleted_since` and `list_deleted` share one filter builder (`_deleted_filters`), so a count
  and its list can no longer drift apart.
- **The digest card printed its time in UTC.** It showed the raw window start ("19:31 UTC"), two
  hours off an operator at UTC+2 — on the one line whose job is to say *when*. It now uses local
  time like every other deletion surface (`format_when`), falls back to a labelled UTC time only if
  the stamp cannot be read, and the "since …" wording is localized (en · ru · fr) with its
  `{when}` placeholder pinned by the parity guard.
