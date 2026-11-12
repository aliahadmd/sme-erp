"""Local D1 binding double backed by real SQLite SQL and constraints."""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parents[1]


class Prepared:
    def __init__(self, binding, sql, params=()):
        self.binding, self.sql, self.params = binding, sql, params

    def bind(self, *params):
        return Prepared(self.binding, self.sql, params)

    async def all(self):
        rows = self.binding.connection.execute(self.sql, self.params).fetchall()
        return {"results": [dict(row) for row in rows], "success": True}

    async def first(self):
        rows = (await self.all())["results"]
        return rows[0] if rows else None

    async def run(self):
        return await self.all()


class SQLiteD1:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.before_batch = None
        for path in sorted((ROOT / "migrations").glob("*.sql")):
            self.connection.executescript(path.read_text())

    def prepare(self, sql):
        return Prepared(self, sql)

    async def batch(self, statements):
        if self.before_batch:
            await self.before_batch(statements)
        self.connection.execute("BEGIN")
        try:
            results = [await statement.all() for statement in statements]
            self.connection.commit()
            return results
        except Exception:
            self.connection.rollback()
            raise
