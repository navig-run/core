# ADR-0002 — Media stays modular: one owner per operation, not one plugin

- **Status:** Accepted
- **Date:** 2026-10-16
- **Deciders:** project owner
- **Supersedes:** Phase 5 workstream #5 in `.navig/plans/CURRENT_PHASE.md` as first written —
  *"Media merge — generate + audio + text (+ pipeline) → one plugin"* (2026-10-09).

## Context

Media capability is split across seven packages under `plugins/` plus core shims:

| Package | Version | Role |
|---|---|---|
| `navig-media-core` | 0.1.1 | library — asset/variant model, store, refs library, provider catalog, ffmpeg helpers |
| `navig-generate` | 0.3.0 | AI generation — provider clients, CLI mounted on core's `navig generate` |
| `navig-audio` | 0.5.0 | TTS/STT, voice, enhance, mastering, beats, podcast render (`navig audio`, `navig voice`) |
| `navig-video` | 0.2.2 | edit lists, kinetic render, parity (`navig video`) |
| `navig-text` | 0.5.0 | writing, lyrics, channel copy, design tokens (`navig text`, `navig design`) |
| `navig-pipeline` | 0.6.0 | content production orchestration (`navig pipeline`) |
| `navig-media` | 0.1.0 | install bundle (unpublished) |

`core/navig/media/` is now almost entirely compatibility shims onto those packages.

An audit on 2026-10-16 found the problem is **not** the package count. It is blurred ownership
*inside* the packages:

- the "library" `navig-media-core` lazily imports `navig_generate` in ~13 places — a hidden
  reverse dependency;
- the same operation is implemented more than once — two ElevenLabs TTS clients, three API-key
  resolvers with different precedence, ~10 private ffmpeg run/probe helpers, five EBU R128
  loudness routines, three subtitle writers, episode draft/translate/publish in both audio and
  pipeline;
- CLI entry-point discovery and the generator registry are both *last registration wins*, with
  no collision report.

Merging everything into one plugin would hide these duplicates inside one package rather than
remove them, and would break advertised standalone installs (`navig-audio`, `navig-generate`,
`navig-video`, `navig-text`, `navig-pipeline` each ship their own console script).

## Decision

**Keep the packages. Consolidate implementations, not packages.** Every media operation has
exactly one implementation owner; every other package reuses it through that owner's public
interface.

| Operation | Owner |
|---|---|
| Asset/variant model, generated-media store, refs library, provider catalog, media key resolution, ffmpeg / loudness / subtitle primitives, generator registry | `navig-media-core` |
| Generation orchestration (generate, reroll, edit, keep/reject, history) and provider HTTP clients for image, video and audio | `navig-generate` |
| Speech (TTS/STT/voices), enhancement, mastering, beat analysis CLI, podcast rendering | `navig-audio` |
| Video editing, kinetic render, encode, parity | `navig-video` |
| Writing and text transformation; `navig design` stays here as a documented bounded capability | `navig-text` |
| Content production orchestration — a linear recipe runner composing the engines above, **not** a general workflow engine (integrated runs go through core Blocks) | `navig-pipeline` (`navig content`; `navig pipeline` is a deprecated alias) |
| Installing a compatible suite — dependencies only, no engine, store or CLI engine of its own | `navig-media` |
| Media utilities on the hub: `navig generate analyze / frames / probe / browse / dedupe-*` | core `navig/commands/media.py` |

**Not created:** `navig-content`, `navig-voice` and `navig-design` packages. Their names appear in
historical plans; the functionality already lives in `navig-pipeline`, `navig-audio` and
`navig-text`, and their CLI verbs are preserved.

### Dependency graph (directed, acyclic)

`A → B` means "A depends on B". Base dependencies unless marked.

```
navig-media-core → navig-sdk
navig-generate   → navig-media-core, navig-vault, navig-sdk
navig-audio      → navig-generate, navig-media-core, navig-vault, navig-sdk
navig-video      → navig-sdk, navig-media-core*      [render extra → navig-audio, navig-generate]
navig-text       → navig-sdk                         [media extra → navig-media-core]*
navig-pipeline   → navig-generate, navig-media-core, navig-audio   [all extra → text, download, social, browser]
navig (core)     → navig-media-core, navig-generate  [audio/voice/video extras]
navig-media      → every package above (bundle only)
```

`*` = edge added by this consolidation (not yet on `main` when this ADR was accepted).

Rules:

1. `navig-media-core` imports only the standard library, `navig-sdk` and optional
   dependencies reached through guarded, function-local imports (`navig_vault`, `navig`). It
   never imports a media plugin built on it (`navig_generate`, `navig_audio`, `navig_video`,
   `navig_text`, `navig_pipeline`, `navig_media`); the one exception is a deprecated identity
   alias left at a moved module's old path. A layering test enforces this.
2. An engine calls another engine through its public module, never a `_private` name.
3. A missing optional engine degrades to an actionable error, never an import crash.
4. Heavy dependencies (Torch, Whisper, video engines) are imported lazily, inside the function
   that needs them.

### Compatibility boundaries

- No storage migration. `generated_media.db` (schema v2), `<space>/.navig/refs/`, asset ids,
  variants, sidecars and ledgers keep their paths and formats.
- Every existing import path keeps working: a moved module leaves a re-export at its old path.
- Every existing command, flag, `--json` shape and exit code keeps working; renamed verbs keep
  the old name as an alias that prints a deprecation notice on stderr only.
- Provider settings keep their precedence: deduplicating a key resolver must not change which
  key a caller picks when both an env var and a vault entry exist.

## Consequences

- Duplicate implementations are replaced by delegation in reviewable steps. Each removal is
  preceded by a parity test showing the owner produces the same result.
- Standalone installs stay supported; `navig-media` stays an optional convenience.
- Command and generator registration report collisions instead of silently picking the last.
- Renaming the `navig-pipeline` distribution to `navig-content` is deferred. It needs a PyPI
  publish and a compatibility package, and is decided separately.

## Implementation status (2026-10-16)

The decision is accepted; implementation lands as a series of focused changes. Until each one
lands, the code still has the gaps listed in *Context*. The ownership table is the target that
reviews are checked against.
