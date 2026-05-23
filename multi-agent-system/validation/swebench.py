from __future__ import annotations

import argparse
import difflib
import json
import shlex
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from tools import Artifact, ArtifactRegister, WorkspaceManager, is_binary_file, read_json_or_jsonl


@dataclass
class SWEBenchConfig:
    enabled: bool = False
    instance_id: str | None = None
    dataset_name: str = "princeton-nlp/SWE-bench_Lite"
    split: str = "test"
    model_name_or_path: str = "mas-benchmark"
    predictions_path: str | Path | None = None
    run_id: str | None = None
    max_workers: int = 1
    timeout_seconds: int = 7200
    command: str | list[str] | None = None
    output_dir: str | Path = ".mas/swebench"
    regenerate_predictions: bool = True

    @classmethod
    def model_validate(cls, value: Any) -> "SWEBenchConfig":
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("SWE-Bench config must be a dict")
        return cls(**value)


@dataclass
class SWEBenchResult:
    status: str
    score: float | None = None
    resolved: bool | None = None
    predictions_path: str | None = None
    report_path: str | None = None
    stdout_path: str | None = None
    stderr_path: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, mode: str = "python") -> dict[str, Any]:
        return asdict(self)

    def model_dump_json(self, indent: int | None = None) -> str:
        return json.dumps(self.model_dump(), indent=indent, ensure_ascii=False)


class SWEBenchValidator:
    def __init__(
        self,
        workspace: WorkspaceManager,
        artifact_register: ArtifactRegister,
    ) -> None:
        self.workspace = workspace
        self.artifact_register = artifact_register

    def validate_stage_artifact(
        self,
        stage: Any,
        artifact: Artifact | None,
        config: SWEBenchConfig,
    ) -> dict[str, Any]:
        details: dict[str, Any] = {}
        current_snapshot: Artifact | None = None
        try:
            if artifact is not None:
                details["artifact_id"] = artifact.message_id
                details["artifact_hash"] = artifact.hash
                current_snapshot = self.artifact_register.snapshot(
                    description=(
                        "temporary snapshot before SWE-Bench validation; "
                        f"stage={stage.role.value}; artifact={artifact.message_id}"
                    ),
                )
                self.artifact_register.restore(artifact)

            result = SWEBenchRunner(config).run(self.workspace)
            result_details = result.model_dump(mode="json")
            result_details.update(details)
            return {
                "stage": stage.role.value,
                "metric": "swebench_resolved",
                "score": result.score,
                "status": result.status,
                "details": result_details,
            }
        except Exception as exc:
            return {
                "stage": stage.role.value,
                "metric": "swebench_resolved",
                "score": None,
                "status": "error",
                "details": {**details, "error": str(exc)},
            }
        finally:
            if current_snapshot is not None:
                self.artifact_register.restore(current_snapshot)


class SWEBenchRunner:
    def __init__(self, config: SWEBenchConfig) -> None:
        self.config = config

    def run(self, workspace: WorkspaceManager | Path) -> SWEBenchResult:
        workspace_root = workspace.root if isinstance(workspace, WorkspaceManager) else Path(workspace)
        workspace_root = workspace_root.resolve()
        if not self.config.enabled:
            return SWEBenchResult(status="skipped", details={"reason": "SWE-Bench validation is disabled"})
        if not self.config.instance_id:
            return SWEBenchResult(status="skipped", details={"reason": "SWE-Bench instance_id is not configured"})

        output_dir = self._resolve_output_dir(workspace_root)
        output_dir.mkdir(parents=True, exist_ok=True)
        run_id = self.config.run_id or f"mas-{self.config.instance_id}"

        predictions_path = self._predictions_path(workspace_root, output_dir)
        if self.config.regenerate_predictions or not predictions_path.exists():
            try:
                prediction_result = self._write_prediction(workspace_root, predictions_path)
                if prediction_result is not None:
                    return prediction_result
            except Exception as exc:
                return SWEBenchResult(
                    status="error",
                    score=None,
                    predictions_path=str(predictions_path),
                    details={"error": str(exc)},
                )

        command = self._command(workspace_root, predictions_path, run_id)
        stdout_path = output_dir / f"{run_id}.stdout.log"
        stderr_path = output_dir / f"{run_id}.stderr.log"
        try:
            completed = subprocess.run(
                command,
                cwd=workspace_root,
                text=True,
                capture_output=True,
                timeout=self.config.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text(exc.stderr or "", encoding="utf-8")
            return SWEBenchResult(
                status="error",
                score=None,
                predictions_path=str(predictions_path),
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                details={"error": f"SWE-Bench timed out after {self.config.timeout_seconds} seconds"},
            )
        stdout_path.write_text(completed.stdout, encoding="utf-8")
        stderr_path.write_text(completed.stderr, encoding="utf-8")

        report = self._load_report(workspace_root, run_id)
        details: dict[str, Any] = {
            "command": command,
            "returncode": completed.returncode,
            "instance_id": self.config.instance_id,
            "dataset_name": self.config.dataset_name,
            "split": self.config.split,
        }
        if report:
            details["report"] = report["payload"]

        if completed.returncode != 0:
            return SWEBenchResult(
                status="error",
                score=None,
                predictions_path=str(predictions_path),
                report_path=str(report["path"]) if report else None,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                details=details,
            )

        resolved = self._is_resolved(report["payload"] if report else {})
        return SWEBenchResult(
            status="passed" if resolved else "failed",
            score=1.0 if resolved else 0.0,
            resolved=resolved,
            predictions_path=str(predictions_path),
            report_path=str(report["path"]) if report else None,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            details=details,
        )

    def _write_prediction(self, workspace_root: Path, predictions_path: Path) -> SWEBenchResult | None:
        patch = self._workspace_patch(workspace_root)
        if not patch.strip():
            return SWEBenchResult(
                status="failed",
                score=0.0,
                resolved=False,
                predictions_path=str(predictions_path),
                details={"reason": "workspace git diff is empty"},
            )

        prediction = {
            "instance_id": self.config.instance_id,
            "model_name_or_path": self.config.model_name_or_path,
            "model_patch": patch,
        }
        predictions_path.parent.mkdir(parents=True, exist_ok=True)
        predictions_path.write_text(json.dumps(prediction, ensure_ascii=False) + "\n", encoding="utf-8")
        return None

    def _workspace_patch(self, workspace_root: Path) -> str:
        if not (workspace_root / ".git").exists():
            raise RuntimeError("SWE-Bench validation requires a git workspace or an existing predictions_path")

        parts = [
            self._git_diff(workspace_root, "--binary", "HEAD"),
            self._git_diff(workspace_root, "--cached", "--binary", "HEAD"),
            self._untracked_patch(workspace_root),
        ]
        return "\n".join(part for part in parts if part.strip())

    def _untracked_patch(self, workspace_root: Path) -> str:
        files = self._git(workspace_root, "ls-files", "--others", "--exclude-standard").splitlines()
        patches: list[str] = []
        for relative_file in files:
            if self._is_ignored_workspace_path(relative_file):
                continue
            path = workspace_root / relative_file
            if not path.is_file() or is_binary_file(path):
                continue
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            diff = difflib.unified_diff(
                [],
                lines,
                fromfile="/dev/null",
                tofile=f"b/{relative_file}",
            )
            patches.append(
                f"diff --git a/{relative_file} b/{relative_file}\n"
                "new file mode 100644\n"
                + "".join(diff)
            )
        return "\n".join(patches)

    def _command(self, workspace_root: Path, predictions_path: Path, run_id: str) -> list[str]:
        values = {
            "workspace": str(workspace_root),
            "predictions_path": str(predictions_path),
            "instance_id": self.config.instance_id or "",
            "dataset_name": self.config.dataset_name,
            "split": self.config.split,
            "run_id": run_id,
            "max_workers": str(self.config.max_workers),
        }
        if self.config.command:
            command = shlex.split(self.config.command) if isinstance(self.config.command, str) else self.config.command
            return [part.format(**values) for part in command]

        return [
            sys.executable,
            "-m",
            "swebench.harness.run_evaluation",
            "--dataset_name",
            self.config.dataset_name,
            "--split",
            self.config.split,
            "--predictions_path",
            str(predictions_path),
            "--max_workers",
            str(self.config.max_workers),
            "--run_id",
            run_id,
            "--instance_ids",
            self.config.instance_id or "",
        ]

    def _load_report(self, workspace_root: Path, run_id: str) -> dict[str, Any] | None:
        candidates = [
            workspace_root / "evaluation_results" / run_id / "results.json",
            workspace_root / "evaluation_results" / run_id / "instance_results.jsonl",
        ]
        candidates.extend((workspace_root / "evaluation_results").glob(f"**/{run_id}/results.json"))
        candidates.extend((workspace_root / "evaluation_results").glob(f"**/{run_id}/instance_results.jsonl"))
        for candidate in candidates:
            if candidate.exists():
                return {"path": candidate, "payload": self._read_report(candidate)}
        return None

    def _read_report(self, path: Path) -> Any:
        return read_json_or_jsonl(path)

    def _is_resolved(self, report: Any) -> bool:
        if isinstance(report, list):
            return any(self._is_resolved(item) for item in report)
        if not isinstance(report, dict):
            return False
        instance_id = self.config.instance_id
        if instance_id and instance_id in report and isinstance(report[instance_id], dict):
            return self._is_resolved(report[instance_id])
        for key in ("resolved", "success", "passed"):
            if key in report:
                return bool(report[key])
        for key in ("resolved_ids", "resolved_instances", "instances_resolved"):
            value = report.get(key)
            if isinstance(value, list):
                return instance_id in value
            if isinstance(value, int):
                return value > 0
        return False

    def _predictions_path(self, workspace_root: Path, output_dir: Path) -> Path:
        if self.config.predictions_path:
            path = Path(self.config.predictions_path)
            return path if path.is_absolute() else (workspace_root / path).resolve()
        return output_dir / "predictions.jsonl"

    def _resolve_output_dir(self, workspace_root: Path) -> Path:
        path = Path(self.config.output_dir)
        return path if path.is_absolute() else workspace_root / path

    def _git(self, workspace_root: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", "-C", str(workspace_root), *args],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
        return completed.stdout

    def _git_diff(self, workspace_root: Path, *args: str) -> str:
        files = self._git(workspace_root, "diff", "--name-only", *args, "--", ".").splitlines()
        files = [path for path in files if not self._is_ignored_workspace_path(path)]
        if not files:
            return ""
        return self._git(workspace_root, "diff", *args, "--", *files)

    def _is_ignored_workspace_path(self, relative_path: str) -> bool:
        return any(part in ArtifactRegister.IGNORED_DIRS for part in Path(relative_path).parts)

def main() -> None:
    parser = argparse.ArgumentParser(description="Run SWE-Bench validation for a workspace patch.")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--dataset-name", default="princeton-nlp/SWE-bench_Lite")
    parser.add_argument("--split", default="test")
    parser.add_argument("--model-name-or-path", default="mas-benchmark")
    parser.add_argument("--predictions-path", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--timeout-seconds", type=int, default=7200)
    parser.add_argument("--artifact-id", help="Restore and evaluate this ArtifactRegister snapshot before running SWE-Bench.")
    parser.add_argument("--command", help="Optional custom command. Placeholders: {workspace}, {predictions_path}, {instance_id}, {dataset_name}, {split}, {run_id}, {max_workers}.")
    args = parser.parse_args()

    config = SWEBenchConfig(
        enabled=True,
        instance_id=args.instance_id,
        dataset_name=args.dataset_name,
        split=args.split,
        model_name_or_path=args.model_name_or_path,
        predictions_path=args.predictions_path,
        run_id=args.run_id,
        max_workers=args.max_workers,
        timeout_seconds=args.timeout_seconds,
        command=args.command,
    )
    workspace = WorkspaceManager(args.workspace)
    if args.artifact_id:
        artifact_register = ArtifactRegister(workspace.root)
        artifact = artifact_register.get(args.artifact_id)
        stage = SimpleNamespace(role=SimpleNamespace(value="developer"))
        result = SWEBenchValidator(
            workspace=workspace,
            artifact_register=artifact_register,
        ).validate_stage_artifact(stage=stage, artifact=artifact, config=config)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    result = SWEBenchRunner(config).run(workspace)
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
