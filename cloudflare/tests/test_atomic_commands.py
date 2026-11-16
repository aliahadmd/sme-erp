"""Exercise real SQLite transactions locally; remote D1 tests are a separate gate."""

import asyncio
import sqlite3
from pathlib import Path

import pytest

from erp_cf.db import AtomicCommands, CommandConflict, Statement, assertion


class LocalDatabase:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        migration = Path(__file__).parents[1] / "migrations/0001_runtime.sql"
        self.connection.executescript(migration.read_text())
        self.connection.executescript(
            "CREATE TABLE stock (id TEXT PRIMARY KEY, quantity INTEGER, revision INTEGER);"
            "INSERT INTO stock VALUES ('p', 5, 0);"
            "CREATE TABLE journal (id TEXT PRIMARY KEY, amount INTEGER);"
        )
        self.lost_response = False

    async def first(self, sql, params=()):
        row = self.connection.execute(sql, params).fetchone()
        return dict(row) if row else None

    async def batch(self, statements):
        with self.connection:
            for statement in statements:
                self.connection.execute(statement.sql, statement.params)
        if self.lost_response:
            self.lost_response = False
            raise ConnectionError("Commit response lost")


@pytest.mark.asyncio
async def test_failure_after_a_write_rolls_back_claim_business_and_outbox():
    db = LocalDatabase()
    commands = AtomicCommands(db)
    with pytest.raises(sqlite3.IntegrityError):
        await commands.execute(
            identity="c1",
            request_hash="h1",
            response={"ok": True},
            statements=[
                Statement("INSERT INTO journal VALUES ('j', 100)"),
                assertion("c1", "0"),
                Statement("INSERT INTO cf_outbox VALUES ('e','c1','posted','{}','now',NULL,NULL)"),
            ],
        )
    for table in ["cf_commands", "journal", "cf_outbox", "cf_assertions"]:
        assert db.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("replay_checked", [False, True])
async def test_duplicate_and_lost_response_return_original_outcome(replay_checked):
    db = LocalDatabase()
    commands = AtomicCommands(db)
    db.lost_response = True
    args = dict(
        identity="c1",
        request_hash="h1",
        response={"invoice": "i1"},
        statements=[Statement("INSERT INTO journal VALUES ('j', 100)")],
        replay_checked=replay_checked,
    )
    assert await commands.execute(**args) == {"invoice": "i1"}
    assert await commands.execute(**args) == {"invoice": "i1"}
    assert db.connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
    with pytest.raises(CommandConflict):
        await commands.execute(**{**args, "request_hash": "different"})


@pytest.mark.asyncio
async def test_stale_revision_cannot_post_a_journal_even_after_zero_row_update():
    db = LocalDatabase()
    commands = AtomicCommands(db)

    async def deliver(command):
        try:
            return await commands.execute(
                identity=command,
                request_hash="same-input",
                response={"delivered": 4},
                statements=[
                    assertion(
                        command,
                        "EXISTS(SELECT 1 FROM stock WHERE id='p' AND revision=0 AND quantity>=4)",
                    ),
                    Statement(
                        "UPDATE stock SET quantity=quantity-4, revision=revision+1 "
                        "WHERE id='p' AND revision=0"
                    ),
                    Statement("INSERT INTO journal VALUES (?, 100)", (command,)),
                ],
            )
        except sqlite3.IntegrityError:
            return {"conflict": True}

    outcomes = await asyncio.gather(deliver("one"), deliver("two"))
    assert outcomes.count({"delivered": 4}) == 1
    assert outcomes.count({"conflict": True}) == 1
    assert db.connection.execute("SELECT quantity FROM stock").fetchone()[0] == 1
    assert db.connection.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1
    assert db.connection.execute("SELECT COUNT(*) FROM cf_commands").fetchone()[0] == 1
