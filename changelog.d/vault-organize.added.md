- **`navig vault classify [--apply] [--category] [--json]`** — organise the vault: every
  item gets a name (only where it has none), a category (ai · search · social · messaging ·
  infra · payments · devtools · internal · hosts · logins · sessions · personal), the issuer
  and the website it is managed on (inferred from the value's shape, e.g. `EAA…` → Meta),
  and `duplicate-of:<other>` tags for byte-identical values. Dry-run by default; `--apply`
  writes metadata only and never overwrites an existing name, category, issuer or website.
  Values are read in memory and never printed (the Hint column shows at most 4 characters).
- **`navig vault list --category/--tag/--search`** (search matches the name, path, provider,
  issuer, website, tags and notes) with Category and Issuer columns, and
  **`navig vault edit --category/--issuer/--website/--note/--tag/--untag`** — organisation
  edits never touch the secret itself.
