- **Cross-Space relations storage — `navig.spaces.relations`.** A Space's
  `.navig/space.json` may now carry a logical `relations` block (`parent` + `links`
  with `include`/`exclude` globs and a `profile`) and a top-level `collections`
  list for UI grouping. Metadata only: paths never move, no new folders, reads of
  a malformed block degrade to "no relations" instead of raising, unknown or
  missing targets resolve as *unavailable*, and writes refuse self-links,
  unregistered targets and parent cycles. v1 access is `read` only — the module
  writes manifests, never another Space's files. CLI verbs (`navig space link`
  and friends) and the deck endpoints follow in this same release.
