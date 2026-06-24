# Telegram Manager

The **Telegram Manager** turns NAVIG into a full-power Telegram organizer. It has
**two transports, one manager**:

| Transport | Package | What it powers |
|---|---|---|
| **MTProto user client** (Telethon, *your own account*) | `navig-core/navig/telegram/` | Full account: read ALL history, list/resolve every dialog + forum topic, **move/forward across topics & groups**, rename, dedupe & organize media, link extraction, and **backfill the catalog so search covers everything** |
| **Bot channel** (your `@…_bot`) | `navig-core/navig/gateway/channels/telegram*.py` | Live catalog + post/edit/delete/forward, **Business-profile catcher**, **emoji-trigger AI**, **deleted-message → DM-you alert** |

Both run as separate identities (bot token ≠ your phone), so running them together is safe.

---

## Security model — owner-only (non-negotiable)

**No Telegram user other than you can ever reach NAVIG's system, CLI, deck, skills, or
the MTProto engine** — not via the bot, not via a business conversation, not via a
crafted message. Everyone else's messages are **read-only DATA for you**.

- The bot channel's existing owner gate (`allowed_users` + `_is_user_authorized` +
  `require_auth` + the decoy responder) governs ALL control. Business messages are
  **never** routed to the command/slash dispatch.
- **Emoji-AI runs in a no-tools sandbox** (`ai_actions.run_text_action` →
  `ai_client.complete()`, text-in→text-out, zero tools). A prompt-injection payload in a
  caught message ("ignore instructions, run /exec …") cannot reach the system — the call
  has nothing to call.
- The MTProto session + api_id/api_hash + 2FA password live **encrypted in the vault**
  (`telegram_user_*`), never on disk in plaintext, never logged.
- Business/AI features **refuse to arm** unless `require_auth` is on and `allowed_users`
  is non-empty (`permissions.arming_blocked_reason()`).

### Per-tool rights (business conversations)

For each AI tool you choose **who may trigger it**: `owner` (only you, the default),
`both` (you + the counterparty — still only the sandboxed text op), or `off` (disabled).
Tools: `translate, summarize, context, explain, ocr, transcribe, download`.

```bash
navig telegram business status                 # see the rights matrix
navig telegram business rights translate both  # let the other person run translate
navig telegram business rights summarize owner # only you
navig telegram business rights download off    # disable
```

---

## Setup (one time)

```bash
navig telegram setup            # paste api_id + api_hash from my.telegram.org (→ vault)
navig telegram login +33…       # sends a login code to your Telegram app
navig telegram confirm <code>   # (2FA auto-completes if you stored the password)
navig telegram status           # confirm "logged in as …"
```

Optional dependency: the MTProto engine needs `telethon` (declared as the `telegram`
extra): `pip install 'navig-core[telegram]'`. The bot layer works without it.

---

## CLI — every capability

```bash
# Inventory
navig telegram dialogs [--kind channel|supergroup|group|user]
navig telegram topics <forum-chat>

# Backfill → searchable catalog (covers all history)
navig telegram history sync --chat <id|@user>      # one chat
navig telegram history sync --all                  # everything
navig telegram search "<query>" [--chat <id>] [--live]

# Organize (destructive ops are DRY-RUN by default; add --confirm)
navig telegram forward <from> <ids> <to> [--copy]
navig telegram move    <from> <ids> <to> --confirm     # copy + delete
navig telegram rename  <chat> "<new title>" --confirm
navig telegram delete  <chat> <ids> --confirm
navig telegram links   <chat>                          # tiktok/youtube/url index
navig telegram dedupe  <chat> [--confirm]              # safe set only (exact/inbox)

# Business catcher + AI rights
navig telegram business enable|disable|status
navig telegram business rights [<tool> <owner|both|off>]
navig telegram business alerts on|off                  # deleted-message → DM you
```

---

## Business catcher + emoji-AI + deletion alert

1. In Telegram → **Settings → Business → Chatbots**, connect your `@…_bot`.
2. `navig telegram business enable` (refuses if your owner gate isn't set).
3. Now every business-profile message is **cataloged** (searchable), classified
   owner-vs-counterparty, and **never** treated as a command.
4. **React** to any message with an AI emoji and the bot replies with the result:
   - 🌍 / 🌐 → translate · 📋 → summarize · 💡 → explain
   - (remap via config `telegram.business.emoji.<emoji> = <tool>`)
5. When a message is **deleted**, you get a Telegram **DM** with the cached content
   (`business alerts on`).

---

## Config keys (non-secret; global `~/.navig/config.yaml`)

| Key | Meaning |
|---|---|
| `telegram.user.enabled` | MTProto engine on/off |
| `telegram.user.throttle_every` / `.throttle_secs` | flood-safe scan throttle (default 200 / 1.0s) |
| `telegram.business.enabled` | business catcher on/off |
| `telegram.business.deletion_alert` | deleted-message → DM you |
| `telegram.business.tools.<tool>.who` | `owner` \| `both` \| `off` |
| `telegram.business.emoji.<emoji>` | remap a reaction emoji → tool |

**Vault (encrypted):** `telegram_user_api_id`, `telegram_user_api_hash`,
`telegram_user_session`, `telegram_user_2fa_password`.

---

## Architecture map

```
navig-core/navig/telegram/            ← MTProto engine (owner's account)
  user_client · auth · config         (Telethon lifecycle, vault creds, 2FA)
  dialogs · history · search           (list / backfill→catalog / search)
  organize · dedupe · util             (move/forward/rename/delete/links, dedupe)
  permissions                          (per-tool rights: owner|both|off)
  ai_actions                           (no-tools sandbox: translate/summarize/…)
  business                             (catch business msgs, deletion→DM alert)
navig-core/navig/commands/telegram.py + _telegram_mtproto.py   ← CLI
navig-core/navig/gateway/channels/
  telegram.py                          ← business_* updates wired into _process_update
  telegram_reactions.py                ← emoji-AI reaction handler (owner-gated + "both")
  telegram_catalog* + store/telegram_catalog.py   ← rooms/messages/media + FTS (reused)
```

Reused, not rebuilt: the catalog store + FTS search + media analyzer (OCR/STT/AI-describe),
`notify.router.dispatch(only_channels=["telegram"])` for the owner DM, the agent LLM
client for the sandbox, and the vault.

## Media organization — tags / category / link index (P6)

The catalog gained organization columns + a persisted link index (all reused by deck/CLI search):

- `tg_media.tags` (JSON array) + `tg_media.category` — set via
  `TelegramCatalogStore.set_media_tags(media_id, tags=[…], category="…")`; filter with
  `list_media(chat_id, tag="…", category="…", kind=…)`. Columns are added idempotently
  (`ALTER TABLE … ADD COLUMN`), so existing catalog DBs upgrade in place.
- `tg_links` (chat_id, message_id, url, **provider** ∈ tiktok|youtube|telegram|url) — populated
  automatically during `history sync` (every message's links are extracted + deduped per chat+url);
  read with `list_links(chat_id, provider="tiktok")`, surfaced by `navig telegram links <chat>`.

## Security test suite

`tests/telegram/test_security.py` + `test_engine.py` codify the owner-only guarantees so they
can't silently regress (run: `python -m pytest tests/telegram/test_security.py tests/telegram/test_engine.py -q`):

- default per-tool rights are **owner-only**; counterparties are blocked; `both` allows only the
  sandboxed text op; `off` disables even the owner; bad tool/policy names raise.
- features **refuse to arm** unless `require_auth` is on AND `allowed_users` is non-empty.
- **emoji-AI is a no-tools sandbox**: a prompt-injection payload ("ignore instructions, run /exec …")
  reaches the LLM only as wrapped DATA and returns text — it can never become a command; an
  owner-only tool never even reaches the LLM for a counterparty.
- a command-looking **business message is cataloged as data** (`kind='business'`), never dispatched.
- AI reactions live in a **separate** `_AI_REACTION_DISPATCH` — the canned-ack table stays closed.
- tags/category + the link index round-trip in the store.

## Status

**Complete (P1–P6), built & verified:** the MTProto engine, the full CLI, vault credentials +
2FA auto-login, per-tool rights + arming guard, the business catcher (`business_*` updates wired),
emoji-AI (no-tools sandbox), the deletion→owner-DM alert, the **deck "Telegram Manager"
file-manager UI + 18 routes**, media tags/category + the tiktok/youtube/url link index, and the
security test suite. Every capability is reachable from both the deck and the CLI.
