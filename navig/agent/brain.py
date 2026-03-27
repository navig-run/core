"""
Brain - AI Decision-Making Component

The Brain is the intelligence center:
- Processes input from Ears
- Analyzes data from Eyes
- Makes decisions about actions
- Creates plans and strategies
- Reasons about problems
- Learns from experience

Integrates with AI providers for advanced reasoning.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from navig.agent.component import Component
from navig.agent.config import AgentConfig, BrainConfig
from navig.agent.nervous_system import Event, EventType, NervousSystem

if TYPE_CHECKING:
    from navig.agent.soul import Soul


class ThoughtType(Enum):
    """Types of thoughts the brain can have."""

    OBSERVATION = auto()  # Noting something
    ANALYSIS = auto()  # Analyzing data
    PLAN = auto()  # Planning actions
    DECISION = auto()  # Making a decision
    REFLECTION = auto()  # Reflecting on past
    LEARNING = auto()  # Learning something new
    WARNING = auto()  # Noticing a concern
    QUESTION = auto()  # Forming a question


@dataclass
class Thought:
    """A thought from the brain."""

    type: ThoughtType
    content: str
    context: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.8
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type.name,
            "content": self.content,
            "context": self.context,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class Plan:
    """A plan of action."""

    goal: str
    steps: list[str]
    reasoning: str
    estimated_duration: str | None = None
    priority: int = 5  # 1-10
    status: str = "pending"  # pending, in_progress, completed, failed
    current_step: int = 0
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "steps": self.steps,
            "reasoning": self.reasoning,
            "estimated_duration": self.estimated_duration,
            "priority": self.priority,
            "status": self.status,
            "current_step": self.current_step,
            "created_at": self.created_at.isoformat(),
        }


@dataclass
class Decision:
    """A decision made by the brain."""

    question: str
    choice: str
    alternatives: list[str]
    reasoning: str
    confidence: float
    timestamp: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "choice": self.choice,
            "alternatives": self.alternatives,
            "reasoning": self.reasoning,
            "confidence": self.confidence,
            "timestamp": self.timestamp.isoformat(),
        }


class Brain(Component):
    """
    AI decision-making component.

    The Brain processes information and makes decisions:
    - Analyzes system state from Eyes
    - Processes user requests from Ears
    - Creates plans for complex tasks
    - Delegates execution to Hands
    - Learns from outcomes
    """

    # System prompt for the AI
    DEFAULT_SYSTEM_PROMPT = """You are an intelligent operations assistant for both systems and life management.
Your role is to help manage computer infrastructure AND personal productivity workflows.

System Operations (DevOps):
- Monitor system health (CPU, memory, disk, services)
- Execute shell commands and NAVIG operations
- Analyze logs and troubleshoot issues
- Manage databases, Docker containers, deployments

Life Operations (LifeOps):
- Track tasks, goals, and daily routines
- Search and retrieve personal knowledge
- Manage schedules and workflows
- Provide proactive reminders and suggestions

Cross-Platform Desktop Automation:
You can control the desktop on Windows, macOS, and Linux:
- `navig auto click 100 200` - Click at screen coordinates
- `navig auto type "text"` - Type text at cursor
- `navig auto open "App"` - Open an application
- `navig auto windows` - List all open windows
- `navig auto snap "App" left` - Snap window to screen half (left/right/top/bottom)
- `navig auto clipboard "text"` - Set clipboard content
- `navig auto clipboard` - Get clipboard content
- `navig auto focus` - Get currently focused window

Windows-Specific (navig ahk):
- `navig ahk generate "goal"` - AI generates automation script
- `navig ahk evolve "goal"` - AI evolves script with feedback
- `navig ahk dashboard` - Show live window dashboard
- `navig ahk snap "App" left/right` - Snap window
- `navig ahk pin "App"` - Toggle Always-On-Top
- `navig ahk screenshot` - Take a screenshot
- `navig ahk ocr` - OCR text from screen
- `navig ahk listen "^!t" "cmd"` - Register global hotkey

AI-Powered Evolution:
You can generate new automation at runtime:
- `navig evolve workflow "Open VS Code and snap right"` - Generate workflow YAML
- `navig evolve script "Backup database to S3"` - Generate Python script
- `navig evolve fix app.py "Fix the validation bug"` - Fix code with AI

Workflow Management:
- `navig workflow list` - List available workflows
- `navig workflow run <name>` - Execute a workflow
- `navig workflow run <name> --var key=value` - Execute with variables

Script Management:
- `navig script list` - List scripts
- `navig script run <name>` - Execute a script

When asked to automate tasks:
1. Check if a workflow already exists with `navig workflow list`
2. If not, generate one with `navig evolve workflow "description"`
3. Run it with `navig workflow run <name>`

Guidelines:
- Prioritize system stability and user productivity
- Explain your reasoning clearly
- Ask for confirmation before destructive actions
- Learn from past interactions
- Be proactive about both system issues and life management
- Use cross-platform automation when the user asks about GUI tasks
"""

    def __init__(
        self,
        config: BrainConfig,
        nervous_system: NervousSystem | None = None,
        agent_config: AgentConfig | None = None,
        soul: Soul | None = None,
    ):
        super().__init__("brain", nervous_system)
        self.config = config
        self.agent_config = agent_config
        self._soul = soul  # Soul component for personality injection

        # State
        self._thoughts: list[Thought] = []
        self._plans: list[Plan] = []
        self._decisions: list[Decision] = []
        self._context: dict[str, Any] = {}

        # Processing
        self._thinking = False
        self._current_plan: Plan | None = None

        # AI client
        self._ai_client = None

    def set_soul(self, soul: Soul) -> None:
        """Set the Soul component for personality injection."""
        self._soul = soul

    async def _on_start(self) -> None:
        """Initialize the brain."""
        # Subscribe to relevant events
        if self.nervous_system:
            self.nervous_system.subscribe(EventType.MESSAGE_RECEIVED, self._on_message)
            self.nervous_system.subscribe(EventType.ALERT_TRIGGERED, self._on_alert)
            self.nervous_system.subscribe(EventType.METRIC_COLLECTED, self._on_metrics)

        # Initialize AI client if possible
        try:
            from navig.ai import get_ai_client

            self._ai_client = get_ai_client()
        except ImportError:
            pass  # optional dependency not installed; feature disabled

    async def _on_stop(self) -> None:
        """Cleanup brain resources."""
        if self.nervous_system:
            self.nervous_system.unsubscribe(
                EventType.MESSAGE_RECEIVED, self._on_message
            )
            self.nervous_system.unsubscribe(EventType.ALERT_TRIGGERED, self._on_alert)
            self.nervous_system.unsubscribe(
                EventType.METRIC_COLLECTED, self._on_metrics
            )

    async def _on_health_check(self) -> dict[str, Any]:
        """Health check for brain."""
        return {
            "thoughts_count": len(self._thoughts),
            "plans_count": len(self._plans),
            "decisions_count": len(self._decisions),
            "thinking": self._thinking,
            "has_ai_client": self._ai_client is not None,
            "current_plan": self._current_plan.goal if self._current_plan else None,
        }

    async def _on_message(self, event: Event) -> None:
        """Handle incoming message."""
        message_data = event.data.get("message", {})
        content = message_data.get("content", "")
        source = message_data.get("source", "unknown")

        # Create observation thought
        thought = Thought(
            type=ThoughtType.OBSERVATION,
            content=f"Received message from {source}: {content[:100]}...",
            context={"source": source, "full_content": content},
        )
        self._record_thought(thought)

        # Process the message
        await self.think(content, context={"source": source})

    async def _on_alert(self, event: Event) -> None:
        """Handle system alert."""
        alert = event.data.get("alert", {})

        thought = Thought(
            type=ThoughtType.WARNING,
            content=f"Alert: {alert.get('message', 'Unknown alert')}",
            context=alert,
        )
        self._record_thought(thought)

        # Analyze the alert
        if alert.get("level") == "critical":
            await self.analyze_and_respond(
                f"Critical alert: {alert.get('message')}", context=alert
            )

    async def _on_metrics(self, event: Event) -> None:
        """Handle collected metrics."""
        metrics = event.data.get("metrics", {})
        self._context["last_metrics"] = metrics

    def _record_thought(self, thought: Thought) -> None:
        """Record a thought."""
        self._thoughts.append(thought)

        # Keep last 100 thoughts
        if len(self._thoughts) > 100:
            self._thoughts = self._thoughts[-100:]

    async def think(
        self,
        prompt: str,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Process a prompt and generate a response.

        This is the main thinking method that:
        1. Analyzes the input
        2. Considers context
        3. Generates a response
        4. Potentially creates plans or decisions
        """
        self._thinking = True
        context = context or {}

        try:
            # Record analysis thought
            thought = Thought(
                type=ThoughtType.ANALYSIS,
                content=f"Analyzing: {prompt[:50]}...",
                context=context,
            )
            self._record_thought(thought)

            # Build context for AI
            ai_context = self._build_context(prompt, context)

            # Get AI response if available
            response = None
            if self._ai_client:
                try:
                    response = await self._query_ai(prompt, ai_context)
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            if response:
                # Emit thought event
                await self.emit(
                    EventType.THOUGHT,
                    {
                        "prompt": prompt,
                        "response": response,
                        "context": context,
                    },
                )

            return response

        finally:
            self._thinking = False

    async def _query_ai(self, prompt: str, context: str) -> str | None:
        """Query the AI model."""
        if not self._ai_client:
            return None

        # Get system prompt - prioritize Soul's personality-enhanced prompt
        system_prompt = self.DEFAULT_SYSTEM_PROMPT

        if self._soul:
            # Use Soul's system prompt which includes SOUL.md if present
            soul_prompt = self._soul.get_system_prompt()
            if soul_prompt:
                system_prompt = f"{self.DEFAULT_SYSTEM_PROMPT}\n\n{soul_prompt}"
        elif self.agent_config and self.agent_config.personality.system_prompt:
            system_prompt = self.agent_config.personality.system_prompt

        full_prompt = f"{context}\n\nUser: {prompt}"

        try:
            response = await asyncio.to_thread(
                self._ai_client.generate,
                full_prompt,
                system=system_prompt,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
            )
            return response
        except Exception:
            return None

    def _build_context(self, prompt: str, context: dict[str, Any]) -> str:
        """Build context string for AI."""
        parts = []

        # Add system metrics if available
        if "last_metrics" in self._context:
            metrics = self._context["last_metrics"]
            parts.append(
                f"System Status: CPU {metrics.get('cpu_percent', 0):.1f}%, "
                f"Memory {metrics.get('memory_percent', 0):.1f}%, "
                f"Disk {metrics.get('disk_percent', 0):.1f}%"
            )

        # Add recent thoughts
        if self._thoughts:
            recent = self._thoughts[-5:]
            thoughts_str = "\n".join([f"- {t.content}" for t in recent])
            parts.append(f"Recent thoughts:\n{thoughts_str}")

        # Add any context passed in
        if context:
            parts.append(f"Context: {json.dumps(context, default=str)}")

        return "\n\n".join(parts)

    async def create_plan(
        self,
        goal: str,
        context: dict[str, Any] | None = None,
    ) -> Plan:
        """
        Create a plan to achieve a goal.

        Uses AI to break down the goal into steps.
        """
        context = context or {}

        # Use AI to create plan if available
        steps = []
        reasoning = "Direct execution"

        if self._ai_client:
            try:
                prompt = f"""Create a step-by-step plan to achieve this goal:

Goal: {goal}

Context: {json.dumps(context, default=str)}

Respond with a JSON object containing:
- steps: list of action strings
- reasoning: explanation of the approach
- estimated_duration: optional time estimate
"""
                response = await self._query_ai(prompt, "")

                if response:
                    # Try to parse JSON from response
                    try:
                        # Find JSON in response
                        start = response.find("{")
                        end = response.rfind("}") + 1
                        if start >= 0 and end > start:
                            data = json.loads(response[start:end])
                            steps = data.get("steps", [])
                            reasoning = data.get("reasoning", reasoning)
                    except json.JSONDecodeError:
                        # If not JSON, treat as plain steps
                        steps = [
                            line.strip()
                            for line in response.split("\n")
                            if line.strip() and not line.startswith("#")
                        ]
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        if not steps:
            steps = [goal]  # Single-step plan

        plan = Plan(
            goal=goal,
            steps=steps,
            reasoning=reasoning,
        )

        self._plans.append(plan)
        self._current_plan = plan

        # Emit plan event
        await self.emit(EventType.PLAN_CREATED, {"plan": plan.to_dict()})

        return plan

    async def make_decision(
        self,
        question: str,
        options: list[str],
        context: dict[str, Any] | None = None,
    ) -> Decision:
        """
        Make a decision between options.
        """
        context = context or {}

        # Default to first option
        choice = options[0] if options else "unknown"
        reasoning = "Default selection"
        confidence = 0.5

        if self._ai_client and len(options) > 1:
            try:
                prompt = f"""Make a decision:

Question: {question}
Options: {json.dumps(options)}
Context: {json.dumps(context, default=str)}

Respond with JSON:
- choice: selected option (must be one from the list)
- reasoning: explanation
- confidence: 0.0-1.0
"""
                response = await self._query_ai(prompt, "")

                if response:
                    try:
                        start = response.find("{")
                        end = response.rfind("}") + 1
                        if start >= 0 and end > start:
                            data = json.loads(response[start:end])
                            if data.get("choice") in options:
                                choice = data["choice"]
                            reasoning = data.get("reasoning", reasoning)
                            confidence = float(data.get("confidence", confidence))
                    except (json.JSONDecodeError, ValueError):
                        pass
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical

        decision = Decision(
            question=question,
            choice=choice,
            alternatives=[o for o in options if o != choice],
            reasoning=reasoning,
            confidence=confidence,
        )

        self._decisions.append(decision)

        # Emit decision event
        await self.emit(EventType.DECISION_MADE, {"decision": decision.to_dict()})

        return decision

    async def analyze_and_respond(
        self,
        situation: str,
        context: dict[str, Any] | None = None,
    ) -> str | None:
        """
        Analyze a situation and formulate a response.

        Used for handling alerts, errors, or complex situations.
        """
        thought = Thought(
            type=ThoughtType.ANALYSIS,
            content=f"Analyzing situation: {situation}",
            context=context or {},
        )
        self._record_thought(thought)

        response = await self.think(
            f"Analyze this situation and recommend action: {situation}", context=context
        )

        return response

    def get_thoughts(self, limit: int = 10) -> list[Thought]:
        """Get recent thoughts."""
        return self._thoughts[-limit:]

    def get_plans(self) -> list[Plan]:
        """Get all plans."""
        return self._plans

    def get_decisions(self, limit: int = 10) -> list[Decision]:
        """Get recent decisions."""
        return self._decisions[-limit:]

    def get_current_plan(self) -> Plan | None:
        """Get the current active plan."""
        return self._current_plan
