"""Migration receipt drift, unknown outcomes and DDL rollback on a fresh target."""

import sqlite3

import pytest

from scripts.apply_migrations import migrate


class Database:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.lose = False
        self.fail = False

    def __call__(self, sql):
        if sql.lstrip().upper().startswith("SELECT"):
            return [dict(row) for row in self.connection.execute(sql)]
        script = "BEGIN;\n" + sql + ("\nBAD SQL;" if self.fail else "") + "\nCOMMIT;"
        try:
            self.connection.executescript(script)
        except sqlite3.Error as exc:
            self.connection.rollback()
            raise RuntimeError("DDL batch rejected") from exc
        if self.lose and "INSERT INTO platform_schema_migrations" in sql:
            self.lose = False
            raise RuntimeError("Commit response was lost")
        return []


def migrations(tmp_path):
    first = tmp_path / "0001_tables.sql"
    first.write_text("CREATE TABLE example(id TEXT PRIMARY KEY);")
    (tmp_path / "0002_index.sql").write_text("CREATE INDEX example_id ON example(id);")
    return first


def test_migrations_reconcile_a_lost_committed_response_and_reject_changed_history(tmp_path):
    first = migrations(tmp_path)
    db = Database()
    assert migrate(db, directory=tmp_path)["applied"] == 0
    assert not db("SELECT name FROM sqlite_schema WHERE type='table'")
    db.lose = True
    assert migrate(db, directory=tmp_path, apply=True) == {"applied": 2, "pending": []}
    assert migrate(db, directory=tmp_path, apply=True) == {"applied": 2, "pending": []}
    first.write_text(first.read_text() + "\n-- changed")
    with pytest.raises(RuntimeError, match="missing or changed"):
        migrate(db, directory=tmp_path, apply=True)
    assert len(db("SELECT * FROM platform_schema_migrations")) == 2


def test_migrations_reject_unreceipted_occupied_targets_and_roll_back_failed_ddl(tmp_path):
    migrations(tmp_path)
    db = Database()
    db("CREATE TABLE occupied(id TEXT);")
    with pytest.raises(RuntimeError, match="Occupied database"):
        migrate(db, directory=tmp_path, apply=True)
    db = Database()
    migrate(db, directory=tmp_path, apply=True)
    (tmp_path / "0003_bad.sql").write_text("CREATE TABLE partial(id TEXT); BAD SQL;")
    with pytest.raises(RuntimeError, match="DDL batch rejected"):
        migrate(db, directory=tmp_path, apply=True)
    assert not db("SELECT name FROM sqlite_schema WHERE name='partial'")
    assert len(db("SELECT * FROM platform_schema_migrations")) == 2
