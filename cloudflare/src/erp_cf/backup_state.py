"""Versioned backup control metadata; never part of captured business row data."""

import uuid

from erp_cf.db import Statement, assertion
from erp_cf.errors import ConflictError, NotFoundError
from erp_cf.store import now

BACKUP_COLUMNS = [
    "id",
    "org_id",
    "run_key",
    "status",
    "fence",
    "schema_json",
    "schema_hash",
    "manifest_hash",
    "table_index",
    "cursor",
    "page_index",
    "rows",
    "bytes",
    "version",
    "created_at",
    "capture_started_at",
    "updated_at",
    "completed_at",
    "last_error",
]
RESTORE_COLUMNS = [
    "id",
    "org_id",
    "backup_id",
    "target_database_id",
    "status",
    "phase",
    "table_index",
    "page_index",
    "object_index",
    "version",
    "created_at",
    "updated_at",
    "completed_at",
    "result_json",
    "last_error",
]
INTEGERS = {"fence", "table_index", "page_index", "object_index", "rows", "bytes", "version"}


async def load(db, table, identity, *, required=True):
    columns = BACKUP_COLUMNS if table == "platform_backup_runs" else RESTORE_COLUMNS
    projection = ",".join(f"CAST({c} AS TEXT) AS {c}" if c in INTEGERS else c for c in columns)
    row = await db.first(f"SELECT {projection} FROM {table} WHERE id=?", (identity,))
    if not row and required:
        raise NotFoundError("Backup/recovery job not found")
    return row


def version_guard(identity, table, row):
    return assertion(
        identity,
        f"EXISTS(SELECT 1 FROM {table} WHERE id=? AND version=CAST(? AS INTEGER))",
        (row["id"], row["version"]),
    )


async def checkpoint(db, table, row, changes, *, lease=None, statements=()):
    identity = "backup-step-" + uuid.uuid4().hex
    allowed = set(BACKUP_COLUMNS if table == "platform_backup_runs" else RESTORE_COLUMNS) - {
        "id",
        "org_id",
        "version",
    }
    if set(changes) - allowed:
        raise ValueError("Invalid control metadata column")
    updates = {**changes, "updated_at": now().isoformat()}
    assignments = [
        f"{key}=" + ("CAST(? AS INTEGER)" if key in INTEGERS else "?") for key in updates
    ]
    values = tuple(str(v) if key in INTEGERS and v is not None else v for key, v in updates.items())
    try:
        await db.batch(
            [
                *([lease.guard(identity)] if lease else []),
                version_guard(identity, table, row),
                *statements,
                Statement(
                    f"UPDATE {table} SET {','.join(assignments)},version=version+1 WHERE id=?",
                    (*values, row["id"]),
                ),
                Statement("DELETE FROM cf_assertions WHERE command_id=?", (identity,)),
            ]
        )
    except Exception as exc:
        if "CHECK constraint failed" in str(exc):
            if lease:
                from erp_cf.maintenance import protected

                await protected(db, lease, [])
                # A still-valid lease means another controller advanced the row.
            raise ConflictError("Backup/recovery progress changed") from exc
        raise


def prefix(row):
    return f"erp/{row['org_id']}/{row['id']}/{row['fence']}/"


def manifest_key(row):
    return prefix(row) + "manifest-" + row["manifest_hash"] + ".json"
