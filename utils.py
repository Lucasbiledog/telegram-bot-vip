from __future__ import annotations
import asyncio
import json, hmac, hashlib, logging
from typing import Optional, Dict
from datetime import datetime, timedelta, timezone


from telegram import Bot
from telegram.error import TelegramError, TimedOut

# Função de preços estáticos removida - agora usa faixas dinâmicas de valor
# baseadas no valor real da transação em USD

def choose_plan_from_usd(amount_usd: float, prices: Dict[int, float] = None) -> Optional[int]:
    """
    Determina plano VIP baseado no valor real em USD da transação.
    Usa faixas de valor em vez de preços fixos.
    """
    # Converter para float se for string
    if isinstance(amount_usd, str):
        try:
            amount_usd = float(amount_usd)
        except (ValueError, TypeError):
            return None
    
    # Faixas de valor dinâmicas baseadas no valor real pago
    # CORRIGIDO: Agora consistente com plan_from_amount em main.py
    if amount_usd < 0.05:  # Menos de 5 centavos - não elegível
        return None
    elif amount_usd < 1.0:  # $0.05 - $0.99
        return 30   # 1 mês (MENSAL)
    elif amount_usd < 1.5:  # $1.00 - $1.49
        return 60   # 2 meses (TRIMESTRAL - nome confuso mas é 60 dias)
    elif amount_usd < 2.0:  # $1.50 - $1.99
        return 180  # 6 meses (SEMESTRAL)
    else:  # $2.00+
        return 365  # 1 ano (ANUAL)
    
    # Fallback para compatibilidade (caso ainda existam preços fixos)
    if prices:
        tol = 0.01
        best_days = None
        best_price = -1.0
        for days, price in prices.items():
            if amount_usd + tol >= price and price > best_price:
                best_price = price
                best_days = days
        return best_days
    
    return None

async def vip_upsert_and_get_until(tg_id: int, username: Optional[str], days: int, first_name: Optional[str] = None) -> datetime:
    """Create or extend VIP membership and return the new expiry."""
    from main import SessionLocal, VipMembership, now_utc
    
    now = now_utc()
    
    with SessionLocal() as s:
        # Buscar ou criar VipMembership
        m = s.query(VipMembership).filter(VipMembership.user_id == tg_id).first()
        if not m:
            # Criar novo membro VIP
            new_until = now + timedelta(days=days)
            m = VipMembership(
                user_id=tg_id,
                username=username,
                first_name=first_name,
                active=True,
                expires_at=new_until,
                created_at=now
            )
            s.add(m)
        else:
            # Estender VIP existente - corrigir timezone antes de comparar
            expires_at = m.expires_at
            if expires_at and expires_at.tzinfo is None:
                # Se expires_at não tem timezone, adicionar UTC
                expires_at = expires_at.replace(tzinfo=timezone.utc)
                m.expires_at = expires_at  # Atualizar no banco
            
            # Determinar base para extensão
            if expires_at and expires_at > now:
                base = expires_at  # VIP ainda ativo, estender do fim atual
            else:
                base = now  # VIP expirado, começar de agora
            
            new_until = base + timedelta(days=days)
            m.expires_at = new_until
            m.active = True
            if username:
                m.username = username
            if first_name:
                m.first_name = first_name
        
        s.commit()
        return m.expires_at

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

# =========================
# VIP Management Functions
# =========================
from enum import Enum

class VipPlan(Enum):
    MENSAL = "mensal"      # 30 dias
    BIMESTRAL = "bimestral"   # 60 dias
    TRIMESTRAL = "trimestral" # 180 dias (3 meses + bônus)
    ANUAL = "anual"        # 365 dias

def plan_to_days(plan: VipPlan) -> int:
    """Converte plano VIP para número de dias."""
    mapping = {
        VipPlan.MENSAL: 30,
        VipPlan.BIMESTRAL: 60,
        VipPlan.TRIMESTRAL: 180,
        VipPlan.ANUAL: 365,
    }
    return mapping.get(plan, 30)

def days_to_plan(days: int) -> VipPlan:
    """Converte número de dias para plano VIP mais adequado."""
    if days >= 365:
        return VipPlan.ANUAL
    elif days >= 180:
        return VipPlan.TRIMESTRAL
    elif days >= 60:
        return VipPlan.BIMESTRAL
    else:
        return VipPlan.MENSAL

# =========================
# Payment Integration Functions  
# =========================
async def create_vip_invite_and_notify(bot: Bot, user_id: int, username: Optional[str], days: int) -> Optional[str]:
    """Cria convite VIP e notifica usuário sobre aprovação."""
    try:
        # Extend VIP membership
        vip_until = await vip_upsert_and_get_until(user_id, username, days, None)
        
        # Create invite link (you'll need to import GROUP_VIP_ID from main or make it configurable)
        invite_link = await create_one_time_invite(bot, -1002432143718, expire_seconds=7200)  # Placeholder ID
        
        if invite_link:
            # Send notification to user
            message = (
                f"✅ Pagamento aprovado!\n"
                f"VIP válido até {vip_until.strftime('%d/%m/%Y')}\n"
                f"Entre no grupo VIP: {invite_link}"
            )
            await bot.send_message(chat_id=user_id, text=message)
            return invite_link
        else:
            # Send notification without invite
            message = (
                f"✅ Pagamento aprovado!\n"
                f"VIP válido até {vip_until.strftime('%d/%m/%Y')}\n"
                f"Entre em contato para receber o convite do grupo VIP."
            )
            await bot.send_message(chat_id=user_id, text=message)
            return None
            
    except Exception as e:
        logging.error(f"Erro ao criar convite VIP para user {user_id}: {e}")
        return None
