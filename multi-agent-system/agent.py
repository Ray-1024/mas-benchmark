from __future__ import annotations

from pathlib import Path
from enum import Enum

from pydantic import BaseModel

class AgentRole(str, Enum):
    REQUIREMENTS_ANALYST = "requirements_analyst"
    ARCHITECT = "architect"
    IMPLEMENTATION_PLANNER = "implementation_planner"
    DEVELOPER = "developer"
    TEST_ENGINEER = "test_engineer"
    DEFECT_REPAIRER = "defect_repairer"
    RELEASE_MANAGER = "release_manager"

class AgentDefinition(BaseModel):
    name: str
    system_prompt: str