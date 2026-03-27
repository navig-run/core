"""
NAVIG Agent Mode - Autonomous Operations Intelligence

Transforms your environment into an intelligent operations hub for both
computer systems (DevOps) and personal life management (LifeOps).
Uses a human-body-inspired architecture for clear separation of concerns.

Components:
- Brain: AI decision-making, reasoning, personality
- Eyes: Monitoring, observation, metrics collection
- Ears: Input listeners (Telegram, MCP, webhooks, API)
- Hands: Execution layer, command runner, file ops
- Heart: Core service orchestrator, keeps everything alive
- NervousSystem: Event bus, inter-component communication
- Memory: Context retention, conversation history
- Soul: Personality configuration, behavioral rules

Capabilities:
- System Ops: Server management, Docker, databases, monitoring
- Life Ops: Task tracking, knowledge search, routine automation

Usage:
    # Install agent mode
    navig agent install --personality friendly

    # Start the agent
    navig agent start

    # Check status
    navig agent status

    # Stop the agent
    navig agent stop
"""

from navig.agent.brain import Brain, Decision, Plan, Thought
from navig.agent.component import Component, ComponentState, HealthStatus
from navig.agent.config import AgentConfig, PersonalityConfig
from navig.agent.ears import Ears, InputMessage
from navig.agent.eyes import Alert, Eyes, SystemMetrics
from navig.agent.goals import Goal, GoalPlanner, GoalState, Subtask, SubtaskState
from navig.agent.hands import CommandResult, CommandStatus, Hands
from navig.agent.heart import Heart
from navig.agent.nervous_system import Event, EventPriority, EventType, NervousSystem
from navig.agent.runner import Agent, run_agent
from navig.agent.soul import Mood, PersonalityProfile, Soul

__all__ = [
    # Main Entry
    "Agent",
    "run_agent",
    # Configuration
    "AgentConfig",
    "PersonalityConfig",
    # Components
    "Component",
    "ComponentState",
    "HealthStatus",
    # Event System
    "NervousSystem",
    "Event",
    "EventType",
    "EventPriority",
    # Goal Planning
    "GoalPlanner",
    "Goal",
    "GoalState",
    "Subtask",
    "SubtaskState",
    # Body Parts
    "Heart",
    "Eyes",
    "SystemMetrics",
    "Alert",
    "Ears",
    "InputMessage",
    "Hands",
    "CommandResult",
    "CommandStatus",
    "Brain",
    "Thought",
    "Plan",
    "Decision",
    "Soul",
    "PersonalityProfile",
    "Mood",
]
