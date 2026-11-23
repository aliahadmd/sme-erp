"""Apply reviewed D1 migrations with atomic SHA-256 receipts using the pinned cf CLI.

Only the verification and public-demo application databases are selectable. Read-only planning
is the default; --apply executes missing migrations. A lost command response is
reconciled against its durable receipt before further work. Never reset a D1
database or rewrite an already applied migration to resolve an error.
"""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATABASES = {
    "app-staging": "0f278204-3190-49e1-96d2-a9312fa57e3d",
    "production": "71ee20e1-b6de-4f2a-8f8f-4a5ecd070225",
    "demo-staging": "71ee20e1-b6de-4f2a-8f8f-4a5ecd070225",
}
RECEIPTS = "platform_schema_migrations"
CREATE_RECEIPTS = (ROOT / "migrations/0003_platform.sql").read_text()


def literal(value):
    return "'" + value.replace("'", "''") + "'"


def query(mode, sql):
    result = subprocess.run(
        ["pnpm", "exec", "cf", "d1", "query", DATABASES[mode], "--mode", mode, "--sql=" + sql],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        reason = "\n".join(result.stderr.strip().splitlines()[-5:])
        raise RuntimeError(
            "D1 command failed; inspect its durable receipt before resuming\n" + reason
        )
    try:
        response = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("D1 command returned an unreadable result; inspect its receipt") from exc
    if not isinstance(response, list) or not all(r.get("success") for r in response):
        raise RuntimeError("D1 returned an unsuccessful result")
    return [row for part in response for row in part.get("results", [])]


def migrate(execute, *, apply=False, directory=ROOT / "migrations"):
    files = sorted(directory.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in files}
    tables = execute(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*'"
    )
    if any(row["name"] == RECEIPTS for row in tables):
        receipts = {row["name"]: row["sha256"] for row in execute(f"SELECT * FROM {RECEIPTS}")}
    else:
        if tables:
            raise RuntimeError("Occupied database has no migration receipts; reconcile it first")
        receipts = {}
    for name, checksum in receipts.items():
        if hashes.get(name) != checksum:
            raise RuntimeError(f"Applied migration is missing or changed: {name}")
    if set(receipts) != {path.name for path in files[: len(receipts)]}:
        raise RuntimeError("Migration receipts are not contiguous; reconcile them first")
    missing = [path for path in files if path.name not in receipts]
    if not apply:
        return {"applied": len(receipts), "pending": [path.name for path in missing]}
    if not tables:
        execute(CREATE_RECEIPTS)
    for path in missing:
        name, checksum = path.name, hashes[path.name]
        # D1 executes all these statements in one atomic batch, including DDL
        # and the receipt. No parameters are mixed with multi-statement SQL.
        sql = (
            path.read_text()
            + f"\nINSERT INTO {RECEIPTS}(name,sha256) "
            + f"VALUES({literal(name)},{literal(checksum)});"
        )
        try:
            execute(sql)
        except RuntimeError, subprocess.TimeoutExpired:
            actual = execute(f"SELECT sha256 FROM {RECEIPTS} WHERE name={literal(name)}")
            if not actual or actual[0]["sha256"] != checksum:
                raise
        actual = execute(f"SELECT sha256 FROM {RECEIPTS} WHERE name={literal(name)}")
        if not actual or actual[0]["sha256"] != checksum:
            raise RuntimeError(f"Migration did not produce its exact receipt: {name}")
        print("Applied " + name, flush=True)
    return {"applied": len(files), "pending": []}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=DATABASES, default="app-staging")
    parser.add_argument("--apply", action="store_true")
    arguments = parser.parse_args()
    print(
        json.dumps(migrate(lambda sql: query(arguments.mode, sql), apply=arguments.apply), indent=2)
    )
