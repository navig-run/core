"""
NAVIG Daemon — Persistent process supervisor.

Manages Telegram bot, gateway, heartbeat and scheduler as supervised
child processes with automatic restart, health monitoring and log
rotation.  Can run standalone or be wrapped by a Windows service
manager (NSSM / Task Scheduler / WinSW).
"""

from navig.daemon.supervisor import NavigDaemon

__all__ = ["NavigDaemon"]
