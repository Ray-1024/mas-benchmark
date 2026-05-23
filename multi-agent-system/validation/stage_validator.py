from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from artifact import Artifact, ArtifactRegister
from pipeline.agent import AgentRole
from pipeline.benchmark import ValidationMethod, ValidationMethodConfig
from validation.bertscore import BertScoreValidator
from validation.swebench import SWEBenchConfig, SWEBenchValidator
from workspace import WorkspaceManager


DEFAULT_STAGE_WEIGHTS = {
    AgentRole.REQUIREMENTS_ANALYST.value: 0.20,
    AgentRole.ARCHITECT.value: 0.15,
    AgentRole.IMPLEMENTATION_PLANNER.value: 0.15,
    AgentRole.DEVELOPER.value: 0.25,
    AgentRole.TEST_ENGINEER.value: 0.15,
    AgentRole.DEFECT_REPAIRER.value: 0.05,
    AgentRole.RELEASE_MANAGER.value: 0.05,
}


class ValidationScore(BaseModel):
    stage: str
    metric: str
    score: float | None = None
    status: str
    details: dict[str, Any] = Field(default_factory=dict)


class StageScore(BaseModel):
    stage: str
    score: float | None = None
    weight: float
    weighted_score: float | None = None
    status: str
    metrics: list[ValidationScore] = Field(default_factory=list)


class ValidationSummary(BaseModel):
    stage_scores: list[StageScore] = Field(default_factory=list)
    weighted_score: float | None = None
    total_weight: float = 0.0


class StageValidator:
    def __init__(
        self,
        workspace: WorkspaceManager,
        artifact_register: ArtifactRegister | None = None,
        validation_references: dict[str, str | Path] | None = None,
        stage_validations: dict[AgentRole, list[ValidationMethodConfig]] | None = None,
        stage_weights: dict[str, float] | None = None,
    ) -> None:
        self.workspace = workspace
        self.artifact_register = artifact_register or ArtifactRegister(workspace.root)
        self.validation_references = validation_references or {}
        self.stage_validations = stage_validations or {}
        self.stage_weights = stage_weights or DEFAULT_STAGE_WEIGHTS
        self.bertscore_validator = BertScoreValidator(
            workspace=workspace,
            validation_references=self.validation_references,
        )
        self.swebench_validator = SWEBenchValidator(
            workspace=workspace,
            artifact_register=self.artifact_register,
        )

    def validate_stage(self, stage: Any, artifact: Artifact | None = None) -> list[ValidationScore]:
        methods = self._methods_for_stage(stage)
        scores: list[ValidationScore] = []
        for method_config in methods:
            score = self._validate_with_method(stage, artifact, method_config)
            if score is not None:
                scores.append(score)
        return scores

    def summarize(self, scores: list[ValidationScore]) -> ValidationSummary:
        by_stage: dict[str, list[ValidationScore]] = {}
        for score in scores:
            by_stage.setdefault(score.stage, []).append(score)

        stage_scores = [
            self._stage_score(stage, stage_scores)
            for stage, stage_scores in by_stage.items()
        ]
        total_weight = sum(item.weight for item in stage_scores if item.score is not None)
        if total_weight == 0:
            weighted_score = None
        else:
            weighted_score = sum(item.weighted_score or 0.0 for item in stage_scores) / total_weight

        return ValidationSummary(
            stage_scores=stage_scores,
            weighted_score=weighted_score,
            total_weight=total_weight,
        )

    def summarize_stage(self, stage: str, scores: list[ValidationScore]) -> StageScore:
        return self._stage_score(stage, scores)

    def write_final_report(
        self,
        run_id: str,
        completed_stages: list[str],
        validation_scores: list[ValidationScore],
        artifacts: list[Any],
    ) -> ValidationSummary:
        summary = self.summarize(validation_scores)
        report_path = self.workspace.resolve(".mas/final_report.md")
        lines = [
            "# Final Report",
            "",
            f"- Run ID: `{run_id}`",
            f"- Completed stages: {len(completed_stages)}",
            f"- Weighted score: `{self._score_text(summary.weighted_score)}`",
            "",
            "## Stage Scores",
            "",
            "| Stage | Score | Weight | Weighted | Status |",
            "| --- | ---: | ---: | ---: | --- |",
        ]
        for stage_score in summary.stage_scores:
            lines.append(
                "| "
                f"{self._markdown_table_cell(stage_score.stage)} | "
                f"{self._score_text(stage_score.score)} | "
                f"{stage_score.weight:.4f} | "
                f"{self._score_text(stage_score.weighted_score)} | "
                f"{self._markdown_table_cell(stage_score.status)} |"
            )

        lines.extend(
            [
                "",
                "## Metric Scores",
                "",
                "| Stage | Metric | Score | Status | Details |",
                "| --- | --- | ---: | --- | --- |",
            ]
        )
        for score in validation_scores:
            detail_text = self._markdown_table_cell(json.dumps(score.details, ensure_ascii=False))
            lines.append(
                "| "
                f"{self._markdown_table_cell(score.stage)} | "
                f"{self._markdown_table_cell(score.metric)} | "
                f"{self._score_text(score.score)} | "
                f"{self._markdown_table_cell(score.status)} | "
                f"`{detail_text}` |"
            )

        lines.extend(["", "## Artifacts", ""])
        for artifact in artifacts:
            artifact_id = getattr(artifact, "message_id", "unknown")
            description = getattr(artifact, "description", "")
            hash_value = getattr(artifact, "hash", "")
            label = description or artifact_id
            lines.append(f"- `{self._markdown_table_cell(label)}`: `{artifact_id}` (`{hash_value}`)")

        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return summary

    def _stage_score(self, stage: str, scores: list[ValidationScore]) -> StageScore:
        numeric_scores = [score.score for score in scores if score.score is not None]
        if not numeric_scores:
            score = None
            status = "skipped"
        else:
            score = sum(numeric_scores) / len(numeric_scores)
            status = "passed" if all(item.status in {"passed", "skipped"} for item in scores) else "failed"

        weight = self.stage_weights.get(stage, 0.0)
        return StageScore(
            stage=stage,
            score=score,
            weight=weight,
            weighted_score=None if score is None else score * weight,
            status=status,
            metrics=scores,
        )

    def _expected_artifacts_score(self, stage: Any) -> ValidationScore:
        expected = stage.expected_artifacts
        if not expected:
            return ValidationScore(
                stage=stage.role.value,
                metric="expected_artifacts",
                score=None,
                status="skipped",
                details={"reason": "stage has no expected artifacts"},
            )

        present = [path for path in expected if self.workspace.resolve(path).exists()]
        missing = sorted(set(expected) - set(present))
        return ValidationScore(
            stage=stage.role.value,
            metric="expected_artifacts",
            score=len(present) / len(expected),
            status="passed" if not missing else "failed",
            details={"present": present, "missing": missing},
        )

    def _methods_for_stage(self, stage: Any) -> list[ValidationMethodConfig]:
        configured = self.stage_validations.get(stage.role)
        if configured is not None:
            return configured

        methods = [ValidationMethodConfig(method=ValidationMethod.EXPECTED_ARTIFACTS)]
        if stage.role in {
            AgentRole.REQUIREMENTS_ANALYST,
            AgentRole.ARCHITECT,
            AgentRole.IMPLEMENTATION_PLANNER,
        }:
            methods.append(ValidationMethodConfig(method=ValidationMethod.BERTSCORE))
        if stage.role == AgentRole.DEVELOPER:
            methods.append(ValidationMethodConfig(method=ValidationMethod.SWEBENCH))
        return methods

    def _validate_with_method(
        self,
        stage: Any,
        artifact: Artifact | None,
        method_config: ValidationMethodConfig,
    ) -> ValidationScore | None:
        if method_config.method == ValidationMethod.EXPECTED_ARTIFACTS:
            return self._expected_artifacts_score(stage)
        if method_config.method == ValidationMethod.BERTSCORE:
            bertscore_score = self.bertscore_validator.validate_stage(stage)
            return None if bertscore_score is None else ValidationScore.model_validate(bertscore_score)
        if method_config.method == ValidationMethod.SWEBENCH:
            return self._swebench_score(stage, artifact, method_config)
        if method_config.method in {ValidationMethod.LLM_JUDGE, ValidationMethod.LINTER, ValidationMethod.TEST_COVERAGE}:
            return ValidationScore(
                stage=stage.role.value,
                metric=method_config.method.value,
                score=None,
                status="skipped",
                details={"reason": "validation method is configured but not wired into StageValidator yet"},
            )
        return ValidationScore(
            stage=stage.role.value,
            metric=method_config.method.value,
            score=None,
            status="skipped",
            details={"reason": "unknown validation method"},
        )

    def _swebench_score(
        self,
        stage: Any,
        artifact: Artifact | None,
        method_config: ValidationMethodConfig | None = None,
    ) -> ValidationScore:
        swebench_config = None if method_config is None else method_config.config
        swebench_config = swebench_config or self.validation_references.get("swebench") or self.validation_references.get("swe_bench")
        if not swebench_config:
            return ValidationScore(
                stage=stage.role.value,
                metric="swebench_resolved",
                score=None,
                status="skipped",
                details={"reason": "SWE-Bench config is not provided"},
            )

        try:
            config = SWEBenchConfig.model_validate(swebench_config)
            result = self.swebench_validator.validate_stage_artifact(stage=stage, artifact=artifact, config=config)
            return ValidationScore.model_validate(result)
        except Exception as exc:
            return ValidationScore(
                stage=stage.role.value,
                metric="swebench_resolved",
                score=None,
                status="error",
                details={"error": str(exc)},
            )

    def _score_text(self, score: float | None) -> str:
        return "n/a" if score is None else f"{score:.4f}"

    def _markdown_table_cell(self, value: str) -> str:
        return value.replace("|", "\\|").replace("\n", " ")
