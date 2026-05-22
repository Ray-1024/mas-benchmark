from __future__ import annotations

from enum import Enum
from typing import Any
import tools

from datetime import datetime

from pydantic import BaseModel, Field

class MessageType(str, Enum):
    REQUEST = "request"
    INFORM = "inform"
    RESULT = "result"
    ERROR = "error"
    HANDOFF = "handoff"

class A2AMessage(BaseModel):
    message_id: str = Field(default_factory=tools.new_id)
    thread_id: str = Field(default_factory=tools.new_id)
    parent_message_id: str | None = None
    sender: str
    recipient: str
    message_type: MessageType
    topic: str
    body: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=tools.utc_now)