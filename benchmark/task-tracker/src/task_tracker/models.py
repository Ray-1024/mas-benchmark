from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Priority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


@dataclass
class User:
    id: int
    email: str
    username: str
    password_hash: str
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    deleted_at: datetime | None = None


@dataclass
class Board:
    id: int
    owner_id: int
    title: str
    description: str = ""
    member_ids: set[int] = field(default_factory=set)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    deleted_at: datetime | None = None


@dataclass
class Column:
    id: int
    board_id: int
    name: str
    position: int
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    deleted_at: datetime | None = None


@dataclass
class Label:
    id: int
    name: str
    color: str = "#808080"


@dataclass
class Task:
    id: int
    column_id: int
    title: str
    description: str = ""
    due_date: datetime | None = None
    priority: Priority = Priority.MEDIUM
    assignee_id: int | None = None
    position: int = 0
    label_ids: set[int] = field(default_factory=set)
    created_at: datetime = field(default_factory=now_utc)
    updated_at: datetime = field(default_factory=now_utc)
    deleted_at: datetime | None = None


@dataclass
class Comment:
    id: int
    task_id: int
    user_id: int
    content: str
    created_at: datetime = field(default_factory=now_utc)
    deleted_at: datetime | None = None


@dataclass
class ActivityLog:
    id: int
    user_id: int
    task_id: int | None
    action: str
    old_value: str | None = None
    new_value: str | None = None
    created_at: datetime = field(default_factory=now_utc)
