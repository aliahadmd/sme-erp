"""In-process event bus for cross-module decoupling.

Two kinds of subscribers:

- **Transactional** (`subscribe_tx` / `dispatch_tx`): run on the publisher's
  session BEFORE it commits. Used for bookkeeping that must be atomic with
  the business document (journal entries). A failing handler raises and the
  whole request rolls back — a posted document can never exist without its
  journal entry.
- **After-commit** (`subscribe` / `publish`): run after the publishing
  transaction committed, with per-handler error isolation. Used for side
  effects that must not fail work that already happened (notifications,
  emails). Handlers open their own session if they need one.
"""

import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger


@dataclass(frozen=True)
class Event:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    org_id: uuid.UUID | None = None


Handler = Callable[[Event], Awaitable[None]]
TxHandler = Callable[[AsyncSession, Event], Awaitable[None]]

_handlers: dict[str, list[Handler]] = defaultdict(list)
_tx_handlers: dict[str, list[TxHandler]] = defaultdict(list)


def subscribe(event_name: str, handler: Handler) -> None:
    _handlers[event_name].append(handler)


def subscribe_tx(event_name: str, handler: TxHandler) -> None:
    _tx_handlers[event_name].append(handler)


def subscribers(event_name: str) -> list[Handler]:
    return list(_handlers.get(event_name, []))


async def dispatch_tx(session: AsyncSession, event: Event) -> None:
    """Run transactional subscribers inside the caller's transaction.

    Exceptions propagate on purpose: the caller must not commit when the
    bookkeeping for its document failed.
    """
    for handler in list(_tx_handlers.get(event.name, [])):
        await handler(session, event)


async def publish(event: Event) -> None:
    """Dispatch after-commit subscribers with per-handler error isolation.

    Handlers run AFTER the publishing transaction committed — a failing
    handler must never surface as a failed request for work that already
    happened. Failures are logged and remaining handlers still run.
    """
    logger = get_logger("app.events")
    for handler in subscribers(event.name):
        try:
            await handler(event)
        except Exception:
            logger.exception(
                "event_handler_failed",
                event_name=event.name,
                handler=getattr(handler, "__qualname__", str(handler)),
            )


async def emit(session: AsyncSession, event: Event) -> None:
    """Transactional half of an event: call BEFORE `session.commit()`, then
    `publish(event)` after the commit for the side-effect half."""
    await dispatch_tx(session, event)


def clear_subscribers() -> None:
    """Test helper — clears after-commit subscribers only (transactional
    bookkeeping subscribers are part of the application's invariants)."""
    _handlers.clear()
