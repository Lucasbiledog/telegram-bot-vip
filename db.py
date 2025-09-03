import os
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, update, inspect, text
from sqlalchemy.exc import IntegrityError
from models import Base, Config, User, Payment, Pack
from datetime import datetime, timezone

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./app.db")
engine = create_async_engine(DATABASE_URL, future=True, echo=False)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await migrate_vip_until_timezone()
    await migrate_pack_is_vip()
    await migrate_pack_sent_at()
    await migrate_pack_sent_fields()


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


async def vip_list() -> list[User]:
    async with get_session() as s:
        res = await s.execute(select(User).where(User.is_vip.is_(True)))
        return res.scalars().all()

async def vip_remove(tg_id: int) -> bool:
    async with get_session() as s:
        res = await s.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        if not user:
            return False
        user.is_vip = False
        user.vip_until = None
        await s.commit()
        return True

async def vip_add(tg_id: int, days: int):
    from utils import vip_upsert_and_get_until
    return await vip_upsert_and_get_until(tg_id, None, days)


async def hash_exists(tx_hash: str) -> bool:
    async with get_session() as s:
        res = await s.execute(select(Payment).where(Payment.tx_hash == tx_hash))
        return res.scalar_one_or_none() is not None


async def hash_store(tx_hash: str, tg_id: int) -> None:
    async with get_session() as s:
        s.add(
            Payment(
                tx_hash=tx_hash,
                tg_id=tg_id,
                validated_at=datetime.now(timezone.utc),
            )
        )
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()


async def pack_create(title: str, previews: list[str], files: list[str], is_vip: bool = False) -> None:
    async with get_session() as s:
         s.add(
            Pack(
                title=title,
                previews=json.dumps(previews),
                files=json.dumps(files),
                is_vip=is_vip,
            )
        )
         await s.commit()
         
async def pack_mark_pending(pack_id: int) -> bool:
    async with get_session() as s:
        res = await s.execute(select(Pack).where(Pack.id == pack_id))
        pack = res.scalar_one_or_none()
        if not pack:
            return False
        pack.sent_at = None
        await s.commit()
        return True
    
async def pack_get_next_vip() -> Optional[Pack]:
    async with get_session() as s:
        res = await s.execute(
            select(Pack)
            .where(Pack.is_vip.is_(True), Pack.sent_at.is_(None))
            .order_by(Pack.id.asc())
        )
        return res.scalars().first()

async def pack_mark_sent(pack_id: int) -> None:
    async with get_session() as s:
        await s.execute(
            update(Pack)
            .where(Pack.id == pack_id)
            .values(sent_at=datetime.now(timezone.utc))
        )
        await s.commit()

async def pack_requeue(pack_id: int) -> None:
    async with get_session() as s:
        await s.execute(
            update(Pack)
            .where(Pack.id == pack_id)
            .values(sent_at=None, requeued_at=datetime.now(timezone.utc))
        )
    
async def migrate_pack_is_vip() -> None:
    async with engine.begin() as conn:
        def _migrate(connection):
            insp = inspect(connection)
            cols = [c["name"] for c in insp.get_columns("packs")]
            if "is_vip" not in cols:
                connection.execute(
                    text("ALTER TABLE packs ADD COLUMN is_vip BOOLEAN NOT NULL DEFAULT 0")
                )
        await conn.run_sync(_migrate)

async def migrate_pack_sent_at() -> None:
    async with engine.begin() as conn:
        def _migrate(connection):
            insp = inspect(connection)
            cols = [c["name"] for c in insp.get_columns("packs")]
            if "sent_at" not in cols:
                connection.execute(
                    text("ALTER TABLE packs ADD COLUMN sent_at DATETIME")
                )
        await conn.run_sync(_migrate)

async def migrate_pack_sent_fields() -> None:
    async with engine.begin() as conn:
        def _migrate(connection):
            insp = inspect(connection)
            cols = [c["name"] for c in insp.get_columns("packs")]
            if "sent_at" not in cols:
                connection.execute(
                    text("ALTER TABLE packs ADD COLUMN sent_at DATETIME")
                )
            if "requeued_at" not in cols:
                connection.execute(
                    text("ALTER TABLE packs ADD COLUMN requeued_at DATETIME")
                )
        await conn.run_sync(_migrate)


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
