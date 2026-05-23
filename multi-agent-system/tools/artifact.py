from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

from tools import utils


class Artifact(BaseModel):
    message_id: str = Field(default_factory=utils.new_id)
    created_at: datetime = Field(default_factory=utils.utc_now)
    version: int = 1
    workspace_path: Path
    hash: str
    size_bytes: int
    description: str


class ArtifactRegister:
    IGNORED_DIRS = {
        ".git",
        ".mas",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
    }
    METADATA_FILE = "artifact.json"

    def __init__(self, workspace_path: str | Path) -> None:
        self.workspace_path = Path(workspace_path).resolve()
        self.workspace_path.mkdir(parents=True, exist_ok=True)
        self.artifacts_path = self.workspace_path / ".mas" / "artifacts"
        self.artifacts_path.mkdir(parents=True, exist_ok=True)

    def snapshot(self, description: str = "") -> Artifact:
        artifact = Artifact(
            workspace_path=self.workspace_path,
            hash="",
            size_bytes=0,
            description=description,
        )
        artifact_path = self._artifact_path(artifact.message_id)
        files_path = artifact_path / "files"
        files_path.mkdir(parents=True, exist_ok=False)

        for source in self._iter_workspace_files():
            relative_path = source.relative_to(self.workspace_path)
            target = files_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        artifact.hash = self._hash_directory(files_path)
        artifact.size_bytes = self._directory_size(files_path)
        self._write_artifact(artifact_path, artifact)
        return artifact

    def make_snapshot(self, description: str = "") -> Artifact:
        return self.snapshot(description)

    def save(self, description: str = "") -> Artifact:
        return self.snapshot(description)

    def restore(self, artifact: str | Artifact, *, clean: bool = True) -> Artifact:
        stored_artifact = self.get(artifact.message_id if isinstance(artifact, Artifact) else artifact)
        files_path = self._artifact_path(stored_artifact.message_id) / "files"
        if not files_path.exists():
            raise FileNotFoundError(f"Artifact files not found: {files_path}")

        if clean:
            self._clean_workspace(files_path)

        for source in self._iter_files(files_path):
            relative_path = source.relative_to(files_path)
            target = self.workspace_path / relative_path
            self._ensure_inside_workspace(target)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        return stored_artifact

    def restore_artifact(self, artifact: str | Artifact, *, clean: bool = True) -> Artifact:
        return self.restore(artifact, clean=clean)

    def get(self, artifact_id: str) -> Artifact:
        artifact_path = self._artifact_path(artifact_id)
        metadata_path = artifact_path / self.METADATA_FILE
        if not metadata_path.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_id}")
        return Artifact.model_validate_json(metadata_path.read_text(encoding="utf-8"))

    def list(self) -> list[Artifact]:
        artifacts: list[Artifact] = []
        for metadata_path in sorted(self.artifacts_path.glob(f"*/{self.METADATA_FILE}")):
            artifacts.append(Artifact.model_validate_json(metadata_path.read_text(encoding="utf-8")))
        return artifacts

    def _artifact_path(self, artifact_id: str) -> Path:
        path = (self.artifacts_path / artifact_id).resolve()
        try:
            path.relative_to(self.artifacts_path)
        except ValueError as exc:
            raise ValueError(f"Artifact id escapes artifacts directory: {artifact_id}") from exc
        return path

    def _iter_workspace_files(self) -> list[Path]:
        return self._iter_files(self.workspace_path, ignored_dirs=self.IGNORED_DIRS)

    def _iter_files(self, root: Path, ignored_dirs: set[str] | None = None) -> list[Path]:
        ignored_dirs = ignored_dirs or set()
        files: list[Path] = []
        for path in root.rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            relative_parts = path.relative_to(root).parts
            if any(part in ignored_dirs for part in relative_parts):
                continue
            files.append(path)
        return sorted(files)

    def _clean_workspace(self, snapshot_files_path: Path) -> None:
        snapshot_paths = {
            path.relative_to(snapshot_files_path)
            for path in self._iter_files(snapshot_files_path)
        }
        for path in reversed(self._iter_workspace_files()):
            relative_path = path.relative_to(self.workspace_path)
            if relative_path in snapshot_paths:
                continue
            path.unlink()

        for directory in sorted(
            (path for path in self.workspace_path.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        ):
            relative_parts = directory.relative_to(self.workspace_path).parts
            if any(part in self.IGNORED_DIRS for part in relative_parts):
                continue
            try:
                directory.rmdir()
            except OSError:
                pass

    def _hash_directory(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in self._iter_files(root):
            relative_path = path.relative_to(root).as_posix()
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            with path.open("rb") as file:
                for chunk in iter(lambda: file.read(1024 * 1024), b""):
                    digest.update(chunk)
            digest.update(b"\0")
        return digest.hexdigest()

    def _directory_size(self, root: Path) -> int:
        return sum(path.stat().st_size for path in self._iter_files(root))

    def _write_artifact(self, artifact_path: Path, artifact: Artifact) -> None:
        metadata_path = artifact_path / self.METADATA_FILE
        metadata_path.write_text(
            json.dumps(artifact.model_dump(mode="json"), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _ensure_inside_workspace(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.workspace_path)
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace root: {path}") from exc
