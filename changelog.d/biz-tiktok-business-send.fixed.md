- **TikTok cards in Telegram Business chats finally post into the conversation.**
  The enrichment path called `channel.send_*` with no `business_connection_id`,
  so Telegram rejected every card, cover and error message in a business chat
  ("Forbidden: bot can't initiate conversation with a user" — three occurrences
  in live gateway logs, 2026-10-18/19) and the other person never saw anything.
  The business layer now hands `offer_card` a `BusinessSender` proxy that stamps
  the connection id on every send, `tk:` button taps read it from the card
  message, and `send_message`/`_post_media` accept and emit the field (JSON body
  for text, multipart form field for media). Sends outside a business chat are
  byte-identical — the field is only added when a connection id exists.
