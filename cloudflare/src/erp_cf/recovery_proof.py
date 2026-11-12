"""Bounded, exact logical snapshot used to prove D1 -> R2 -> D1 recovery.

This feasibility path is deliberately limited to the synthetic proof tables.
It does not establish the paginated, quiesced production backup requirement.
All snapshot reads share one D1 batch transaction. Every integer crosses the
language boundary as text, including values beyond Number's safe range.
"""

import hashlib
import json

from erp_cf.db import D1Database, Statement, assertion

SCHEMA_SQL = (
    "SELECT type,name,tbl_name,sql FROM sqlite_schema "
    "WHERE sql IS NOT NULL AND type IN ('table','index') "
    "AND (name GLOB 'cf_*' OR name GLOB 'proof_*') "
    "ORDER BY type DESC,name"
)
MAX_BYTES = 4 * 1024 * 1024
MAX_RESTORE_STATEMENTS = 400


def identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


async def capture(db: D1Database) -> dict:
    schema = await db.all(SCHEMA_SQL)
    tables = []
    reads = [Statement(SCHEMA_SQL)]
    for entry in schema:
        if entry["type"] != "table":
            continue
        name = entry["name"]
        columns = await db.all(f"PRAGMA table_xinfo({identifier(name)})")
        if any(col["hidden"] or col["type"] not in {"TEXT", "INTEGER"} for col in columns):
            raise ValueError("Proof backup only supports ordinary TEXT/INTEGER columns")
        projections = []
        for col in columns:
            quoted = identifier(col["name"])
            projections.append(f"CAST({quoted} AS TEXT) AS {quoted}")
        primary = sorted((c for c in columns if c["pk"]), key=lambda c: c["pk"])
        order = ",".join(identifier(c["name"]) for c in primary) if primary else "rowid"
        reads.append(
            Statement(f"SELECT {','.join(projections)} FROM {identifier(name)} ORDER BY {order}")
        )
        tables.append(
            {"name": name, "columns": [{"name": c["name"], "type": c["type"]} for c in columns]}
        )
    results = await db.batch(reads)
    if results[0]["results"] != schema:
        raise ValueError("Schema changed while preparing snapshot")
    for table, result in zip(tables, results[1:], strict=True):
        table["rows"] = result["results"]
    content = {"schema": schema, "tables": tables}
    if len(canonical(content).encode()) > MAX_BYTES:
        raise ValueError("Proof snapshot exceeds 4 MiB; use the production backup path")
    return {"format": "erp-d1-exact-proof-v1", "content": content, "sha256": digest(content)}


async def restore(db: D1Database, snapshot: dict) -> None:
    if snapshot["format"] != "erp-d1-exact-proof-v1":
        raise ValueError("Unsupported proof snapshot format")
    content = snapshot["content"]
    if digest(content) != snapshot["sha256"]:
        raise ValueError("Snapshot checksum mismatch")
    occupied = await db.all(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' LIMIT 1"
    )
    if occupied:
        raise ValueError("Recovery proof requires an empty isolated target database")
    statements = [Statement("PRAGMA defer_foreign_keys=ON")]
    statements.extend(
        Statement(entry["sql"]) for entry in content["schema"] if entry["type"] == "table"
    )
    for table in content["tables"]:
        names = ",".join(identifier(c["name"]) for c in table["columns"])
        values = ",".join(
            "CAST(? AS INTEGER)" if c["type"] == "INTEGER" else "?" for c in table["columns"]
        )
        sql = f"INSERT INTO {identifier(table['name'])} ({names}) VALUES ({values})"
        statements.extend(
            Statement(sql, tuple(row[c["name"]] for c in table["columns"])) for row in table["rows"]
        )
    statements.extend(
        Statement(entry["sql"]) for entry in content["schema"] if entry["type"] == "index"
    )
    statements.extend(
        [
            assertion("restore-validation", "NOT EXISTS(SELECT 1 FROM pragma_foreign_key_check)"),
            Statement("DELETE FROM cf_assertions WHERE command_id='restore-validation'"),
        ]
    )
    if len(statements) > MAX_RESTORE_STATEMENTS:
        raise ValueError("Proof restore exceeds its bounded single-batch limit")
    await db.batch(statements)


async def round_trip(env, key: str) -> dict:
    source = await capture(D1Database(env.DB))
    payload = canonical(source)
    await env.BACKUPS.put(key, payload, {"httpMetadata": {"contentType": "application/json"}})
    stored = await env.BACKUPS.get(key)
    if stored is None:
        raise ValueError("R2 snapshot could not be read back")
    downloaded = json.loads(await stored.text())
    await restore(D1Database(env.RESTORE_DB), downloaded)
    recovered = await capture(D1Database(env.RESTORE_DB))
    if recovered["sha256"] != source["sha256"]:
        raise ValueError("Restored schema or exact row values do not match the source")
    return {
        "key": key,
        "sha256": source["sha256"],
        "tables": len(source["content"]["tables"]),
        "rows": sum(len(t["rows"]) for t in source["content"]["tables"]),
        "exact_match": True,
    }
