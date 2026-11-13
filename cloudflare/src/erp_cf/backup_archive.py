"""Rebuild control receipts from private R2 when the source database is lost."""

import json
import re

from erp_cf.backup_format import (
    FORMAT,
    MAX_MANIFEST_BYTES,
    MAX_PAGES,
    BackupError,
    canonical,
    get,
    sha,
)
from erp_cf.backup_state import load
from erp_cf.db import D1Database, Statement
from erp_cf.errors import ConflictError, PermissionDeniedError
from erp_cf.identity import from_token
from erp_cf.store import UnitOfWork, now


async def administrator(env, token):
    actor = await from_token(env, token)
    actor.require("core.settings.update")
    if not actor.is_superuser:
        raise PermissionDeniedError("Only administrators may recover database archives")
    return actor


async def import_backup(env, token, identity, key):
    actor = await administrator(env, token)
    db, bucket, org = D1Database(env.DB), env.BACKUPS, str(env.ORG_ID)
    obj = await bucket.get(f"commits/{org}/{identity}.json")
    if obj is None or int(obj.size) > 4096:
        raise BackupError("No committed archive exists for this organization/ID")
    marker = json.loads(await obj.text())
    checksum = marker.get("manifest_sha256", "")
    root = marker.get("manifest_key", "")
    if (
        marker.get("format") != FORMAT
        or marker.get("backup_id") != identity
        or marker.get("org_id") != org
        or not re.fullmatch(r"[0-9a-f]{64}", checksum)
        or not root.startswith(f"erp/{org}/{identity}/")
        or not root.endswith(f"/manifest-{checksum}.json")
    ):
        raise BackupError("Archive marker identity is invalid")
    manifest = await get(bucket, root, checksum, bound=MAX_MANIFEST_BYTES)
    base = root.rsplit("/manifest-", 1)[0] + "/"
    await get(bucket, base + "COMMITTED.json", sha(marker))
    if (
        manifest.get("format") != FORMAT
        or manifest.get("org_id") != org
        or manifest.get("backup_id") != identity
        or sha(manifest["schema"]) != manifest["schema_sha256"]
        or len(manifest["pages"]) > MAX_PAGES
    ):
        raise BackupError("Archive manifest identity/format is invalid")
    for index, ref in enumerate(manifest["pages"]):
        if int(ref["sequence"]) != index or not ref["object_key"].startswith(base + "pages/"):
            raise BackupError("Archive page sequence or namespace is invalid")
    existing = await load(db, "platform_backup_runs", identity, required=False)
    if existing:
        if (
            existing["org_id"] == org
            and existing["status"] == "complete"
            and existing["manifest_hash"] == checksum
        ):
            return {"id": identity, "status": "complete"}
        raise ConflictError("Local archive metadata conflicts with this committed snapshot")
    plan = UnitOfWork(
        db,
        org,
        actor.id,
        key,
        "backups.import",
        {"id": identity, "manifest_sha256": checksum},
        fingerprint_key=str(env.COMMAND_HASH_KEY),
    )
    old = await plan.replay()
    if old is not None:
        return old["body"]
    actor.guard(plan)
    timestamp = now().isoformat()
    plan.statements.append(
        Statement(
            "INSERT INTO platform_backup_runs(id,org_id,run_key,status,fence,schema_json,"
            "schema_hash,manifest_hash,table_index,page_index,rows,bytes,created_at,"
            "capture_started_at,updated_at,completed_at) "
            "VALUES (?,?,?,'complete',CAST(? AS INTEGER),?,?,?,CAST(? AS INTEGER),"
            "CAST(? AS INTEGER),"
            "CAST(? AS INTEGER),CAST(? AS INTEGER),?,?,?,?)",
            (
                identity,
                org,
                "import:" + identity,
                manifest["fence"],
                canonical(manifest["schema"]),
                manifest["schema_sha256"],
                checksum,
                str(len(manifest["schema"]["tables"])),
                str(len(manifest["pages"])),
                manifest["rows"],
                manifest["bytes"],
                manifest["captured_at"],
                manifest["captured_at"],
                timestamp,
                timestamp,
            ),
        )
    )
    for ref in manifest["pages"]:
        plan.statements.append(
            Statement(
                "INSERT INTO platform_backup_pages(backup_id,sequence,table_name,object_key,"
                "sha256,rows,bytes,first_cursor,last_cursor) "
                "VALUES (?,CAST(? AS INTEGER),?,?,?,CAST(? AS INTEGER),"
                "CAST(? AS INTEGER),?,?)",
                (
                    identity,
                    ref["sequence"],
                    ref["table_name"],
                    ref["object_key"],
                    ref["sha256"],
                    ref["rows"],
                    ref["bytes"],
                    ref["first_cursor"],
                    ref["last_cursor"],
                ),
            )
        )
    plan.audit("import", "system.backup", identity, after={"manifest_sha256": checksum})
    await plan.commit({"status": 200, "body": {"id": identity, "status": "complete"}})
    return {"id": identity, "status": "complete"}
