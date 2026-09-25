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
    for handler in subscribers(event.name):
        await handler(event)


def clear_subscribers() -> None:
    """Test helper."""
    _handlers.clear()
