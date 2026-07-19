# Extraction rubrics

One rubric per input type. Produce **every field** — if a field is genuinely N/A, write "—" so
it's clear you looked. The goal is a note someone (or a future prompt) can *act on* without
seeing the original. Map every "where it applies" onto the surfaces named in
`project-profile.md`; never invent a token or route.

---

## image-art → facet: `style`

You can view the image directly (Read the file). Extract:

- **Palette** — 4–8 hex swatches, dominant → accent, each named (`#0B0B0E charcoal-black`…).
  Note the ratio (mostly dark? one hot accent?).
- **Typography feel** (if any text/logo) — serif/sans/mono, weight, case, tracking, display vs
  body, any custom letterforms.
- **Composition** — grid/symmetry, negative space, focal point, rule-of-thirds vs centered, density.
- **Motifs & iconography** — recurring shapes/symbols (grids, brackets, glyphs, masks…).
- **Texture & finish** — grain, noise, scanlines, halftone, gloss/matte, chromatic aberration,
  film vs clean.
- **Lighting & mood** — light source, contrast, temperature, and the *emotional register* in 3–5 words.
- **Subject** — what is literally depicted.
- **★ Reusable image-gen prompt** — one paste-ready prompt (Midjourney/DALL·E/SD flavor) that
  would regenerate this aesthetic: subject + style + palette + lighting + finish + mood + aspect.
  This is the single most valuable output — make it good enough to paste and run.
- **Project mapping** — which design tokens/motifs (from the profile) it maps to, and `fit`.

---

## product-shot → facet: `product` (+ `style` if the visual language is notable)

- **What it is** — product + which surface (feed, composer, settings, onboarding…).
- **★ The pattern worth stealing** — name it in a phrase ("inline slash-command palette",
  "sticky compare rail").
- **Flow / interaction** — step-by-step of the notable interaction (what the user does → what happens).
- **Layout & hierarchy** — regions, what's primary/secondary, how attention is directed.
- **Microcopy & tone** — verbatim notable copy; the voice.
- **What NOT to copy** — anti-patterns / friction you can see.
- **Project application** — exact route/surface/component it maps to (per the profile); if
  actionable, note a pointer to the project's backlog/ideas location.

---

## text → facet: `writing` (voice/lore) · `product` (product ideas) · `system` (tech ideas)

First: **copy the original to the profile's `originals` dir** (rule 2). Then:

- **Thesis** — the whole thing in one sentence.
- **★ Ideas useful to this project** — 3–7 bullets, each concrete and specific.
- **Quotable lines** — verbatim, with attribution — for manifesto/marketing/lore reuse.
- **Frameworks / models** — any named model, ladder, or mental model introduced.
- **Objections / risks** — where it wouldn't apply or could mislead.
- **Project application** — links to the surface/plan/doc it should feed.
- Pick facet by dominant payload: brand/voice → `writing`; a feature/UX idea → `product`; an
  architecture/algorithm → `system`. Split into multiple notes if it's genuinely two things.

---

## video → facet: `motion` (+ `style` / `product` per content)

**Preprocess first** (`references/preprocess.md`): pull keyframes with ffmpeg, and a transcript
if it has speech. Then extract from the frames + transcript:

- **Pacing & rhythm** — cut frequency, hold times, build/release, where it lingers.
- **Transitions & camera** — cut/dissolve/whip/match-cut; push-in, drift, parallax, handheld vs locked.
- **Motion signatures ("juice")** — easing/spring feel, weight, overshoot, stagger, anticipation.
- **Grade over time** — how color/light shifts across the piece (day↔night, flares, bloom).
- **Sound sync** (if any) — hits landing on beats, whooshes, stingers (also spawn a `sonic` note if rich).
- **★ Project application** — which motion moment it informs (ambient background, milestone
  cinematics, press-state feel, transition language). Be specific.
- List every keyframe path under `Sources`.

---

## audio → facet: `sonic` (+ `writing` if it's speech worth quoting)

**Preprocess first**: transcript (whisper) for speech; for music/SFX, describe from listening
notes / metadata / the source page. Then:

- **Type** — speech / music / SFX / ambient.
- If **speech** → run the `text` rubric on the transcript (facet `writing`) *and* note delivery/tone here.
- If **sonic** → instrumentation & texture, tempo/energy, key emotional register, duration.
- **★ Project mapping** — which UI sound it should become: boot sting, hum, UI tick, success
  chord, error buzz, fanfare. Note length + when it fires. (Keep all product sound mutable.)

---

## code → facet: `system`

- **What it does** + the specific **technique worth lifting** (not the whole repo).
- **Key files / functions** — where the good part lives.
- **Stack mapping** — how it ports onto this project's stack (per the profile). Call out mismatches.
- **License / origin** — if third-party, where it came from; if it's a keeper, note where the
  reference should be kept.
- **Project application** — the system/route/component it would improve, + a plan pointer if actionable.
