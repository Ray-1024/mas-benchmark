from __future__ import annotations

from pathlib import Path


class WorkspaceManager:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, relative_path: str) -> Path:
        candidate = (self.root / relative_path).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"Path escapes workspace root: {relative_path}") from exc
        return candidate

    def ensure_layout(self) -> None:
        for relative_path in ("docs", "src", "tests", ".mas"):
            self.resolve(relative_path).mkdir(parents=True, exist_ok=True)

    def list_files(self) -> list[str]:
        items: list[str] = []
        for candidate in sorted(self.root.rglob("*")):
            if candidate.is_file():
                items.append(candidate.relative_to(self.root).as_posix())
        return items
