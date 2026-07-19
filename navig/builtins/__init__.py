"""navig.builtins — first-party plugins that ship *inside* core.

These are real plugins (same contract as the standalone ``plugins/navig-*``
packages), not kernel code — they just travel with core instead of being
installed. The plugin host (``navig.plugins``) scans this directory with
``source="builtin"`` and imports each as ``navig.builtins.<name>.plugin``.

Keeping builtins here — separate from ``navig/plugins/``, which is the
host/loader itself — keeps the kernel free of plugin implementations (the
microkernel boundary). Add one as ``navig/builtins/<name>/plugin.py`` exposing
module-level ``name`` and ``app`` (a Typer app), and optionally
``description`` / ``version``.
"""
