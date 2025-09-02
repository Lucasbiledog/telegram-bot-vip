from __future__ import annotations
import json, time, hmac, hashlib
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone

from telegram import Bot

DEFAULT_VIP_PRICES_USD = {30: 7.99, 90: 19.99, 180: 34.99, 365: 59.99}

def get_vip_plan_prices_usd_sync(cfg_val: Optional[str]) -> Dict[int, float]:
    if not cfg_val:
        return DEFAULT_VIP_PRICES_USD
    try:
        data = json.loads(cfg_val)
        parsed = {int(k): float(v) for k, v in data.items()}
        return parsed or DEFAULT_VIP_PRICES_USD
    except Exception:
        return DEFAULT_VIP_PRICES_USD

def choose_plan_from_usd(amount_usd: float, prices: Dict[int, float]) -> Optional[int]:
    # escolhe o maior plano cujo preço <= amount_usd (com tolerância 1%)
    tol = 0.01
    best_days = None
    best_price = -1.0
    for days, price in prices.items():
        if amount_usd + tol >= price and price > best_price:
            best_price = price
            best_days = days
    return best_days

async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], days: int) -> datetime:
    # estende a partir de agora ou a partir do vip_until atual, o que for maior
    from db import get_session, user_set_vip_until
    from sqlalchemy import select
    from models import User
    async with get_session() as s:
        res = await s.execute(select(User).where(User.tg_id == tg_id))
        user = res.scalar_one_or_none()
        now = datetime.now(timezone.utc)
        base = now
        if user and user.vip_until and user.vip_until > now:
            base = user.vip_until
        new_until = base + timedelta(days=days)
        await user_set_vip_until(tg_id, new_until)
        return new_until

async def create_one_time_invite(bot: Bot, chat_id: int, expire_seconds: int = 7200, member_limit: int = 1) -> str:
    # cria link de convite 1 uso com expiração (usa datetime)
    expire_dt = datetime.utcfromtimestamp(int(time.time()) + int(expire_seconds))
    invite = await bot.create_chat_invite_link(
        chat_id=chat_id,
        creates_join_request=False,
        expire_date=expire_dt,
        member_limit=member_limit
    )
    return invite.invite_link

def make_link_sig(secret: str, uid: int, ts: int) -> str:
    raw = f"{uid}:{ts}".encode()
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
