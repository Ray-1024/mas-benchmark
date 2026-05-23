from __future__ import annotations


def score_text(score: float | None) -> str:
    return "n/a" if score is None else f"{score:.4f}"


def markdown_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
