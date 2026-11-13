"""Contacts, catalog and currency commands on the shared native D1 batch path."""

import uuid
from decimal import DecimalException

from app.modules.catalog.schemas import (
    ProductCategoryIn,
    ProductCategoryOut,
    ProductIn,
    ProductOut,
    ProductUpdateIn,
    TaxIn,
    TaxOut,
    UomIn,
    UomOut,
)
from app.modules.crm.schemas import ContactArchiveIn, ContactIn, ContactOut, ContactUpdateIn
from app.modules.currencies.schemas import CurrencyIn, CurrencyOut, RateIn, RateOut

from erp_cf.db import assertion
from erp_cf.errors import ConflictError, ValidationError
from erp_cf.money import coefficient, quantize
from erp_cf.numbering import next_number
from erp_cf.store import tables


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


async def validate_links(plan, data, links):
    for field, table in links.items():
        if data.get(field):
            row = await plan.repository(table).get(str(data[field]))
            plan.guard(table, row)


async def create_contact(plan, actor, payload):
    body = ContactIn.model_validate(payload["body"])
    try:
        body.validate_flags()
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    data = body.model_dump()
    org = await plan.repository("core_organizations").get(plan.org_id)
    plan.guard("core_organizations", org)
    data["currency"] = data["currency"] or org["base_currency"]
    await validate_links(plan, data, {"owner_user_id": "core_users"})
    data["code"] = await next_number(plan, "contact")
    row = plan.add("crm_contacts", decimal_values(data, "crm_contacts"))
    plan.audit("create", "crm.contact", row["id"], after={"code": row["code"], "name": row["name"]})
    return ContactOut.model_validate(row)


async def update_contact(plan, actor, payload):
    body = ContactUpdateIn.model_validate(payload["body"])
    row = await plan.repository("crm_contacts").get(payload["id"])
    changes = body.model_dump(exclude_unset=True)
    if "currency" in changes and not changes["currency"]:
        del changes["currency"]
    merged = {**row, **changes}
    if not merged["is_customer"] and not merged["is_supplier"]:
        raise ValidationError("Contact must be a customer and/or a supplier")
    await validate_links(plan, changes, {"owner_user_id": "core_users"})
    updated = plan.update("crm_contacts", row, decimal_values(changes, "crm_contacts"))
    plan.audit("update", "crm.contact", row["id"], after={"name": updated["name"]})
    return ContactOut.model_validate(updated)


async def archive_contact(plan, actor, payload):
    body = ContactArchiveIn.model_validate(payload["body"])
    row = await plan.repository("crm_contacts").get(payload["id"])
    updated = plan.update(
        "crm_contacts", row, {"status": "archived" if body.archived else "active"}
    )
    plan.audit("archive" if body.archived else "unarchive", "crm.contact", row["id"])
    return ContactOut.model_validate(updated)


async def delete_contact(plan, actor, payload):
    row = await plan.repository("crm_contacts").get(payload["id"])
    checks = [
        ("sales_sales_orders", "customer_id"),
        ("sales_quotations", "customer_id"),
        ("purchasing_purchase_orders", "supplier_id"),
        ("invoicing_invoices", "party_id"),
        ("invoicing_payments", "party_id"),
    ]
    for table, column in checks:
        if await plan.repository(table).first(f"{column}=?", (row["id"],)):
            raise ConflictError(
                "Contact is used by orders, quotations, invoices or payments — archive it instead"
            )
    plan.statements.append(
        assertion(
            plan.identity,
            " AND ".join(
                f"NOT EXISTS(SELECT 1 FROM {table} WHERE {column}=?)" for table, column in checks
            ),
            (row["id"],) * len(checks),
        )
    )
    plan.audit(
        "delete", "crm.contact", row["id"], before={"code": row["code"], "name": row["name"]}
    )
    plan.delete("crm_contacts", row)


PRODUCT_LINKS = {
    "category_id": "catalog_product_categories",
    "uom_id": "catalog_uoms",
    "sale_tax_id": "catalog_taxes",
    "purchase_tax_id": "catalog_taxes",
}


async def create_product(plan, actor, payload):
    body = ProductIn.model_validate(payload["body"])
    if body.type == "service" and body.track_inventory:
        raise ValidationError("Services cannot track inventory")
    letters = "".join(c for c in body.name.upper() if c.isalnum())[:4] or "SKU"
    data = body.model_dump(exclude={"tags"})
    data["sku"] = (body.sku or f"{letters}-{uuid.uuid4().hex[:6]}").upper()
    if await plan.repository("catalog_products").first("sku=?", (data["sku"],)):
        raise ConflictError(f"SKU {data['sku']} already exists")
    await validate_links(plan, data, PRODUCT_LINKS)
    row = plan.add("catalog_products", decimal_values(data, "catalog_products"))
    plan.audit(
        "create", "catalog.product", row["id"], after={"sku": row["sku"], "name": row["name"]}
    )
    return ProductOut.model_validate(row)


async def update_product(plan, actor, payload):
    row = await plan.repository("catalog_products").get(payload["id"])
    changes = ProductUpdateIn.model_validate(payload["body"]).model_dump(exclude_unset=True)
    merged = {**row, **changes}
    if merged["type"] == "service" and merged["track_inventory"]:
        raise ValidationError("Services cannot track inventory")
    await validate_links(plan, changes, PRODUCT_LINKS)
    updated = plan.update("catalog_products", row, decimal_values(changes, "catalog_products"))
    plan.audit("update", "catalog.product", row["id"], after={"name": updated["name"]})
    return ProductOut.model_validate(updated)


async def archive_product(plan, actor, payload):
    row = await plan.repository("catalog_products").get(payload["id"])
    archived = payload["archived"]
    if not isinstance(archived, bool):
        raise ValidationError("Archived must be a boolean")
    updated = plan.update("catalog_products", row, {"status": "archived" if archived else "active"})
    plan.audit("archive" if archived else "unarchive", "catalog.product", row["id"])
    return ProductOut.model_validate(updated)


async def create_category(plan, actor, payload):
    data = ProductCategoryIn.model_validate(payload["body"]).model_dump()
    await validate_links(plan, data, {"parent_id": "catalog_product_categories"})
    row = plan.add("catalog_product_categories", data)
    plan.audit("create", "catalog.category", row["id"], after={"name": row["name"]})
    return ProductCategoryOut.model_validate(row)


async def delete_category(plan, actor, payload):
    row = await plan.repository("catalog_product_categories").get(payload["id"])
    if await plan.repository("catalog_products").first("category_id=?", (row["id"],)):
        raise ConflictError("Category is used by products — remove the link first")
    plan.statements.append(
        assertion(
            plan.identity,
            "NOT EXISTS(SELECT 1 FROM catalog_products WHERE category_id=?)",
            (row["id"],),
        )
    )
    plan.audit("delete", "catalog.category", row["id"], before={"name": row["name"]})
    plan.delete("catalog_product_categories", row)


async def create_coded(plan, data, table, dto, entity):
    data["code"] = data["code"].upper()
    if await plan.repository(table).first("code=?", (data["code"],)):
        raise ConflictError(f"Code {data['code']} already exists")
    row = plan.add(table, decimal_values(data, table))
    plan.audit("create", entity, row["id"], after={"code": row["code"]})
    return dto.model_validate(row)


async def create_uom(plan, actor, payload):
    return await create_coded(
        plan,
        UomIn.model_validate(payload["body"]).model_dump(),
        "catalog_uoms",
        UomOut,
        "catalog.uom",
    )


async def create_tax(plan, actor, payload):
    return await create_coded(
        plan,
        TaxIn.model_validate(payload["body"]).model_dump(),
        "catalog_taxes",
        TaxOut,
        "catalog.tax",
    )


async def update_tax(plan, actor, payload):
    row = await plan.repository("catalog_taxes").get(payload["id"])
    changes = TaxIn.model_validate(payload["body"]).model_dump(exclude_unset=True, exclude={"code"})
    updated = plan.update("catalog_taxes", row, decimal_values(changes, "catalog_taxes"))
    plan.audit(
        "update",
        "catalog.tax",
        row["id"],
        after={"code": row["code"], "rate": str(updated["rate_pct"])},
    )
    return TaxOut.model_validate(updated)


async def create_currency(plan, actor, payload):
    return await create_coded(
        plan,
        CurrencyIn.model_validate(payload["body"]).model_dump(),
        "core_currencies",
        CurrencyOut,
        "core.currency",
    )


async def put_rate(plan, actor, payload):
    data = decimal_values(RateIn.model_validate(payload["body"]).model_dump(), "core_fx_rates")
    if data["rate"] <= 0:
        raise ValidationError("FX rate rounds to zero at eight decimal places")
    data["currency"] = data["currency"].upper()
    row = await plan.repository("core_fx_rates").first(
        "currency=? AND rate_date=?", (data["currency"], data["rate_date"].isoformat())
    )
    updated = (
        plan.update("core_fx_rates", row, {"rate": data["rate"]})
        if row
        else plan.add("core_fx_rates", data)
    )
    plan.audit(
        "create",
        "core.fx_rate",
        updated["id"],
        after={"currency": data["currency"], "rate": str(updated["rate"])},
    )
    return RateOut.model_validate(updated)


COMMANDS = {
    "crm.contact.create": ("crm.contact.create", 201, create_contact),
    "crm.contact.update": ("crm.contact.update", 200, update_contact),
    "crm.contact.archive": ("crm.contact.archive", 200, archive_contact),
    "crm.contact.delete": ("crm.contact.delete", 204, delete_contact),
    "catalog.product.create": ("catalog.product.create", 201, create_product),
    "catalog.product.update": ("catalog.product.update", 200, update_product),
    "catalog.product.archive": ("catalog.product.archive", 200, archive_product),
    "catalog.category.create": ("catalog.category.create", 201, create_category),
    "catalog.category.delete": ("catalog.category.delete", 204, delete_category),
    "catalog.uom.create": ("catalog.uom.create", 201, create_uom),
    "catalog.tax.create": ("catalog.tax.create", 201, create_tax),
    "catalog.tax.update": ("catalog.tax.update", 200, update_tax),
    "core.currency.create": ("core.settings.update", 201, create_currency),
    "core.fx_rate.update": ("core.settings.update", 200, put_rate),
}
