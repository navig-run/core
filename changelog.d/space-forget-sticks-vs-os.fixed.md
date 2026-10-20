- **`navig space forget` sticks while NAVIG Desktop is running.** The OS workspace sync
  re-registers every local workspace on each run; its `registerBrainSpace` scaffolded a
  bare `.navig/` first, which passed the deck register endpoint's space check and lifted
  the tombstone — forgotten ghost rows returned within minutes (exactly the ghosts
  carrying an empty `.navig/`). `POST /api/deck/spaces/register` now accepts `auto: true`
  for that background back-fill and answers `{skipped: "forgotten"}` (tombstone intact,
  nothing written) or `{skipped: "not_a_space"}` (bare folder — the caller may scaffold,
  then retry). Explicit registers are unchanged: `navig space register <path>` still
  brings a forgotten space back.
