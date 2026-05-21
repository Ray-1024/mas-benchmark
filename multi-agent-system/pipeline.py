from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from langgraph.graph import END, StateGraph
from pydantic import BaseModel, ConfigDict, Field

from a2a_message import A2AMessage, MessageType
from agent import AgentDefinition, AgentRole
from runner import OpenhandsRunner
from stage_validator import StageScore, StageValidator, ValidationScore, ValidationSummary
from tools import new_uuid, utc_now
from workspace import WorkspaceManager


IGNORED_DIRS = {".git", ".mas", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


class FileSnapshotEntry(BaseModel):
    path: str
    sha256: str
    size_bytes: int
    modified_at: datetime


class WorkspaceSnapshot(BaseModel):
    snapshot_id: str = Field(default_factory=new_uuid)
    stage: str
    phase: str
    created_at: datetime = Field(default_factory=utc_now)
    files: dict[str, FileSnapshotEntry] = Field(default_factory=dict)


class FileChange(BaseModel):
    path: str
    change_type: str
    before_sha256: str | None = None
    after_sha256: str | None = None
    artifact_path: str | None = None


class StageArtifact(BaseModel):
    stage: str
    agent: str
    request_message_id: str
    before_snapshot_path: str
    after_snapshot_path: str
    changed_files: list[FileChange] = Field(default_factory=list)
    validation_scores: list[ValidationScore] = Field(default_factory=list)
    stage_score: StageScore | None = None
    manifest_path: str


class PipelineStage(BaseModel):
    role: AgentRole
    prompt: str
    required_artifacts: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)


class PipelineState(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    task: str
    workspace_root: Path
    run_id: str = Field(default_factory=new_uuid)
    messages: list[A2AMessage] = Field(default_factory=list)
    snapshots: list[WorkspaceSnapshot] = Field(default_factory=list)
    artifacts: list[StageArtifact] = Field(default_factory=list)
    validation_scores: list[ValidationScore] = Field(default_factory=list)
    validation_summary: ValidationSummary | None = None
    completed_stages: list[str] = Field(default_factory=list)


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


class LangGraphPipeline:
    def __init__(
        self,
        runner: OpenhandsRunner,
        workspace: WorkspaceManager,
        agents: dict[AgentRole, AgentDefinition] | None = None,
        stages: list[PipelineStage] | None = None,
        validation_references: dict[str, str | Path] | None = None,
        sender: str = "pipeline",
    ) -> None:
        self.runner = runner
        self.workspace = workspace
        self.agents = agents or default_agent_definitions()
        self.stages = stages or DEFAULT_STAGES
        self.stage_validator = StageValidator(
            workspace=workspace,
            validation_references=validation_references,
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
        agent = self._agent_for(stage.role)
        stage_name = stage.role.value
        request = self._send_request(state, stage, agent)
        before = self._snapshot_workspace(stage_name, "before", state.run_id)

        self.runner.run_task(agent, self._task_for_stage(state, stage, request))

        after = self._snapshot_workspace(stage_name, "after", state.run_id)
        validation_scores = self.stage_validator.validate_stage(stage)
        stage_score = self.stage_validator.summarize_stage(stage_name, validation_scores)
        artifact = self._save_changed_files(
            run_id=state.run_id,
            stage=stage,
            agent=agent,
            request=request,
            before=before,
            after=after,
            validation_scores=validation_scores,
            stage_score=stage_score,
        )

        state.messages.append(request)
        state.snapshots.extend([before, after])
        state.artifacts.append(artifact)
        state.validation_scores.extend(validation_scores)
        state.completed_stages.append(stage_name)
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

    def _send_request(
        self,
        state: PipelineState,
        stage: PipelineStage,
        agent: AgentDefinition,
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
                "available_artifacts": [artifact.model_dump(mode="json") for artifact in state.artifacts],
            },
        )
        self._append_jsonl(self._messages_path(state.run_id), message.model_dump(mode="json"))
        return message

    def _task_for_stage(
        self,
        state: PipelineState,
        stage: PipelineStage,
        request: A2AMessage,
    ) -> str:
        required = "\n".join(f"- {path}" for path in stage.required_artifacts) or "- none"
        expected = "\n".join(f"- {path}" for path in stage.expected_artifacts) or "- not specified"
        return (
            f"Pipeline request message id: {request.message_id}\n"
            f"Pipeline stage: {stage.role.value}\n\n"
            f"Original task:\n{state.task}\n\n"
            f"Stage request:\n{stage.prompt}\n\n"
            f"Required artifacts to inspect:\n{required}\n\n"
            f"Expected artifacts to create or update:\n{expected}\n\n"
            "Work inside the current workspace. Preserve unrelated user changes."
        )

    def _snapshot_workspace(self, stage: str, phase: str, run_id: str) -> WorkspaceSnapshot:
        snapshot = WorkspaceSnapshot(stage=stage, phase=phase)
        for path in self._iter_workspace_files():
            relative_path = path.relative_to(self.workspace.root).as_posix()
            stat = path.stat()
            snapshot.files[relative_path] = FileSnapshotEntry(
                path=relative_path,
                sha256=self._sha256(path),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime).astimezone(),
            )

        self._write_json(
            self._snapshot_path(run_id, stage, phase),
            snapshot.model_dump(mode="json"),
        )
        return snapshot

    def _save_changed_files(
        self,
        run_id: str,
        stage: PipelineStage,
        agent: AgentDefinition,
        request: A2AMessage,
        before: WorkspaceSnapshot,
        after: WorkspaceSnapshot,
        validation_scores: list[ValidationScore],
        stage_score: StageScore,
    ) -> StageArtifact:
        stage_dir = self._artifacts_dir(run_id) / stage.role.value
        changed_files_dir = stage_dir / "changed_files"
        changed_files_dir.mkdir(parents=True, exist_ok=True)

        changes = self._diff_snapshots(before, after)
        for change in changes:
            if change.change_type == "deleted":
                continue
            source = self.workspace.resolve(change.path)
            target = changed_files_dir / change.path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            change.artifact_path = target.relative_to(self.workspace.root).as_posix()

        manifest_path = stage_dir / "manifest.json"
        artifact = StageArtifact(
            stage=stage.role.value,
            agent=agent.name,
            request_message_id=request.message_id,
            before_snapshot_path=self._snapshot_path(run_id, stage.role.value, "before")
            .relative_to(self.workspace.root)
            .as_posix(),
            after_snapshot_path=self._snapshot_path(run_id, stage.role.value, "after")
            .relative_to(self.workspace.root)
            .as_posix(),
            changed_files=changes,
            validation_scores=validation_scores,
            stage_score=stage_score,
            manifest_path=manifest_path.relative_to(self.workspace.root).as_posix(),
        )
        self._write_json(manifest_path, artifact.model_dump(mode="json"))
        self._write_root_artifact_manifest(run_id)
        return artifact

    def _diff_snapshots(
        self,
        before: WorkspaceSnapshot,
        after: WorkspaceSnapshot,
    ) -> list[FileChange]:
        changes: list[FileChange] = []
        all_paths = sorted(set(before.files) | set(after.files))
        for path in all_paths:
            before_entry = before.files.get(path)
            after_entry = after.files.get(path)
            if before_entry is None and after_entry is not None:
                changes.append(FileChange(path=path, change_type="added", after_sha256=after_entry.sha256))
            elif before_entry is not None and after_entry is None:
                changes.append(FileChange(path=path, change_type="deleted", before_sha256=before_entry.sha256))
            elif before_entry and after_entry and before_entry.sha256 != after_entry.sha256:
                changes.append(
                    FileChange(
                        path=path,
                        change_type="modified",
                        before_sha256=before_entry.sha256,
                        after_sha256=after_entry.sha256,
                    )
                )
        return changes

    def _write_root_artifact_manifest(self, run_id: str) -> None:
        artifacts_root = self._artifacts_dir(run_id)
        manifests = sorted(
            path.relative_to(self.workspace.root).as_posix()
            for path in artifacts_root.glob("*/manifest.json")
        )
        self._write_json(
            self.workspace.resolve(".mas/artifact_manifest.json"),
            {
                "run_id": run_id,
                "updated_at": utc_now().isoformat(),
                "stage_manifests": manifests,
            },
        )

    def _iter_workspace_files(self) -> list[Path]:
        files: list[Path] = []
        for path in self.workspace.root.rglob("*"):
            if not path.is_file():
                continue
            relative_parts = path.relative_to(self.workspace.root).parts
            if any(part in IGNORED_DIRS for part in relative_parts):
                continue
            files.append(path)
        return sorted(files)

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _snapshot_path(self, run_id: str, stage: str, phase: str) -> Path:
        return self.workspace.resolve(f".mas/snapshots/{run_id}/{stage}_{phase}.json")

    def _messages_path(self, run_id: str) -> Path:
        return self.workspace.resolve(f".mas/messages/{run_id}.jsonl")

    def _artifacts_dir(self, run_id: str) -> Path:
        return self.workspace.resolve(f".mas/artifacts/{run_id}")

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
