from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field

class CommandParameter(BaseModel):
    type: str
    description: str
    required: bool = False
    default: Optional[Any] = None
    options: Optional[List[str]] = None

class NavigCommand(BaseModel):
    name: str
    syntax: str
    description: str
    risk: str = "safe" # safe, moderate, destructive
    confirmation_required: bool = False
    confirmation_msg: Optional[str] = None
    parameters: Optional[Dict[str, CommandParameter]] = None
    source_skill: Optional[str] = None

class SkillExample(BaseModel):
    user: str
    thought: str
    command: str

class SkillManifest(BaseModel):
    name: str
    description: str
    version: str = "0.0.1"
    author: Optional[str] = None
    category: Optional[str] = "uncategorized"
    risk_level: str = Field(alias="risk-level", default="safe")
    user_invocable: bool = Field(alias="user-invocable", default=True)
    requires: List[str] = []
    tags: List[str] = []
    navig_commands: List[NavigCommand] = Field(alias="navig-commands", default=[])
    examples: List[SkillExample] = []
    
    class Config:
        populate_by_name = True

class PackStep(BaseModel):
    name: str = "unnamed-step"
    description: Optional[str] = None
    command: str
    continue_on_error: bool = False

class NavigPack(BaseModel):
    name: str
    description: str
    version: str = "1.0.0"
    author: Optional[str] = "unknown"
    type: str = "runbook" # runbook, checklist, workflow
    tags: List[str] = []
    steps: List[PackStep] = []
    
    class Config:
        populate_by_name = True
