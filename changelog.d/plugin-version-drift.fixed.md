- **Fresh installs no longer get stale plugins.** Twelve plugins had source that moved under
  an already-published version (`check_plugin_version_drift.py`): blackbox, cabinet, calendar,
  dedupe, download, ebay, explore, generate, github, mini, msstore, vm are patch-bumped so the
  published version and the tree agree again; `__version__` skew inside seven of them is
  aligned; the hard-dep floor `navig-generate>=0.4.1` in pyproject + requirements.txt keeps a
  pre-extraction generate out of fresh installs.
