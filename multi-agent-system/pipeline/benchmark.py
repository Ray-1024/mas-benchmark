from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pipeline.agent import AgentRole

class BenchmarkType(str, Enum):
    BUGFIX = "bugfix"
    FEATURE = "feature"
    REFACTORING = "refactoring"
    TEST_GENERATION = "test_generation"
    REGRESSION_RECOVERY = "regression_recovery"
    UNKNOWN = "unknown"


class ValidationMethod(str, Enum):
    EXPECTED_ARTIFACTS = "expected_artifacts"
    BERTSCORE = "bertscore"
    LLM_JUDGE = "llm_judge"
    SWEBENCH = "swebench"
    LINTER = "linter"
    TEST_COVERAGE = "test_coverage"
    CUSTOM = "custom"


class ValidationMethodConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    method: ValidationMethod
    enabled: bool = True
    weight: float | None = None
    artifacts: list[str] = Field(default_factory=list)
    prompt: str | None = None
    reference_key: str | None = None
    command: str | list[str] | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class StageValidationConfig(BaseModel):
    stage: AgentRole
    methods: list[ValidationMethodConfig] = Field(default_factory=list)

    def enabled_methods(self) -> list[ValidationMethodConfig]:
        return [method for method in self.methods if method.enabled]


class Benchmark(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    benchmark_type: BenchmarkType = BenchmarkType.UNKNOWN
    task: str | None = None
    task_file: Path | None = None
    reference_response: dict[str, str | Path] = Field(default_factory=dict)
    stage_validations: list[StageValidationConfig] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def validation_for_stage(self, stage: AgentRole) -> StageValidationConfig | None:
        for validation in self.stage_validations:
            if validation.stage == stage:
                return validation
        return None

    def validation_methods_for_stage(self, stage: AgentRole) -> list[ValidationMethodConfig]:
        validation = self.validation_for_stage(stage)
        if validation is None:
            return []
        return validation.enabled_methods()
