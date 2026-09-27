- **The deletion digest names who, not just how many.** Live feedback: the card read "удалено: 2 в
  2 чатах" and the operator had to tap Show to learn even which conversations. It now lists each
  chat — busiest first, five before it folds into "+N more" — and whose messages went:
  `• Yck 🧢 — 1 (от тебя)` · `• Elvira — 3 (от тебя: 1)` · `• Elvira — 2`. "Whose" is the sender:
  Telegram's deletion update does not say who deleted, but a message is almost always deleted by
  the person who sent it. **Names and counts only, never message text** — the card is a
  notification preview that shows on a lock screen, and the content stays behind Show. The names
  come from the same scope as the card's count (`deleted_by_chat_since` shares the filter
  builder), so muted chats and deck deletions stay out and the lines always add up to the number.
  Localized (en · ru · fr) with placeholders pinned by the parity guard.
- **`business.primary_owner()`** — the install's owner when there is no connection id in hand.
  `resolve_owner(None)` skipped the connection registry and checked only `allowed_users`, so an
  owner recorded only in the registry was invisible to code with no update to read an id from.
