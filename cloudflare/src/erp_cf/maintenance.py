"""Database-enforced, expiring write pause for consistent paginated backups.

The lease is global to one D1 database. It is not an in-memory/DO mutex: DML
triggers cover commands, authentication and transport metadata in every Worker.
Only ephemeral SQL assertions and this control row remain writable while held.
"""

import uuid
from dataclasses import dataclass

from erp_cf.db import Statement, assertion, plain
from erp_cf.errors import ConflictError, DomainError, ValidationError

CLOCK_SQL = "CAST(strftime('%s','now') AS INTEGER)*1000"
EXEMPT = {"cf_assertions", "platform_maintenance"}
TABLES_SQL = (
    "SELECT name FROM sqlite_schema WHERE type='table' "
    "AND name NOT GLOB 'sqlite_*' AND name NOT GLOB '_cf_*' ORDER BY name"
)


def identifier(name):
    return '"' + name.replace('"', '""') + '"'


def guard_name(table, operation):
    return f"cf_quiesce_{table}_{operation.lower()}"


def guard_sql(table, operation):
    return (
        f"CREATE TRIGGER {identifier(guard_name(table, operation))} "
        f"BEFORE {operation} ON {identifier(table)} "
        f"WHEN EXISTS(SELECT 1 FROM platform_maintenance WHERE singleton=1 "
        f"AND owner IS NOT NULL AND expires_at>{CLOCK_SQL}) "
        "BEGIN SELECT RAISE(ABORT,'erp_maintenance'); END"
    )


class LeaseLost(DomainError):
    def __init__(self):
        super().__init__(
            "The backup write-pause lease expired or changed",
            code="maintenance_lease_lost",
            status=409,
        )


@dataclass(frozen=True)
class Lease:
    owner: str
    fence: str

    def guard(self, identity):
        return assertion(
            identity,
            "EXISTS(SELECT 1 FROM platform_maintenance WHERE singleton=1 "
            f"AND owner=? AND fence=CAST(? AS INTEGER) AND expires_at>{CLOCK_SQL})",
            (self.owner, self.fence),
        )


def lifetime(seconds):
    if type(seconds) is not int or not 10 <= seconds <= 900:
        raise ValidationError("Write-pause leases must last 10-900 seconds")
    return str(seconds * 1000)


async def coverage(db):
    """Refuse to freeze an upgraded schema with an unguarded writer/table."""
    tables = await db.all(TABLES_SQL)
    triggers = await db.all("SELECT name,sql FROM sqlite_schema WHERE type='trigger'")
    by_name = {row["name"]: row["sql"] for row in triggers}
    for row in tables:
        table = row["name"]
        if table in EXEMPT:
            continue
        for operation in ["INSERT", "UPDATE", "DELETE"]:
            if by_name.get(guard_name(table, operation)) != guard_sql(table, operation):
                raise DomainError(
                    "The schema is missing a verified maintenance guard",
                    code="maintenance_guard_missing",
                    status=503,
                )
    return [row["name"] for row in tables]


async def state(db):
    row = await db.first(
        "SELECT purpose,CAST(fence AS TEXT) AS fence,CAST(expires_at AS TEXT) AS expires_at,"
        f"owner IS NOT NULL AND expires_at>{CLOCK_SQL} AS active "
        "FROM platform_maintenance WHERE singleton=1"
    )
    return row or {"purpose": None, "fence": "0", "expires_at": None, "active": 0}


async def acquire(db, owner, *, seconds=300):
    duration = lifetime(seconds)
    if not isinstance(owner, str) or not 1 <= len(owner) <= 128:
        raise ValidationError("Invalid backup owner")
    await coverage(db)
    identity = "pause-" + uuid.uuid4().hex
    statements = [
        assertion(
            identity,
            "NOT EXISTS(SELECT 1 FROM platform_maintenance WHERE singleton=1 "
            f"AND owner IS NOT NULL AND owner!=? AND expires_at>{CLOCK_SQL})",
            (owner,),
        ),
        # A mail send must be able to persist its result before we pause all DML.
        # New prepares cannot cross the trigger-protected claim after acquisition.
        assertion(
            identity,
            "NOT EXISTS(SELECT 1 FROM cf_mail_outbox "
            "WHERE status='uncertain' AND julianday(claim_until)>julianday('now'))",
        ),
        Statement(
            "INSERT INTO platform_maintenance(singleton,owner,purpose,fence,expires_at) "
            f"VALUES (1,?,'backup',1,{CLOCK_SQL}+CAST(? AS INTEGER)) "
            "ON CONFLICT(singleton) DO UPDATE SET owner=excluded.owner,purpose='backup',"
            "fence=CASE WHEN platform_maintenance.owner=excluded.owner "
            f"AND platform_maintenance.expires_at>{CLOCK_SQL} "
            "THEN platform_maintenance.fence ELSE platform_maintenance.fence+1 END,"
            "expires_at=excluded.expires_at",
            (owner, duration),
        ),
        Statement("DELETE FROM cf_assertions WHERE command_id=?", (identity,)),
        Statement(
            "SELECT CAST(fence AS TEXT) AS fence FROM platform_maintenance WHERE singleton=1"
        ),
    ]
    try:
        result = await db.batch(statements)
    except Exception as exc:
        if "CHECK constraint failed" in str(exc):
            raise ConflictError(
                "The database is paused or a mail send is still in progress"
            ) from exc
        raise
    return Lease(owner, plain(result[-1]["results"][0])["fence"])


async def protected(db, lease, statements):
    identity = "paused-read-" + uuid.uuid4().hex
    try:
        result = await db.batch(
            [
                lease.guard(identity),
                *statements,
                Statement("DELETE FROM cf_assertions WHERE command_id=?", (identity,)),
            ]
        )
    except Exception as exc:
        if "CHECK constraint failed" in str(exc):
            raise LeaseLost() from exc
        raise
    return result[1:-1]


async def renew(db, lease, *, seconds=300):
    await protected(
        db,
        lease,
        [
            Statement(
                f"UPDATE platform_maintenance SET expires_at={CLOCK_SQL}+CAST(? AS INTEGER) "
                "WHERE singleton=1",
                (lifetime(seconds),),
            )
        ],
    )


async def release(db, lease):
    await protected(
        db,
        lease,
        [
            Statement(
                "UPDATE platform_maintenance SET owner=NULL,purpose=NULL,expires_at=NULL "
                "WHERE singleton=1"
            )
        ],
    )
