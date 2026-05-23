from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any


def read_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_json_or_jsonl(path: Path) -> Any:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(payload, ensure_ascii=False))
        file.write("\n")


def read_text_input(text: str | None, file_path: Path | None) -> str | None:
    if text:
        return text
    if file_path:
        return file_path.read_text(encoding="utf-8")
    return None


def resolve_project_path(value: str | Path, project_root: Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path

    candidates = [project_root / path]
    if path.parts and path.parts[0] == "benchmarks":
        candidates.append(project_root / "benchmark" / Path(*path.parts[1:]))
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return candidates[0].resolve()


def prepare_workspace_copy(source: Path, workspace_path: Path, *, explicit_workspace: bool) -> None:
    if explicit_workspace:
        workspace_path.mkdir(parents=True, exist_ok=True)
        return
    if not source.exists():
        raise FileNotFoundError(f"Benchmark workspace does not exist: {source}")
    if workspace_path.exists():
        raise FileExistsError(f"Run workspace already exists: {workspace_path}")

    workspace_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, workspace_path)


def is_binary_file(path: Path, sample_size: int = 4096) -> bool:
    return b"\0" in path.read_bytes()[:sample_size]
