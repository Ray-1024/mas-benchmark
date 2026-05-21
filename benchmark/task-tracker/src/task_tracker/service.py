from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any

from .errors import AuthenticationError, AuthorizationError, ConflictError, NotFoundError, ValidationError
from .models import ActivityLog, Board, Column, Comment, Label, Priority, Task, User, now_utc
from .security import create_token, hash_password, verify_password, verify_token
from .store import InMemoryStore


class TaskTrackerService:
    def __init__(self, store: InMemoryStore | None = None, secret_key: str = "dev-secret-change-me") -> None:
        self.store = store or InMemoryStore()
        self.secret_key = secret_key

    def register_user(self, email: str, username: str, password: str) -> dict[str, Any]:
        self._require(email, "email")
        self._require(username, "username")
        if len(password) < 8:
            raise ValidationError("Password must contain at least 8 characters")
        if any(user.email == email and user.deleted_at is None for user in self.store.users.values()):
            raise ConflictError("Email already registered")
        if any(user.username == username and user.deleted_at is None for user in self.store.users.values()):
            raise ConflictError("Username already registered")

        user = User(
            id=self.store.next_id("users"),
            email=email,
            username=username,
            password_hash=hash_password(password),
        )
        self.store.users[user.id] = user
        return self._public_user(user)

    def login(self, email: str, password: str) -> dict[str, Any]:
        user = next((item for item in self.store.users.values() if item.email == email and item.deleted_at is None), None)
        if user is None or not verify_password(password, user.password_hash):
            raise AuthenticationError("Invalid email or password")
        return {"access_token": create_token(user.id, self.secret_key), "token_type": "bearer"}

    def user_from_token(self, token: str) -> dict[str, Any]:
        return self._public_user(self._user(verify_token(token, self.secret_key)))

    def update_profile(self, user_id: int, email: str | None = None, username: str | None = None) -> dict[str, Any]:
        user = self._user(user_id)
        if email:
            user.email = email
        if username:
            user.username = username
        user.updated_at = now_utc()
        return self._public_user(user)

    def change_password(self, user_id: int, old_password: str, new_password: str) -> None:
        user = self._user(user_id)
        if not verify_password(old_password, user.password_hash):
            raise AuthenticationError("Invalid current password")
        if len(new_password) < 8:
            raise ValidationError("Password must contain at least 8 characters")
        user.password_hash = hash_password(new_password)
        user.updated_at = now_utc()

    def create_board(self, user_id: int, title: str, description: str = "") -> dict[str, Any]:
        self._require(title, "title")
        board = Board(
            id=self.store.next_id("boards"),
            owner_id=user_id,
            title=title,
            description=description,
            member_ids={user_id},
        )
        self.store.boards[board.id] = board
        return self._board_payload(board)

    def list_boards(self, user_id: int, offset: int = 0, limit: int = 20) -> list[dict[str, Any]]:
        boards = [
            board
            for board in self.store.boards.values()
            if board.deleted_at is None and user_id in board.member_ids
        ]
        return [self._board_payload(board) for board in sorted(boards, key=lambda item: item.id)[offset : offset + limit]]

    def get_board(self, user_id: int, board_id: int) -> dict[str, Any]:
        board = self._board_for_user(user_id, board_id)
        payload = self._board_payload(board)
        payload["columns"] = [self._column_payload(column) for column in self._columns_for_board(board.id)]
        for column_payload in payload["columns"]:
            column_payload["tasks"] = [
                self._task_payload(task)
                for task in self._tasks_for_column(column_payload["id"])
            ]
        return payload

    def update_board(self, user_id: int, board_id: int, title: str | None = None, description: str | None = None) -> dict[str, Any]:
        board = self._board_for_owner(user_id, board_id)
        if title is not None:
            self._require(title, "title")
            board.title = title
        if description is not None:
            board.description = description
        board.updated_at = now_utc()
        return self._board_payload(board)

    def delete_board(self, user_id: int, board_id: int) -> None:
        board = self._board_for_owner(user_id, board_id)
        board.deleted_at = now_utc()

    def add_board_member(self, owner_id: int, board_id: int, user_id: int) -> dict[str, Any]:
        board = self._board_for_owner(owner_id, board_id)
        self._user(user_id)
        board.member_ids.add(user_id)
        board.updated_at = now_utc()
        return self._board_payload(board)

    def create_column(self, user_id: int, board_id: int, name: str, position: int | None = None) -> dict[str, Any]:
        self._board_for_user(user_id, board_id)
        self._require(name, "name")
        columns = self._columns_for_board(board_id)
        column = Column(
            id=self.store.next_id("columns"),
            board_id=board_id,
            name=name,
            position=len(columns) if position is None else position,
        )
        self.store.columns[column.id] = column
        self._normalize_column_positions(board_id)
        return self._column_payload(column)

    def update_column(self, user_id: int, column_id: int, name: str | None = None, position: int | None = None) -> dict[str, Any]:
        column = self._column_for_user(user_id, column_id)
        if name is not None:
            self._require(name, "name")
            column.name = name
        if position is not None:
            column.position = position
            self._normalize_column_positions(column.board_id)
        column.updated_at = now_utc()
        return self._column_payload(column)

    def delete_column(self, user_id: int, column_id: int, move_tasks_to: int | None = None) -> None:
        column = self._column_for_user(user_id, column_id)
        tasks = self._tasks_for_column(column.id)
        if tasks and move_tasks_to is None:
            raise ValidationError("move_tasks_to is required when deleting a column with tasks")
        if move_tasks_to is not None:
            target = self._column_for_user(user_id, move_tasks_to)
            for task in tasks:
                task.column_id = target.id
                task.position = len(self._tasks_for_column(target.id))
        column.deleted_at = now_utc()
        self._normalize_column_positions(column.board_id)

    def reorder_columns(self, user_id: int, board_id: int, column_ids: list[int]) -> list[dict[str, Any]]:
        self._board_for_user(user_id, board_id)
        existing = {column.id for column in self._columns_for_board(board_id)}
        if set(column_ids) != existing:
            raise ValidationError("column_ids must include every active column exactly once")
        for index, column_id in enumerate(column_ids):
            self.store.columns[column_id].position = index
        return [self._column_payload(column) for column in self._columns_for_board(board_id)]

    def create_task(
        self,
        user_id: int,
        column_id: int,
        title: str,
        description: str = "",
        due_date: datetime | None = None,
        priority: str | Priority = Priority.MEDIUM,
        assignee_id: int | None = None,
        labels: list[str] | None = None,
    ) -> dict[str, Any]:
        column = self._column_for_user(user_id, column_id)
        self._require(title, "title")
        task = Task(
            id=self.store.next_id("tasks"),
            column_id=column.id,
            title=title,
            description=description,
            due_date=due_date,
            priority=self._priority(priority),
            assignee_id=assignee_id,
            position=len(self._tasks_for_column(column.id)),
        )
        self.store.tasks[task.id] = task
        for label_name in labels or []:
            task.label_ids.add(self._label(label_name).id)
        self._log(user_id, task.id, "task_created", None, title)
        return self._task_payload(task)

    def update_task(self, user_id: int, task_id: int, **changes: Any) -> dict[str, Any]:
        task = self._task_for_user(user_id, task_id)
        before = self._task_payload(task)
        for field_name in ("title", "description", "due_date", "assignee_id"):
            if field_name in changes and changes[field_name] is not None:
                setattr(task, field_name, changes[field_name])
        if "priority" in changes and changes["priority"] is not None:
            task.priority = self._priority(changes["priority"])
        task.updated_at = now_utc()
        self._log(user_id, task.id, "task_updated", str(before), str(self._task_payload(task)))
        return self._task_payload(task)

    def delete_task(self, user_id: int, task_id: int) -> None:
        task = self._task_for_user(user_id, task_id)
        task.deleted_at = now_utc()
        self._normalize_task_positions(task.column_id)
        self._log(user_id, task.id, "task_deleted", task.title, None)

    def move_task(self, user_id: int, task_id: int, target_column_id: int, position: int | None = None) -> dict[str, Any]:
        task = self._task_for_user(user_id, task_id)
        old_column_id = task.column_id
        target = self._column_for_user(user_id, target_column_id)
        task.column_id = target.id
        task.position = len(self._tasks_for_column(target.id)) if position is None else position
        self._normalize_task_positions(old_column_id)
        self._normalize_task_positions(target.id)
        task.updated_at = now_utc()
        self._log(user_id, task.id, "task_moved", str(old_column_id), str(target.id))
        return self._task_payload(task)

    def reorder_tasks(self, user_id: int, column_id: int, task_ids: list[int]) -> list[dict[str, Any]]:
        self._column_for_user(user_id, column_id)
        existing = {task.id for task in self._tasks_for_column(column_id)}
        if set(task_ids) != existing:
            raise ValidationError("task_ids must include every active task in the column exactly once")
        for index, task_id in enumerate(task_ids):
            self.store.tasks[task_id].position = index
        return [self._task_payload(task) for task in self._tasks_for_column(column_id)]

    def search_tasks(
        self,
        user_id: int,
        board_id: int,
        query: str | None = None,
        priority: str | Priority | None = None,
        assignee_id: int | None = None,
        labels: list[str] | None = None,
        sort_by: str = "created_at",
    ) -> list[dict[str, Any]]:
        self._board_for_user(user_id, board_id)
        column_ids = {column.id for column in self._columns_for_board(board_id)}
        tasks = [task for task in self.store.tasks.values() if task.deleted_at is None and task.column_id in column_ids]
        if query:
            needle = query.lower()
            tasks = [task for task in tasks if needle in task.title.lower() or needle in task.description.lower()]
        if priority:
            selected_priority = self._priority(priority)
            tasks = [task for task in tasks if task.priority == selected_priority]
        if assignee_id:
            tasks = [task for task in tasks if task.assignee_id == assignee_id]
        if labels:
            label_ids = {self._label(label).id for label in labels}
            tasks = [task for task in tasks if label_ids.issubset(task.label_ids)]
        if sort_by not in {"created_at", "due_date", "priority"}:
            raise ValidationError("Unsupported sort field")
        tasks.sort(key=lambda task: getattr(task, sort_by) or datetime.max.replace(tzinfo=now_utc().tzinfo))
        return [self._task_payload(task) for task in tasks]

    def add_comment(self, user_id: int, task_id: int, content: str) -> dict[str, Any]:
        self._task_for_user(user_id, task_id)
        self._require(content, "content")
        comment = Comment(id=self.store.next_id("comments"), task_id=task_id, user_id=user_id, content=content)
        self.store.comments[comment.id] = comment
        self._log(user_id, task_id, "comment_added", None, content)
        return self._payload(comment)

    def list_comments(self, user_id: int, task_id: int) -> list[dict[str, Any]]:
        self._task_for_user(user_id, task_id)
        return [
            self._payload(comment)
            for comment in self.store.comments.values()
            if comment.task_id == task_id and comment.deleted_at is None
        ]

    def add_label(self, user_id: int, task_id: int, name: str, color: str = "#808080") -> dict[str, Any]:
        task = self._task_for_user(user_id, task_id)
        label = self._label(name, color)
        task.label_ids.add(label.id)
        task.updated_at = now_utc()
        self._log(user_id, task.id, "label_added", None, name)
        return self._task_payload(task)

    def activity_for_task(self, user_id: int, task_id: int) -> list[dict[str, Any]]:
        self._task_for_user(user_id, task_id)
        return [
            self._payload(activity)
            for activity in self.store.activities.values()
            if activity.task_id == task_id
        ]

    def health(self) -> dict[str, str]:
        return {"status": "ok"}

    def _user(self, user_id: int) -> User:
        user = self.store.users.get(user_id)
        if user is None or user.deleted_at is not None:
            raise NotFoundError("User not found")
        return user

    def _board_for_user(self, user_id: int, board_id: int) -> Board:
        board = self.store.boards.get(board_id)
        if board is None or board.deleted_at is not None:
            raise NotFoundError("Board not found")
        if user_id not in board.member_ids:
            raise AuthorizationError("Board access denied")
        return board

    def _board_for_owner(self, user_id: int, board_id: int) -> Board:
        board = self._board_for_user(user_id, board_id)
        if board.owner_id != user_id:
            raise AuthorizationError("Only the board owner can perform this action")
        return board

    def _column_for_user(self, user_id: int, column_id: int) -> Column:
        column = self.store.columns.get(column_id)
        if column is None or column.deleted_at is not None:
            raise NotFoundError("Column not found")
        self._board_for_user(user_id, column.board_id)
        return column

    def _task_for_user(self, user_id: int, task_id: int) -> Task:
        task = self.store.tasks.get(task_id)
        if task is None or task.deleted_at is not None:
            raise NotFoundError("Task not found")
        self._column_for_user(user_id, task.column_id)
        return task

    def _columns_for_board(self, board_id: int) -> list[Column]:
        return sorted(
            [column for column in self.store.columns.values() if column.board_id == board_id and column.deleted_at is None],
            key=lambda column: column.position,
        )

    def _tasks_for_column(self, column_id: int) -> list[Task]:
        return sorted(
            [task for task in self.store.tasks.values() if task.column_id == column_id and task.deleted_at is None],
            key=lambda task: task.position,
        )

    def _normalize_column_positions(self, board_id: int) -> None:
        for index, column in enumerate(self._columns_for_board(board_id)):
            column.position = index

    def _normalize_task_positions(self, column_id: int) -> None:
        for index, task in enumerate(self._tasks_for_column(column_id)):
            task.position = index

    def _label(self, name: str, color: str = "#808080") -> Label:
        self._require(name, "label")
        for label in self.store.labels.values():
            if label.name == name:
                return label
        label = Label(id=self.store.next_id("labels"), name=name, color=color)
        self.store.labels[label.id] = label
        return label

    def _log(self, user_id: int, task_id: int | None, action: str, old_value: str | None, new_value: str | None) -> None:
        activity = ActivityLog(
            id=self.store.next_id("activities"),
            user_id=user_id,
            task_id=task_id,
            action=action,
            old_value=old_value,
            new_value=new_value,
        )
        self.store.activities[activity.id] = activity

    def _public_user(self, user: User) -> dict[str, Any]:
        payload = self._payload(user)
        payload.pop("password_hash", None)
        return payload

    def _board_payload(self, board: Board) -> dict[str, Any]:
        payload = self._payload(board)
        payload["member_ids"] = sorted(board.member_ids)
        return payload

    def _column_payload(self, column: Column) -> dict[str, Any]:
        return self._payload(column)

    def _task_payload(self, task: Task) -> dict[str, Any]:
        payload = self._payload(task)
        payload["priority"] = task.priority.value
        payload["label_ids"] = sorted(task.label_ids)
        payload["labels"] = [self._payload(self.store.labels[label_id]) for label_id in sorted(task.label_ids)]
        return payload

    def _payload(self, item: Any) -> dict[str, Any]:
        if not is_dataclass(item):
            return dict(item)
        payload = asdict(item)
        for key, value in list(payload.items()):
            if isinstance(value, datetime):
                payload[key] = value.isoformat()
        return payload

    def _priority(self, value: str | Priority) -> Priority:
        if isinstance(value, Priority):
            return value
        try:
            return Priority(value)
        except ValueError as exc:
            raise ValidationError("Priority must be Low, Medium, or High") from exc

    def _require(self, value: str, field_name: str) -> None:
        if not value or not value.strip():
            raise ValidationError(f"{field_name} is required")
