from __future__ import annotations

from pathlib import Path
from typing import Any

from pipeline.agent import AgentRole
from workspace import WorkspaceManager


BERTSCORE_ARTIFACTS = {
    AgentRole.REQUIREMENTS_ANALYST: ("docs/requirements.md", "requirements_file"),
    AgentRole.ARCHITECT: ("docs/architecture.md", "architecture_file"),
    AgentRole.IMPLEMENTATION_PLANNER: ("docs/implementation_plan.md", "implementation_plan_file"),
}


class BertScoreValidator:
    def __init__(
        self,
        workspace: WorkspaceManager,
        validation_references: dict[str, str | Path],
    ) -> None:
        self.workspace = workspace
        self.validation_references = validation_references

    def validate_stage(self, stage: Any) -> dict[str, Any] | None:
        bertscore_config = BERTSCORE_ARTIFACTS.get(stage.role)
        if bertscore_config is None:
            return None

        candidate_file, reference_key = bertscore_config
        return self.validate_artifact(stage, candidate_file, reference_key)

    def validate_artifact(self, stage: Any, candidate_file: str, reference_key: str) -> dict[str, Any]:
        candidate_path = self.workspace.resolve(candidate_file)
        reference_path = self._reference_path(reference_key)
        details: dict[str, Any] = {
            "candidate_file": candidate_path.relative_to(self.workspace.root).as_posix(),
            "reference_key": reference_key,
            "reference_file": str(reference_path) if reference_path else None,
        }

        if reference_path is None or not reference_path.exists():
            return {
                "stage": stage.role.value,
                "metric": "bertscore_f1",
                "score": None,
                "status": "missing_reference",
                "details": details,
            }
        if not candidate_path.exists():
            return {
                "stage": stage.role.value,
                "metric": "bertscore_f1",
                "score": 0.0,
                "status": "missing_candidate",
                "details": details,
            }

        try:
            from bert_score import score as bert_score

            precision, recall, f1 = bert_score(
                [candidate_path.read_text(encoding="utf-8")],
                [reference_path.read_text(encoding="utf-8")],
                lang="en",
                verbose=False,
            )
            precision_value = float(precision.mean().item())
            recall_value = float(recall.mean().item())
            f1_value = float(f1.mean().item())
            details.update({"precision": precision_value, "recall": recall_value})
            return {
                "stage": stage.role.value,
                "metric": "bertscore_f1",
                "score": f1_value,
                "status": "passed",
                "details": details,
            }
        except Exception as exc:
            details["error"] = str(exc)
            return {
                "stage": stage.role.value,
                "metric": "bertscore_f1",
                "score": None,
                "status": "error",
                "details": details,
            }

    def _reference_path(self, key: str) -> Path | None:
        raw_path = self.validation_references.get(key)
        if raw_path is None:
            return None
        path = Path(raw_path)
        if path.is_absolute():
            return path

        candidates = [
            Path.cwd() / path,
            self.workspace.root.parent.parent.parent / path,
            self.workspace.root / path,
        ]
        if path.parts and path.parts[0] == "benchmarks":
            candidates.append(Path.cwd() / "benchmark" / Path(*path.parts[1:]))
        for candidate in candidates:
            if candidate.exists():
                return candidate.resolve()
        return candidates[0].resolve()
