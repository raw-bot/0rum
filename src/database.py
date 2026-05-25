"""Async SQLAlchemy engine, session factory, and declarative Base."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from src.config import get_settings

_engine: AsyncEngine | None = None
_sessionmaker = async_sessionmaker(
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


def get_engine() -> AsyncEngine:
    """Create the async engine lazily after application logging is configured."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
        )
        _sessionmaker.configure(bind=_engine)
    return _engine


class _AsyncSessionLocalFactory:
    """Callable session factory that preserves the old AsyncSessionLocal() API."""

    def __call__(self, *args, **kwargs) -> AsyncSession:
        get_engine()
        return _sessionmaker(*args, **kwargs)


AsyncSessionLocal = _AsyncSessionLocalFactory()


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""

    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields an async database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
