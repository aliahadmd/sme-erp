"""Bounded primary reads validated against the business command commit epoch.

Every supported business writer (including jobs) must use UnitOfWork. Bootstrap
also advances the epoch. Direct maintenance/restore writes must quiesce readers
and writers or advance it in the same batch. Authentication/session writes do
not alter reporting inputs. This is application validation, not D1 snapshot API.
"""

from erp_cf.db import Statement, plain
from erp_cf.errors import DomainError


class ConsistentReader:
    page_size = 500

    def __init__(self, db, org_id):
        self.db, self.org_id = db, str(org_id)

    def epoch_query(self):
        return Statement(
            "SELECT CAST(version AS TEXT) AS version FROM cf_business_epochs WHERE org_id=?",
            (self.org_id,),
        )

    async def epoch(self):
        query = self.epoch_query()
        row = await self.db.first(query.sql, query.params)
        return row["version"] if row else "0"

    async def run(self, build, *, initial=()):
        for _ in range(3):
            if initial:
                results = await self.db.batch([self.epoch_query(), *initial])
                pages = [[plain(row) for row in result["results"]] for result in results]
                before = pages[0][0]["version"] if pages[0] else "0"
                result = await build(pages[1:])
            else:
                before = await self.epoch()
                result = await build()
            if before == await self.epoch():
                return result
        raise DomainError(
            "Data changed while reading the report. Please retry.", code="report_busy", status=503
        )

    def page(self, projection, source, where, params, *, cursors, cursor=None):
        # All SQL fragments are internal constants; user values stay bound.
        cursor = cursor if cursor is not None else tuple("0" for _ in cursors)
        projected = ",".join(
            f"CAST({value} AS TEXT) AS _cursor{i}" for i, value in enumerate(cursors)
        )
        if len(cursors) == 1:
            predicate = f"{cursors[0]}>CAST(? AS INTEGER)"
        else:
            predicate = (
                "("
                + ",".join(cursors)
                + ")>("
                + ",".join("CAST(? AS INTEGER)" for _ in cursors)
                + ")"
            )
        return Statement(
            f"SELECT {projected},{projection} FROM {source} "
            f"WHERE ({where}) AND ({predicate}) ORDER BY " + ",".join(cursors) + " LIMIT ?",
            (*params, *cursor, self.page_size),
        )

    async def rows(self, projection, source, where, params, *, cursors, initial_rows=None):
        cursor = None
        while True:
            if initial_rows is not None:
                rows, initial_rows = initial_rows, None
            else:
                query = self.page(projection, source, where, params, cursors=cursors, cursor=cursor)
                rows = await self.db.all(query.sql, query.params)
            for row in rows:
                yield row
            if len(rows) < self.page_size:
                return
            cursor = tuple(rows[-1][f"_cursor{i}"] for i in range(len(cursors)))
