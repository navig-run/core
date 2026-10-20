"""Projects — every folder with a ``.navig/`` directory, managed as one thing.

A *project* is a registered space (``navig.spaces.registry``): there is no second
registry. This package adds what a code project needs on top of a space — a
profile (stack, runnable scripts), an index of its docs, its todos, a budgeted
context pack, a script runner, and links to the systems that know about it
(finance, the Store).

Everything here is read-mostly and degrades: an unreadable file is skipped, a
missing git is "no git", a failing link provider is omitted. Writes go through
:mod:`navig.projects.textio`, which preserves the file's own line endings.
"""

from __future__ import annotations

__all__: list[str] = []
