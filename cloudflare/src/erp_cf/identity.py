"""Fresh database identity and immediate, primary-backed authorization."""

import time
from dataclasses import dataclass

from fastapi import Depends, Request

from erp_cf.crypto import decode_access
from erp_cf.db import D1Database, Statement, assertion, plain
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
    role_table = tables()["core_roles"]
    # Keep identity, roles and permissions at one primary read boundary. This
    # eliminates sequential network trips without caching authorization across
    # requests or across the independent coordinator commit boundary.
    results = await db.batch(
        [
            Statement(
                f"SELECT {user_table.projection('u')} FROM core_users u "
                "JOIN platform_sessions s ON s.user_id=u.id "
                "WHERE u.id=? AND s.id=? AND u.is_active=1 "
                "AND s.revoked_at IS NULL AND s.expires_at>?",
                (payload["sub"], payload["sid"], int(time.time())),
            ),
            Statement(
                f"SELECT {role_table.projection('r')} FROM core_roles r "
                "JOIN core_user_roles ur ON ur.role_id=r.id WHERE ur.user_id=? ORDER BY r.code",
                (payload["sub"],),
            ),
            Statement(
                "SELECT p.code FROM core_permissions p WHERE EXISTS("
                "SELECT 1 FROM core_users u WHERE u.id=? AND u.is_superuser=1) "
                "UNION SELECT p.code FROM core_permissions p "
                "JOIN core_role_permissions rp ON rp.permission_id=p.id "
                "JOIN core_user_roles ur ON ur.role_id=rp.role_id "
                "WHERE ur.user_id=? ORDER BY code",
                (payload["sub"], payload["sub"]),
            ),
        ]
    )
    if not results[0]["results"]:
        raise AuthenticationError("Account or session is disabled")
    user = user_table.decode(plain(results[0]["results"][0]))
    roles = tuple(role_table.decode(plain(row)) for row in results[1]["results"])
    permissions = frozenset(plain(row)["code"] for row in results[2]["results"])
    return Actor(user, roles, permissions, payload["sid"], payload["exp"])


async def current_actor(request: Request) -> Actor:
    return await from_token(environment(request), bearer(request))


def require(permission: str):
    async def check(actor: Actor = Depends(current_actor)):
        actor.require(permission)
        return actor

    return check
