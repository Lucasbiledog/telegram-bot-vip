from __future__ import annotations
import json, time, hmac, hashlib, logging
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone


from telegram import Bot
from telegram.error import TelegramError, TimedOut

# Planos pedidos
DEFAULT_VIP_PRICES_USD = {
    30: 0.05,
    60: 1.00,
    180: 1.50,
    365: 2.00,
}

def get_prices_sync(cfg_val: Optional[str]) -> Dict[int, float]:
    if not cfg_val:
        return DEFAULT_VIP_PRICES_USD
    try:
        data = json.loads(cfg_val)
        parsed = {int(k): float(v) for k, v in data.items()}
        return parsed or DEFAULT_VIP_PRICES_USD
    except Exception:
        return DEFAULT_VIP_PRICES_USD

def choose_plan_from_usd(amount_usd: float, prices: Dict[int, float]) -> Optional[int]:
    # escolhe o maior plano cujo preço <= amount_usd (tolerância 1 cent)
    tol = 0.01
    best_days = None
    best_price = -1.0
    for days, price in prices.items():
        if amount_usd + tol >= price and price > best_price:
            best_price = price
            best_days = days
    return best_days

async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], days: int) -> datetime:
    """Create or extend VIP membership and return the new expiry."""
    from db import user_get_or_create, user_set_vip_until

    user = await user_get_or_create(tg_id, username)
    now = datetime.now(timezone.utc)
    base = user.vip_until if (user.vip_until and user.vip_until > now) else now
    new_until = base + timedelta(days=days)
    await user_set_vip_until(tg_id, new_until)
    return new_until

async def create_one_time_invite(bot: Bot, chat_id: int, expire_seconds: int = 7200, member_limit: int = 1) -> Optional[str]:
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
