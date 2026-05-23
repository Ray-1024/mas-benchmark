from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


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


class PipelineStage(BaseModel):
    role: AgentRole
    prompt: str
    required_artifacts: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)


DEFAULT_STAGES = [
    PipelineStage(
        role=AgentRole.REQUIREMENTS_ANALYST,
        prompt="Analyze the task and produce or update requirements artifacts.",
        expected_artifacts=["docs/requirements.md"],
    ),
    PipelineStage(
        role=AgentRole.ARCHITECT,
        prompt="Design the solution architecture using the requirements and workspace context.",
        required_artifacts=["docs/requirements.md"],
        expected_artifacts=["docs/architecture.md"],
    ),
    PipelineStage(
        role=AgentRole.IMPLEMENTATION_PLANNER,
        prompt="Create or update the implementation plan from the requirements and architecture.",
        required_artifacts=["docs/requirements.md", "docs/architecture.md"],
        expected_artifacts=["docs/implementation_plan.md"],
    ),
    PipelineStage(
        role=AgentRole.DEVELOPER,
        prompt="Implement the requested changes according to the available artifacts.",
        required_artifacts=["docs/implementation_plan.md"],
    ),
    PipelineStage(
        role=AgentRole.TEST_ENGINEER,
        prompt="Add or update tests and run relevant verification.",
    ),
    PipelineStage(
        role=AgentRole.DEFECT_REPAIRER,
        prompt="Repair any defects found during testing without reverting unrelated work.",
    ),
    PipelineStage(
        role=AgentRole.RELEASE_MANAGER,
        prompt="Prepare final delivery artifacts and summarize the completed work.",
        expected_artifacts=["docs/delivery_report.md", ".mas/artifact_manifest.json"],
    ),
]


def default_agent_definitions() -> dict[AgentRole, AgentDefinition]:
    return {
        role: AgentDefinition(
            name=role.value,
            system_prompt=(
                f"You are the {role.value.replace('_', ' ')} in a multi-agent SDLC pipeline. "
                "Work only in the provided workspace, respect existing files, and leave concise artifacts."
            ),
        )
        for role in AgentRole
    }
