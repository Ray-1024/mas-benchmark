from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from .errors import TaskTrackerError
from .service import TaskTrackerService


app = FastAPI(title="Kanban Task Tracker", version="1.0.0")
service = TaskTrackerService()


class RegisterRequest(BaseModel):
    email: str
    username: str
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    email: str
    password: str


class ProfileUpdateRequest(BaseModel):
    email: str | None = None
    username: str | None = None


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class BoardRequest(BaseModel):
    title: str
    description: str = ""


class ColumnRequest(BaseModel):
    name: str
    position: int | None = None


class ColumnDeleteRequest(BaseModel):
    move_tasks_to: int | None = None


class ReorderColumnsRequest(BaseModel):
    column_ids: list[int]


class TaskCreateRequest(BaseModel):
    title: str
    description: str = ""
    due_date: datetime | None = None
    priority: str = "Medium"
    assignee_id: int | None = None
    labels: list[str] = Field(default_factory=list)


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    due_date: datetime | None = None
    priority: str | None = None
    assignee_id: int | None = None


class TaskMoveRequest(BaseModel):
    target_column_id: int
    position: int | None = None


class ReorderTasksRequest(BaseModel):
    column_id: int
    task_ids: list[int]


class CommentRequest(BaseModel):
    content: str


class LabelRequest(BaseModel):
    name: str
    color: str = "#808080"


def current_user_id(authorization: str = Header(default="")) -> int:
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    return handle_errors(lambda: service.user_from_token(authorization.removeprefix("Bearer "))["id"])


def handle_errors(operation: Any) -> Any:
    try:
        return operation()
    except TaskTrackerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/health")
def health() -> dict[str, str]:
    return service.health()


@app.post("/api/v1/auth/register", status_code=201)
def register(payload: RegisterRequest) -> dict[str, Any]:
    return handle_errors(lambda: service.register_user(payload.email, payload.username, payload.password))


@app.post("/api/v1/auth/login")
def login(payload: LoginRequest) -> dict[str, Any]:
    return handle_errors(lambda: service.login(payload.email, payload.password))


@app.post("/api/v1/auth/logout")
def logout(_: int = Depends(current_user_id)) -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/v1/users/me")
def get_profile(user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service._public_user(service._user(user_id)))


@app.put("/api/v1/users/me")
def update_profile(payload: ProfileUpdateRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.update_profile(user_id, payload.email, payload.username))


@app.post("/api/v1/users/me/change-password", status_code=204)
def change_password(payload: PasswordChangeRequest, user_id: int = Depends(current_user_id)) -> None:
    return handle_errors(lambda: service.change_password(user_id, payload.old_password, payload.new_password))


@app.post("/api/v1/boards", status_code=201)
def create_board(payload: BoardRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.create_board(user_id, payload.title, payload.description))


@app.get("/api/v1/boards")
def list_boards(
    user_id: int = Depends(current_user_id),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> list[dict[str, Any]]:
    return handle_errors(lambda: service.list_boards(user_id, offset, limit))


@app.get("/api/v1/boards/{board_id}")
def get_board(board_id: int, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.get_board(user_id, board_id))


@app.put("/api/v1/boards/{board_id}")
def update_board(board_id: int, payload: BoardRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.update_board(user_id, board_id, payload.title, payload.description))


@app.delete("/api/v1/boards/{board_id}", status_code=204)
def delete_board(board_id: int, user_id: int = Depends(current_user_id)) -> None:
    return handle_errors(lambda: service.delete_board(user_id, board_id))


@app.post("/api/v1/boards/{board_id}/members/{member_id}")
def add_member(board_id: int, member_id: int, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.add_board_member(user_id, board_id, member_id))


@app.post("/api/v1/boards/{board_id}/columns", status_code=201)
def create_column(board_id: int, payload: ColumnRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.create_column(user_id, board_id, payload.name, payload.position))


@app.put("/api/v1/columns/{column_id}")
def update_column(column_id: int, payload: ColumnRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.update_column(user_id, column_id, payload.name, payload.position))


@app.delete("/api/v1/columns/{column_id}", status_code=204)
def delete_column(column_id: int, payload: ColumnDeleteRequest, user_id: int = Depends(current_user_id)) -> None:
    return handle_errors(lambda: service.delete_column(user_id, column_id, payload.move_tasks_to))


@app.patch("/api/v1/columns/reorder")
def reorder_columns(payload: ReorderColumnsRequest, board_id: int, user_id: int = Depends(current_user_id)) -> list[dict[str, Any]]:
    return handle_errors(lambda: service.reorder_columns(user_id, board_id, payload.column_ids))


@app.post("/api/v1/columns/{column_id}/tasks", status_code=201)
def create_task(column_id: int, payload: TaskCreateRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.create_task(user_id, column_id, **payload.model_dump()))


@app.get("/api/v1/boards/{board_id}/tasks")
def search_tasks(
    board_id: int,
    user_id: int = Depends(current_user_id),
    query: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    labels: list[str] = Query(default_factory=list),
    sort_by: str = "created_at",
) -> list[dict[str, Any]]:
    return handle_errors(lambda: service.search_tasks(user_id, board_id, query, priority, assignee_id, labels, sort_by))


@app.get("/api/v1/tasks/{task_id}")
def get_task(task_id: int, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service._task_payload(service._task_for_user(user_id, task_id)))


@app.put("/api/v1/tasks/{task_id}")
def update_task(task_id: int, payload: TaskUpdateRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.update_task(user_id, task_id, **payload.model_dump(exclude_unset=True)))


@app.delete("/api/v1/tasks/{task_id}", status_code=204)
def delete_task(task_id: int, user_id: int = Depends(current_user_id)) -> None:
    return handle_errors(lambda: service.delete_task(user_id, task_id))


@app.patch("/api/v1/tasks/{task_id}/move")
def move_task(task_id: int, payload: TaskMoveRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.move_task(user_id, task_id, payload.target_column_id, payload.position))


@app.patch("/api/v1/tasks/reorder")
def reorder_tasks(payload: ReorderTasksRequest, user_id: int = Depends(current_user_id)) -> list[dict[str, Any]]:
    return handle_errors(lambda: service.reorder_tasks(user_id, payload.column_id, payload.task_ids))


@app.post("/api/v1/tasks/{task_id}/comments", status_code=201)
def add_comment(task_id: int, payload: CommentRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.add_comment(user_id, task_id, payload.content))


@app.get("/api/v1/tasks/{task_id}/comments")
def list_comments(task_id: int, user_id: int = Depends(current_user_id)) -> list[dict[str, Any]]:
    return handle_errors(lambda: service.list_comments(user_id, task_id))


@app.post("/api/v1/tasks/{task_id}/labels")
def add_label(task_id: int, payload: LabelRequest, user_id: int = Depends(current_user_id)) -> dict[str, Any]:
    return handle_errors(lambda: service.add_label(user_id, task_id, payload.name, payload.color))


@app.get("/api/v1/tasks/{task_id}/activity")
def activity(task_id: int, user_id: int = Depends(current_user_id)) -> list[dict[str, Any]]:
    return handle_errors(lambda: service.activity_for_task(user_id, task_id))
