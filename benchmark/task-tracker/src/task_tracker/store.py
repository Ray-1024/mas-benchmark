from __future__ import annotations

from collections import defaultdict
from itertools import count

from .models import ActivityLog, Board, Column, Comment, Label, Task, User


class InMemoryStore:
    def __init__(self) -> None:
        self.users: dict[int, User] = {}
        self.boards: dict[int, Board] = {}
        self.columns: dict[int, Column] = {}
        self.tasks: dict[int, Task] = {}
        self.comments: dict[int, Comment] = {}
        self.labels: dict[int, Label] = {}
        self.activities: dict[int, ActivityLog] = {}
        self._ids = defaultdict(lambda: count(1))

    def next_id(self, entity: str) -> int:
        return next(self._ids[entity])
