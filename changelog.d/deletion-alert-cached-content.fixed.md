- **The business-chat deletion alert said "(content was not cached)" for content NAVIG had
  seen.** Two causes, both measured on the live catalog: the bot-echo loop guard dropped EVERY
  `is_bot` sender, so a conversation with any other bot was cataloged one-sided (the owner's
  lines only) and every deleted bot message came back "not cached"; and a media message
  (photo/sticker/voice) was stored as `text=""` and nothing else — 20% of business rows — while
  the alert read only `text`. Now the bot's OWN echo is what's skipped (by bot id, or
  `sender_business_bot`), another bot's messages are cataloged as DATA only (no auto-reply,
  no commands — two bots answering each other never stops), and business media keeps its
  `file_id`. The alert is ONE DM per deletion event (Telegram sends one payload per chat, with
  every id), names who wrote each line (`you:` / the counterparty), shows the media kind +
  caption, **re-sends the cached photo/voice/… to the owner by `file_id`** (only after the
  summary was delivered, so muted types and quiet hours still hold), labels the chat by the
  person's name rather than their `@username`, and says *why* a line has no text — "not seen
  by NAVIG (sent before it watched this chat, or while it was offline)" is not the same thing
  as a caption-less photo.
