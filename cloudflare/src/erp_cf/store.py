"""Typed, lossless D1 repositories and a native atomic command plan.

Reads never project financial INTEGERs as JavaScript Numbers. Writes are
prepared SQL collected into one guarded D1 batch, including audit/outbox rows.
All SQL fragments passed here are application code; user values are parameters.
"""

import copy
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, DecimalException
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from pydantic import BaseModel

from erp_cf.db import AtomicCommands, CommandConflict, Statement, assertion
from erp_cf.errors import ConflictError, NotFoundError, ValidationError
from erp_cf.money import INT64_MAX, INT64_MIN, INTEGER, coefficient, from_coefficient, quantize

SEARCH_COLUMNS = {
    "crm_contacts": {"name": "_name_fold", "code": "_code_fold", "legal_name": "_legal_fold"},
    "catalog_products": {"name": "_name_fold", "sku": "_sku_fold"},
    "sales_sales_orders": {"number": "_number_fold", "customer_name": "_party_fold"},
    "purchasing_purchase_orders": {"number": "_number_fold", "supplier_name": "_party_fold"},
    "sales_quotations": {"number": "_number_fold", "customer_name": "_party_fold"},
}


def now() -> datetime:
    return datetime.now(UTC)


def wire(value):
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): wire(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [wire(v) for v in value]
    return value


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@dataclass(frozen=True)
class Column:
    name: str
    kind: str
    nullable: bool
    default_json: str
    precision: int = 18
    scale: int = 0
    max_length: int | None = None

    @classmethod
    def load(cls, data):
        return cls(
            **{k: v for k, v in data.items() if k != "default"},
            default_json=json.dumps(data.get("default")),
        )

    def initial(self):
        default = json.loads(self.default_json)
        if isinstance(default, dict):
            if default.get("factory") == "uuid7":
                return str(uuid.uuid7())
            if default.get("factory") == "now":
                return now()
            return copy.deepcopy(default["value"])
        return default

    @property
    def placeholder(self):
        return "CAST(? AS INTEGER)" if self.kind in {"decimal", "int", "bool"} else "?"

    def encode(self, value):
        if value is None:
            if not self.nullable:
                raise ValueError(f"{self.name} cannot be null")
            return None
        if self.kind == "decimal":
            return coefficient(value, self.scale, self.precision)
        if self.kind == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"{self.name} must be a boolean")
            return "1" if value else "0"
        if self.kind == "int":
            if isinstance(value, bool) or not isinstance(value, int | str):
                raise ValueError(f"{self.name} must be an integer")
            if isinstance(value, str) and not INTEGER.fullmatch(value):
                raise ValueError(f"{self.name} must be an integer")
            if not INT64_MIN <= int(value) <= INT64_MAX:
                raise ValueError(f"{self.name} exceeds integer storage")
            return str(int(value))
        if self.kind == "uuid":
            return str(uuid.UUID(str(value)))
        if self.kind == "json":
            return json.dumps(wire(value), separators=(",", ":"), allow_nan=False)
        if self.kind == "datetime":
            if isinstance(value, str):
                value = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError(f"{self.name} requires a timezone-aware datetime")
            return value.astimezone(UTC).isoformat()
        if self.kind == "date":
            if isinstance(value, str):
                value = date.fromisoformat(value)
            if not isinstance(value, date) or isinstance(value, datetime):
                raise ValueError(f"{self.name} requires a date")
            return value.isoformat()
        if not isinstance(value, str):
            raise ValueError(f"{self.name} must be a string")
        if self.max_length is not None and len(value) > self.max_length:
            raise ValueError(f"{self.name} exceeds {self.max_length} characters")
        return value

    def decode(self, value):
        if value is None:
            return None
        if self.kind == "decimal":
            return from_coefficient(value, self.scale)
        if self.kind in {"int", "bool"}:
            if not isinstance(value, str) or not INTEGER.fullmatch(value):
                raise ValueError("D1 integers must be projected as text")
            return bool(int(value)) if self.kind == "bool" else int(value)
        if self.kind == "json":
            return json.loads(value)
        if self.kind == "datetime":
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        if self.kind == "date":
            return date.fromisoformat(value)
        return value


@dataclass(frozen=True)
class Table:
    name: str
    columns: tuple[Column, ...]
    primary: tuple[str, ...]
    owner: tuple[str, str, str] | None = None

    @property
    def fields(self):
        return {c.name: c for c in self.columns}

    def projection(self, alias: str | None = None):
        prefix = quote(alias) + "." if alias else ""
        columns = []
        for col in self.columns:
            name = prefix + quote(col.name)
            if col.kind in {"decimal", "int", "bool"}:
                name = f"CAST({name} AS TEXT)"
            columns.append(name + " AS " + quote(col.name))
        columns.append(f"CAST({prefix}_revision AS TEXT) AS _revision")
        return ",".join(columns)

    def decode(self, row):
        return {c.name: c.decode(row[c.name]) for c in self.columns} | {
            "_revision": int(row["_revision"]),
        }

    def insert(self, data: dict):
        unknown = set(data) - set(self.fields)
        if unknown:
            raise ValueError(f"Unknown {self.name} fields: {sorted(unknown)}")
        record = {c.name: data[c.name] if c.name in data else c.initial() for c in self.columns}
        encoded = tuple(c.encode(record[c.name]) for c in self.columns)
        sql = (
            f"INSERT INTO {quote(self.name)} ("
            + ",".join(quote(c.name) for c in self.columns)
            + ") VALUES ("
            + ",".join(c.placeholder for c in self.columns)
            + ")"
        )
        # Normalize records through the same codec as subsequent D1 reads.
        normalized = {c.name: c.decode(v) for c, v in zip(self.columns, encoded, strict=True)}
        return Statement(sql, encoded), normalized | {"_revision": 0}


@lru_cache(maxsize=1)
def tables():
    data = json.loads(Path(__file__).with_name("business_schema.json").read_text())
    definitions = {t["name"]: t for t in data["tables"]}
    owners = {}
    for name, definition in definitions.items():
        parents = [
            f
            for f in definition["foreign_keys"]
            if f["on_delete"] == "CASCADE"
            and any(c["name"] == "org_id" for c in definitions[f["table"]]["columns"])
        ]
        if len(parents) > 1:
            raise ValueError(f"Ambiguous organization ownership for {name}")
        parent = parents[0] if parents else None
        owners[name] = (
            (parent["columns"][0], parent["table"], parent["references"][0]) if parent else None
        )
    return MappingProxyType(
        {
            t["name"]: Table(
                t["name"],
                tuple(Column.load(c) for c in t["columns"]),
                tuple(t["primary_key"]),
                owners[t["name"]],
            )
            for t in data["tables"]
        }
    )


def decimal_values(data, table):
    result = dict(data)
    for name, column in tables()[table].fields.items():
        if column.kind == "decimal" and name in result and result[name] is not None:
            try:
                value = quantize(result[name], column.scale)
                coefficient(value, column.scale, column.precision)
            except (DecimalException, ValueError) as exc:
                raise ValidationError(f"Invalid precision for {name}") from exc
            result[name] = value
    return result


class Repository:
    def __init__(self, db, name: str, org_id: str | None = None):
        self.db = db
        self.table = tables()[name]
        self.org_id = org_id

    def condition(self, where, params):
        if self.org_id is not None:
            if "org_id" in self.table.fields:
                return f"({where}) AND org_id=?", (*params, self.org_id)
            if self.table.owner:
                local, parent, remote = self.table.owner
                return (
                    f"({where}) AND {quote(local)} IN (SELECT {quote(remote)} "
                    f"FROM {quote(parent)} WHERE org_id=?)",
                    (*params, self.org_id),
                )
        return where, params

    async def find(self, where="1", params=(), *, order_by="", limit=None, offset=0):
        where, params = self.condition(where, params)
        sql = f"SELECT {self.table.projection()} FROM {quote(self.table.name)} WHERE {where}"
        if order_by:
            sql += " ORDER BY " + order_by
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params = (*params, limit, offset)
        return [self.table.decode(r) for r in await self.db.all(sql, params)]

    async def first(self, where="1", params=()):
        result = await self.find(where, params, limit=1)
        return result[0] if result else None

    async def get(self, identity: str, *, required=True):
        if self.table.primary != ("id",):
            raise ValueError("Use an explicit condition for composite keys")
        result = await self.first("id=?", (str(identity),))
        if result is None and required:
            raise NotFoundError(f"{self.table.name} record not found")
        return result

    async def count(self, where="1", params=()):
        where, params = self.condition(where, params)
        result = await self.db.first(
            f"SELECT CAST(COUNT(*) AS TEXT) AS n FROM {quote(self.table.name)} WHERE {where}",
            params,
        )
        return int(result["n"])

    async def page(self, limit, offset, *, where="1", params=(), order_by=""):
        return {
            "items": await self.find(where, params, order_by=order_by, limit=limit, offset=offset),
            "total": await self.count(where, params),
            "limit": limit,
            "offset": offset,
        }


class UnitOfWork:
    def __init__(
        self,
        db,
        org_id: str,
        actor_id: str,
        key: str,
        operation: str,
        payload: dict,
        *,
        fingerprint_key: str | None = None,
    ):
        self.db = db
        self.org_id = org_id
        self.actor_id = actor_id
        self.commands = AtomicCommands(db)
        self.identity = self.commands.identity(org_id, actor_id, key)
        self.request_hash = self.commands.request_hash(operation, wire(payload))
        if fingerprint_key is not None:
            self.request_hash = hmac.new(
                fingerprint_key.encode(), self.request_hash.encode(), hashlib.sha256
            ).hexdigest()
        self.statements: list[Statement] = []
        self.pending_numbers: dict[tuple, dict] = {}

    def repository(self, name: str):
        return Repository(self.db, name, self.org_id)

    def add(self, name: str, data: dict):
        table = tables()[name]
        if "org_id" in table.fields:
            data = {**data, "org_id": self.org_id}
        try:
            statement, row = table.insert(data)
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc
        self.ownership_guard(table, row)
        self.statements.append(statement)
        self.search_keys(name, row)
        return row

    def search_keys(self, name, row):
        fields = SEARCH_COLUMNS.get(name)
        if fields:
            self.statements.append(
                Statement(
                    f"UPDATE {quote(name)} SET "
                    + ",".join(quote(v) + "=?" for v in fields.values())
                    + " WHERE id=?",
                    (*(str(row.get(k) or "").casefold() for k in fields), row["id"]),
                )
            )

    def ownership_guard(self, table: Table, row: dict):
        if "org_id" in table.fields and row["org_id"] != self.org_id:
            raise NotFoundError("Record not found")
        if table.owner:
            local, parent, remote = table.owner
            self.statements.append(
                assertion(
                    self.identity,
                    f"EXISTS(SELECT 1 FROM {quote(parent)} WHERE {quote(remote)}=? AND org_id=?)",
                    (row[local], self.org_id),
                )
            )

    def guard(self, name: str, row: dict):
        table = tables()[name]
        self.ownership_guard(table, row)
        primary = " AND ".join(quote(k) + "=?" for k in table.primary)
        self.statements.append(
            assertion(
                self.identity,
                f"EXISTS(SELECT 1 FROM {quote(name)} WHERE {primary} AND _revision=?)",
                (*(row[k] for k in table.primary), row["_revision"]),
            )
        )

    def update(self, name: str, row: dict, changes: dict):
        table = tables()[name]
        fields = table.fields
        if set(changes) - set(fields) or set(changes) & (set(table.primary) | {"org_id"}):
            raise ValueError("Cannot change unknown, identity, or organization fields")
        self.guard(name, row)
        if "updated_at" in fields:
            changes = {**changes, "updated_at": now()}
        try:
            encoded = {key: fields[key].encode(value) for key, value in changes.items()}
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc
        assignments = [quote(key) + "=" + fields[key].placeholder for key in encoded]
        assignments.append("_revision=_revision+1")
        primary = " AND ".join(quote(k) + "=?" for k in table.primary)
        self.statements.append(
            Statement(
                f"UPDATE {quote(name)} SET {','.join(assignments)} WHERE {primary}",
                (*encoded.values(), *(row[k] for k in table.primary)),
            )
        )
        updated = {
            **row,
            **{k: fields[k].decode(v) for k, v in encoded.items()},
            "_revision": row["_revision"] + 1,
        }
        self.search_keys(name, updated)
        return updated

    def delete(self, name: str, row: dict):
        self.guard(name, row)
        table = tables()[name]
        self.statements.append(
            Statement(
                f"DELETE FROM {quote(name)} WHERE "
                + " AND ".join(quote(k) + "=?" for k in table.primary),
                tuple(row[k] for k in table.primary),
            )
        )

    def audit(
        self, action: str, entity_type: str, entity_id=None, *, before=None, after=None, ip=None
    ):
        return self.add(
            "core_audit_logs",
            {
                "user_id": self.actor_id,
                "action": action,
                "entity_type": entity_type,
                "entity_id": str(entity_id) if entity_id is not None else None,
                "before": wire(before),
                "after": wire(after),
                "ip": ip,
            },
        )

    def emit(self, event_type, payload):
        event_id = str(uuid.uuid7())
        self.statements.append(
            Statement(
                "INSERT INTO cf_outbox(id,command_id,event_type,payload_json) VALUES (?,?,?,?)",
                (
                    event_id,
                    self.identity,
                    event_type,
                    json.dumps(wire({**payload, "org_id": self.org_id}), separators=(",", ":")),
                ),
            )
        )
        return event_id

    async def replay(self):
        try:
            return await self.commands.replay(self.identity, self.request_hash)
        except CommandConflict as exc:
            raise ConflictError(str(exc)) from exc

    async def commit(self, response):
        try:
            return await self.commands.execute(
                identity=self.identity,
                request_hash=self.request_hash,
                statements=self.statements,
                response=wire(response),
            )
        except CommandConflict as exc:
            raise ConflictError(str(exc)) from exc
        except Exception as exc:
            if any(
                text in str(exc)
                for text in [
                    "CHECK constraint failed",
                    "UNIQUE constraint failed",
                    "FOREIGN KEY constraint failed",
                ]
            ):
                raise ConflictError("The command conflicts with current data") from exc
            raise
