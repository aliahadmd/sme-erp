"""Fresh, bounded configuration/account reads shared within one command only."""

from datetime import UTC, datetime

from app.modules.accounting.defaults import DEFAULT_MAPPING
from app.modules.core.defaults import DEFAULT_SETTINGS

from erp_cf.db import Statement, plain
from erp_cf.store import quote

SETTING_KEYS = ("accounting.mapping", "numbering.prefixes", "inventory.guard")


async def load(plan):
    year = datetime.now(UTC).year
    definitions = []

    def select(name, where, params):
        repository = plan.repository(name)
        where, params = repository.condition(where, params)
        definitions.append(repository.table)
        return Statement(
            f"SELECT {repository.table.projection()} FROM {quote(name)} WHERE {where}", params
        )

    entities = tuple(DEFAULT_SETTINGS["numbering.prefixes"])
    keys, codes = tuple(DEFAULT_MAPPING), tuple(set(DEFAULT_MAPPING.values()))
    statements = [
        select("core_organizations", "id=?", (plan.org_id,)),
        select("core_settings", "key IN (?,?,?)", SETTING_KEYS),
        select(
            "core_document_sequences",
            "year=? AND entity IN (" + ",".join("?" for _ in entities) + ")",
            (year, *entities),
        ),
        select(
            "accounting_accounts",
            "code IN (" + ",".join("?" for _ in codes) + ") OR code IN ("
            "SELECT j.value FROM core_settings s,json_each(s.value) j "
            "WHERE s.org_id=? AND s.key='accounting.mapping' AND j.type='text' "
            "AND j.key IN (" + ",".join("?" for _ in keys) + "))",
            (*codes, plan.org_id, *keys),
        ),
    ]
    result = await plan.db.batch(statements)
    rows = [
        [table.decode(plain(row)) for row in part["results"]]
        for table, part in zip(definitions, result, strict=True)
    ]
    return {
        "year": year,
        "organization": rows[0][0] if rows[0] else None,
        "settings": {row["key"]: row for row in rows[1]},
        "sequences": {row["entity"]: row for row in rows[2]},
        "accounts": {row["code"]: row for row in rows[3]},
    }
