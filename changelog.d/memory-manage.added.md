- **Manage what NAVIG remembers: pin, edit, forget, and a profile summary.** Pinned
  memories ("always keep in mind") are injected into every conversation, ahead of
  query-relevant ones (max 10, approved only) — `navig memory pin|unpin <id>`.
  `navig memory edit <id> "…"` corrects a memory in place (through the secrets filter).
  `navig memory summary [--refresh]` writes a short "What NAVIG knows about you" from
  approved memories, cached until they change. Deck routes for the Personalization page:
  `GET /api/deck/memory/facts`, `PATCH|DELETE /api/deck/memory/facts/{id}`,
  `GET|POST /api/deck/memory/summary` — deck auth only. The key-fact store gains an
  additive `pinned` column (schema v2), and `kf_meta.schema_version` now records the
  migrated version instead of the version the db was created at.
