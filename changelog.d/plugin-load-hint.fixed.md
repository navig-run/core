- **A plugin that cannot import now names the distribution — and, for an editable install, the
  checkout it came from.** `pip install -e plugins/<name>` pins the module to a checkout;
  delete that checkout and the distribution metadata and its `navig.plugins` entry point
  survive while the import resolves nothing, so every NAVIG command warned only
  `navig plugin 'router' failed to load: No module named 'navig_router'` — no distribution,
  no path, nothing to act on. The loader (`navig/core/plugins.py`) now appends
  `<dist> is installed editable from <path>, which is gone or incomplete — reinstall it from
  that checkout or remove it (pip uninstall <dist>)`, and a plain reinstall/remove hint when
  the install is not editable. A plugin that raises on its own still reports its own error
  with no install advice, because reinstalling it would send the operator the wrong way.
  Found live: `navig_router` and `navig_tasks` 0.1.0 both pointed at a deleted
  `.dev/worktrees/distribution` checkout. Pinned by `core/tests/plugins/test_load_hint.py`.
