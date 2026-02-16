"""
NAVIG Agent-to-Agent Communication Protocol

Enables multi-agent orchestration with:
- Agent discovery and registration
- Message passing between agents
- Task delegation and coordination
- Shared context and memory
"""
import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class AgentRole(Enum):
    """Agent roles in the multi-agent system."""
    COORDINATOR = "coordinator"  # Orchestrates other agents
    SPECIALIST = "specialist"    # Domain-specific expert
    WORKER = "worker"           # Executes tasks
    MONITOR = "monitor"         # Watches and reports


class MessageType(Enum):
    """Types of inter-agent messages."""
    REQUEST = "request"         # Task request
    RESPONSE = "response"       # Task response
    BROADCAST = "broadcast"     # Broadcast to all agents
    HEARTBEAT = "heartbeat"     # Health check
    HANDOFF = "handoff"         # Transfer conversation/task
    CONTEXT = "context"         # Shared context update
    ERROR = "error"             # Error notification


@dataclass
class AgentInfo:
    """Information about a registered agent."""
    agent_id: str
    name: str
    role: AgentRole
    capabilities: List[str]
    endpoint: Optional[str] = None  # HTTP/WS endpoint if remote
    metadata: Dict[str, Any] = field(default_factory=dict)
    last_heartbeat: Optional[datetime] = None
    status: str = "active"


@dataclass
class AgentMessage:
    """Message between agents."""
    message_id: str
    message_type: MessageType
    from_agent: str
    to_agent: str  # Can be "*" for broadcast
    content: Any
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    reply_to: Optional[str] = None  # For response messages
    ttl: int = 60  # Time-to-live in seconds
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return {
            "message_id": self.message_id,
            "message_type": self.message_type.value,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "content": self.content,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
            "reply_to": self.reply_to,
            "ttl": self.ttl,
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'AgentMessage':
        """Create from dictionary."""
        return cls(
            message_id=data["message_id"],
            message_type=MessageType(data["message_type"]),
            from_agent=data["from_agent"],
            to_agent=data["to_agent"],
            content=data["content"],
            context=data.get("context", {}),
            timestamp=datetime.fromisoformat(data["timestamp"]),
            reply_to=data.get("reply_to"),
            ttl=data.get("ttl", 60),
        )


@dataclass
class TaskRequest:
    """A task delegation request."""
    task_id: str
    task_type: str
    description: str
    parameters: Dict[str, Any]
    priority: int = 5  # 1-10, higher = more important
    timeout: int = 300  # seconds
    require_confirmation: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "description": self.description,
            "parameters": self.parameters,
            "priority": self.priority,
            "timeout": self.timeout,
            "require_confirmation": self.require_confirmation,
        }


@dataclass
class TaskResult:
    """Result of a delegated task."""
    task_id: str
    success: bool
    result: Any
    error: Optional[str] = None
    execution_time: float = 0.0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "success": self.success,
            "result": self.result,
            "error": self.error,
            "execution_time": self.execution_time,
        }


class AgentRegistry:
    """
    Registry for agent discovery and management.
    
    Maintains list of available agents and their capabilities.
    """
    
    def __init__(self):
        self._agents: Dict[str, AgentInfo] = {}
        self._capabilities_index: Dict[str, List[str]] = {}  # capability -> agent_ids
    
    def register(self, agent: AgentInfo) -> bool:
        """Register an agent."""
        if agent.agent_id in self._agents:
            logger.warning(f"Agent {agent.agent_id} already registered, updating")
        
        self._agents[agent.agent_id] = agent
        
        # Update capabilities index
        for cap in agent.capabilities:
            if cap not in self._capabilities_index:
                self._capabilities_index[cap] = []
            if agent.agent_id not in self._capabilities_index[cap]:
                self._capabilities_index[cap].append(agent.agent_id)
        
        logger.info(f"Registered agent: {agent.name} ({agent.agent_id})")
        return True
    
    def unregister(self, agent_id: str) -> bool:
        """Unregister an agent."""
        if agent_id not in self._agents:
            return False
        
        agent = self._agents.pop(agent_id)
        
        # Remove from capabilities index
        for cap in agent.capabilities:
            if cap in self._capabilities_index:
                self._capabilities_index[cap] = [
                    a for a in self._capabilities_index[cap] if a != agent_id
                ]
        
        logger.info(f"Unregistered agent: {agent.name} ({agent_id})")
        return True
    
    def get(self, agent_id: str) -> Optional[AgentInfo]:
        """Get agent by ID."""
        return self._agents.get(agent_id)
    
    def find_by_capability(self, capability: str) -> List[AgentInfo]:
        """Find agents with a specific capability."""
        agent_ids = self._capabilities_index.get(capability, [])
        return [self._agents[aid] for aid in agent_ids if aid in self._agents]
    
    def find_by_role(self, role: AgentRole) -> List[AgentInfo]:
        """Find agents with a specific role."""
        return [a for a in self._agents.values() if a.role == role]
    
    def list_all(self) -> List[AgentInfo]:
        """List all registered agents."""
        return list(self._agents.values())
    
    def update_heartbeat(self, agent_id: str):
        """Update agent's last heartbeat."""
        if agent_id in self._agents:
            self._agents[agent_id].last_heartbeat = datetime.now()


class MessageBus:
    """
    Async message bus for agent communication.
    
    Handles message routing, delivery, and acknowledgment.
    """
    
    def __init__(self, registry: AgentRegistry):
        self.registry = registry
        self._handlers: Dict[str, Callable] = {}  # agent_id -> message handler
        self._pending_responses: Dict[str, asyncio.Future] = {}
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
    
    def register_handler(
        self,
        agent_id: str,
        handler: Callable[[AgentMessage], asyncio.Future],
    ):
        """Register a message handler for an agent."""
        self._handlers[agent_id] = handler
    
    def unregister_handler(self, agent_id: str):
        """Unregister a message handler."""
        self._handlers.pop(agent_id, None)
    
    async def send(
        self,
        message: AgentMessage,
        wait_response: bool = False,
        timeout: float = 30.0,
    ) -> Optional[AgentMessage]:
        """
        Send a message to an agent.
        
        Args:
            message: Message to send
            wait_response: Whether to wait for a response
            timeout: Response timeout in seconds
            
        Returns:
            Response message if wait_response=True, else None
        """
        if message.to_agent == "*":
            # Broadcast
            await self._broadcast(message)
            return None
        
        if message.to_agent not in self._handlers:
            logger.warning(f"No handler for agent {message.to_agent}")
            return None
        
        if wait_response:
            # Create future for response
            future = asyncio.get_event_loop().create_future()
            self._pending_responses[message.message_id] = future
        
        # Queue message
        await self._message_queue.put(message)
        
        if wait_response:
            try:
                return await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                logger.warning(f"Timeout waiting for response to {message.message_id}")
                self._pending_responses.pop(message.message_id, None)
                return None
        
        return None
    
    async def _broadcast(self, message: AgentMessage):
        """Broadcast message to all agents."""
        for agent_id in self._handlers:
            if agent_id != message.from_agent:
                msg_copy = AgentMessage(
                    message_id=f"{message.message_id}-{agent_id}",
                    message_type=message.message_type,
                    from_agent=message.from_agent,
                    to_agent=agent_id,
                    content=message.content,
                    context=message.context,
                    timestamp=message.timestamp,
                    ttl=message.ttl,
                )
                await self._message_queue.put(msg_copy)
    
    async def _process_messages(self):
        """Process messages from queue."""
        while self._running:
            try:
                message = await asyncio.wait_for(
                    self._message_queue.get(),
                    timeout=1.0,
                )
                
                handler = self._handlers.get(message.to_agent)
                if handler:
                    try:
                        response = await handler(message)
                        
                        # Check if this is a response to a pending request
                        if message.reply_to and message.reply_to in self._pending_responses:
                            future = self._pending_responses.pop(message.reply_to)
                            if not future.done():
                                future.set_result(response)
                        
                    except Exception as e:
                        logger.error(f"Error handling message: {e}")
                
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                logger.error(f"Error in message processor: {e}")
    
    async def start(self):
        """Start the message bus."""
        self._running = True
        asyncio.create_task(self._process_messages())
        logger.info("Message bus started")
    
    async def stop(self):
        """Stop the message bus."""
        self._running = False
        logger.info("Message bus stopped")


class AgentCoordinator:
    """
    Coordinates multi-agent interactions.
    
    Features:
    - Task delegation based on capabilities
    - Load balancing across agents
    - Conversation handoffs
    - Shared context management
    """
    
    def __init__(self):
        self.registry = AgentRegistry()
        self.bus = MessageBus(self.registry)
        self._shared_context: Dict[str, Any] = {}
    
    async def start(self):
        """Start the coordinator."""
        await self.bus.start()
    
    async def stop(self):
        """Stop the coordinator."""
        await self.bus.stop()
    
    def register_agent(
        self,
        agent_id: str,
        name: str,
        role: AgentRole,
        capabilities: List[str],
        handler: Callable,
        **metadata,
    ):
        """Register an agent with the coordinator."""
        agent = AgentInfo(
            agent_id=agent_id,
            name=name,
            role=role,
            capabilities=capabilities,
            metadata=metadata,
        )
        self.registry.register(agent)
        self.bus.register_handler(agent_id, handler)
    
    def unregister_agent(self, agent_id: str):
        """Unregister an agent."""
        self.registry.unregister(agent_id)
        self.bus.unregister_handler(agent_id)
    
    async def delegate_task(
        self,
        task: TaskRequest,
        from_agent: str,
        to_agent: Optional[str] = None,
    ) -> Optional[TaskResult]:
        """
        Delegate a task to an agent.
        
        Args:
            task: Task to delegate
            from_agent: Requesting agent ID
            to_agent: Target agent ID (auto-select if None)
            
        Returns:
            Task result or None if failed
        """
        # Auto-select agent based on task type
        if to_agent is None:
            candidates = self.registry.find_by_capability(task.task_type)
            if not candidates:
                logger.warning(f"No agent found for task type: {task.task_type}")
                return TaskResult(
                    task_id=task.task_id,
                    success=False,
                    result=None,
                    error=f"No agent available for {task.task_type}",
                )
            
            # Simple selection: first available
            # Could implement load balancing here
            to_agent = candidates[0].agent_id
        
        # Create task message
        message = AgentMessage(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.REQUEST,
            from_agent=from_agent,
            to_agent=to_agent,
            content=task.to_dict(),
            ttl=task.timeout,
        )
        
        # Send and wait for response
        response = await self.bus.send(
            message,
            wait_response=True,
            timeout=float(task.timeout),
        )
        
        if response and response.content:
            return TaskResult(**response.content)
        
        return TaskResult(
            task_id=task.task_id,
            success=False,
            result=None,
            error="No response from agent",
        )
    
    async def handoff_conversation(
        self,
        from_agent: str,
        to_agent: str,
        conversation_context: Dict[str, Any],
        reason: str = "",
    ) -> bool:
        """
        Hand off a conversation to another agent.
        
        Args:
            from_agent: Current agent ID
            to_agent: Target agent ID
            conversation_context: Context to transfer
            reason: Reason for handoff
            
        Returns:
            True if handoff successful
        """
        message = AgentMessage(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.HANDOFF,
            from_agent=from_agent,
            to_agent=to_agent,
            content={
                "conversation": conversation_context,
                "reason": reason,
            },
        )
        
        response = await self.bus.send(message, wait_response=True, timeout=10.0)
        
        if response and response.message_type == MessageType.RESPONSE:
            return response.content.get("accepted", False)
        
        return False
    
    def update_shared_context(self, key: str, value: Any, from_agent: str):
        """Update shared context."""
        self._shared_context[key] = {
            "value": value,
            "updated_by": from_agent,
            "updated_at": datetime.now().isoformat(),
        }
    
    def get_shared_context(self, key: Optional[str] = None) -> Any:
        """Get shared context."""
        if key:
            return self._shared_context.get(key, {}).get("value")
        return {k: v["value"] for k, v in self._shared_context.items()}
    
    async def broadcast_context_update(self, from_agent: str):
        """Broadcast context update to all agents."""
        message = AgentMessage(
            message_id=str(uuid.uuid4()),
            message_type=MessageType.CONTEXT,
            from_agent=from_agent,
            to_agent="*",
            content=self.get_shared_context(),
        )
        await self.bus.send(message)


# Global coordinator instance
_coordinator: Optional[AgentCoordinator] = None


def get_coordinator() -> AgentCoordinator:
    """Get or create the global agent coordinator."""
    global _coordinator
    if _coordinator is None:
        _coordinator = AgentCoordinator()
    return _coordinator


async def initialize_coordinator():
    """Initialize and start the global coordinator."""
    coordinator = get_coordinator()
    await coordinator.start()
    return coordinator
