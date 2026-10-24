- **Lens values (GM storage) no longer live where the page can read them on Chrome.** A Lens
  with a storage grant (`GM_getValue`/`setValue`/… or the `GM.*` forms) now compiles for the
  Dock with `world: "USER_SCRIPT"` and a `bridge: ["storage"]` capability, and the runtime
  keeps its values through that closure-only bridge: preloaded before the Lens runs, never
  written to the page's `localStorage`, old page-stored values migrated out and scrubbed, and
  page-fired `storage` events ignored. A failed load runs the page on memory only rather than
  writing defaults over a stored API key. `// @sandbox raw` is the explicit page-world opt-out.
  `navig.onUrlChange` also follows the page's navigations from an isolated world (Navigation API).
