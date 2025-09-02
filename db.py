import os
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, update
from models import Base, Config, User
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./app.db")
engine = create_async_engine(DATABASE_URL, future=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_vip_until_timezone()


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session

async def cfg_get(key: str) -> Optional[str]:
    async with get_session() as s:
        res = await s.execute(select(Config).where(Config.key == key))
        row = res.scalar_one_or_none()
        return row.value if row else None

async def cfg_set(key: str, value: str) -> None:
    async with get_session() as s:
        res = await s.execute(select(Config).where(Config.key == key))
        row = res.scalar_one_or_none()
        if row:
            await s.execute(update(Config).where(Config.key == key).values(value=value))
        else:
            s.add(Config(key=key, value=value))
        await s.commit()

async def user_get_or_create(tg_id: int, username: Optional[str] = None) -> User:
    async with get_session() as s:
        res = await s.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if not user:
            user = User(tg_id=tg_id, username=username or "")
            s.add(user)
            await s.commit()
            await s.refresh(user)
        else:
            if username and user.username != username:
                user.username = username
                await s.commit()
        return user

async def user_set_vip_until(tg_id: int, until: datetime) -> None:
    if until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    async with get_session() as s:
        res = await s.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if not user:
            user = User(tg_id=tg_id, username="", is_vip=True, vip_until=until)
            s.add(user)
        else:
            user.is_vip = True
            user.vip_until = until
        await s.commit()

async def migrate_vip_until_timezone() -> None:
            """Ensure `vip_until` timestamps are timezone-aware.

    Any user records missing timezone information in their ``vip_until`` field
    are updated to use UTC. The migration is idempotent; running it multiple
    times has no effect once all timestamps include timezone data.
    """
            async with get_session() as s:
                res = await s.execute(select(User).where(User.vip_until.is_not(None)))
                users = res.scalars().all()
                updated = False
                for user in users:
                    if user.vip_until.tzinfo is None:
                        user.vip_until = user.vip_until.replace(tzinfo=timezone.utc)
                updated = True
                if updated:
                    await s.commit()

