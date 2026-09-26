- **GitHub Models is a provider in every surface, not only in the dispatcher.** The
  registry offered it (15 models, auto-detection priority #2) while `BUILTIN_PROVIDERS` had
  no entry, so `navig ai providers` omitted it, `--test github_models` and
  `navig ai models --provider github_models` answered "Unknown provider", a fallback spec
  naming a GitHub-only model (`phi-4`, `deepseek-r1`) resolved to an EMPTY chain, and the
  verifier logged a WARN on every Telegram `/providers` screen and onboarding run. The
  recorded reason for leaving it — "needs context-window metadata" — was measured false:
  `context_window` has exactly one reader, the `navig ai models` table. The entry now mirrors
  the manifest's model list (pinned by a test) and sits LAST so a shared id like `gpt-4o`
  still infers its native provider. `navig ai models` no longer crops the Model ID column.
- **A `github_models.token` in config.yaml now counts as configured.** Five dispatch paths
  honour that documented key (the provider's own error message names it), but the key
  store behind `navig ai providers` / `--test` / the fallback client factory did not — a
  token that worked for every chat showed as `✗ not set`. `resolve_auth` reads it after
  env and vault, labelled `config:github_models.token`; no other provider grows the
  convention (tested).
- **The registry's "run `verify_all_providers()` and confirm zero failures" is now a test**
  — every enabled provider must pass the machine-independent structural checks (factory +
  ProviderConfig). The only prior test asserted `hasattr(result, "ok")`.
- **`navig ai providers` no longer prints `✓ subscription` next to `not_found`.** `resolve_auth`
  reports "nothing" as the truthy string `"not_found"`, so `source or "connection"` kept it and
  the row contradicted itself on every subscription-backed provider. The credential on that
  branch IS the routable connection, so the row says `connection`.
- **A credential probe no longer fails on a retired model id.** `navig ai providers --test`
  and `navig connect`'s validation both sent `BUILTIN_PROVIDERS[p].models[0]`, and on
  2026-09-19 that id was retired for EVERY provider the operator held a key for (xai 404
  "deprecated 2025-09-15", openai 404, nvidia 410 "end of life 2026-08-26", openrouter 404
  "no endpoints") — four working keys reported as broken, and a new connection recorded the
  dead id as its `default_model`. The probe now walks the known ids (registry manifest first,
  then the table; `navig/providers/probe_models.py`), skips the ones the provider says are
  gone, stops at the first credential error, and lists the id that answered FIRST so the
  connection's default model is one that just worked. When every id is retired it says so
  instead of blaming the key. Measured after: all four answer on the first candidate.
  ⚠ The persisted half: `navig connect test` only ever *filled* an empty `default_model`, and
  inference routes every unspecified request to it — so a connection created on a since-retired
  id revalidated HEALTHY on a neighbour and then failed every real request. A green revalidate
  now replaces a default the provider reported retired with the id that answered (logged);
  a live default the operator chose is never overridden, and a failing verdict changes nothing.
- **The retired-model guard now covers the table the probes read.** `liveness.RETIRED_MODELS`
  already named three of the four dead first rows, and the offline guard asserted no shipped
  mode and no manifest references them — but never looked at `BUILTIN_PROVIDERS`, the third
  list, the one `models[0]` came from. Widened; the five denylisted ids are out of the table
  (xai refilled with the three ids that answered on 2026-09-19, nvidia led by its live manifest
  id), the two new retirements (`openai:gpt-4-turbo-preview`, `openrouter:anthropic/claude-3.5-sonnet`)
  are denylisted after a second confirmation against a live control, and `probe_candidates`
  skips denylisted ids up front so a known retirement never costs a call again.
  Every remaining table row of the four keyed providers was then called: three more were dead
  (`openrouter:google/gemini-pro-1.5`, `nvidia:mistralai/mistral-7b-instruct-v0.3`,
  `nvidia:nvidia/llama-3.1-nemotron-70b-instruct` — 404 ×4 each, live controls on the same keys)
  and are denylisted and gone; every id `navig ai models` now lists for those four answered today.
