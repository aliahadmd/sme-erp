"""In-process event bus for cross-module decoupling.

Contract: publishers emit AFTER their transaction commits; handlers run
inline in the publisher's context and must not write to closed sessions —
they open their own if needed. Keep handlers fast; heavy work belongs in
background tasks (added when justified).
"""

import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.core.logging import get_logger


@dataclass(frozen=True)
class Event:
    name: str
    payload: dict[str, Any] = field(default_factory=dict)
    org_id: uuid.UUID | None = None


Handler = Callable[[Event], Awaitable[None]]

_handlers: dict[str, list[Handler]] = defaultdict(list)


def subscribe(event_name: str, handler: Handler) -> None:
    _handlers[event_name].append(handler)


def subscribers(event_name: str) -> list[Handler]:
    return list(_handlers.get(event_name, []))


async def publish(event: Event) -> None:
    """Dispatch to subscribers with per-handler error isolation.

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


def clear_subscribers() -> None:
    """Test helper."""
    _handlers.clear()
