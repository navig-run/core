"""
Base Component System

Provides the foundation for all agent components with:
- Unified lifecycle management (start/stop/restart)
- Health checking
- State tracking
- Event emission
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from navig.agent.nervous_system import EventPriority, EventType, NervousSystem


class ComponentState(Enum):
    """Component lifecycle states."""

    CREATED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()
    STOPPED = auto()
    ERROR = auto()
    DEGRADED = auto()  # Running but with issues


@dataclass
class HealthStatus:
    """Component health status."""

    healthy: bool
    state: ComponentState
    message: str = ""
    last_check: datetime = field(default_factory=datetime.now)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "state": self.state.name,
            "message": self.message,
            "last_check": self.last_check.isoformat(),
            "details": self.details,
        }


class Component(ABC):
    """
    Base class for all agent components.

    All body parts (Brain, Eyes, Ears, Hands, Heart) inherit from this.
    Provides unified lifecycle management and health monitoring.
    """

    def __init__(self, name: str, nervous_system: NervousSystem | None = None):
        self.name = name
        self.nervous_system = nervous_system
        self._state = ComponentState.CREATED
        self._started_at: datetime | None = None
        self._error: Exception | None = None
        self._restart_count = 0
        self._last_health_check: HealthStatus | None = None

    @property
    def state(self) -> ComponentState:
        """Current component state."""
        return self._state

    @property
    def is_running(self) -> bool:
        """Check if component is running."""
        return self._state in (ComponentState.RUNNING, ComponentState.DEGRADED)

    @property
    def uptime_seconds(self) -> float:
        """Get component uptime in seconds."""
        if self._started_at and self.is_running:
            return (datetime.now() - self._started_at).total_seconds()
        return 0.0

    async def start(self) -> None:
        """Start the component."""
        if self._state == ComponentState.RUNNING:
            return

        self._state = ComponentState.STARTING
        self._error = None

        try:
            await self._on_start()
            self._state = ComponentState.RUNNING
            self._started_at = datetime.now()

            if self.nervous_system:
                from navig.agent.nervous_system import EventType

                await self.nervous_system.emit(
                    EventType.COMPONENT_STARTED,
                    source=self.name,
                    data={"component": self.name},
                )
        except Exception as e:
            self._state = ComponentState.ERROR
            self._error = e

            if self.nervous_system:
                from navig.agent.nervous_system import EventType

                await self.nervous_system.emit(
                    EventType.COMPONENT_ERROR,
                    source=self.name,
                    data={"component": self.name, "error": str(e)},
                )
            raise

    async def stop(self) -> None:
        """Stop the component."""
        if self._state in (ComponentState.STOPPED, ComponentState.CREATED):
            return

        self._state = ComponentState.STOPPING

        try:
            await self._on_stop()
            self._state = ComponentState.STOPPED

            if self.nervous_system:
                from navig.agent.nervous_system import EventType

                await self.nervous_system.emit(
                    EventType.COMPONENT_STOPPED,
                    source=self.name,
                    data={"component": self.name},
                )
        except Exception as e:
            self._state = ComponentState.ERROR
            self._error = e
            raise

    async def restart(self) -> None:
        """Restart the component."""
        self._restart_count += 1
        await self.stop()
        await asyncio.sleep(0.1)  # Brief pause
        await self.start()

    async def health_check(self) -> HealthStatus:
        """Check component health."""
        try:
            details = await self._on_health_check()

            status = HealthStatus(
                healthy=self._state == ComponentState.RUNNING,
                state=self._state,
                message=(
                    "OK" if self._state == ComponentState.RUNNING else f"State: {self._state.name}"
                ),
                details=details,
            )
        except Exception as e:
            status = HealthStatus(
                healthy=False,
                state=self._state,
                message=str(e),
                details={"error": str(e)},
            )

        self._last_health_check = status
        return status

    def set_nervous_system(self, nervous_system: NervousSystem) -> None:
        """Set the nervous system for event communication."""
        self.nervous_system = nervous_system

    async def emit(
        self,
        event_type: EventType,
        data: dict[str, Any] | None = None,
        priority: EventPriority | None = None,
    ) -> None:
        """Emit an event through the nervous system."""
        if self.nervous_system:
            from navig.agent.nervous_system import EventPriority

            await self.nervous_system.emit(
                event_type,
                source=self.name,
                data=data or {},
                priority=priority or EventPriority.NORMAL,
            )

    def get_status(self) -> dict[str, Any]:
        """Get component status as dictionary."""
        return {
            "name": self.name,
            "state": self._state.name,
            "running": self.is_running,
            "uptime_seconds": self.uptime_seconds,
            "restart_count": self._restart_count,
            "error": str(self._error) if self._error else None,
            "last_health": (self._last_health_check.to_dict() if self._last_health_check else None),
        }

    @abstractmethod
    async def _on_start(self) -> None:
        """
        Internal start implementation.

        Override this in subclasses to implement component-specific
        initialization logic.
        """
        pass

    @abstractmethod
    async def _on_stop(self) -> None:
        """
        Internal stop implementation.

        Override this in subclasses to implement component-specific
        cleanup logic.
        """
        pass

    async def _on_health_check(self) -> dict[str, Any]:
        """
        Internal health check implementation.

        Override this in subclasses to provide component-specific
        health metrics.
        """
        return {}

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.name}) state={self._state.name}>"
