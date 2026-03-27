"""
NAVIG Heartbeat Module

Provides periodic health checks and proactive monitoring.
"""

from navig.heartbeat.runner import HeartbeatConfig, HeartbeatRunner

__all__ = ["HeartbeatRunner", "HeartbeatConfig"]
