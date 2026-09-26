- **Deletion alerts are a digest by default, with a Show button — and three switches instead of
  one boolean.** A live account was flooded: every deleted message was its own DM *plus* an
  immediate re-send of its photo or voice, and the only control was on/off. Now one card per
  window says *"🗑 4 deleted in 2 chats"* with **Show** (the detail and the files arrive when you
  tap, and the button keeps working after the process that sent the card is gone) and **Quiet**
  (turns alerts off from the card). The three decisions that were tangled into that boolean are
  separate, because they are genuinely different questions —
  **`navig telegram business deletions status|mode|record|window|target|mute|flush`**, the deck's
  Business tab, or `telegram.business.deletions.*`:
  · **record** — whether a deletion is written down *at all*. The catalog row is the only
  surviving trace of a deleted message, so going quiet must not cost the record, and switching
  the watch off must leave no trace.
  · **mode** — `digest` (default) · `instant` (the old behaviour) · `off` (recorded silently).
  · **target** — the owner's DM, or a separate **log channel**. A second *bot* cannot do this
  (a bot only receives `deleted_business_messages` for the account it is connected to, so a "log
  bot" would never see them); a separate chat gives the same separation for free.
  Plus per-chat `mute`, so one noisy conversation can be silenced without going blind everywhere.
  The digest holds **no content in memory** — it keeps a watermark and re-reads the catalog at
  flush time, so a restart mid-window loses nothing, and the watermark only advances on a
  delivered card (a failed send that consumed it would silently drop the window it was
  reporting). Card, buttons and toasts are localized (en · ru · fr), with a parity test.
- **`tg_messages.deleted_at`** — when a message was deleted, as opposed to when it was first
  seen. The digest needs it: a watermark over `created_at` selects by the message's *arrival*, so
  deleting a month-old message would fall outside every window and never be reported. Additive;
  rows deleted before the column existed keep NULL, which every reader treats as "unknown when".
