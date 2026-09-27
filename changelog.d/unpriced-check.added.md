- **`navig ai models --check` names the live models whose cost would read $0.00.** A model with no
  entry in the price table is costed at $0.00 on every agent turn — xAI's default `grok-4.6` was,
  until the 2026-09-27 audit. The catalog audit now lists every LIVE id with no price, for the
  providers the table models, and names once the providers it does not model at all (groq, nvidia,
  openrouter — open-weight hosts and an aggregator), rather than burying the ones that matter under
  34 ids. `--json` rows carry `priced`. Never an exit failure: a missing price is accounting, not a
  broken model.
