"""Exercise exact logical recovery with real SQLite constraints locally."""

import copy
import sqlite3
from pathlib import Path

import pytest

from erp_cf.recovery_proof import capture, digest, restore


class LocalDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys=ON")

    async def all(self, sql, params=()):
        return [dict(row) for row in self.connection.execute(sql, params).fetchall()]

    async def batch(self, statements):
        self.connection.execute("BEGIN")
        try:
            results = [{"results": await self.all(s.sql, s.params)} for s in statements]
            self.connection.commit()
            return results
        except Exception:
            self.connection.rollback()
            raise


def source_database():
    db = LocalDatabase()
    root = Path(__file__).parents[1]
    for path in sorted((root / "migrations").glob("*.sql")):
        db.connection.executescript(path.read_text())
    db.connection.executescript((root / "fixtures/0001_feasibility.sql").read_text())
    db.connection.executescript(
        "INSERT INTO proof_precision VALUES ('large', 999999999999999999);"
        "INSERT INTO proof_precision VALUES ('negative', -999999999999999999);"
        "INSERT INTO cf_commands VALUES ('c','h','{}','now');"
        "INSERT INTO cf_outbox(id,command_id,event_type,payload_json,created_at) "
        "VALUES ('e','c','posted','{}','now');"
    )
    return db


@pytest.mark.asyncio
async def test_exact_recovery_preserves_schema_int64_nulls_and_outbox():
    source = source_database()
    snapshot = await capture(source)
    target = LocalDatabase()
    await restore(target, snapshot)
    recovered = await capture(target)
    assert recovered == snapshot
    assert (
        target.connection.execute(
            "SELECT CAST(amount AS TEXT) FROM proof_precision WHERE id='large'"
        ).fetchone()[0]
        == "999999999999999999"
    )
    assert target.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(ValueError, match="empty isolated"):
        await restore(target, snapshot)


@pytest.mark.asyncio
async def test_tampering_and_broken_foreign_keys_cannot_restore_partial_data():
    snapshot = await capture(source_database())
    corrupt = copy.deepcopy(snapshot)
    outbox = next(t for t in corrupt["content"]["tables"] if t["name"] == "cf_outbox")
    outbox["rows"][0]["command_id"] = "missing"
    target = LocalDatabase()
    with pytest.raises(ValueError, match="checksum"):
        await restore(target, corrupt)
    corrupt["sha256"] = digest(corrupt["content"])
    with pytest.raises(sqlite3.IntegrityError):
        await restore(target, corrupt)
    assert await target.all("SELECT name FROM sqlite_schema WHERE type='table'") == []


@pytest.mark.asyncio
async def test_an_unrelated_user_table_also_makes_a_restore_target_occupied():
    target = LocalDatabase()
    target.connection.execute("CREATE TABLE unrelated (value TEXT)")
    with pytest.raises(ValueError, match="empty isolated"):
        await restore(target, await capture(source_database()))
