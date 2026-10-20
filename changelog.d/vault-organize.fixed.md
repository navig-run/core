- **`navig vault edit --label` no longer wipes the secret it renames.** A label- or
  metadata-only update re-encrypted the payload after parsing it as JSON; a raw secret (any
  value stored with `vault set <path>`) parsed as `{}` and was overwritten with `{}`. The
  vault now patches metadata in place without touching the encrypted value (ships in
  navig-vault 0.13.0). The dead hidden `vault remove` alias, which
  the path-based `vault remove` always shadowed, is gone.
- **`navig vault list --tag/--category` that matches nothing no longer says "Vault is empty"**
  (with a "use `vault add`" nudge) over a full vault; it says no credentials match the filters.
