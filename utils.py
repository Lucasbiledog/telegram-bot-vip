from __future__ import annotations
import asyncio
import json, hmac, hashlib, logging
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
    vip_until = user.vip_until
    if vip_until and vip_until.tzinfo is None:
        vip_until = vip_until.replace(tzinfo=timezone.utc)

    base = vip_until if (vip_until and vip_until > now) else now
    new_until = base + timedelta(days=days)
    await user_set_vip_until(tg_id, new_until)
    return new_until

async def create_one_time_invite(
    bot: Bot,
    chat_id: int,
    expire_seconds: int = 7200,
    member_limit: int = 1,
    *,
    timeout: Optional[float] = None,
    retries: int = 3,
) -> Optional[str]:
    expire_dt = datetime.now(timezone.utc) + timedelta(seconds=expire_seconds)
    for attempt in range(retries):
        try:
            kwargs = dict(
                chat_id=chat_id,
                creates_join_request=False,
                expire_date=expire_dt,
                member_limit=member_limit,
            )
            if timeout is not None:
                kwargs["timeout"] = timeout
            invite = await bot.create_chat_invite_link(**kwargs)
            return invite.invite_link
        except (TelegramError, TimedOut) as e:
            if attempt == retries - 1:
                logging.exception(
                    "Attempt %d/%d failed to create one-time invite link", attempt + 1, retries
                )
                return None
            logging.warning(
                "Attempt %d/%d failed to create one-time invite link: %s",
                attempt + 1,
                retries,
                e,
            )
            await asyncio.sleep(2 ** attempt)
    return None

async def send_with_retry(func, *args, retries: int = 3, base_delay: float = 1.0, **kwargs):
    for attempt in range(retries):
        try:
            return await func(*args, **kwargs)
        except (TelegramError, TimedOut) as e:
            if attempt == retries - 1:
                logging.error(
                    "Failed to send after %d attempts: %s", retries, e
                )
                return None
            logging.warning(
                "Attempt %d/%d failed to send: %s", attempt + 1, retries, e
            )
            await asyncio.sleep(base_delay * (2 ** attempt))
    return None


async def reply_with_retry(message, *args, **kwargs):
    return await send_with_retry(message.reply_text, *args, **kwargs)

def make_link_sig(secret: str, uid: int, ts: int) -> str:
    raw = f"{uid}:{ts}".encode()
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
