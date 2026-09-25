"""CRM contacts API."""

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.errors import NotFoundError, ValidationError
from app.modules.core.deps import CurrentUser, require
from app.modules.core.models import Organization
from app.modules.core.service import get_organization, write_audit
from app.modules.crm.models import Contact
from app.modules.crm.schemas import (
    ContactArchiveIn,
    ContactIn,
    ContactOut,
    ContactPage,
    ContactUpdateIn,
)
from app.shared.numbering import next_number
from app.shared.pagination import PageParamsDep, paginate

router = APIRouter(prefix="/crm")


async def _get_contact(session: AsyncSession, org_id: uuid.UUID, contact_id: uuid.UUID) -> Contact:
    contact = await session.get(Contact, contact_id)
    if not contact or contact.org_id != org_id:
        raise NotFoundError("Contact not found")
    return contact


@router.get("/contacts", response_model=ContactPage)
async def list_contacts(
    params: PageParamsDep,
    type: str | None = Query(None, pattern=r"^(customer|supplier)$"),
    q: str | None = Query(None, max_length=100),
    tag: str | None = None,
    include_archived: bool = False,
    _user: CurrentUser = Depends(require("crm.contact.read")),
    session: AsyncSession = Depends(get_session),
) -> ContactPage:
    org = await get_organization(session)
    stmt = select(Contact).where(Contact.org_id == org.id).order_by(Contact.name)
    if type == "customer":
        stmt = stmt.where(Contact.is_customer.is_(True))
    elif type == "supplier":
        stmt = stmt.where(Contact.is_supplier.is_(True))
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(Contact.name.ilike(like), Contact.code.ilike(like), Contact.legal_name.ilike(like))
        )
    if tag:
        stmt = stmt.where(Contact.tags.contains([tag]))
    if not include_archived:
        stmt = stmt.where(Contact.status == "active")
    rows, total = await paginate(session, stmt, params)
    return ContactPage(
        items=[ContactOut.model_validate(r) for r in rows],
        total=total,
        limit=params.limit,
        offset=params.offset,
    )


@router.get("/contacts/{contact_id}", response_model=ContactOut)
async def get_contact(
    contact_id: uuid.UUID,
    _user: CurrentUser = Depends(require("crm.contact.read")),
    session: AsyncSession = Depends(get_session),
) -> Contact:
    org = await get_organization(session)
    return await _get_contact(session, org.id, contact_id)


@router.post("/contacts", response_model=ContactOut, status_code=201)
async def create_contact(
    body: ContactIn,
    user: CurrentUser = Depends(require("crm.contact.create")),
    session: AsyncSession = Depends(get_session),
) -> Contact:
    try:
        body.validate_flags()
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    org = await get_organization(session)
    code = await next_number(session, org.id, "contact", "C")
    contact = Contact(org_id=org.id, code=code, **body.model_dump())
    session.add(contact)
    await write_audit(
        session,
        actor=user.user,
        action="create",
        entity_type="crm.contact",
        entity_id=contact.id,
        after={"code": code, "name": contact.name},
    )
    await session.commit()
    await session.refresh(contact)
    return contact


@router.patch("/contacts/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: uuid.UUID,
    body: ContactUpdateIn,
    user: CurrentUser = Depends(require("crm.contact.update")),
    session: AsyncSession = Depends(get_session),
) -> Contact:
    org = await get_organization(session)
    contact = await _get_contact(session, org.id, contact_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("is_customer") is False and data.get("is_supplier") is False:
        raise ValidationError("Contact must be a customer and/or a supplier")
    for field, value in data.items():
        setattr(contact, field, value)
    await write_audit(
        session,
        actor=user.user,
        action="update",
        entity_type="crm.contact",
        entity_id=contact.id,
        after={"name": contact.name},
    )
    await session.commit()
    await session.refresh(contact)
    return contact


@router.post("/contacts/{contact_id}/archive", response_model=ContactOut)
async def archive_contact(
    contact_id: uuid.UUID,
    body: ContactArchiveIn,
    user: CurrentUser = Depends(require("crm.contact.archive")),
    session: AsyncSession = Depends(get_session),
) -> Contact:
    org = await get_organization(session)
    contact = await _get_contact(session, org.id, contact_id)
    contact.status = "archived" if body.archived else "active"
    await write_audit(
        session,
        actor=user.user,
        action="archive" if body.archived else "unarchive",
        entity_type="crm.contact",
        entity_id=contact.id,
    )
    await session.commit()
    await session.refresh(contact)
    return contact


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: uuid.UUID,
    user: CurrentUser = Depends(require("crm.contact.delete")),
    session: AsyncSession = Depends(get_session),
) -> None:
    """Hard delete — only sensible before the contact has documents. Once orders
    exist (plans 6-8) this endpoint refuses via reference checks."""
    org: Organization = await get_organization(session)
    contact = await _get_contact(session, org.id, contact_id)
    # Reference checks are added by the document modules; CRM alone can delete.
    await write_audit(
        session,
        actor=user.user,
        action="delete",
        entity_type="crm.contact",
        entity_id=contact.id,
        before={"code": contact.code, "name": contact.name},
    )
    await session.delete(contact)
    await session.commit()
