"""Auth dependencies: current user extraction and permission enforcement."""

import uuid
from dataclasses import dataclass, field
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import AuthenticationError, PermissionDeniedError
from app.core.security import ACCESS_TOKEN, decode_token
from app.modules.core.models import User
from app.modules.core.service import get_user_permissions

_bearer = HTTPBearer(auto_error=False)


@dataclass
class CurrentUser:
    user: User
    permissions: list[str] = field(default_factory=list)

    @property
    def id(self) -> uuid.UUID:
        return self.user.id

    @property
    def is_superuser(self) -> bool:
        return self.user.is_superuser

    @property
    def org_id(self) -> uuid.UUID | None:
        return None  # single-org in phase 1; services resolve org explicitly

    def has(self, code: str) -> bool:
        return self.is_superuser or code in self.permissions


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    if credentials is None:
        raise AuthenticationError("Not authenticated")
    payload = decode_token(credentials.credentials, expected_type=ACCESS_TOKEN)
    user = await session.get(User, uuid.UUID(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("Account not found or disabled")
    permissions = await get_user_permissions(session, user)
    return CurrentUser(user=user, permissions=permissions)


def require(permission: str):
    """Route dependency factory: enforce a permission code."""

    async def checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has(permission):
            raise PermissionDeniedError(f"Missing permission: {permission}")
        return user

    return checker


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None
