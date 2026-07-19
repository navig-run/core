# 📓 NAVIG Telegram — Command Cheatsheet

Everything the Telegram Manager exposes, in one page. Two transports, one manager:
the **MTProto user client** (your own account, full power) + the **bot** (business
catcher, emoji-AI, TikTok). Destructive ops are **dry-run by default** — add
`--confirm`. All secrets live in the vault; counterparties can never reach the system.

---

## 🔐 Setup & login (one time)

```bash
navig telegram setup                 # paste api_id + api_hash (my.telegram.org) → vault
navig telegram login +33XXXXXXXXX    # sends a login code to your Telegram app
navig telegram confirm <code>        # 2FA auto-completes if you stored the password
navig telegram status                # "logged in as …"
navig telegram logout
```

## 🗂️ Inventory & search

```bash
navig telegram dialogs [--kind channel|supergroup|group|user]
navig telegram topics <forum-chat>
navig telegram history sync --chat <id|@user>   # backfill one chat → searchable catalog
navig telegram history sync --all               # backfill everything
navig telegram search "<query>" [--chat <id>] [--live]
navig telegram links <chat>                     # tiktok / youtube / url index
```

## 🧹 Organize (⚠️ dry-run by default — add `--confirm`)

```bash
navig telegram forward <from> <ids> <to> [--copy]
navig telegram move    <from> <ids> <to> --confirm     # copy + delete
navig telegram rename  <chat> "<new title>" --confirm
navig telegram delete  <chat> <ids> --confirm
navig telegram dedupe  <chat> [--confirm]              # safe set only (exact/inbox)
```

## 🛡️ Business catcher + AI rights

```bash
navig telegram business status                  # state + rights matrix + emoji legend
navig telegram business enable | disable        # refuses to arm if owner-gate isn't set
navig telegram business rights                  # list all per-tool policies
navig telegram business rights <tool> <who>     # who ∈ owner | both | off
navig telegram business alerts on | off         # deleted message → DM you
navig telegram business emoji                   # list emoji → tool map
navig telegram business emoji <emoji> <tool>    # remap a reaction (tool|off)
navig telegram business ping <owner|both|off>   # who gets a /ping reply in business chats
```

**`/ping` in a business chat** → a live status report (🏓 pong + message/room/media
counts + round-trip). Owner-only by default; the one safe canned reply (no system
access, never a command). Set `both` to let a counterparty ping, `off` to disable.

`tool` ∈ `translate · summarize · context · explain · ocr · transcribe · download`.
`who`: **owner** (only you) · **both** (you + counterparty, still sandboxed) · **off**.

## 🎵 TikTok (yt-dlp)

```bash
navig tiktok download <url> [-o DIR] [--watermark]   # organized download
navig tiktok profile  <@user> [--max N] [-o DIR]     # whole profile
navig tiktok info     <url>                          # creator · country · description · stats
navig tiktok comments <url> [--top N]                # top comments by likes
navig tiktok analyse  <url> [-c N]                   # AI markdown briefing (desc + best comments)
```
> `navig tt …` is a shorthand alias. Country is best-effort — TikTok rarely exposes it.

## 🐙 GitHub (mirroring — search · backup · clone)

```bash
navig github search "<query>" -o ./mirrors --limit 50 -y
navig github token set <github-token>
```

---

## 😀 Bot reactions (react with an emoji on a message)

| Emoji | Action | Gated by |
|---|---|---|
| 🌍 / 🌎 / 🌐 | Translate | `translate` policy |
| 📋 / 📝 | Summarize | `summarize` policy |
| 🤔 | Context | `context` policy |
| 💡 | Explain | `explain` policy |
| 🎵 / 🎬 / 📹 | **TikTok analyse** (briefing) | `download` policy |
| 👍 👎 🔥 💯 | feedback / refine / bookmark / pin | — |

Remap any emoji in the **deck → ✈️ Telegram → Business** tab, or via
`navig telegram business emoji <emoji> <tool>`. Owner-only by default; set a tool to
**both** to let a counterparty trigger it, or **off** to disable.

## 🔘 Bot buttons

When a TikTok link appears in a (business) chat, the bot replies with a card
(creator · country · description · stats) and two buttons:

- **⬇️ Download** — fetches the video and uploads it back to the chat.
- **🔍 Analyse** — posts a markdown briefing (description + best comments combined).

## 📝 Rich messages

AI outputs (TikTok briefings, 🌍/📋/🤔/💡 replies) are sent as **rich messages**
(`sendRichMessage`) — Telegram renders the markdown natively: headings, lists,
tables, block quotes, collapsible `<details>`, footnotes, formulas. It's a brand-new
API, so navig **falls back to HTML automatically** where it isn't enabled yet (learned
per-bot, no per-message latency). Disable with `navig config set telegram.rich_messages false`.

## 🗑️ Deletion alert

With `business alerts on`, deleting a message in a business conversation DMs **you**
the cached content — only you, never the deck or other channels.

---

## 🖥️ In the deck (✈️ Telegram app)

- **Manage** — file-manager: filter chats (All · Channels · Groups · DMs · Forums),
  multi-select messages → Move / Forward / Delete / Dedupe.
- **Contacts** — people / DMs, separate from messages.
- **History** — backfill with a live progress bar + search-all.
- **Business** — rights matrix (owner|both|off), master enable, deletion alert, and the
  **emoji editor** (remap / add / turn off — including the TikTok 🎵 🎬 📹).
- **Login** — phone → code → 2FA.

## 🔒 Security one-liner

No Telegram user but you can reach NAVIG's system/CLI/deck. Business messages are DATA,
never commands. Emoji-AI runs in a no-tools sandbox. MTProto secrets live only in the
vault. Features refuse to arm unless `require_auth` is on and `allowed_users` is set.
