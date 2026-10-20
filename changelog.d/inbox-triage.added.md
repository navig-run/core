- **Capture triage in the Deck and NAVIG Desktop.** `GET /api/deck/inbox` now carries the two
  fields triage groups by — `content_hash` (sha256[:16] of what the classifier read) and
  `url` (the captured page, from a v1 `url:` or legacy `Source:` line; history rows keep
  the hash only). The Deck's Documents stream gains a **Triage** mode and the OS Inbox a
  **Triage** tab: duplicates group by page, one header per group with its count, and a
  keyboard pass — `j/k` move · `Enter` route · `x` skip · `e` expand a group · `1–9`
  category · `Esc` — with the row itself always one click too. The grouping and key map
  live in one module, copied byte-identically between the two apps and asserted by a
  parity test.
