"""Fresh database identity and immediate, primary-backed authorization."""

import time
from dataclasses import dataclass

from fastapi import Request

from erp_cf.crypto import decode_access
from erp_cf.db import D1Database, assertion
from erp_cf.errors import AuthenticationError, PermissionDeniedError
from erp_cf.store import tables


def environment(request: Request):
    return request.scope["env"]


def bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise AuthenticationError("Not authenticated")
    return token


@dataclass(frozen=True)
class Actor:
    user: dict
    roles: tuple[dict, ...]
    permissions: frozenset[str]
    session_id: str
    expires_at: int

    @property
    def id(self):
        return self.user["id"]

    @property
    def is_superuser(self):
        return self.user["is_superuser"]

    def require(self, permission):
        if not self.is_superuser and permission not in self.permissions:
            raise PermissionDeniedError(f"Missing permission: {permission}")

    def guard(self, plan):
        plan.statements.append(
            assertion(
                plan.identity,
                "EXISTS(SELECT 1 FROM platform_sessions s JOIN core_users u ON u.id=s.user_id "
                "WHERE s.id=? AND s.user_id=? AND s.revoked_at IS NULL AND s.expires_at>? "
                "AND u.is_active=1 AND u._revision=?) AND ?>?",
                (
                    self.session_id,
                    self.id,
                    int(time.time()),
                    self.user["_revision"],
                    self.expires_at,
                    int(time.time()),
                ),
            )
        )


async def from_token(env, token: str) -> Actor:
    payload = decode_access(env, token)
    db = D1Database(env.DB)
    user_table = tables()["core_users"]
    row = await db.first(
        f"SELECT {user_table.projection('u')} FROM core_users u "
        "JOIN platform_sessions s ON s.user_id=u.id "
        "WHERE u.id=? AND s.id=? AND u.is_active=1 AND s.revoked_at IS NULL AND s.expires_at>?",
        (payload["sub"], payload["sid"], int(time.time())),
    )
    if row is None:
        raise AuthenticationError("Account or session is disabled")
    user = user_table.decode(row)
    role_table = tables()["core_roles"]
    roles = tuple(
        role_table.decode(r)
        for r in await db.all(
            f"SELECT {role_table.projection('r')} FROM core_roles r "
            "JOIN core_user_roles ur ON ur.role_id=r.id WHERE ur.user_id=? ORDER BY r.code",
            (user["id"],),
        )
    )
    if user["is_superuser"]:
        permissions = await db.all("SELECT code FROM core_permissions ORDER BY code")
    else:
        permissions = await db.all(
            "SELECT DISTINCT p.code FROM core_permissions p "
            "JOIN core_role_permissions rp ON rp.permission_id=p.id "
            "JOIN core_user_roles ur ON ur.role_id=rp.role_id WHERE ur.user_id=? ORDER BY p.code",
            (user["id"],),
        )
    return Actor(
        user, roles, frozenset(p["code"] for p in permissions), payload["sid"], payload["exp"]
    )


async def current_actor(request: Request) -> Actor:
    return await from_token(environment(request), bearer(request))


def require(permission: str):
    async def check(request: Request):
        actor = await current_actor(request)
        actor.require(permission)
        return actor

    return check
