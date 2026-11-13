"""Headers, matching lines and pagination totals use one D1 read snapshot."""

from erp_cf.db import Statement, plain
from erp_cf.errors import NotFoundError
from erp_cf.store import tables


async def read_documents(
    db, org_id, kind, *, limit=100, offset=0, identity=None, status=None, q=None, party_id=None
):
    conditions, values = ["d.org_id=?"], [org_id]
    if identity:
        conditions.append("d.id=?")
        values.append(str(identity))
    if status:
        conditions.append("d.status=?")
        values.append(status)
    if q:
        conditions.append("(d._number_fold LIKE ? OR d._party_fold LIKE ?)")
        values.extend(["%" + q.casefold() + "%"] * 2)
    if party_id:
        conditions.append(f"d.{kind.party}=?")
        values.append(str(party_id))
    where = " AND ".join(conditions)
    selected = (
        f"FROM {kind.table} d WHERE {where} ORDER BY d.created_at DESC,d.id DESC LIMIT ? OFFSET ?"
    )
    head, line = tables()[kind.table], tables()[kind.lines]
    line_order = "l.position,l.id" if "position" in line.fields else "l.id"
    results = await db.batch(
        [
            Statement(f"SELECT {head.projection('d')} {selected}", (*values, limit, offset)),
            Statement(
                f"SELECT {line.projection('l')} FROM {kind.lines} l WHERE l.{kind.parent} "
                f"IN (SELECT d.id {selected}) ORDER BY {line_order}",
                (*values, limit, offset),
            ),
            Statement(
                f"SELECT CAST(COUNT(*) AS TEXT) AS total FROM {kind.table} d WHERE {where}",
                tuple(values),
            ),
        ]
    )
    headers = [head.decode(plain(r)) for r in results[0]["results"]]
    children = {r["id"]: [] for r in headers}
    for record in results[1]["results"]:
        row = line.decode(plain(record))
        children[row[kind.parent]].append(row)
    items = [{**row, "lines": children[row["id"]]} for row in headers]
    if identity:
        if not items:
            raise NotFoundError(f"{kind.label.capitalize()} not found")
        return items[0]
    return {
        "items": items,
        "total": int(plain(results[2]["results"][0])["total"]),
        "limit": limit,
        "offset": offset,
    }
