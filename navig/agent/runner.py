"""
Agent Runner - Main Entry Point

Provides the main agent lifecycle:
- Initialize all components
- Start the agent
- Run the main loop
- Handle graceful shutdown
"""

from __future__ import annotations

import asyncio
import signal
import sys
from datetime import datetime
from typing import Any

from navig.agent.brain import Brain
from navig.agent.config import AgentConfig
from navig.agent.ears import Ears
from navig.agent.eyes import Eyes
from navig.agent.goals import GoalPlanner
from navig.agent.hands import Hands
from navig.agent.heart import Heart
from navig.agent.nervous_system import EventType, NervousSystem
from navig.agent.soul import Soul


class Agent:
    """
    The complete autonomous agent.

    Combines all components into a unified system:
    - NervousSystem: Event coordination
    - Heart: Component orchestration
    - Brain: Decision making
    - Eyes: System monitoring
    - Ears: Input reception
    - Hands: Command execution
    - Soul: Personality
    """

    def __init__(self, config: AgentConfig | None = None):
        self.config = config or AgentConfig.load()

        # Create nervous system first
        self.nervous_system = NervousSystem()

        # Create components
        self.heart = Heart(
            config=self.config.heart,
            nervous_system=self.nervous_system,
            agent_config=self.config,
        )

        self.brain = Brain(
            config=self.config.brain,
            nervous_system=self.nervous_system,
            agent_config=self.config,
        )

        self.eyes = Eyes(
            config=self.config.eyes,
            nervous_system=self.nervous_system,
        )

        self.ears = Ears(
            config=self.config.ears,
            nervous_system=self.nervous_system,
        )

        self.hands = Hands(
            config=self.config.hands,
            nervous_system=self.nervous_system,
        )

        self.soul = Soul(
            config=self.config.personality,
            nervous_system=self.nervous_system,
        )

        # Connect Soul to Brain for personality injection
        self.brain.set_soul(self.soul)

        # Register components with heart
        self.heart.register_component("brain", self.brain)
        self.heart.register_component("eyes", self.eyes)
        self.heart.register_component("ears", self.ears)
        self.heart.register_component("hands", self.hands)
        self.heart.register_component("soul", self.soul)

        # Create and attach goal planner
        self.goal_planner = GoalPlanner(
            storage_dir=self.config.workspace,
        )
        self.heart.set_goal_planner(self.goal_planner)

        # State
        self._running = False
        self._started_at: datetime | None = None
        self._main_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the agent."""
        if self._running:
            return

        self._running = True
        self._started_at = datetime.now()

        # Ensure workspace exists
        self.config.workspace.mkdir(parents=True, exist_ok=True)

        # Start the heart (which starts all components)
        await self.heart.start()

        # Start main processing loop
        self._main_task = asyncio.create_task(self._main_loop())

    async def stop(self) -> None:
        """Stop the agent gracefully."""
        if not self._running:
            return

        self._running = False

        # Cancel main loop
        if self._main_task:
            self._main_task.cancel()
            try:
                await self._main_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        # Stop heart (which stops all components)
        await self.heart.stop()

    async def _main_loop(self) -> None:
        """Main agent processing loop."""
        while self._running:
            try:
                # Check for incoming messages
                if self.ears.has_messages():
                    message = await self.ears.get_next_message(timeout=0.1)
                    if message:
                        await self._process_message(message)

                # Small delay to prevent busy-waiting
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                break
            except Exception:
                # Log but continue
                await asyncio.sleep(1)

    async def _process_message(self, message) -> None:
        """Process an incoming message."""
        content = message.content
        source = message.source

        # Get response from brain
        response = await self.brain.think(
            content,
            context={
                "source": source,
                "user_id": message.user_id,
            },
        )

        if response:
            # Format with personality
            formatted = self.soul.format_response(response)

            # Emit response event
            await self.nervous_system.emit(
                EventType.RESPONSE_GENERATED,
                source="agent",
                data={
                    "response": formatted,
                    "original_message": message.to_dict(),
                },
            )

    def get_status(self) -> dict[str, Any]:
        """Get agent status."""
        return {
            "running": self._running,
            "started_at": self._started_at.isoformat() if self._started_at else None,
            "uptime_seconds": (
                (datetime.now() - self._started_at).total_seconds()
                if self._started_at
                else 0
            ),
            "mode": self.config.mode,
            "personality": self.config.personality.profile,
            "components": self.heart.get_component_states(),
        }

    @property
    def is_running(self) -> bool:
        return self._running


async def run_agent(config: AgentConfig | None = None) -> None:
    """
    Run the agent as a foreground process.

    Sets up signal handlers for graceful shutdown.
    """
    agent = Agent(config)

    # Setup signal handlers
    loop = asyncio.get_running_loop()

    def signal_handler():
        print("\nShutting down agent...")
        asyncio.create_task(agent.stop())

    if sys.platform != "win32":
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, signal_handler)

    try:
        print("Starting NAVIG Agent...")
        print(f"  Mode: {agent.config.mode}")
        print(f"  Personality: {agent.config.personality.profile}")
        print(f"  Workspace: {agent.config.workspace}")
        print()

        await agent.start()

        greeting = agent.soul.get_greeting()
        print(f"Agent: {greeting}")
        print()
        print("Press Ctrl+C to stop.")
        print()

        # Keep running until stopped
        while agent.is_running:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        print("\nInterrupted.")
    finally:
        if agent.is_running:
            await agent.stop()

        farewell = agent.soul.get_farewell()
        print(f"\nAgent: {farewell}")


def main():
    """Entry point for agent."""
    try:
        asyncio.run(run_agent())
    except KeyboardInterrupt:
        pass  # user interrupted; clean exit


if __name__ == "__main__":
    main()
