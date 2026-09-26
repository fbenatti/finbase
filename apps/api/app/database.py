from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Sync engine for background thread ETL processing (avoids blocking the event loop)
sync_engine = create_engine(
    settings.database_url_sync,
    echo=False,
    pool_size=5,
    max_overflow=5,
    pool_recycle=3600,
    pool_pre_ping=True,
)
SyncSession = sessionmaker(bind=sync_engine, expire_on_commit=False)


def run_async_isolated(coro):
    """Run one coroutine, then drop the asyncpg pool.

    The worker calls this once per task. The global async engine keeps
    connections tied to that loop; the next task would otherwise fail with
    asyncpg ``another operation is in progress``.
    """
    import asyncio

    async def _wrapped():
        try:
            return await coro
        finally:
            await engine.dispose()

    return asyncio.run(_wrapped())


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
