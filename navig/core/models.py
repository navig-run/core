from typing import Any

from pydantic import BaseModel, Field


class CommandParameter(BaseModel):
    type: str
    description: str
    required: bool = False
    default: Any | None = None
    options: list[str] | None = None


class NavigCommand(BaseModel):
    name: str
    syntax: str
    description: str
    risk: str = "safe"  # safe, moderate, destructive
    confirmation_required: bool = False
    confirmation_msg: str | None = None
    parameters: dict[str, CommandParameter] | None = None
    source_skill: str | None = None


class SkillExample(BaseModel):
    user: str
    thought: str
    command: str


class SkillManifest(BaseModel):
    name: str
    description: str
    version: str = "0.0.1"
    author: str | None = None
    category: str | None = "uncategorized"
    risk_level: str = Field(alias="risk-level", default="safe")
    user_invocable: bool = Field(alias="user-invocable", default=True)
    requires: list[str] = []
    tags: list[str] = []
    navig_commands: list[NavigCommand] = Field(alias="navig-commands", default=[])
    examples: list[SkillExample] = []

    class Config:
        populate_by_name = True


class PackStep(BaseModel):
    name: str = "unnamed-step"
    description: str | None = None
    command: str
    continue_on_error: bool = False


class NavigPack(BaseModel):
    name: str
    description: str
    version: str = "1.0.0"
    author: str | None = "unknown"
    type: str = "runbook"  # runbook, checklist, workflow
    tags: list[str] = []
    steps: list[PackStep] = []

    class Config:
        populate_by_name = True
