"""Primary D1 FX snapshots: document units per one base unit."""

from decimal import Decimal

from erp_cf.db import assertion
from erp_cf.errors import ValidationError
from erp_cf.money import money


async def snapshot(plan, currency, on_date, total):
    org = await plan.repository("core_organizations").get(plan.org_id)
    plan.guard("core_organizations", org)
    rate = Decimal("1.00000000")
    if currency != org["base_currency"]:
        rows = await plan.repository("core_fx_rates").find(
            "currency=? AND rate_date<=?",
            (currency, on_date.isoformat()),
            order_by="rate_date DESC",
            limit=1,
        )
        if not rows:
            raise ValidationError(f"No FX rate for {currency} on or before {on_date}")
        row = rows[0]
        plan.guard("core_fx_rates", row)
        plan.statements.append(
            assertion(
                plan.identity,
                "NOT EXISTS(SELECT 1 FROM core_fx_rates WHERE org_id=? AND currency=? "
                "AND rate_date>? AND rate_date<=?)",
                (plan.org_id, currency, row["rate_date"].isoformat(), on_date.isoformat()),
            )
        )
        rate = row["rate"]
    if rate <= 0:
        raise ValidationError("FX rate must be positive")
    return {"fx_rate": rate, "total_base": money(total / rate)}
