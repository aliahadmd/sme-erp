"""Resumable, leased D1 capture and a separately published R2 commit marker."""

import json

from erp_cf.backup_format import (
    FORMAT,
    MAX_MANIFEST_BYTES,
    MAX_PAGES,
    SCHEMA_SQL,
    BackupError,
    canonical,
    get,
    page,
    put,
    schema,
    sha,
)
from erp_cf.backup_state import checkpoint, manifest_key, prefix
from erp_cf.db import Statement, plain
from erp_cf.maintenance import Lease, acquire, protected, renew
from erp_cf.store import now

RUNS = "platform_backup_runs"


async def start(db, row):
    lease = await acquire(db, row["id"], seconds=300)
    started = now().isoformat()
    description = await schema(db, lease)
    await checkpoint(
        db,
        RUNS,
        row,
        {
            "status": "capturing",
            "capture_started_at": started,
            "fence": lease.fence,
            "schema_json": canonical(description),
            "schema_hash": sha(description),
            "last_error": None,
        },
        lease=lease,
    )


async def capture_page(db, bucket, row):
    lease = Lease(row["id"], row["fence"])
    await renew(db, lease)
    description = json.loads(row["schema_json"])
    index = int(row["table_index"])
    if index >= len(description["tables"]):
        await seal(db, bucket, row, lease, description)
        return
    table = description["tables"][index]
    value = await page(db, lease, table, row["cursor"])
    if not value["rows"]:
        await checkpoint(db, RUNS, row, {"table_index": index + 1, "cursor": None}, lease=lease)
        return
    sequence = int(row["page_index"])
    if sequence >= MAX_PAGES:
        raise BackupError("Backup exceeds the supported page count")
    checksum = sha(value)
    key = prefix(row) + f"pages/{sequence:06d}-{checksum}.json"
    _, size = await put(bucket, key, value)
    count, first, last = len(value["rows"]), value["rows"][0]["cursor"], value["rows"][-1]["cursor"]
    await checkpoint(
        db,
        RUNS,
        row,
        {
            "page_index": sequence + 1,
            "cursor": last,
            "rows": int(row["rows"]) + count,
            "bytes": int(row["bytes"]) + size,
            "last_error": None,
        },
        lease=lease,
        statements=[
            Statement(
                "INSERT INTO platform_backup_pages(backup_id,sequence,table_name,object_key,"
                "sha256,rows,bytes,first_cursor,last_cursor) "
                "VALUES (?,CAST(? AS INTEGER),?,?,?,CAST(? AS INTEGER),CAST(? AS INTEGER),?,?)",
                (
                    row["id"],
                    str(sequence),
                    table["name"],
                    key,
                    checksum,
                    str(count),
                    str(size),
                    first,
                    last,
                ),
            )
        ],
    )


async def seal(db, bucket, row, lease, description):
    actual = await protected(
        db, lease, [Statement(SCHEMA_SQL), Statement("PRAGMA foreign_key_check")]
    )
    if [plain(r) for r in actual[0]["results"]] != description["objects"] or actual[1]["results"]:
        raise BackupError("Schema or foreign-key integrity changed during capture")
    refs = await db.all(
        "SELECT CAST(sequence AS TEXT) AS sequence,table_name,object_key,sha256,"
        "CAST(rows AS TEXT) AS rows,CAST(bytes AS TEXT) AS bytes,first_cursor,last_cursor "
        "FROM platform_backup_pages WHERE backup_id=? ORDER BY platform_backup_pages.sequence",
        (row["id"],),
    )
    if len(refs) != int(row["page_index"]):
        raise BackupError("Backup page ledger is incomplete")
    manifest = {
        "format": FORMAT,
        "backup_id": row["id"],
        "org_id": row["org_id"],
        "fence": row["fence"],
        "captured_at": row["capture_started_at"],
        "schema": description,
        "schema_sha256": row["schema_hash"],
        "pages": refs,
        "rows": row["rows"],
        "bytes": row["bytes"],
    }
    checksum = sha(manifest)
    ready = {**row, "manifest_hash": checksum}
    await put(bucket, manifest_key(ready), manifest, bound=MAX_MANIFEST_BYTES)
    # This atomic seal proves the read window before releasing writers. A root
    # manifest alone is provisional: recovery also requires COMMITTED.json.
    await checkpoint(
        db,
        RUNS,
        row,
        {"status": "publishing", "manifest_hash": checksum},
        lease=lease,
        statements=[
            Statement(
                "UPDATE platform_maintenance SET owner=NULL,purpose=NULL,expires_at=NULL "
                "WHERE singleton=1"
            )
        ],
    )


async def publish(db, bucket, row):
    await get(bucket, manifest_key(row), row["manifest_hash"], bound=MAX_MANIFEST_BYTES)
    marker = {
        "format": FORMAT,
        "backup_id": row["id"],
        "org_id": row["org_id"],
        "manifest_sha256": row["manifest_hash"],
        "manifest_key": manifest_key(row),
    }
    await put(bucket, prefix(row) + "COMMITTED.json", marker)
    await put(bucket, f"commits/{row['org_id']}/{row['id']}.json", marker)
    await checkpoint(
        db, RUNS, row, {"status": "complete", "completed_at": now().isoformat(), "last_error": None}
    )


async def committed(bucket, row):
    if row["status"] != "complete":
        raise BackupError("Backup is not committed")
    marker = {
        "format": FORMAT,
        "backup_id": row["id"],
        "org_id": row["org_id"],
        "manifest_sha256": row["manifest_hash"],
        "manifest_key": manifest_key(row),
    }
    await get(bucket, prefix(row) + "COMMITTED.json", sha(marker))
    manifest = await get(bucket, manifest_key(row), row["manifest_hash"], bound=MAX_MANIFEST_BYTES)
    if (
        manifest["format"] != FORMAT
        or manifest["backup_id"] != row["id"]
        or manifest["org_id"] != row["org_id"]
        or sha(manifest["schema"]) != row["schema_hash"]
    ):
        raise BackupError("Backup identity or schema does not match its receipt")
    return manifest
