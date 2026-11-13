"""Native D1 access and atomic, idempotent command execution.

SQLAlchemy is permitted for suitable queries. Multi-statement writes go through
this batch boundary; a sequence of ORM commits is never a transaction here.
"""

import hashlib
import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Statement:
    sql: str
    params: tuple = ()


def advance_business_epoch(org_id):
    return Statement(
        "INSERT INTO cf_business_epochs(org_id,version) VALUES (?,1) "
        "ON CONFLICT(org_id) DO UPDATE SET version=version+1",
        (org_id,),
    )


def plain(value):
    return value.to_py() if hasattr(value, "to_py") else value


class D1Database:
    def __init__(self, binding):
        self.binding = binding

    def prepare(self, statement: Statement):
        prepared = self.binding.prepare(statement.sql)
        if statement.params:
            # workers-runtime-sdk handles Python None -> JavaScript null.
            prepared = prepared.bind(*statement.params)
        return prepared

    async def all(self, sql: str, params: tuple = ()) -> list[dict]:
        result = plain(await self.prepare(Statement(sql, params)).all())
        return [plain(row) for row in result["results"]]

    async def first(self, sql: str, params: tuple = ()) -> dict | None:
        rows = await self.all(sql, params)
        return rows[0] if rows else None

    async def batch(self, statements: list[Statement]) -> list[dict]:
        # The SDK recursively unwraps prepared-statement binding wrappers. A
        # raw to_js() creates borrowed Python proxies that expire before D1
        # consumes the array, so pass the native Python list to the SDK.
        prepared = [self.prepare(statement) for statement in statements]
        return [plain(result) for result in await self.binding.batch(prepared)]


class CommandConflict(Exception):
    pass


class AtomicCommands:
    def __init__(self, db: Any):
        self.db = db

    @staticmethod
    def identity(org_id: str, actor_id: str, key: str) -> str:
        if not 1 <= len(key) <= 128:
            raise ValueError("Idempotency key must contain 1 to 128 characters")
        return hashlib.sha256(json.dumps([org_id, actor_id, key]).encode()).hexdigest()

    @staticmethod
    def request_hash(operation: str, payload: dict) -> str:
        encoded = json.dumps([operation, payload], sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode()).hexdigest()

    async def replay(self, identity: str, request_hash: str) -> dict | None:
        row = await self.db.first(
            "SELECT request_hash, response_json FROM cf_commands WHERE id = ?", (identity,)
        )
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise CommandConflict("Idempotency key was already used for different input")
        return json.loads(row["response_json"])

    async def execute(
        self,
        *,
        identity: str,
        request_hash: str,
        statements: list[Statement],
        response: dict,
    ) -> dict:
        existing = await self.replay(identity, request_hash)
        if existing is not None:
            return existing
        claim = Statement(
            "INSERT INTO cf_commands (id, request_hash, response_json) VALUES (?, ?, ?)",
            (identity, request_hash, json.dumps(response, separators=(",", ":"))),
        )
        cleanup = Statement("DELETE FROM cf_assertions WHERE command_id = ?", (identity,))
        try:
            await self.db.batch([claim, *statements, cleanup])
        except Exception:
            # The commit may have succeeded before its response was lost, or a
            # simultaneous duplicate may have claimed the key. D1 is the authority.
            existing = await self.replay(identity, request_hash)
            if existing is not None:
                return existing
            raise
        return response


def assertion(identity: str, predicate_sql: str, params: tuple = ()) -> Statement:
    """Fail inside the batch if a trusted SQL predicate is false.

    Unlike a zero-row UPDATE, inserting valid=0 violates CHECK(valid=1) and
    makes D1 roll back the entire batch, including prior writes.
    """
    return Statement(
        "INSERT INTO cf_assertions (command_id, valid) "
        f"SELECT ?, CASE WHEN ({predicate_sql}) THEN 1 ELSE 0 END",
        (identity, *params),
    )
