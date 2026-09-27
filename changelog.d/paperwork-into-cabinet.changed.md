- **`navig paperwork` now ships inside `navig-cabinet` — one plugin for documents.** The
  separate `navig-paperwork` package (never published) was folded in; `navig[paperwork]`
  is kept as an alias of `navig[cabinet]`, and every `navig paperwork` verb, flag and file
  is unchanged. What the merge changes is where a person's own documents end up:
  `navig paperwork handoff` now **encrypts ID scans and medical records into the cabinet**
  instead of copying them as plain files into `human-health-space` / `company-paperwork`,
  where one `git add` would have committed them. The document type decides, so a manifest
  written before this change cannot route a medical record back into a plaintext tree.
  Originals are left in place; `--to cabinet` selects just that half. Benefits and housing
  letters keep their space route.
