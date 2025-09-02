
import os
from datetime import datetime, timezone, timedelta
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

async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], days: int) -> datetime:
    """
    Cria/atualiza VIP:
    - se não existir usuário: cria com vip_until = agora + days
    - se existir e vip_until > agora: soma days a partir de vip_until
    - senão: soma days a partir de agora
    Retorna o datetime final de vip_until.
    """
    now = datetime.now(timezone.utc)

    async with get_session() as s:
        res = await s.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()

        if user is None:
            # cria novo
            base = now
            new_until = base + timedelta(days=days)
            user = User(
                tg_id=tg_id,
                username=username or "",
                is_vip=True,
                vip_until=new_until,
            )
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return new_until

        # atualiza username se veio diferente
        if username and user.username != username:
            user.username = username

        # normaliza vip_until (se vier naive, trata como UTC)
        if user.vip_until:
            current_until = (
                user.vip_until.replace(tzinfo=timezone.utc)
                if user.vip_until.tzinfo is None
                else user.vip_until
            )
            base = current_until if current_until > now else now
        else:
            base = now

        new_until = base + timedelta(days=days)
        user.is_vip = True
        user.vip_until = new_until

        await s.commit()
        # opcional: await s.refresh(user)
        return new_until
