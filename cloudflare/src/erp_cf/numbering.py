"""Number allocation is planned in the same batch as its business document."""

from datetime import UTC, datetime

from app.modules.core.defaults import DEFAULT_SETTINGS

from erp_cf.errors import ValidationError


def valid_prefix(value):
    return (
        isinstance(value, str)
        and 0 < len(value) <= 10
        and value.replace("-", "").replace("_", "").isalnum()
    )


async def next_number(plan, entity, default=None, *, year=None):
    default = default or DEFAULT_SETTINGS["numbering.prefixes"][entity]
    setting = await plan.repository("core_settings").first("key=?", ("numbering.prefixes",))
    if setting:
        plan.guard("core_settings", setting)
    chosen = (setting["value"] if setting else {}).get(entity, default)
    if not valid_prefix(chosen):
        raise ValidationError("Invalid numbering prefix")
    year = year or datetime.now(UTC).year
    key = (entity, year)
    sequence = plan.pending_numbers.get(key)
    if sequence is None:
        sequence = await plan.repository("core_document_sequences").first(
            "entity=? AND year=?", (entity, year)
        )
    value = sequence["last_number"] + 1 if sequence else 1
    if sequence:
        sequence = plan.update("core_document_sequences", sequence, {"last_number": value})
    else:
        sequence = plan.add(
            "core_document_sequences", {"entity": entity, "year": year, "last_number": value}
        )
    plan.pending_numbers[key] = sequence
    return f"{chosen}-{year}-{value:04d}"
