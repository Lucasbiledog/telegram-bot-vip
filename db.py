import os
import json
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select, update, inspect, text
from sqlalchemy.exc import IntegrityError
from models import Base, Config, User, Payment, Pack, ScheduledMessage
from datetime import datetime, timezone, time as dtime

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
    await migrate_pack_scheduled_at()
    await migrate_user_remove_sent_fields()


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

async def pack_get(pack_id: int) -> Optional[Pack]:
    async with get_session() as s:
        res = await s.execute(select(Pack).where(Pack.id == pack_id))
        return res.scalar_one_or_none()

async def pack_list(is_vip: bool) -> list[Pack]:
    async with get_session() as s:
        res = await s.execute(
            select(Pack).where(Pack.is_vip == is_vip).order_by(Pack.id)
        )
        return res.scalars().all()

async def pack_get_next_vip() -> Optional[Pack]:
    async with get_session() as s:
        res = await s.execute(
            select(Pack)
            .where(Pack.is_vip.is_(True), Pack.sent_at.is_(None))
            .order_by(Pack.id.asc())
        )
        return res.scalars().first()

async def pack_get_next_free() -> Optional[Pack]:
    async with get_session() as s:
        res = await s.execute(
            select(Pack)
            .where(Pack.is_vip.is_(False), Pack.sent_at.is_(None))
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
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()

async def pack_remove_item(pack_id: int, index: int, *, preview: bool) -> bool:
    async with get_session() as s:
        res = await s.execute(select(Pack).where(Pack.id == pack_id))
        pack = res.scalar_one_or_none()
        if not pack:
            return False
        items = json.loads(pack.previews if preview else pack.files)
        if index < 0 or index >= len(items):
            return False
        items.pop(index)
        if preview:
            pack.previews = json.dumps(items)
        else:
            pack.files = json.dumps(items)
        await s.commit()
        return True

async def pack_delete(pack_id: int) -> bool:
    async with get_session() as s:
        res = await s.execute(select(Pack).where(Pack.id == pack_id))
        pack = res.scalar_one_or_none()
        if not pack:
            return False
        await s.delete(pack)
        await s.commit()
        return True

async def pack_schedule(pack_id: int, when: datetime) -> bool:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    async with get_session() as s:
        res = await s.execute(select(Pack).where(Pack.id == pack_id))
        pack = res.scalar_one_or_none()
        if not pack:
            return False
        pack.scheduled_at = when
        await s.commit()
        return True

async def packs_get_due(now: datetime) -> list[Pack]:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    async with get_session() as s:
        res = await s.execute(
            select(Pack)
            .where(Pack.scheduled_at.is_not(None), Pack.scheduled_at <= now, Pack.sent_at.is_(None))
            .order_by(Pack.scheduled_at.asc())
        )
        return res.scalars().all()

async def scheduled_msg_create(tier: str, when: dtime, text: str) -> ScheduledMessage:
    async with get_session() as s:
        sm = ScheduledMessage(tier=tier, time=when, text=text)
        s.add(sm)
        await s.commit()
        await s.refresh(sm)
        return sm


async def scheduled_msg_list(tier: str) -> list[ScheduledMessage]:
    async with get_session() as s:
        res = await s.execute(
            select(ScheduledMessage).where(ScheduledMessage.tier == tier).order_by(ScheduledMessage.id)
        )
        return res.scalars().all()


async def scheduled_msg_get(msg_id: int) -> Optional[ScheduledMessage]:
    async with get_session() as s:
        res = await s.execute(select(ScheduledMessage).where(ScheduledMessage.id == msg_id))
        return res.scalar_one_or_none()


async def scheduled_msg_update(msg_id: int, when: Optional[dtime] = None, text: Optional[str] = None) -> bool:
    async with get_session() as s:
        res = await s.execute(select(ScheduledMessage).where(ScheduledMessage.id == msg_id))
        sm = res.scalar_one_or_none()
        if not sm:
            return False
        if when is not None:
            sm.time = when
        if text is not None:
            sm.text = text
        await s.commit()
        return True


async def scheduled_msg_toggle(msg_id: int) -> Optional[bool]:
    async with get_session() as s:
        res = await s.execute(select(ScheduledMessage).where(ScheduledMessage.id == msg_id))
        sm = res.scalar_one_or_none()
        if not sm:
            return None
        sm.enabled = not sm.enabled
        await s.commit()
        return sm.enabled


async def scheduled_msg_delete(msg_id: int) -> bool:
    async with get_session() as s:
        res = await s.execute(select(ScheduledMessage).where(ScheduledMessage.id == msg_id))
        sm = res.scalar_one_or_none()
        if not sm:
            return False
        await s.delete(sm)
        await s.commit()
        return True
            
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

async def migrate_pack_scheduled_at() -> None:
    async with engine.begin() as conn:
        def _migrate(connection):
            insp = inspect(connection)
            cols = [c["name"] for c in insp.get_columns("packs")]
            if "scheduled_at" not in cols:
                connection.execute(
                    text("ALTER TABLE packs ADD COLUMN scheduled_at DATETIME")
                )
        await conn.run_sync(_migrate)
        
async def migrate_user_remove_sent_fields() -> None:
    async with engine.begin() as conn:
        def _migrate(connection):
            insp = inspect(connection)
            cols = [c["name"] for c in insp.get_columns("users")]
            if "sent_at" in cols:
                connection.execute(text("ALTER TABLE users DROP COLUMN sent_at"))
            if "requeued_at" in cols:
                connection.execute(text("ALTER TABLE users DROP COLUMN requeued_at"))
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
