from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Union


# =============================================================================
# Stream Events
# =============================================================================

class EventPhase(str, Enum):
    STATUS = "status"
    CHUNK = "chunk"
    FINAL = "final"
    ERROR = "error"


@dataclass(frozen=True)
class StreamStatus:
    step: str
    detail: str = ""
    progress: int = 0
    
    @property
    def phase(self) -> EventPhase:
        return EventPhase.STATUS


@dataclass(frozen=True)
class StreamChunk:
    chunk: str

    @property
    def phase(self) -> EventPhase:
        return EventPhase.CHUNK


@dataclass(frozen=True)
class StreamFinal:
    output: Any

    @property
    def phase(self) -> EventPhase:
        return EventPhase.FINAL


@dataclass(frozen=True)
class StreamError:
    message: str
    code: str = "error"

    @property
    def phase(self) -> EventPhase:
        return EventPhase.ERROR


ExecutionEvent = Union[StreamStatus, StreamChunk, StreamFinal, StreamError]
EventCallback = Callable[[ExecutionEvent], None]


# =============================================================================
# Execution Context & Requests
# =============================================================================

@dataclass
class ExecutionContext:
    """Normalized environment context for tool execution."""
    session_id: str = ""
    agent_id: str = ""
    cwd: str = ""
    env: Dict[str, str] = field(default_factory=dict)
    owner_only: bool = False
    

@dataclass
class ExecutionRequest:
    """Bundled invocation encapsulating the tool call and rules."""
    tool_name: str
    args: Dict[str, Any]
    context: ExecutionContext = field(default_factory=ExecutionContext)
    timeout_s: float = 120.0
    cancellation_token: Optional[asyncio.Event] = None
    request_id: str = ""
    lane: str = "main"

    @property
    def is_cancelled(self) -> bool:
        return self.cancellation_token.is_set() if self.cancellation_token else False


# =============================================================================
# Execution Result
# =============================================================================

class EndState(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ExecutionResult:
    """Strict, state-machine output of the execution loop."""
    state: EndState
    output: Any = None
    error: Optional[str] = None
    elapsed_ms: float = 0.0


# =============================================================================
# Tool & Skill Specifications
# =============================================================================

@dataclass
class ToolSpec:
    """Declarative schema for a tool's inputs, outputs, and requirements."""
    id: str
    name: str = ""
    description: str = ""
    domain: str = "system"
    parameters: Dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False
    owner_only: bool = False

    def get_meta(self) -> Dict[str, Any]:
        """Returns JSON schema representation compatible with existing router."""
        return {
            "id": self.id,
            "name": self.name or self.id,
            "description": self.description,
            "parameters": self.parameters,
            "ownerOnly": self.owner_only,
            "domain": self.domain,
        }

    def validate_args(self, args: Dict[str, Any]) -> bool:
        # Simplistic validation stub until JSON Schema engine is ready
        return True


@dataclass
class SkillSpec:
    """A capability grouping containing environment constraints and execution directives."""
    id: str
    name: str
    description: str
    tools: List[ToolSpec] = field(default_factory=list)
    version: str = "1.0.0"
