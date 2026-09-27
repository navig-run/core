- **The plugin core-floor check no longer reports an import that has a fallback.**
  `scripts/check_plugin_core_floor.py` flagged every `from navig.x import y`, including
  one inside `try: … except ImportError:` whose handler supplies the fallback. That
  shape cannot raise the ImportError the check exists to prevent. Only a handler that
  NAMES `ImportError` / `ModuleNotFoundError` counts, and only the `try` body is
  guarded. `except Exception` stays a finding, because it swallows everything else too
  and turns a missing core feature into a silently dead one. Measured across every
  plugin: the only result that changed is navig-cabinet's, whose fallbacks are real.
