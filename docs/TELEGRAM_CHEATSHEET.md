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

## 👥 Members → contacts (⚠️ dry-run by default — add `--confirm`)

```bash
navig telegram members <chat> [--bots] [--limit N] [--json]   # who is in a group
navig telegram contacts list [--tag MTP] [--notes] [--json]   # your saved contacts
navig telegram contacts tag <chat>                            # PLAN only — writes nothing
navig telegram contacts tag <chat> --confirm --limit 5        # pilot batch
navig telegram contacts tag-list <file.jsonl> [--tag X]       # mark an explicit handle list
navig telegram contacts prune --status gone [--tag X]         # drop unreachable contacts
navig telegram contacts runs                                  # past runs
navig telegram contacts undo [<run-id>] --confirm             # reverse a run
```

**`contacts tag-list`** marks a cohort that shares no chat — an export, a match
list — from a JSONL file, one object per line:

```jsonl
{"handle": "@someone", "note": "DAVI · Lyon · 22y · 2026-08-23"}
```

Only `handle` is required; a per-line `note` is how per-person facts travel.
Handles that no longer resolve, and handles that turn out to be channels or
bots, are listed under `unresolved` rather than dropped.

**`contacts prune`** removes contacts by last-seen bucket
(`gone` · `last-month` · `last-week`).

> ⚠️ **`gone` is a heuristic, not a blocked-list.** *"Last seen a long time ago"*
> is what Telegram shows when someone **blocks you** — and equally what it shows
> when someone sets last-seen privacy to Nobody and has been away a while. The
> API cannot distinguish them. Note that `recently` is the *normal* privacy
> bucket and is never pruned. Always scope an import sweep with `--tag` so it
> cannot take contacts you added by hand, and read the dry run first.

A prune is journalled like any other run, so `contacts undo <run-id> --confirm`
adds the people back (by username — a pruned contact without one cannot be
restored automatically, and says so).

`contacts tag` files a group's members into your contact list carrying a marker,
so a whole cohort stays identifiable afterwards. Nobody is notified, and
`add_phone_privacy_exception` is hard-wired off, so your number is never shared.

**Where the marker goes — `--mark`:**

| `--mark` | Effect |
|---|---|
| `both` *(default)* | Surname **and** note — findable *and* documented. |
| `surname` | Appends to the saved surname: `Popov` → `Popov MTP`. **The only form contact search can find.** |
| `note` | Telegram's private per-contact note: `MTP · vibe_sud_france · 2026-08-21`. Real names stay untouched, but **not searchable**. |

The note is what iOS calls *"notes only visible to you"* — capped at 128 chars,
templated with `--note-template` (`{tag}` `{group}` `{date}`), and it rides along
in the same `addContact` request, so it costs no extra round trip.

> ⚠️ **Telegram's contact search indexes names, never notes.** In tdesktop,
> `UserData::note()` is read in four places and all four are display/edit UI —
> it never reaches `_nameWords`, the index search queries. So a marker written
> *only* to the note is invisible when you type it into the contacts search box.
> That is why `both` is the default; use `note` only if you never intend to
> search for the cohort in the app.

**Read the plan before you confirm.** It buckets every member: `add` ·
`update` · `already marked` · `already a contact, unmarked` · `bot` ·
`deleted` · `you`. People already in your contacts are **left alone** unless you
pass `--update-existing`, and a name you curated always wins over whatever
nickname they use in the group today. Re-running is idempotent — an
already-marked person is skipped, never marked twice.

**Run it in pieces.** `--limit N` writes N people and stops; people written by
one batch are contacts by the next, so they drop out of the plan on their own.
Repeat the same command until the plan is empty.

**On flood limits.** Reading Telegram's own clients, `contacts.addContact` is
*not* the flood-policed operation: `PeerFloodType` is `{Send, InviteGroup,
InviteChannel}`, and tdesktop's `addContact` call has no failure handler at all.
The policed operations are messaging strangers and mass group-inviting. Generic
`FLOOD_WAIT` can still hit any method, so writes are spaced `--delay 5` apart, a
`FLOOD_WAIT` under 5 min is slept off, and `PEER_FLOOD` still aborts the run
rather than retrying — belt and braces.

> ⚠️ **Check `Privacy → Invites` first.** If it is set to *My Contacts*, then
> every person you file this way can add you to arbitrary groups. That is the
> real cost of a large run — not a ban.

Every write is journalled to `~/.navig/telegram/contact_runs.jsonl` *before the
next one is attempted*, so a crash or an abort still leaves an exact undo
record. `contacts undo` deletes what the run created and restores the previous
name on anything it merely renamed.

**A write can be accepted and still not stick.** `contacts.addContact` can
return success without the person becoming a contact — seen once in ~70 writes
on a real account, and a plain retry fixed it. So every run re-reads the contact
list once at the end and reports any write that did not land:

```
  1 write(s) accepted but the contact did not stick:
    · @someone
```

Re-run the same command to pick them up — it is idempotent, so anyone already
filed is skipped. Without this, a run counts *requests sent* rather than
*people filed*, and quietly over-reports.

> A **broadcast channel** has no readable member list — only a group/supergroup
> does. `navig telegram members` on one fails with `ChatAdminRequiredError`
> rather than pretending the group is empty.

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

When a TikTok link appears in a chat, the bot replies with a card
(creator · country · description · stats) and four buttons:

- **⬇️ Download** — fetches the video and uploads it back to the chat.
- **🔍 Analyse** — posts a markdown briefing (description + best comments combined).
- **📝 Transcript** — transcribes what is actually *said* in the clip (audio-only
  download → speech-to-text). Needs an STT provider; `navig voice --help`.
- **🎧 Audio** — extracts just the soundtrack and sends it as a playable track.

A file too large to upload (>50 MB), or one Telegram rejects, is **kept on disk** and
the reply tells you the path.

**Photo posts (slideshows) work too.** A TikTok `/photo/` post is a carousel of
images with a soundtrack, not a video — the card says `🖼 photo post`, and the
buttons do the right thing for one:

| Button | On a slideshow |
|--------|----------------|
| ⬇️ Download | sends the **slides**. A carousel over 10 images says so rather than quietly showing part of it. |
| 🎧 Audio | the soundtrack, exactly as for a video. |
| 📝 Transcript | reads the audio **and** OCRs the slides — a slideshow has no frames to sample. Past 10 slides it says which part it read. |
| 🔍 Analyse | description + comments — and when the caption is too thin to brief from, it reads the **slides**, not the backing track (see below). |

You don't have to do anything: a shared `vm.tiktok.com` link is resolved and
classified for you.

The briefing header carries what is actually known — author (+handle), date,
duration, sound, and 👁/❤️/💬/🔁 stats — and silently omits what isn't (TikTok
rarely exposes country, so nothing is printed rather than a permanent "n/a").

**🔍 Analyse listens when the caption says nothing.** TikTok captions are very
often a bare hashtag, and a briefing built from that plus comments describes the
*reaction* to a video without ever describing the video. When the description has
fewer than four non-hashtag words, Analyse reads the post first and briefs from
what it actually contains. A post with a real caption skips that step and costs
nothing extra.

*What* it reads depends on the post, and the header says which — a slideshow has
no speech of its own, so reading its audio would summarise the licensed song
playing behind it:

| Post | Analyse reads | Header |
|------|---------------|--------|
| video | the spoken words | `📝 from speech` |
| slideshow | the text printed on the slides | `📝 from slide text` |
| slideshow with no printed text | the audio track (a voiceover — or a song) | `📝 from the audio track` |

If OCR can't be trusted — Tesseract missing, or installed without the pack for
the language on the slides — a note under the briefing says so, because a
briefing built on misread glyphs otherwise reads exactly like a briefing built on
the post. See **OCR languages** below for installing a pack.

Slides are read 10 at a time (TikTok allows 35 per post). When a post is longer,
📝 and 🔍 both say which part they read — `🖼 Read the first 10 of 30 slides.` —
rather than presenting a third of a post as the whole of it.

**Speech gets the same treatment.** "No speech in this clip" is a real answer for
a silent one and a false one on an install with no transcription backend, where
nothing ever listened — so when speech comes back empty *and* there is no backend,
📝 and 🔍 say so and name the fix:

```
pip install faster-whisper        # local, no API key
# …or set OPENAI_API_KEY / DEEPGRAM_API_KEY to use an API backend instead
```

**The caption arrives whole.** The card gives the description whatever the header
and stats leave of Telegram's 4096-character message — so a long caption lands
complete instead of being clipped. Past that ceiling a **📄 Full text** button
appears (and *only* then) and sends the rest.

**The 🎧 track is labelled.** Telegram shows the sound's name and performer, its
real length, and a caption naming the creator with a link back to the post —
rather than `7652338755679964436.m4a` reading `00:00`.

**The cover image comes first.** A separate message, necessarily: Telegram caps a
photo *caption* at 1024 characters against a message's 4096, so a picture and a
full caption cannot share one bubble. Turn it off with
`navig config set telegram.tiktok_cards.photo false`.

### Where it fires — including **business chats**

| Chat | When | Who can trigger it |
|------|------|--------------------|
| **Your own chat with the bot** | the message is *essentially just the link* — a question that merely mentions one goes to the agent instead, so you still get an answer rather than a card | you |
| **Business chats** (your personal account, via Telegram Business) | any message containing a link | the `download` policy decides |

**Yes — the card and all its buttons already work in your business conversations.**
Nothing to install; two switches control it:

```bash
navig telegram business status                   # what is on, and every tool's rights
navig telegram business enable                   # master switch for the business layer
navig telegram business rights download owner    # only YOUR messages get the card  [default]
navig telegram business rights download both     # the other person's links too
navig telegram business rights download off      # no cards in business chats
```

`owner` is the default, so out of the box a link **you** send gets the card and a
counterparty's does not. `both` is what you want if you're using it to read what
*they* send you. The same `download` policy governs the 🎵/🎬/📹 reaction.

Whichever way you start an action — a button, a 🎵 reaction, or the reply menu —
asking for the same thing twice while it is still running answers `⏳ Already
working on that one` instead of doing it again. Worth knowing because 🔍 on a
slideshow now downloads slides, OCRs them and pays for an AI briefing.

The business layer refuses to arm at all until the owner gate is set
(`telegram.require_auth` on **and** `telegram.allowed_users` naming you) — without
that, anyone messaging the bot could drive it.

Turn the 1:1 card off with `navig config set telegram.tiktok_cards.enabled false`
(bare music links behave the same way — `telegram.music_links.enabled`).

**Language.** One global preference drives transcription, **on-screen text (OCR)**
*and* briefings; each feature may override it. Default is **auto** — follow the
content, so a Russian clip gets a Russian transcript and a Russian briefing.

From the bot:

```
/lang              → shows the current language
/lang Russian      → sets it
/lang auto         → follow the content
```

Or from the CLI:

```
navig config set user.language Russian                    # everything: STT, briefings, summaries
navig config set user.language auto                       # back to following the content

navig config set telegram.tiktok_cards.language English   # override just the TikTok card
```

Resolution order is **feature override → `user.language` → auto**, and "auto"
reaches the provider as *detect*, never as a language code. Nothing is ever
silently pinned: a transcriber that has to guess is allowed to guess.

You write a **name** ("Russian"); each engine is given the form it accepts — an
ISO code for speech-to-text, a traineddata pack for OCR. A name nothing can map
falls back to detect rather than to a value the engine would refuse.

**OCR needs the language pack installed.** Tesseract ships English only and does
not decline when pointed at another script — it returns confident-looking
nonsense. So `navig doctor` **warns** when `user.language` names a script whose
pack is missing (rather than showing a green OCR row over garbage), and the
📝 Transcript reply says so too. Install one with:

```
scoop install tesseract-languages      # Windows
brew install tesseract-lang            # macOS
sudo apt install tesseract-ocr-rus     # Linux — one package per language
```

Check what you have: `navig doctor` → **OCR (on-screen text)** names the languages
it reads in.

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
