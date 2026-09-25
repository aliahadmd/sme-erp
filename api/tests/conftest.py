"""Test bootstrap.

Environment must be prepared BEFORE any app import: point DATABASE_URL at a
dedicated erp_test database so tests never touch dev data.
"""

import asyncio
import os

_base_url = os.environ.get(
    "DATABASE_URL", "postgresql+asyncpg://erp:erp_dev_password@localhost:55432/erp"
)
_head, _, _db = _base_url.rpartition("/")
TEST_DATABASE_URL = f"{_head}/erp_test"
BASE_DATABASE_URL = f"{_head}/postgres"

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ.setdefault("REDIS_URL", "redis://localhost:56379/0")
os.environ.setdefault("S3_ENDPOINT", "http://localhost:18333")
os.environ.setdefault("S3_BUCKET", "erp-dev")
os.environ.pop("OPENROUTER_API_KEY", None)  # AI-disabled path is the deterministic default

import pytest_asyncio  # noqa: E402
from alembic.config import Config  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from alembic import command  # noqa: E402
from app.main import app  # noqa: E402


def _run_migrations() -> None:
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", TEST_DATABASE_URL)
    command.upgrade(cfg, "head")


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _database():
    """Recreate erp_test, apply migrations, and seed base data once per session."""
    admin_engine = create_async_engine(BASE_DATABASE_URL, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        await conn.exec_driver_sql('DROP DATABASE IF EXISTS "erp_test" WITH (FORCE)')
        await conn.exec_driver_sql('CREATE DATABASE "erp_test"')
    await admin_engine.dispose()

    await asyncio.to_thread(_run_migrations)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = create_async_engine(TEST_DATABASE_URL)
    from app.core.seed import seed

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await seed(session)
    await engine.dispose()
    yield


@pytest_asyncio.fixture(scope="session")
async def db_engine():
    engine = create_async_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    """A session rolled back after each test — tests never persist data."""
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    # ASGITransport does not run lifespan on its own — drive it explicitly.
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app), base_url="http://test") as c,
    ):
        yield c
