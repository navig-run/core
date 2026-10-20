"""Navig Recall — a provenance-aware knowledge bank built from AI conversations.

Recall reads conversation transcripts (Claude Code, Codex, ChatGPT captures) and earlier
session audits, keeps a redacted immutable archive of what was said, and derives typed
records (decisions, plans, ideas, tables, findings…) that always cite their evidence.

Layout under ``<data_dir>/recall/``:

* ``archive/``  — immutable, secret-redacted turn segments + manifests (source of truth
  for evidence; survives deletion of the original transcript)
* ``vault/``    — canonical Markdown records (YAML front matter) and ``.events/`` — the
  append-only history log
* ``index.db``  — a rebuildable SQLite/FTS5 cache (``navig recall reindex``)

Nothing is read until the operator runs ``navig recall enable`` (``recall.read_local``).
Transcript text never goes to logs.
"""

from __future__ import annotations

__all__ = ["recall_root"]


def recall_root():
    """``<data_dir>/recall`` — every Recall file lives below this folder."""
    from navig.recall.paths import recall_root as _root  # noqa: PLC0415

    return _root()
