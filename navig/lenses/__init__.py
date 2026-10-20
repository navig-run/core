"""Lenses — NAVIG's one format for code that augments a website.

A Lens is a Tampermonkey-compatible ``.user.js`` (``==UserScript==`` header, ``@match``,
``@run-at``, a ``GM_*`` subset) plus optional ``@navig-*`` keys. Core owns the store and
compiles each Lens with the single page runtime (``runtime.js``); every host injects the
same compiled script:

* NAVIG OS — the WebView2 browser pane + the agent's browser worker (``/api/deck/lenses``)
* ``navig cdp`` browsers (navig-browser, in-process)
* the Chrome extension's web bar (``chrome.userScripts``)

Spec: ``docs/lenses.md``.
"""

from __future__ import annotations

from navig.lenses.compile import HOSTS, CompiledLens, compile_lens
from navig.lenses.fetch import LensFetchError, fetch_text
from navig.lenses.manifest import LensFormatError, LensManifest, parse_manifest
from navig.lenses.store import CompiledSet, LensRecord, LensStore, lenses_dir

__all__ = [
    "HOSTS",
    "CompiledLens",
    "CompiledSet",
    "LensFetchError",
    "LensFormatError",
    "LensManifest",
    "LensRecord",
    "LensStore",
    "compile_lens",
    "fetch_text",
    "lenses_dir",
    "parse_manifest",
]
