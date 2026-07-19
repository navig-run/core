"""Builtin plugin package: ``navig mini`` — control remote NAVIG Mini daemons.

This ``__init__.py`` is load-bearing for packaging: setuptools ``find_packages``
only ships directories that contain one, so without it this builtin is pruned
from the wheel and ``navig mini`` fails to load on a packaged (non-editable)
install. Every ``navig/builtins/<name>/`` package must keep an ``__init__.py``.
"""
