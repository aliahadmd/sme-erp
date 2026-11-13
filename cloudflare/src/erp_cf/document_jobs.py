"""Bounded quote maintenance plans, ready for the scheduler command path."""


async def expire_quotes(plan, on_date, *, after="", limit=200):
    rows = await plan.repository("sales_quotations").find(
        "status='sent' AND valid_until<? AND id>?",
        (on_date.isoformat(), after),
        order_by="id",
        limit=limit,
    )
    for row in rows:
        plan.update("sales_quotations", row, {"status": "expired"})
    return {
        "expired": len(rows),
        "cursor": rows[-1]["id"] if rows else after,
        "has_more": len(rows) == limit,
    }
