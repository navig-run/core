# navig-core/host/internal/desktop/agent_wrapper.py
"""NAVIG Desktop Agent — OS dispatcher.

Detects the current platform and launches the appropriate sidecar agent.
This is the unified entry point, regardless of operating system.

Usage (called by Go client.go):
    python agent_wrapper.py

The wrapper itself speaks the same JSON-RPC protocol, forwarding all
requests to the OS-specific sidecar and streaming responses back.
"""
from __future__ import annotations

import os
import platform
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

_SYSTEM = platform.system()

if _SYSTEM == "Windows":
    _SIDECAR = os.path.join(_HERE, "agent.py")
elif _SYSTEM == "Darwin":
    _SIDECAR = os.path.join(_HERE, "agent_darwin.py")
elif _SYSTEM == "Linux":
    _SIDECAR = os.path.join(_HERE, "agent_linux.py")
else:
    sys.stderr.write(f"error: unsupported platform: {_SYSTEM}\n")
    sys.exit(1)

if not os.path.isfile(_SIDECAR):
    sys.stderr.write(f"error: sidecar not found: {_SIDECAR}\n")
    sys.exit(1)

# Execute the sidecar directly in this process (replaces the process image)
# so that stdin/stdout are passed through unchanged.
os.execv(sys.executable, [sys.executable, _SIDECAR])
