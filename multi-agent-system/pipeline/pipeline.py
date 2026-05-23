from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from artifact import Artifact, ArtifactRegister
from pipeline.a2a_message import A2AMessage, MessageType
from pipeline.benchmark import ValidationMethodConfig
from pipeline.runner import OpenhandsRunner
from pipeline.sdlc import AgentDefinition, AgentRole, DEFAULT_STAGES, PipelineStage, default_agent_definitions
from tools import new_uuid, utc_now
from validation.stage_validator import StageScore, StageValidator, ValidationScore, ValidationSummary
from workspace import WorkspaceManager


class PipelineMode(str, Enum):
    SINGLE_AGENT_BASELINE = "single_agent_baseline"
    MULTI_AGENT_BASELINE = "multi_agent_baseline"
    MULTI_AGENT_WITH_ARTIFACTS = "multi_agent_with_artifacts"
    MULTI_AGENT_WITH_RECOVERY = "multi_agent_with_recovery"


class PipelineState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: str
    workspace_root: Path
    run_id: str = Field(default_factory=new_uuid)
    messages: list[A2AMessage] = Field(default_factory=list)
    snapshots: list[Artifact] = Field(default_factory=list)
    artifacts: list[Artifact] = Field(default_factory=list)
    validation_scores: list[ValidationScore] = Field(default_factory=list)
    stage_scores: list[StageScore] = Field(default_factory=list)
    validation_summary: ValidationSummary | None = None
    completed_stages: list[str] = Field(default_factory=list)


class LangGraphPipeline:
    def __init__(
        self,
        runner: OpenhandsRunner,
        workspace: WorkspaceManager,
        agents: dict[AgentRole, AgentDefinition] | None = None,
        stages: list[PipelineStage] | None = None,
        validation_references: dict[str, str | Path] | None = None,
        stage_validations: dict[AgentRole, list[ValidationMethodConfig]] | None = None,
        mode: PipelineMode | str = PipelineMode.MULTI_AGENT_BASELINE,
        single_agent_role: AgentRole = AgentRole.DEVELOPER,
        recovery_threshold: float = 0.8,
        max_restarts: int = 5,
        sender: str = "pipeline",
    ) -> None:
        self.runner = runner
        self.workspace = workspace
        self.agents = agents or default_agent_definitions()
        self.stages = stages or DEFAULT_STAGES
        self.mode = PipelineMode(mode)
        self.single_agent_role = single_agent_role
        self.recovery_threshold = recovery_threshold
        self.max_restarts = max_restarts
        self.artifact_register = ArtifactRegister(workspace.root)
        self.stage_validator = StageValidator(
            workspace=workspace,
            artifact_register=self.artifact_register,
            validation_references=validation_references,
            stage_validations=stage_validations,
        )
        self.sender = sender
        self.app = self._build_graph()

    def run(self, task: str, run_id: str | None = None) -> PipelineState:
        self.workspace.ensure_layout()
        initial_state = PipelineState(
            task=task,
            workspace_root=self.workspace.root,
            run_id=run_id or new_uuid(),
        )
        result = self.app.invoke(initial_state)
        if isinstance(result, PipelineState):
            return result
        return PipelineState.model_validate(result)

    def _build_graph(self) -> Any:
        graph = StateGraph(PipelineState)
        previous_node: str | None = None

        for index, stage in enumerate(self.stages):
            node_name = self._node_name(index, stage)
            graph.add_node(node_name, self._make_stage_node(stage))
            if previous_node is None:
                graph.set_entry_point(node_name)
            else:
                graph.add_edge(previous_node, node_name)
            previous_node = node_name

        if previous_node is None:
            raise ValueError("Pipeline must contain at least one stage")

        graph.add_edge(previous_node, END)
        return graph.compile()

    def _make_stage_node(self, stage: PipelineStage) -> Callable[[PipelineState | dict[str, Any]], dict[str, Any]]:
        def node(state: PipelineState | dict[str, Any]) -> dict[str, Any]:
            return self._execute_stage(state, stage)

        return node

    def _execute_stage(self, state: PipelineState | dict[str, Any], stage: PipelineStage) -> dict[str, Any]:
        state = self._coerce_state(state)
        agent = self._agent_for_stage(stage)
        stage_name = stage.role.value
        before = self._snapshot_workspace(stage_name, "before", state.run_id, attempt=1)
        state.snapshots.append(before)
        retry_feedback: list[dict[str, Any]] = []
        attempt = 1

        while True:
            request = self._send_request(state, stage, agent, attempt=attempt)
            self.runner.run_task(
                agent,
                self._task_for_stage(
                    state=state,
                    stage=stage,
                    request=request,
                    attempt=attempt,
                    retry_feedback=retry_feedback,
                ),
            )

            after = self._snapshot_workspace(stage_name, "after", state.run_id, attempt=attempt)
            validation_scores = self.stage_validator.validate_stage(stage, artifact=after)
            self._annotate_scores(validation_scores, attempt)
            stage_score = self.stage_validator.summarize_stage(stage_name, validation_scores)

            state.messages.append(request)
            state.snapshots.append(after)
            state.validation_scores.extend(validation_scores)
            state.stage_scores.append(stage_score)
            self._write_root_artifact_manifest(state.run_id, state.artifacts, state.snapshots)

            if not self._should_recover(stage_score, attempt):
                state.artifacts.append(after)
                break

            retry_feedback.append(
                {
                    "attempt": attempt,
                    "score": stage_score.model_dump(mode="json"),
                    "validation_scores": [score.model_dump(mode="json") for score in validation_scores],
                }
            )
            self.artifact_register.restore(before)
            attempt += 1

        state.completed_stages.append(stage_name)
        self._write_root_artifact_manifest(state.run_id, state.artifacts, state.snapshots)
        state.validation_summary = self.stage_validator.write_final_report(
            run_id=state.run_id,
            completed_stages=state.completed_stages,
            validation_scores=state.validation_scores,
            artifacts=state.artifacts,
        )
        return state.model_dump(mode="python")

    def _coerce_state(self, state: PipelineState | dict[str, Any]) -> PipelineState:
        if isinstance(state, PipelineState):
            return state
        return PipelineState.model_validate(state)

    def _agent_for(self, role: AgentRole) -> AgentDefinition:
        try:
            return self.agents[role]
        except KeyError as exc:
            raise ValueError(f"No agent definition configured for role: {role.value}") from exc

    def _agent_for_stage(self, stage: PipelineStage) -> AgentDefinition:
        if self.mode == PipelineMode.SINGLE_AGENT_BASELINE:
            return self._agent_for(self.single_agent_role)
        return self._agent_for(stage.role)

    def _send_request(
        self,
        state: PipelineState,
        stage: PipelineStage,
        agent: AgentDefinition,
        attempt: int,
    ) -> A2AMessage:
        message = A2AMessage(
            sender=self.sender,
            recipient=agent.name,
            message_type=MessageType.REQUEST,
            topic=f"run-stage:{stage.role.value}",
            body={
                "task": state.task,
                "stage": stage.model_dump(mode="json"),
                "completed_stages": state.completed_stages,
                "pipeline_mode": self.mode.value,
                "attempt": attempt,
                "available_artifacts": self._available_artifacts_for_message(state),
            },
        )
        self._append_jsonl(self._messages_path(state.run_id), message.model_dump(mode="json"))
        return message

    def _task_for_stage(
        self,
        state: PipelineState,
        stage: PipelineStage,
        request: A2AMessage,
        attempt: int,
        retry_feedback: list[dict[str, Any]],
    ) -> str:
        required = "\n".join(f"- {path}" for path in stage.required_artifacts) or "- none"
        expected = "\n".join(f"- {path}" for path in stage.expected_artifacts) or "- not specified"
        artifact_feedback = self._artifact_feedback_for_prompt(state)
        retry_context = self._retry_feedback_for_prompt(retry_feedback)
        return (
            f"Pipeline request message id: {request.message_id}\n"
            f"Pipeline mode: {self.mode.value}\n"
            f"Pipeline stage: {stage.role.value}\n"
            f"Stage attempt: {attempt}\n\n"
            f"Original task:\n{state.task}\n\n"
            f"Stage request:\n{stage.prompt}\n\n"
            f"Required artifacts to inspect:\n{required}\n\n"
            f"Expected artifacts to create or update:\n{expected}\n\n"
            f"{artifact_feedback}"
            f"{retry_context}"
            "Work inside the current workspace. Preserve unrelated user changes."
        )

    def _snapshot_workspace(self, stage: str, phase: str, run_id: str, attempt: int) -> Artifact:
        return self.artifact_register.snapshot(
            description=f"run_id={run_id}; stage={stage}; phase={phase}; attempt={attempt}",
        )

    def _write_root_artifact_manifest(
        self,
        run_id: str,
        artifacts: list[Artifact],
        snapshots: list[Artifact],
    ) -> None:
        self._write_json(
            self.workspace.resolve(".mas/artifact_manifest.json"),
            {
                "run_id": run_id,
                "pipeline_mode": self.mode.value,
                "updated_at": utc_now().isoformat(),
                "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
                "snapshots": [snapshot.model_dump(mode="json") for snapshot in snapshots],
            },
        )

    def _available_artifacts_for_message(self, state: PipelineState) -> list[dict[str, Any]]:
        if self.mode in {
            PipelineMode.MULTI_AGENT_WITH_ARTIFACTS,
            PipelineMode.MULTI_AGENT_WITH_RECOVERY,
        }:
            return [artifact.model_dump(mode="json") for artifact in state.artifacts]
        return []

    def _artifact_feedback_for_prompt(self, state: PipelineState) -> str:
        if self.mode not in {
            PipelineMode.MULTI_AGENT_WITH_ARTIFACTS,
            PipelineMode.MULTI_AGENT_WITH_RECOVERY,
        }:
            return ""
        if not state.validation_scores:
            return ""

        lines = ["Previous artifact validation results:"]
        for score in state.validation_scores[-8:]:
            score_text = "n/a" if score.score is None else f"{score.score:.4f}"
            details = json.dumps(score.details, ensure_ascii=False)
            lines.append(
                f"- stage={score.stage}; metric={score.metric}; "
                f"score={score_text}; status={score.status}; details={details}"
            )
        return "\n".join(lines) + "\n\n"

    def _retry_feedback_for_prompt(self, retry_feedback: list[dict[str, Any]]) -> str:
        if not retry_feedback:
            return ""

        lines = [
            "Previous attempts for this stage failed validation. "
            "Use this feedback to correct the next attempt:"
        ]
        for item in retry_feedback:
            lines.append(f"- attempt={item['attempt']}; stage_score={json.dumps(item['score'], ensure_ascii=False)}")
            for score in item["validation_scores"]:
                lines.append(f"  validation={json.dumps(score, ensure_ascii=False)}")
        return "\n".join(lines) + "\n\n"

    def _annotate_scores(self, scores: list[ValidationScore], attempt: int) -> None:
        for score in scores:
            score.details["attempt"] = attempt

    def _should_recover(self, stage_score: StageScore, attempt: int) -> bool:
        if self.mode != PipelineMode.MULTI_AGENT_WITH_RECOVERY:
            return False
        if attempt > self.max_restarts:
            return False
        if stage_score.score is None:
            return False
        return stage_score.score < self.recovery_threshold

    def _messages_path(self, run_id: str) -> Path:
        return self.workspace.resolve(f".mas/messages/{run_id}.jsonl")

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    def _append_jsonl(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False))
            file.write("\n")

    def _node_name(self, index: int, stage: PipelineStage) -> str:
        return f"{index:02d}_{stage.role.value}"
