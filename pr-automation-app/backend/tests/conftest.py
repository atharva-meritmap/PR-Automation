import os
from collections.abc import AsyncGenerator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.security import hash_password
from app.db.base import Base
from app.db.models import User
from app.db.session import get_db
from app.main import app

TEST_DB_URL = os.getenv("TEST_DATABASE_URL", "sqlite+aiosqlite:///:memory:")


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(TEST_DB_URL, future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seeded_users(db_session: AsyncSession) -> dict[str, User]:
    admin = User(
        email="admin@example.com",
        password_hash=hash_password("AdminPass123!"),
        full_name="Alice Admin",
        role="admin",
        is_active=True,
    )
    officer_a = User(
        email="officer.a@example.com",
        password_hash=hash_password("OfficerAPass123!"),
        full_name="Oscar A",
        role="officer",
        is_active=True,
    )
    officer_b = User(
        email="officer.b@example.com",
        password_hash=hash_password("OfficerBPass123!"),
        full_name="Olivia B",
        role="officer",
        is_active=True,
    )
    db_session.add_all([admin, officer_a, officer_b])
    await db_session.commit()
    for user in (admin, officer_a, officer_b):
        await db_session.refresh(user)
    return {"admin": admin, "officer_a": officer_a, "officer_b": officer_b}
