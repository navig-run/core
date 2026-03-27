"""
navig-commands-core/commands/sysinfo.py

CPU, memory, and disk information.
Uses psutil when available; falls back to stdlib shutil/os.
"""

from __future__ import annotations

import os
import shutil
from typing import Any


def handle(args: dict, ctx: Any = None) -> dict:
    """
    Return system resource information.

    args:
      section (str, optional): 'cpu', 'memory', 'disk', or 'all' (default).
      path (str, optional): Disk path for disk section (default '/').
    """
    section = args.get("section", "all").lower()
    disk_path = args.get("path", "/")

    info: dict[str, Any] = {}

    # CPU
    if section in ("cpu", "all"):
        try:
            import psutil  # type: ignore

            info["cpu"] = {
                "percent": psutil.cpu_percent(interval=0.2),
                "count_logical": psutil.cpu_count(logical=True),
                "count_physical": psutil.cpu_count(logical=False),
            }
        except ImportError:
            info["cpu"] = {
                "count_logical": os.cpu_count(),
                "note": "psutil not installed",
            }

    # Memory
    if section in ("memory", "all"):
        try:
            import psutil  # type: ignore

            vm = psutil.virtual_memory()
            info["memory"] = {
                "total_gb": round(vm.total / 1e9, 2),
                "available_gb": round(vm.available / 1e9, 2),
                "percent": vm.percent,
            }
        except ImportError:
            info["memory"] = {"note": "psutil not installed"}

    # Disk
    if section in ("disk", "all"):
        try:
            usage = shutil.disk_usage(disk_path)
            info["disk"] = {
                "path": disk_path,
                "total_gb": round(usage.total / 1e9, 2),
                "used_gb": round(usage.used / 1e9, 2),
                "free_gb": round(usage.free / 1e9, 2),
                "percent": round(usage.used / usage.total * 100, 1),
            }
        except Exception as exc:
            info["disk"] = {"error": str(exc)}

    return {"status": "ok", "data": info}
