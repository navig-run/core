# Bootstrap prompt — stand up the distillery in a non-NAVIG project

Every NAVIG space is born with this distillery (scaffolded by `navig space init`). To add the
same engine to a plain repo that isn't a NAVIG space, paste the block below into a fresh Claude
Code session there.

---

> **Build me an inspiration distillery for this project.** I want a repeatable process where I
> drop reference material — images/art, video, audio, articles, app screenshots, code — into one
> folder, run one command, and you extract the *reusable essence* and file it as organized notes
> that link back to the original.
>
> First explore the repo and confirm with me: where raw drops go (an `inbox/`), where distilled
> notes live (a git-tracked library subfoldered by facet: `style`, `product`, `writing`, `motion`,
> `sonic`, `system`), and where big binaries already live so notes just link them.
>
> Then build: **(1)** a Claude Code skill (`.claude/skills/<name>/SKILL.md` + `references/`) that
> enumerates the inbox, **classifies** each item (image-art · app-screenshot · text · video · audio ·
> code · URL), applies a **per-type extraction rubric**, writes one markdown note per essence,
> updates an **INDEX**, and preserves the source; **(2)** per-type **rubrics** with the exact fields
> to produce (art → paste-ready image-gen prompt + palette/motifs/mood; screenshot → the UX pattern +
> flow; article → thesis + ideas + quotable lines; video/audio → an ffmpeg/whisper preprocess step
> then notes; code → the technique worth lifting + how it ports to the stack); **(3)** a note
> **template** (`title, facet, source, source_asset, captured, tags, fit, applies_to` + body:
> Essence · What to steal · Reusable prompt/spec · Where it applies · Sources · Raw notes); **(4)** an
> INDEX + README; **(5)** a small `project-profile.md` that captures the per-project bindings so the
> engine stays identical across projects and only the profile differs.
>
> **Hard rules, non-negotiable:** never delete OR move a source (processing = read + copy a text
> to backup, never move/remove); back up texts verbatim to `.inbox/_originals/` before distilling,
> then leave the source in place — nothing is shuffled; a `ledger.jsonl` records what's done
> (binaries already on a media root stay put); every note links its `source_asset`; one note =
> one facet; index everything and flag
> partial coverage instead of silently capping.
>
> When built, **demonstrate it end-to-end** on real items in the repo and show me where notes
> landed. Adapt every rubric's "where it applies" and design vocabulary to *this* project's stack
> and design system — read its style guide / component library first.

---

## Re-use notes
- Swap the distilled library location for wherever the project wants curated notes
  (`.navig/refs/notes/`, a `knowledge/` dir, a `docs/inspiration/` folder…). Record it in the profile.
- `fit` = a relevance field so low-signal refs are kept but tagged.
- ffmpeg + whisper are optional; the skill should degrade gracefully and print the exact command
  when a tool is missing.
