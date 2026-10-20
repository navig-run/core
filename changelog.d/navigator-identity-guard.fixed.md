- **The shipped prompt library calls the identity by its right name again — "NAVIG — The
  Navigator" — and a guard keeps the retired one from coming back.** The builtin-content
  packaging change re-shipped "Living Octopus-Mind" inside `core/navig/builtin/prompts` and
  `builtin/agents`, so a fresh install (and the Prompt Library every surface reads) named the
  identity something the owner retired on 2026-07-08. The six shipped surfaces now say **The
  Navigator**; the octopus stays the visual mark and the Octopus Doctrine keeps its name. A new
  test scans every shipped prompt/persona file for the retired noun in any spelling (ASCII
  hyphen, U+2011, space, any case) and fails listing file and line, so packaging or a prompt
  edit cannot silently reintroduce it.
