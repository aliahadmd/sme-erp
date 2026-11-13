"""Restore committed snapshots only into an empty, isolated target database.

Target receipts make every DML/DDL page safe after a lost response. All captured
row hashes are verified before operational reconciliation changes sessions or
mail. Nullable self references are repaired in a second pass; financial
immutability triggers are installed only after the original rows are complete.
"""

from erp_cf.backup_capture import committed
from erp_cf.backup_format import (
    SCHEMA_SQL,
    BackupError,
    canonical,
    get,
    identifier,
    sha,
    table_order,
    target_chunk,
)
from erp_cf.backup_state import checkpoint
from erp_cf.db import Statement
from erp_cf.store import now

RUNS = "platform_restore_runs"


async def receipt(db, identity, key, checksum):
    row = await db.first(
        "SELECT sha256 FROM platform_restore_receipts WHERE restore_id=? AND step_key=?",
        (identity, key),
    )
    if row and row["sha256"] != checksum:
        raise BackupError("Recovery step receipt conflicts with this snapshot")
    return row is not None


def receipt_statement(row, key, checksum):
    return Statement(
        "INSERT INTO platform_restore_receipts(restore_id,step_key,sha256,created_at) "
        "VALUES (?,?,?,?)",
        (row["id"], key, checksum, now().isoformat()),
    )


async def initialize(source, target, bucket, row, backup, manifest=None):
    if manifest is None:
        manifest = await committed(bucket, backup)
    occupied = await target.all(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*'"
    )
    if occupied:
        if not any(r["name"] == "platform_restore_receipts" for r in occupied):
            raise BackupError("Recovery requires an empty isolated target database")
        anchor = await target.first(
            "SELECT restore_id,sha256 FROM platform_restore_receipts WHERE step_key='anchor'"
        )
        if (
            not anchor
            or anchor["restore_id"] != row["id"]
            or anchor["sha256"] != backup["manifest_hash"]
        ):
            raise BackupError("Recovery target is occupied by other data or another run")
    else:
        statements = [
            Statement(obj["sql"]) for obj in manifest["schema"]["objects"] if obj["type"] == "table"
        ]
        statements.append(receipt_statement(row, "anchor", backup["manifest_hash"]))
        await target.batch(statements)
    await checkpoint(source, RUNS, row, {"status": "restoring"})


def self_fields(table):
    return {fk["from"] for fk in table["foreign_keys"] if fk["table"] == table["name"]}


async def rows_step(target, bucket, row, table, ref, phase):
    key = f"{phase}:{ref['sequence']}"
    if await receipt(target, row["id"], key, ref["sha256"]):
        return
    value = await get(bucket, ref["object_key"], ref["sha256"])
    if value["table"] != table["name"] or len(value["rows"]) != int(ref["rows"]):
        raise BackupError("Backup page identity/count differs from its manifest")
    fields, statements = table["columns"], []
    deferred = self_fields(table)
    if phase == "data":
        names = ",".join(["_rowid_", *[identifier(c["name"]) for c in fields]])
        values = ",".join(
            [
                "CAST(? AS INTEGER)",
                *["CAST(? AS INTEGER)" if c["type"] == "INTEGER" else "?" for c in fields],
            ]
        )
        sql = f"INSERT INTO {identifier(table['name'])}({names}) VALUES ({values})"
        for record in value["rows"]:
            values = [
                None if c["name"] in deferred else v
                for c, v in zip(fields, record["values"], strict=True)
            ]
            if any(v is not None and not isinstance(v, str) for v in values):
                raise BackupError("Captured row values must be text or null")
            statements.append(Statement(sql, (record["cursor"], *values)))
    elif phase == "links":
        for record in value["rows"]:
            cells = dict(zip([c["name"] for c in fields], record["values"], strict=True))
            names = sorted(deferred)
            statements.append(
                Statement(
                    f"UPDATE {identifier(table['name'])} SET "
                    + ",".join(f"{identifier(name)}=?" for name in names)
                    + " WHERE _rowid_=CAST(? AS INTEGER)",
                    (*(cells[name] for name in names), record["cursor"]),
                )
            )
    elif phase == "verify":
        actual = await target_chunk(target, table, ref)
        if sha(actual) != ref["sha256"]:
            raise BackupError("Restored row values differ from the exact snapshot")
    statements.append(receipt_statement(row, key, ref["sha256"]))
    await target.batch(statements)


async def step(source, target, bucket, row, backup, manifest=None):
    if manifest is None:
        manifest = await committed(bucket, backup)
    anchor = await target.first(
        "SELECT restore_id,sha256 FROM platform_restore_receipts WHERE step_key='anchor'"
    )
    if (
        not anchor
        or anchor["restore_id"] != row["id"]
        or anchor["sha256"] != backup["manifest_hash"]
    ):
        raise BackupError("Recovery target ownership changed")
    tables = {table["name"]: table for table in manifest["schema"]["tables"]}
    order = table_order(list(tables.values()))
    phase, index, page_index = row["phase"], int(row["table_index"]), int(row["page_index"])
    if phase in {"data", "links", "verify"}:
        if index >= len(order):
            next_phase = {"data": "links", "links": "verify", "verify": "objects"}[phase]
            await checkpoint(
                source, RUNS, row, {"phase": next_phase, "table_index": 0, "page_index": 0}
            )
            return
        table = tables[order[index]]
        refs = [ref for ref in manifest["pages"] if ref["table_name"] == table["name"]]
        if phase == "links" and not self_fields(table):
            page_index = len(refs)
        if page_index < len(refs):
            await rows_step(target, bucket, row, table, refs[page_index], phase)
            await checkpoint(source, RUNS, row, {"page_index": page_index + 1})
        else:
            if phase == "verify":
                count = await target.first(
                    f"SELECT CAST(COUNT(*) AS TEXT) AS n FROM {identifier(table['name'])}"
                )
                if int(count["n"]) != sum(int(ref["rows"]) for ref in refs):
                    raise BackupError("Restored table row count differs from the snapshot")
            await checkpoint(source, RUNS, row, {"table_index": index + 1, "page_index": 0})
        return
    if phase == "objects":
        objects = [obj for obj in manifest["schema"]["objects"] if obj["type"] != "table"]
        cursor = int(row["object_index"])
        batch = objects[cursor : cursor + 25]
        if batch:
            key, checksum = f"objects:{cursor}", sha(batch)
            if not await receipt(target, row["id"], key, checksum):
                await target.batch(
                    [
                        *[Statement(obj["sql"]) for obj in batch],
                        receipt_statement(row, key, checksum),
                    ]
                )
            await checkpoint(source, RUNS, row, {"object_index": cursor + len(batch)})
        else:
            if await target.all(SCHEMA_SQL) != manifest["schema"]["objects"]:
                raise BackupError("Restored schema differs from the snapshot")
            if await target.all("PRAGMA foreign_key_check"):
                raise BackupError("Restored foreign-key integrity check failed")
            await checkpoint(source, RUNS, row, {"phase": "reconcile"})
        return
    if phase == "reconcile":
        checksum, timestamp = backup["manifest_hash"], now().isoformat()
        if not await receipt(target, row["id"], "reconcile", checksum):
            await target.batch(
                [
                    Statement(
                        "UPDATE platform_sessions SET revoked_at="
                        "CAST(strftime('%s','now') AS INTEGER) WHERE revoked_at IS NULL"
                    ),
                    Statement(
                        "INSERT INTO platform_recovery_policy(singleton,hold_external,"
                        "restored_from,restored_at) VALUES(1,1,?,?)",
                        (backup["id"], timestamp),
                    ),
                    Statement(
                        "UPDATE cf_outbox SET "
                        "payload_json=json_set(payload_json,'$.recovery_hold',1),"
                        "dispatched_at=NULL,available_at=NULL WHERE completed_at IS NULL"
                    ),
                    Statement(
                        "UPDATE cf_mail_outbox SET status='uncertain',claim_until=NULL,"
                        "completed_at=NULL,last_error='recovery_review_required',version=version+1 "
                        "WHERE status IN ('pending','uncertain','dead')"
                    ),
                    Statement(
                        "UPDATE cf_mail_attempts SET state='uncertain',"
                        "error_code='recovery_review_required',finished_at=? "
                        "WHERE state='prepared'",
                        (timestamp,),
                    ),
                    Statement(
                        "INSERT INTO cf_event_receipts(event_id,org_id,outcome,processed_at) "
                        "SELECT id,?,'mail_recovery_review',? FROM cf_outbox "
                        "WHERE event_type='mail.delivery.requested' AND completed_at IS NULL",
                        (row["org_id"], timestamp),
                    ),
                    Statement(
                        "UPDATE cf_outbox SET completed_at=?,delivery_state='complete',"
                        "last_error=NULL WHERE event_type='mail.delivery.requested' "
                        "AND completed_at IS NULL",
                        (timestamp,),
                    ),
                    receipt_statement(row, "reconcile", checksum),
                ]
            )
        result = {
            "exact_rows_verified": True,
            "schema_verified": True,
            "foreign_keys_clean": True,
            "sessions_revoked": True,
            "external_delivery_held": True,
            "tables": len(tables),
            "pages": len(manifest["pages"]),
            "rows": manifest["rows"],
        }
        await checkpoint(
            source,
            RUNS,
            row,
            {
                "status": "complete",
                "completed_at": timestamp,
                "result_json": canonical(result),
                "last_error": None,
            },
        )
        return
    raise BackupError("Unknown recovery phase")
