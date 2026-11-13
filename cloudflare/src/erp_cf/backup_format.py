"""Exact, bounded logical backup format shared by capture and recovery."""

import hashlib
import json
from graphlib import TopologicalSorter

from erp_cf.db import Statement, plain
from erp_cf.errors import DomainError
from erp_cf.maintenance import EXEMPT, identifier, protected

FORMAT = "erp-d1-exact-v2"
PAGE_BYTES = 2 * 1024 * 1024
MAX_OBJECT_BYTES = 16 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_PAGES = 20000
SCHEMA_SQL = (
    "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL "
    "AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' "
    "AND type IN ('table','index','trigger','view') ORDER BY type,name"
)


def attribute(value, name):
    # R2 list metadata includes opaque native JsProxy instances nested inside
    # a converted dict. Those expose properties but do not support subscripts.
    return value[name] if isinstance(value, dict) else getattr(value, name)


class BackupError(DomainError):
    def __init__(self, detail, code="backup_invalid"):
        super().__init__(detail, code=code, status=409)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def table_order(tables):
    names = {table["name"] for table in tables}
    graph = {}
    for table in tables:
        refs = {fk["table"] for fk in table["foreign_keys"]}
        if refs - names:
            raise BackupError("A captured table references excluded row data")
        graph[table["name"]] = sorted(refs - {table["name"]})
        fields = {col["name"]: col for col in table["columns"]}
        if any(
            fields[fk["from"]]["notnull"]
            for fk in table["foreign_keys"]
            if fk["table"] == table["name"]
        ):
            raise BackupError("Non-null self references need a new restore strategy")
    try:
        return list(TopologicalSorter(graph).static_order())
    except ValueError as exc:
        raise BackupError("Cross-table reference cycles need a new restore strategy") from exc


async def schema(db, lease):
    result = await protected(db, lease, [Statement(SCHEMA_SQL)])
    objects = [plain(row) for row in result[0]["results"]]
    tables = []
    for obj in objects:
        if obj["type"] != "table" or obj["name"] in EXEMPT:
            continue
        name = obj["name"]
        if "WITHOUT ROWID" in obj["sql"].upper() or "VIRTUAL TABLE" in obj["sql"].upper():
            raise BackupError("Only ordinary rowid tables are supported")
        result = await protected(
            db,
            lease,
            [
                Statement(f"PRAGMA table_xinfo({identifier(name)})"),
                Statement(f"PRAGMA foreign_key_list({identifier(name)})"),
            ],
        )
        columns = [plain(row) for row in result[0]["results"]]
        if any(
            c["hidden"]
            or c["type"] not in {"TEXT", "INTEGER"}
            or c["name"].lower() in {"rowid", "_rowid_", "oid"}
            for c in columns
        ):
            raise BackupError("Unsupported backup column or rowid shadowing")
        tables.append(
            {
                "name": name,
                "columns": columns,
                "foreign_keys": [plain(row) for row in result[1]["results"]],
            }
        )
    table_order(tables)
    return {"objects": objects, "tables": tables, "excluded_data": sorted(EXEMPT)}


def row_json(table):
    return (
        "json_array("
        + ",".join(f"CAST({identifier(c['name'])} AS TEXT)" for c in table["columns"])
        + ")"
    )


def chunk(table, records):
    return {
        "table": table["name"],
        "rows": [{"cursor": row["cursor"], "values": json.loads(row["value"])} for row in records],
    }


async def page(db, lease, table, cursor, *, limit=50):
    # Size candidates inside SQLite before crossing the Worker boundary. Even
    # very large rows cannot make a 50-row result consume the Worker's memory.
    where = " WHERE _rowid_>CAST(? AS INTEGER)" if cursor is not None else ""
    sql = (
        f"WITH candidates AS (SELECT _rowid_ AS rid,{row_json(table)} AS value "
        f"FROM {identifier(table['name'])}{where} ORDER BY _rowid_ LIMIT ?), "
        "sized AS (SELECT rid,value,SUM(length(CAST(value AS BLOB))) OVER(ORDER BY rid) AS bytes,"
        "ROW_NUMBER() OVER(ORDER BY rid) AS n FROM candidates) "
        "SELECT CAST(rid AS TEXT) AS cursor,value FROM sized WHERE bytes<=? OR n=1 ORDER BY rid"
    )
    params = (*((cursor,) if cursor is not None else ()), min(max(limit, 1), 50), PAGE_BYTES)
    result = await protected(db, lease, [Statement(sql, params)])
    value = chunk(table, [plain(row) for row in result[0]["results"]])
    if len(canonical(value).encode()) > MAX_OBJECT_BYTES:
        raise BackupError("A backup row exceeds the supported object bound")
    return value


async def target_chunk(db, table, ref):
    records = await db.all(
        f"SELECT CAST(_rowid_ AS TEXT) AS cursor,{row_json(table)} AS value "
        f"FROM {identifier(table['name'])} WHERE _rowid_>=CAST(? AS INTEGER) "
        "AND _rowid_<=CAST(? AS INTEGER) ORDER BY _rowid_",
        (ref["first_cursor"], ref["last_cursor"]),
    )
    return chunk(table, records)


async def put(bucket, key, value, *, bound=MAX_OBJECT_BYTES):
    text = canonical(value)
    if len(text.encode()) > bound:
        raise BackupError("Backup object exceeds its bounded format")
    checksum = sha(value)
    # Content-addressed immutable keys make an ambiguous put safe to repeat.
    await bucket.put(
        key,
        text,
        {
            "httpMetadata": {"contentType": "application/json"},
            "customMetadata": {"sha256": checksum},
        },
    )
    return checksum, len(text.encode())


async def get(bucket, key, expected, *, bound=MAX_OBJECT_BYTES):
    obj = await bucket.get(key)
    if obj is None or int(obj.size) > bound:
        raise BackupError("Backup object is missing or too large")
    text = await obj.text()
    if len(text.encode()) > bound:
        raise BackupError("Backup object exceeds its declared size")
    try:
        value = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise BackupError("Backup object is not valid JSON") from exc
    if sha(value) != expected:
        raise BackupError("Backup checksum mismatch")
    return value
