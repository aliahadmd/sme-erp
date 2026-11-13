"""Shared order/quotation plans, with portable contracts and exact snapshots."""

from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from app.modules.purchasing import schemas as purchasing
from app.modules.sales import schemas as sales
from app.shared.document_states import ORDER_TRANSITIONS, QUOTE_TRANSITIONS
from app.shared.totals import compute_line, compute_totals

from erp_cf.db import assertion
from erp_cf.errors import ConflictError, ValidationError
from erp_cf.fx import snapshot
from erp_cf.numbering import next_number
from erp_cf.store import decimal_values, tables


@dataclass(frozen=True)
class DocumentKind:
    table: str
    lines: str
    parent: str
    entity: str
    audit_type: str
    party: str
    party_name: str
    date: str
    due: str
    permission: str
    label: str
    create: type
    update: type
    out: type
    move: str = ""
    purchase: bool = False


SALES = DocumentKind(
    "sales_sales_orders",
    "sales_sales_order_lines",
    "order_id",
    "sales_order",
    "sales_order",
    "customer_id",
    "customer_name",
    "order_date",
    "expected_date",
    "sales.order",
    "sales order",
    sales.OrderCreateIn,
    sales.OrderUpdateIn,
    sales.OrderOut,
    "qty_delivered",
)
PURCHASE = DocumentKind(
    "purchasing_purchase_orders",
    "purchasing_purchase_order_lines",
    "order_id",
    "purchase_order",
    "purchase_order",
    "supplier_id",
    "supplier_name",
    "order_date",
    "expected_date",
    "purchasing.order",
    "purchase order",
    purchasing.OrderCreateIn,
    purchasing.OrderUpdateIn,
    purchasing.OrderOut,
    "qty_received",
    True,
)
QUOTE = DocumentKind(
    "sales_quotations",
    "sales_quotation_lines",
    "quotation_id",
    "quotation",
    "sales.quotation",
    "customer_id",
    "customer_name",
    "quote_date",
    "valid_until",
    "sales.quote",
    "quotation",
    sales.QuoteCreateIn,
    sales.QuoteUpdateIn,
    sales.QuoteOut,
)
KINDS = {"sales": SALES, "purchasing": PURCHASE, "quotation": QUOTE}


def today():
    return datetime.now(UTC).date()


def totals(lines, *, quotation=False):
    return asdict(
        compute_totals(
            [(r["qty"], r["unit_price"], r["discount_pct"], r["tax_rate_pct"]) for r in lines],
            round_gross_per_line=not quotation,
        )
    )


class DocumentPlan:
    def __init__(self, plan, kind):
        self.plan, self.kind = plan, kind
        self.references = {}

    async def reference(self, table, identity, message):
        key = (table, str(identity))
        if key not in self.references:
            row = await self.plan.repository(table).get(str(identity), required=False)
            if row is None:
                raise ValidationError(message)
            self.plan.guard(table, row)
            self.references[key] = row
        return self.references[key]

    async def preload(self, table, identities):
        missing = {
            str(value)
            for value in identities
            if value is not None and (table, str(value)) not in self.references
        }
        rows = await self.plan.repository(table).many(missing, required=False)
        for identity, row in rows.items():
            self.plan.guard(table, row)
            self.references[(table, identity)] = row

    async def party(self, identity):
        party = await self.reference("crm_contacts", identity, "Unknown contact")
        if party["status"] != "active":
            raise ValidationError(f"{self.kind.label.capitalize()} requires an active contact")
        if not party["is_supplier" if self.kind.purchase else "is_customer"]:
            raise ValidationError(
                "Contact is not a supplier" if self.kind.purchase else "Contact is not a customer"
            )
        return party

    async def build_lines(self, inputs):
        if not inputs:
            raise ValidationError(f"{self.kind.label.capitalize()} needs at least one line")
        await self.preload("catalog_products", [item.product_id for item in inputs])
        products = [
            row for (table, _), row in self.references.items() if table == "catalog_products"
        ]
        await self.preload("catalog_uoms", [row["uom_id"] for row in products])
        await self.preload(
            "catalog_taxes",
            [item.tax_id for item in inputs]
            + [row["purchase_tax_id" if self.kind.purchase else "sale_tax_id"] for row in products],
        )
        lines = []
        for position, item in enumerate(inputs):
            data = item.model_dump()
            data["position"] = position
            if data.get("product_id"):
                product = await self.reference(
                    "catalog_products", data["product_id"], "Unknown product in line"
                )
                data["product_name"] = product["name"]
                if data["unit_price"] is None:
                    data["unit_price"] = product[
                        "cost_price" if self.kind.purchase else "sale_price"
                    ]
                if product["uom_id"]:
                    uom = await self.reference("catalog_uoms", product["uom_id"], "Unknown unit")
                    data["uom_code"] = uom["code"]
                if not data["tax_id"]:
                    data["tax_id"] = product[
                        "purchase_tax_id" if self.kind.purchase else "sale_tax_id"
                    ]
            if data.get("unit_price") is None:
                raise ValidationError("Line unit price is required")
            data["tax_rate_pct"] = 0
            if data.get("tax_id"):
                tax = await self.reference("catalog_taxes", data["tax_id"], "Unknown tax in line")
                data["tax_rate_pct"] = tax["rate_pct"]
            data = decimal_values(data, self.kind.lines)
            if data["qty"] <= 0:
                raise ValidationError("Line quantity must be positive after rounding")
            math = compute_line(
                data["qty"], data["unit_price"], data["discount_pct"], data["tax_rate_pct"]
            )
            lines.append(
                {**data, "line_subtotal": math.base, "line_tax": math.tax, "line_total": math.total}
            )
        return lines

    async def load(self, identity):
        row = await self.plan.repository(self.kind.table).get(str(identity))
        lines = await self.plan.repository(self.kind.lines).find(
            f"{self.kind.parent}=?", (row["id"],), order_by="position,id"
        )
        return {**row, "lines": lines}

    def add_lines(self, identity, lines):
        return [
            self.plan.add(self.kind.lines, {self.kind.parent: identity, **line}) for line in lines
        ]

    async def create(self, payload):
        cfg = self.kind
        body = cfg.create.model_validate(payload["body"])
        data = body.model_dump(exclude={"lines"})
        party = await self.party(data[cfg.party])
        org = await self.plan.repository("core_organizations").get(self.plan.org_id)
        data[cfg.party], data[cfg.party_name] = party["id"], party["name"]
        data[cfg.date] = data[cfg.date] or today()
        data["currency"] = data["currency"] or party["currency"] or org["base_currency"]
        lines = await self.build_lines(body.lines)
        data.update(totals(lines, quotation=cfg is QUOTE))
        data.update(await snapshot(self.plan, data["currency"], data[cfg.date], data["total"]))
        data.update(number=await next_number(self.plan, cfg.entity), created_by=self.plan.actor_id)
        row = self.plan.add(cfg.table, data)
        row["lines"] = self.add_lines(row["id"], lines)
        self.plan.audit(
            "create",
            cfg.audit_type,
            row["id"],
            after={"number": row["number"], "total": str(row["total"])},
        )
        return cfg.out.model_validate(row)

    async def update(self, payload):
        cfg = self.kind
        body = cfg.update.model_validate(payload["body"])
        row = await self.load(payload["id"])
        if row["status"] != "draft":
            raise ConflictError(f"Only draft {cfg.label}s can be edited (status: {row['status']})")
        changes = body.model_dump(exclude_unset=True)
        replace = changes.pop("lines", None) is not None
        if changes.get(cfg.party):
            party = await self.party(changes[cfg.party])
            changes[cfg.party], changes[cfg.party_name] = party["id"], party["name"]
        else:
            changes.pop(cfg.party, None)
        if not changes.get("currency"):
            changes.pop("currency", None)
        if cfg.date in changes and changes[cfg.date] is None:
            raise ValidationError("Document date cannot be null")
        lines = row["lines"]
        if replace:
            lines = await self.build_lines(body.lines)
            changes.update(totals(lines, quotation=cfg is QUOTE))
        merged = {**row, **changes}
        changes.update(
            await snapshot(self.plan, merged["currency"], merged[cfg.date], merged["total"])
        )
        updated = self.plan.update(cfg.table, row, changes)
        if replace:
            for line in row["lines"]:
                self.plan.delete(cfg.lines, line)
            lines = self.add_lines(row["id"], lines)
        updated["lines"] = lines
        self.plan.audit("update", cfg.audit_type, row["id"], after={"number": row["number"]})
        return cfg.out.model_validate(updated)

    async def transition(self, payload, action):
        cfg = self.kind
        row = await self.load(payload["id"])
        quote = cfg is QUOTE
        current = row["status"]
        if quote and current == "sent" and row["valid_until"] and row["valid_until"] < today():
            current = "expired"
        target = {
            "confirm": "confirmed",
            "send": "sent",
            "accept": "accepted",
            "reject": "rejected",
            "cancel": "cancelled",
        }[action]
        transitions = QUOTE_TRANSITIONS if quote else ORDER_TRANSITIONS
        if target not in transitions.get(current, set()):
            raise ConflictError(f"Cannot move {cfg.label} from '{current}' to '{target}'")
        changes = {"status": target}
        for line in row["lines"]:
            self.plan.guard(cfg.lines, line)
        if action == "confirm":
            if not row["lines"]:
                raise ValidationError("Cannot confirm a document without lines")
            changes.update(confirmed_at=today(), confirmed_by=self.plan.actor_id)
            self.plan.emit(
                f"{cfg.entity}.confirmed", {"order_id": row["id"], "number": row["number"]}
            )
        if quote and action in {"send", "accept"}:
            changes["sent_at" if action == "send" else "accepted_at"] = today()
        if not quote and action == "cancel":
            if any(line[cfg.move] > 0 or line["qty_invoiced"] > 0 for line in row["lines"]):
                raise ConflictError(
                    f"{cfg.label.capitalize()} has goods moved or invoiced — "
                    "void those documents first"
                )
            self.plan.statements.append(
                assertion(
                    self.plan.identity,
                    f"NOT EXISTS(SELECT 1 FROM {cfg.lines} WHERE order_id=? "
                    f"AND ({cfg.move}>0 OR qty_invoiced>0))",
                    (row["id"],),
                )
            )
        updated = self.plan.update(cfg.table, row, changes)
        updated["lines"] = row["lines"]
        self.plan.audit(
            action, cfg.audit_type, row["id"], after={"number": row["number"], "status": target}
        )
        return cfg.out.model_validate(updated)


async def convert_quote(plan, actor, payload):
    quote = await DocumentPlan(plan, QUOTE).load(payload["id"])
    if quote["status"] != "accepted":
        raise ConflictError(f"Cannot move quotation from '{quote['status']}' to 'converted'")
    if not quote["customer_id"] or not quote["lines"]:
        raise ValidationError("Quotation needs a customer and lines to convert")
    await DocumentPlan(plan, QUOTE).party(quote["customer_id"])
    lines = []
    for line in quote["lines"]:
        plan.guard(QUOTE.lines, line)
        lines.append(
            {
                k: v
                for k, v in line.items()
                if k in tables()[SALES.lines].fields and k not in {"id", "order_id"}
            }
        )
    data = {
        "number": await next_number(plan, "sales_order"),
        "customer_id": quote["customer_id"],
        "customer_name": quote["customer_name"],
        "order_date": today(),
        "currency": quote["currency"],
        "created_by": actor.id,
        "notes": f"From {quote['number']}" + (f" — {quote['notes']}" if quote["notes"] else ""),
        **totals(lines),
    }
    data.update(await snapshot(plan, data["currency"], data["order_date"], data["total"]))
    order = plan.add(SALES.table, data)
    DocumentPlan(plan, SALES).add_lines(order["id"], lines)
    plan.update(QUOTE.table, quote, {"status": "converted", "converted_order_id": order["id"]})
    plan.audit(
        "convert",
        QUOTE.audit_type,
        quote["id"],
        after={"number": quote["number"], "order": order["number"]},
    )
    plan.audit(
        "create",
        SALES.audit_type,
        order["id"],
        after={"number": order["number"], "from_quotation": quote["number"]},
    )
    return {
        "order_id": order["id"],
        "order_number": order["number"],
        "quotation_number": quote["number"],
    }


def command(kind, action):
    async def run(plan, actor, payload):
        document = DocumentPlan(plan, kind)
        if action in {"create", "update"}:
            return await getattr(document, action)(payload)
        return await document.transition(payload, action)

    return run


COMMANDS = {"sales.quote.convert": ("sales.order.create", 200, convert_quote)}
for kind in KINDS.values():
    for action in [
        "create",
        "update",
        "cancel",
        *(["send", "accept", "reject"] if kind is QUOTE else ["confirm"]),
    ]:
        permission = "update" if action in {"send", "accept", "reject"} else action
        COMMANDS[f"{kind.permission}.{action}"] = (
            f"{kind.permission}.{permission}",
            201 if action == "create" else 200,
            command(kind, action),
        )
