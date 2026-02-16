"""
NAVIG Gateway - Autonomous Agent Control Plane

Central 24/7 service that coordinates:
- Multiple channels (Telegram, Discord, etc.)
- Heartbeat-based monitoring
- Session persistence
- Cron scheduling
- Background task management
"""

from .server import NavigGateway, GatewayConfig
from .session_manager import SessionManager, Session, NavigSessionKey
from .channel_router import ChannelRouter
from .config_watcher import ConfigWatcher, WorkspaceManager
from .system_events import (
    SystemEventQueue, 
    SystemEvent, 
    EventPriority, 
    EventTypes,
    SmartNotificationFilter,
)

__all__ = [
    'NavigGateway',
    'GatewayConfig',
    'SessionManager', 
    'Session',
    'NavigSessionKey',
    'ChannelRouter',
    'ConfigWatcher',
    'WorkspaceManager',
    'SystemEventQueue',
    'SystemEvent',
    'EventPriority',
    'EventTypes',
    'SmartNotificationFilter',
]
