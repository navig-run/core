- **A module toggle from the deck erased settings the CLI had written since the daemon
  started.** `ConfigSingleton.set(scope="global")` wrote into the copy this process loaded —
  in a daemon, possibly days ago — and `save()` wrote that copy back, so `navig config set
  telegram.y 2` followed by `set_enabled` (a set + save) left the file with the override and
  without `telegram.y`. The project-scope branch refreshed first; the global one never did. The
  same gap made a READ discard an unsaved write: `get()` refreshed the global copy whenever the
  file's mtime or the config dir had moved, by replacing the dict — which is why
  `test_registry_string_override_disables` was green alone and red on whichever xdist worker
  had bounced `NAVIG_CONFIG_DIR` earlier. Global writes now go into a pending ledger on a fresh
  base, a refresh re-applies the ledger over the new copy, `save()` is refresh → re-apply →
  write (and re-stamps the mtime it just produced), and `enable_plugin`/`disable_plugin` use the
  same path. Pinned by `tests/regression/test_shared_config_set_survives_refresh.py`.
