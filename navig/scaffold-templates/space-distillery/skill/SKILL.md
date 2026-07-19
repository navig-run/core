---
name: inbox
description: >
  Distill dropped reference material — images/art, video, audio, articles, product
  or webapp screenshots, and third-party code — into reusable, indexed inspiration:
  paste-ready art-style prompts, extracted UX/product patterns, writing/voice notes,
  motion and sonic notes, liftable code techniques. Files each result as a markdown
  note under this project's distilled library with frontmatter that links back to the
  original asset, and updates the library index. Use when the user runs /inbox, says
  "process the inbox", "distill this", "extract the style/idea from this", "turn this
  into a doc", or drops files/URLs to be mined. NEVER deletes or overwrites a source;
  texts get an original backup first. Reads references/project-profile.md for where
  things live and how to map essence onto THIS project.
---

# /inbox — the distillery

Raw material in → concentrated, reusable essence out. You (Claude) are the extraction
engine; this skill is the fixed procedure that makes every extraction consistent, in any
project. **Everything project-specific lives in one file: `references/project-profile.md`.**
Read it first — it tells you where the inbox and library are, where big binaries live, and
how to map essence onto this project's design system.

## Six hard rules (non-negotiable)

1. **Never delete or move a source.** Not images, not video, not audio, not text. Ever.
   "Processing" = read (plus copy a *text* to `_originals/`), never move or remove. Sources stay
   exactly where they were dropped — the **ledger**, not a folder move, records what's done.
2. **Back up inbox texts before distilling.** Copy the original file verbatim into the profile's
   `originals` dir (keep the filename) *before* you write the distilled note. Anything already
   living on a media root — binaries **and** texts/PDFs under `<media>/docs/` — is already safe
   in place: leave it there, provenance link only.
3. **Every distilled note carries provenance** — `source` + `source_asset` frontmatter
   pointing at the exact original file (or URL). No orphan insights.
4. **Route by facet, not by hunch** — one note lands in exactly one facet folder
   (§Facets). Cross-link into the project's real docs/design system when relevant.
5. **Index everything** — append a row to the library `INDEX.md` **and** a line to the coverage
   ledger (§Ledger). A note missing either entry does not exist.
6. **Finish the batch.** A run ends only when every gathered item is ledgered as `distilled`,
   `skipped` (with a reason), or `blocked` (a required tool is genuinely missing — name it and
   give the exact command). If the tool exists, preprocessing runs in *this* pass. Never end a
   run by asking whether to continue — the procedure is the permission.

## Facets → where distilled notes land

Under the profile's **distilled library** (default `.navig/refs/notes/`):

| Facet | Folder | For… |
|-------|--------|------|
| **style**   | `style/`   | Visual art direction: palette, type, motifs, mood → a reusable image-gen prompt |
| **product** | `product/` | A webapp / feature / flow / UX pattern worth stealing |
| **writing** | `writing/` | Voice, tone, lore, manifesto, article theses, quotable lines |
| **motion**  | `motion/`  | Video: pacing, transitions, camera, "juice", grade-over-time |
| **sonic**   | `sonic/`   | Audio: stings, UI ticks, ambient, success chords, music refs |
| **system**  | `system/`  | Architecture / code technique worth lifting into the stack |

A single source can spawn *multiple* notes (a product video → one `motion` + one `product`).

## The ledger — coverage state (`<inbox>/ledger.jsonl`)

The inbox is **incremental**: every run diffs reality against the ledger and processes only the
difference. One JSON object per line; one line per asset **or per cluster**:

```json
{"path": "<asset or dir>", "status": "distilled|skipped|blocked|pending", "note": "<library note path>", "reason": "<why skipped / the unblock command>", "sampled": ["<file read>", "…"], "date": "YYYY-MM-DD"}
```

- `distilled` — note written **and** indexed (`note` required). A cluster line lists the files
  actually read under `sampled`; the whole cluster counts as covered — later runs only look at
  files added to that dir after the line's `date`.
- `skipped` — out of scope for the distillery (`reason` required). Never re-enters a batch.
- `blocked` — a required tool is missing (`reason` = the exact command to run). **Re-tested
  every run** — the moment the tool exists, process the item.
- `pending` — enumerated but deferred (only allowed for oversized batches, §3); first
  priority next run.

If the ledger doesn't exist yet, create it on first gather.

## Procedure

### 0 — Load the project profile
Read `references/project-profile.md`. If its `Status:` is `UNCONFIGURED`, **auto-detect** the
bindings (find the inbox drop zone, choose/confirm a distilled library location, locate the
media/large-asset roots, and find the project's design system — its tokens/style-guide or,
failing that, its component library / README), **confirm them with the user**, then rewrite
the profile with `Status: CONFIGURED`. Everything below uses the profile's paths and vocab.

### 1 — Gather
Enumerate **(a)** the inbox drop zone (ignore `README.md`, `_originals/`, and the ledger
itself) and **(b)** every media root named in the profile. Diff
against the ledger: drop anything `distilled` or `skipped`; re-test anything `blocked`; include
everything `pending` or new — plus any explicit path / URL / pasted text the user gave. That
difference **is the batch**. List it back with counts so the user sees exactly what this run
will cover.

### 2 — Classify
Decide each item's input type → this picks the rubric and the facet(s):

| If the item is… | Type | Likely facet(s) |
|---|---|---|
| `.png/.jpg/.webp/.avif` that is artwork/photo/render | **image-art** | style |
| a screenshot of an app / website / dashboard | **product-shot** | product (+ style) |
| `.md/.txt/.pdf` article, thread export, notes | **text** | writing / product / system |
| `.mp4/.mov/.webm` | **video** | motion (+ style / product) |
| `.mp3/.wav/.m4a` | **audio** | sonic (+ writing if speech) |
| a source-code folder / repo / gist | **code** | system |
| a URL | fetch it first (WebFetch), then reclassify | — |
| session archives, chat/agent exports, logs, DB dumps, build artifacts | **out-of-scope** | none — ledger `skipped` + reason |

Out-of-scope items are not reference material — don't read them, don't move them; one ledger
line with the reason removes them from every future batch. (The profile may pre-declare
out-of-scope paths.)

### 3 — Extract  →  read `references/rubrics.md`
Apply the matching rubric. Each rubric lists the *exact fields* to produce so extraction is
deep and repeatable, not vibes. **For video and audio you cannot consume the file directly** —
first run the preprocessing in `references/preprocess.md` (ffmpeg keyframes / whisper
transcript) and extract from the frames/transcript. **Preprocessing is not optional when the
tool exists** (`command -v ffmpeg`): if it's installed, preprocess and extract in this same run;
only a genuinely missing tool makes an item `blocked`. PDFs need no preprocessing — Read them
directly with the `pages` parameter.

**Big piles (≳15 files in one directory):** never sample ad hoc. (1) Cluster by filename origin
(generator prefixes like `ChatGPT Image …` / `grok-image-…` / `Gemini_…`, UUID batches) and
mtime; (2) Read 3–5 representative files per cluster; (3) write **one clustered note per
aesthetic** (§4) — if a sample turns out heterogeneous, split the cluster and re-sample;
(4) ledger one line per cluster with the files actually read under `sampled`. A ledgered cluster
counts as covered. Only if the batch is still oversized after clustering (> ~25 notes' worth)
may the remainder be ledgered `pending` — stated in the report as what the *next* run starts
with, never as a question.

### 4 — Write the note  →  use `references/template.md`
One markdown file per note: `<library>/<facet>/YYYY-MM-DD-<slug>.md`
(date = today from context; slug = short kebab of the essence). Fill the template's frontmatter
(`source`, `source_asset`, `captured`, `tags`, `fit`, `applies_to`) and body (Essence · What
to steal · Reusable prompt/spec · Where it applies · Sources · Raw notes). Map "Where it
applies" onto the surfaces/plans named in the profile.

When many assets share one aesthetic (e.g. a folder of dark refs), prefer **one clustered
note** that lists all source files under `Sources` over dozens of thin files.

### 5 — Preserve the source (in place — nothing moves)
- **Everything stays where it was dropped.** Sources are never moved or reorganised; the ledger
  records what's been distilled, so the drop zone never needs shuffling.
- **Anything on a media root** (image/video/audio — and texts/PDFs under `<media>/docs/`): leave
  it; the note's `source_asset` links it there.
- **A text dropped in the inbox**: copy it verbatim to the profile's `originals` dir (a frozen
  backup — so the drop-zone copy is safe for *you* to delete later), then leave the working copy
  in place.
- **A binary dropped loose in the inbox**: leave it; link it in place. (Prefer keeping big
  binaries on a media root — the profile says which.)
- Confirm nothing was deleted or moved.

### 6 — Index + ledger
Append a row to the library `INDEX.md`:
`| date | facet | [title](path) | fit | tags | one-line essence |`
and write/update the ledger line for **every** item the run touched — distilled, skipped,
blocked, or pending (§Ledger). The index makes notes findable; the ledger makes runs
incremental.

### 7 — Report
Report the run's full accounting straight from the ledger: **distilled** (each note: facet +
path), **skipped** (+ reason), **blocked** (+ the exact unblock command), **pending** (what the
next run starts with). Name the single best find. Then stop. **Never close with "want me to
continue?"** — if something processable remains, you aren't done; go process it (rule 6).

## Notes
- `fit: high|medium|low` is your honest call on relevance to this project — low-fit refs are
  still worth keeping, just tagged so they don't pollute the high-signal set.
- Adapt every rubric's "where it applies" to the design vocabulary in the profile. Never invent
  a token or surface — if the profile doesn't name one, describe the essence generically and
  say so.
- Keep the engine files (`SKILL.md`, `references/rubrics.md`, `references/template.md`,
  `references/preprocess.md`) identical across projects — **only `project-profile.md` differs.**
