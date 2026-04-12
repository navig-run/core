"""Compatibility shim for conversational agent imports.

This module forwards to the established compatibility implementation so
existing imports remain stable while gateway paths use ``navig.agent.conv``.
"""

from __future__ import annotations

import sys

from navig.agent import conversational_legacy as _compat

sys.modules[__name__] = _compat
