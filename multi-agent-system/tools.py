from uuid import uuid4
from datetime import UTC, datetime


def utc_now() -> datetime:
    return datetime.now(tz = UTC)


def new_uuid() -> str:
    return uuid4().hex


def new_id() -> str:
    return new_uuid()
