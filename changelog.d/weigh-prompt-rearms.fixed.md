- **The morning weigh-in looked like it was being asked several times a day.** It was not:
  `job_31 habit:weigh` fired once a day at 08:10 for eighteen consecutive days, each run
  logging a single "✓ Weigh-in sent" — the daemon restarting never re-sent it. What repeated
  was the Telegram *client's* `force_reply` box. It stays pointed at the prompt's message id
  and re-arms itself whenever the app restarts, rendering its own cached copy of the original
  question. Rewriting the message in place — all the previous fix did — changes the text but
  not the target: on 2026-09-27 not one `editMessageText` was rejected, and the operator still
  had the morning's question in their compose box at 14:52, forty minutes after answering it,
  quoting a weight the answer had already superseded. There is no API for clearing a
  `force_reply` (`editMessageReplyMarkup` takes an inline keyboard only), so an answered
  prompt is now **deleted**, which removes the reply target and with it the box. Prompts too
  old for a bot to delete (Telegram's 48-hour limit) still fall back to the rewrite. The
  7-day average that used to live in the rewritten prompt moves into the ✅ receipt — still
  exactly one message per answer, and nothing is lost.
