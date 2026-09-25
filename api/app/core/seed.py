"""Bootstrap seed — the real implementation lands with plan-4 (ERP Core)."""

import asyncio

from app.core.logging import get_logger

logger = get_logger(__name__)


async def main() -> None:
    logger.info("seed_skipped", reason="core seed arrives with plan-4")


if __name__ == "__main__":
    asyncio.run(main())
