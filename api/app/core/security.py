"""Password hashing (argon2) and JWT issuing/verification."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings
from app.core.errors import AuthenticationError

_hasher = PasswordHasher()

ACCESS_TOKEN = "access"
REFRESH_TOKEN = "refresh"


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _create_token(
    subject: str, token_type: str, expires_delta: timedelta, extra: dict[str, Any] | None = None
) -> tuple[str, str]:
    """Returns (token, jti)."""
    now = datetime.now(UTC)
    jti = uuid.uuid4().hex
    payload: dict[str, Any] = {
        "sub": subject,
        "type": token_type,
        "jti": jti,
        "iat": now,
        "exp": now + expires_delta,
    }
    if extra:
        payload.update(extra)
    token = jwt.encode(payload, get_settings().jwt_secret, algorithm="HS256")
    return token, jti


def create_access_token(user_id: str, extra: dict[str, Any] | None = None) -> tuple[str, str]:
    return _create_token(
        user_id, ACCESS_TOKEN, timedelta(minutes=get_settings().access_token_minutes), extra
    )


def create_refresh_token(user_id: str) -> tuple[str, str]:
    return _create_token(user_id, REFRESH_TOKEN, timedelta(days=get_settings().refresh_token_days))


def decode_token(token: str, expected_type: str | None = None) -> dict[str, Any]:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid token") from exc
    if expected_type and payload.get("type") != expected_type:
        raise AuthenticationError(f"Expected a {expected_type} token")
    return payload
