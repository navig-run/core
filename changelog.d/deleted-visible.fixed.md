- **A deletion alert that was correct read as broken: every line now says WHEN the message was
  sent.** Live report, 2026-09-22: three alerts said *"(no text — media sent before NAVIG kept
  copies)"*. All three were true — the rows were sent 09-21, before media was kept — but the
  line never carried the one fact that explains it, so a deletion of yesterday's photo and
  today's photo going missing rendered identically. Lines are now **who · when · what**
  (`• you · 21 Sep 17:18 · 📷 photo — caption`), and "not seen" distinguishes *"NAVIG has
  watched this chat since 14 Sep, so this one is older"* from *"never cataloged this chat"*.
- **A counterparty was introduced by their raw handle.** The same DM called a chat "Yck 🧢" and
  its only other participant "a646f6e7474727974686174", because `sender_name` was stored
  username-first while the chat label was name-first. Senders are stored name-first
  (`_person_name`), and in a private chat the chat's own label wins — which also rescues every
  row already written the old way. A group still shows per-sender names.
- **A message with no text and no file is named, not called empty.** A poll, location, contact,
  shared story, dice, gift or video-chat event carries no `file_id`, so it was stored blank and
  read back as "(no text)" — a capture failure for a message NAVIG saw perfectly. The content
  kind is now kept (`📊 poll`, `📍 location`) and shown by every surface.
- **An edit could erase the text the deletion alert is the last reader of.** The business path
  wrote `text=""`, and an empty string OVERWRITES through the upsert's `COALESCE`, so a
  caption-less edit or a re-delivered update blanked stored text (3 rows in the live catalog).
  It now writes NULL, which preserves it.
- **`edited_at` stored the literal string `"yes"`** (627 rows), making the column unsortable and
  unreadable by every other consumer — `edited_at` is a timestamp everywhere else in that table.
  It now stores Telegram's `edit_date`, and the alert marks an edited message.
- **A failed media re-send is reported, not swallowed.** The summary promises a photo or voice;
  when Telegram no longer serves that file id, the owner is told in the same DM thread instead of
  a log line nobody reads.
- **The deck rendered "Invalid Date" on every business message.** `new Date(m.date)` assumed ISO,
  but the business path stores a unix-timestamp string. One parser (`tgWhen`) now handles both,
  everywhere a message date is shown.
