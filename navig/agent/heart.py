"""
Heart - Agent Orchestrator

The Heart is the central orchestrator that:
- Manages component lifecycles
- Monitors component health via heartbeats
- Coordinates startup/shutdown sequences
- Handles component failures and restarts
- Provides the main agent loop
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from navig.agent.component import Component, ComponentState, HealthStatus
from navig.agent.config import AgentConfig, HeartConfig
from navig.agent.goals import GoalPlanner, GoalState, SubtaskState
from navig.agent.nervous_system import EventPriority, EventType, NervousSystem
from navig.agent.remediation import RemediationEngine

if TYPE_CHECKING:
    pass


class Heart(Component):
    """
    The Heart orchestrates all agent components.
    
    Responsibilities:
    - Start/stop components in correct order
    - Monitor component health via heartbeats
    - Restart failed components
    - Coordinate graceful shutdown
    - Maintain the agent's main event loop
    """

    # Startup order (dependencies)
    STARTUP_ORDER = ['nervous_system', 'eyes', 'ears', 'hands', 'brain', 'soul']

    def __init__(
        self,
        config: HeartConfig,
        nervous_system: NervousSystem,
        agent_config: Optional[AgentConfig] = None,
    ):
        super().__init__("heart", nervous_system)
        self.config = config
        self.agent_config = agent_config or AgentConfig()

        # Component registry
        self._components: Dict[str, Component] = {}

        # Remediation engine for auto-healing
        self._remediation = RemediationEngine()

        # Goal planner — autonomous goal execution
        self._goal_planner: Optional[GoalPlanner] = None

        # Background tasks
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._health_check_task: Optional[asyncio.Task] = None
        self._goal_processing_task: Optional[asyncio.Task] = None

        # Metrics
        self._beat_count = 0
        self._last_beat: Optional[datetime] = None
        self._start_time: Optional[datetime] = None

    def register_component(self, name: str, component: Component) -> None:
        """Register a component for lifecycle management."""
        self._components[name] = component
        component.set_nervous_system(self.nervous_system)

    def get_component(self, name: str) -> Optional[Component]:
        """Get a registered component by name."""
        return self._components.get(name)

    @property
    def goal_planner(self) -> Optional[GoalPlanner]:
        """Access the goal planner (None if not initialized)."""
        return self._goal_planner

    def set_goal_planner(self, planner: GoalPlanner) -> None:
        """Attach a GoalPlanner to the Heart for autonomous goal processing."""
        self._goal_planner = planner

    async def _on_start(self) -> None:
        """Start the heart and all registered components."""
        self._start_time = datetime.now()

        # Emit starting event
        await self.emit(EventType.AGENT_STARTING, {'components': list(self._components.keys())})

        # Start remediation engine first
        self._remediation._heart = self  # Give remediation access to Heart
        await self._remediation.start()

        # Start components in order
        for name in self.STARTUP_ORDER:
            if name in self._components:
                component = self._components[name]
                try:
                    await component.start()
                except Exception as e:
                    await self.emit(
                        EventType.COMPONENT_ERROR,
                        {'component': name, 'error': str(e)},
                        priority=EventPriority.CRITICAL
                    )
                    # Continue with other components

        # Start any remaining components not in startup order
        for name, component in self._components.items():
            if name not in self.STARTUP_ORDER and not component.is_running:
                try:
                    await component.start()
                except Exception as e:
                    await self.emit(
                        EventType.COMPONENT_ERROR,
                        {'component': name, 'error': str(e)}
                    )

        # Start background tasks
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._health_check_task = asyncio.create_task(self._health_check_loop())

        # Start goal processing if planner is attached
        if self._goal_planner:
            self._goal_processing_task = asyncio.create_task(self._goal_processing_loop())

        # Emit started event
        await self.emit(EventType.AGENT_STARTED, {'components': list(self._components.keys())})

    async def _on_stop(self) -> None:
        """Stop all components gracefully."""
        # Emit stopping event
        await self.emit(EventType.AGENT_STOPPING, {})

        # Stop remediation engine first
        await self._remediation.stop()

        # Cancel background tasks
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if self._goal_processing_task:
            self._goal_processing_task.cancel()
            try:
                await self._goal_processing_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        # Stop components in reverse order
        stop_order = list(reversed(self.STARTUP_ORDER))

        for name in stop_order:
            if name in self._components:
                component = self._components[name]
                if component.is_running:
                    try:
                        await asyncio.wait_for(
                            component.stop(),
                            timeout=10.0  # 10 second timeout per component
                        )
                    except asyncio.TimeoutError:
                        await self.emit(
                            EventType.SYSTEM_WARNING,
                            {'message': f'Component {name} stop timed out'}
                        )
                    except Exception as e:
                        await self.emit(
                            EventType.COMPONENT_ERROR,
                            {'component': name, 'error': str(e)}
                        )

        # Stop remaining components
        for name, component in self._components.items():
            if name not in stop_order and component.is_running:
                try:
                    await component.stop()
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

        # Emit stopped event
        await self.emit(EventType.AGENT_STOPPED, {})

    async def _on_health_check(self) -> Dict[str, Any]:
        """Check heart health."""
        return {
            'beat_count': self._beat_count,
            'last_beat': self._last_beat.isoformat() if self._last_beat else None,
            'uptime': self.uptime_seconds,
            'components': len(self._components),
            'running_components': sum(
                1 for c in self._components.values() if c.is_running
            ),
        }

    async def _heartbeat_loop(self) -> None:
        """Main heartbeat loop."""
        while True:
            try:
                await asyncio.sleep(self.config.heartbeat_interval)

                self._beat_count += 1
                self._last_beat = datetime.now()

                # Collect component statuses
                statuses = {}
                for name, component in self._components.items():
                    statuses[name] = component.state.name

                await self.emit(
                    EventType.HEARTBEAT,
                    {
                        'beat': self._beat_count,
                        'uptime': self.uptime_seconds,
                        'components': statuses,
                    }
                )

            except asyncio.CancelledError:
                break
            except Exception:
                # Log but continue
                pass

    async def _goal_processing_loop(self) -> None:
        """Periodic goal processing loop.
        
        Checks for in-progress goals with pending subtasks, executes the
        next subtask whose dependencies are met, and routes execution
        through the Hands component for safety/approval integration.
        """
        while True:
            try:
                # Process goals every heartbeat interval
                await asyncio.sleep(self.config.heartbeat_interval)
                await self._process_goals()
            except asyncio.CancelledError:
                break
            except Exception:
                # Log but continue — don't let goal errors crash the loop
                pass

    async def _process_goals(self) -> None:
        """Execute the next ready subtask for each in-progress goal.
        
        For each IN_PROGRESS goal:
        1. Find the next subtask with all dependencies met
        2. If the subtask has a command, execute it via Hands
        3. Mark subtask completed/failed based on result
        4. Emit events for monitoring
        """
        if not self._goal_planner:
            return

        hands = self.get_component('hands')
        active_goals = self._goal_planner.list_goals(GoalState.IN_PROGRESS)

        for goal in active_goals:
            subtask = self._goal_planner.get_next_subtask(goal.id)
            if not subtask:
                continue

            # Mark subtask in progress
            subtask.state = SubtaskState.IN_PROGRESS
            subtask.started_at = datetime.now()
            self._goal_planner._save_goals()

            await self.emit(EventType.SYSTEM_INFO, {
                'message': f'Executing subtask: {subtask.description}',
                'goal_id': goal.id,
                'subtask_id': subtask.id,
                'command': subtask.command,
            })

            if not subtask.command:
                # No command — mark as completed (manual/descriptive subtask)
                self._goal_planner.complete_subtask(
                    goal.id, subtask.id, result="No command — auto-completed"
                )
                continue

            if not hands:
                # No Hands component — can't execute
                self._goal_planner.fail_subtask(
                    goal.id, subtask.id, error="Hands component not available"
                )
                continue

            # Execute command via Hands (inherits safety/approval checks)
            try:
                result = await hands.execute(subtask.command)
                if result.success:
                    self._goal_planner.complete_subtask(
                        goal.id, subtask.id, result=result.stdout or "OK"
                    )
                else:
                    self._goal_planner.fail_subtask(
                        goal.id, subtask.id,
                        error=result.stderr or f"Exit code: {result.exit_code}"
                    )
            except Exception as e:
                self._goal_planner.fail_subtask(
                    goal.id, subtask.id, error=str(e)
                )

    async def _health_check_loop(self) -> None:
        """Periodic health check loop."""
        while True:
            try:
                await asyncio.sleep(self.config.health_check_interval)

                # Check all components
                results: Dict[str, HealthStatus] = {}
                failed: List[str] = []

                for name, component in self._components.items():
                    health = await component.health_check()
                    results[name] = health

                    if not health.healthy and component.state == ComponentState.ERROR:
                        failed.append(name)

                # Emit health check event
                await self.emit(
                    EventType.HEALTH_CHECK,
                    {
                        'results': {n: h.to_dict() for n, h in results.items()},
                        'failed': failed,
                    }
                )

                # Attempt to restart failed components
                for name in failed:
                    component = self._components[name]
                    if component._restart_count < self.config.max_restart_attempts:
                        # Schedule remediation with exponential backoff
                        action_id = await self._remediation.schedule_restart(
                            component=name,
                            reason=results[name].message,
                            metadata={'health': results[name].to_dict()}
                        )

                        await self.emit(
                            EventType.SYSTEM_INFO,
                            {
                                'message': f'Scheduled restart for {name}',
                                'action_id': action_id,
                                'attempt': component._restart_count + 1
                            }
                        )

            except asyncio.CancelledError:
                break
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

    async def restart_component(self, name: str, reason: str = "Manual restart") -> bool:
        """
        Restart a specific component with remediation tracking.
        
        Args:
            name: Component name to restart
            reason: Reason for restart (for logging)
            
        Returns:
            True if restart succeeded, False otherwise
        """
        if name not in self._components:
            return False

        component = self._components[name]

        try:
            # Schedule restart through remediation engine
            action_id = await self._remediation.schedule_restart(
                component=name,
                reason=reason,
                metadata={'manual': True}
            )

            await self.emit(
                EventType.SYSTEM_INFO,
                {
                    'message': f'Scheduled restart for {name}',
                    'action_id': action_id,
                    'reason': reason
                }
            )

            # Wait for remediation to attempt restart
            await asyncio.sleep(2)

            # Check if restart succeeded
            return component.state == ComponentState.RUNNING

        except Exception as e:
            await self.emit(
                EventType.COMPONENT_ERROR,
                {'component': name, 'error': str(e)}
            )
            return False

    def get_status(self) -> Dict[str, Any]:
        """Get comprehensive agent status."""
        return {
            'heart': super().get_status(),
            'agent': {
                'mode': self.agent_config.mode,
                'uptime': self.uptime_seconds,
                'beat_count': self._beat_count,
                'last_beat': self._last_beat.isoformat() if self._last_beat else None,
            },
            'components': {
                name: component.get_status()
                for name, component in self._components.items()
            },
        }

    def get_component_states(self) -> Dict[str, str]:
        """Get state of all components."""
        return {
            name: component.state.name
            for name, component in self._components.items()
        }
