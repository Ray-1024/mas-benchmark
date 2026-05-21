from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time

from .errors import AuthenticationError


DEFAULT_TOKEN_TTL_SECONDS = 24 * 60 * 60


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return f"pbkdf2_sha256${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt_text, digest_text = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False

    salt = base64.urlsafe_b64decode(salt_text.encode())
    expected = base64.urlsafe_b64decode(digest_text.encode())
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 120_000)
    return hmac.compare_digest(actual, expected)


def create_token(user_id: int, secret: str, ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS) -> str:
    payload = {"sub": user_id, "exp": int(time.time()) + ttl_seconds}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    signature = _sign(body, secret)
    return f"{body}.{signature}"


def verify_token(token: str, secret: str) -> int:
    try:
        body, signature = token.split(".", 1)
    except ValueError as exc:
        raise AuthenticationError("Invalid token") from exc

    if not hmac.compare_digest(_sign(body, secret), signature):
        raise AuthenticationError("Invalid token signature")

    payload = json.loads(base64.urlsafe_b64decode(_pad(body)).decode("utf-8"))
    if payload["exp"] < int(time.time()):
        raise AuthenticationError("Token expired")
    return int(payload["sub"])


def _sign(body: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body.encode("utf-8"), hashlib.sha256).digest()
    return _b64(digest)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _pad(value: str) -> bytes:
    return (value + "=" * (-len(value) % 4)).encode("ascii")
