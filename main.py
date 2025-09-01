import os
import logging
import asyncio
import datetime as dt
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple
from types import SimpleNamespace
from io import BytesIO
from evm_pay import find_tx_any_chain, pick_tier



import html
import json
import pytz
from dotenv import load_dotenv
from config import CHAIN_CONFIGS

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
import uvicorn
import httpx

from telegram import Update, InputMediaPhoto
from telegram.error import BadRequest
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    JobQueue,
    ConversationHandler,
    filters,
    ApplicationHandlerStop,
    ChatJoinRequestHandler,
)

# Redes suportadas para checagem de transações
CHAINS_TO_CHECK = [
    {
        "name": "Polygon",
        "rpc": "https://polygon-rpc.com",
        "explorer": "https://polygonscan.com/tx/",
        "symbol": "MATIC",
        "decimals": 18,
    },
    {
        "name": "BSC",
        "rpc": "https://bsc-dataseed.binance.org/",
        "explorer": "https://bscscan.com/tx/",
        "symbol": "BNB",
        "decimals": 18,
    },
    {
        "name": "Ethereum",
        "rpc": "https://mainnet.infura.io/v3/${INFURA_PROJECT_ID}",  # coloque seu ID aqui
        "explorer": "https://etherscan.io/tx/",
        "symbol": "ETH",
        "decimals": 18,
    },
    # pode adicionar outras redes que quiser
]

# === Imports ===
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    Boolean,
    ForeignKey,
    Text,
    BigInteger,
    UniqueConstraint,
    text,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.engine import make_url

def strtobool(val: str) -> bool:
    val = val.lower()
    if val in ("y", "yes", "t", "true", "on", "1"):
        return True
    if val in ("n", "no", "f", "false", "off", "0"):
        return False
    raise ValueError(f"Invalid truth value: {val}")
from web3 import Web3
from web3.exceptions import TransactionNotFound
import logging

async def find_tx_any_chain(txhash: str):
    for chain in CHAINS_TO_CHECK:
        try:
            info = await find_tx_on_chain(chain, txhash)
            if info:
                return info
        except Exception:
            continue
    return None



    logging.info(f"[tx-scan] procurando {txhash} em: {', '.join(CHAINS.keys())}")

    for key, cfg in CHAINS.items():
        rpc = cfg["rpc"]
        try:
            w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 20}))
            if not w3.is_connected():
                logging.warning(f"[tx-scan] {key}: RPC não conectou")
                continue

            # Confirmada?
            try:
                receipt = w3.eth.get_transaction_receipt(txhash)
                if receipt:
                    tx = w3.eth.get_transaction(txhash)
                    return {"chain": key, "tx": tx, "receipt": receipt}
            except TransactionNotFound:
                pass

            # Pendente?
            try:
                tx = w3.eth.get_transaction(txhash)
                if tx:
                    return {"chain": key, "tx": tx, "receipt": None}
            except TransactionNotFound:
                pass

        except Exception as e:
            logging.warning(f"[tx-scan] {key} falhou: {e}")
            continue

    return None

from decimal import Decimal

def wei_to_eth(wei: int) -> Decimal:
    return Decimal(wei) / Decimal(10**18)

async def value_usd_from_tx(result) -> Decimal:
    key = result["chain"]
    cfg = CHAINS[key]
    native_amount = wei_to_eth(result["tx"]["value"])
    # use seu fetch de preço (ex.: CoinGecko) com cfg["coingecko_id"]
    price = await get_price_usd(cfg["coingecko_id"])  # implemente/plugue na sua função já existente
    return native_amount * Decimal(str(price))

# === Config DB ===
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./bot.db")
url = make_url(DB_URL)
engine = create_engine(url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

def ensure_bigint_columns():
    if not url.get_backend_name().startswith("postgresql"):
        return
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE admins ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE payments ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint"))
            except Exception:
                pass
    except Exception as e:
        logging.warning("Falha em ensure_bigint_columns: %s", e)

def ensure_pack_tier_column():
    try:
        with engine.begin() as conn:
            try: conn.execute(text("ALTER TABLE packs ADD COLUMN tier VARCHAR"))
            except Exception: pass
            try: conn.execute(text("UPDATE packs SET tier='vip' WHERE tier IS NULL"))
            except Exception: pass
            try: conn.execute(text("ALTER TABLE scheduled_messages ADD COLUMN tier VARCHAR"))
            except Exception: pass
            try: conn.execute(text("UPDATE scheduled_messages SET tier='vip' WHERE tier IS NULL"))
            except Exception: pass
    except Exception:
        pass

def ensure_packfile_src_columns():
    try:
        with engine.begin() as conn:
            try: conn.execute(text("ALTER TABLE pack_files ADD COLUMN src_chat_id BIGINT"))
            except Exception: pass
            try: conn.execute(text("ALTER TABLE pack_files ADD COLUMN src_message_id INTEGER"))
            except Exception: pass
    except Exception as e:
        logging.warning("Falha em ensure_packfile_src_columns: %s", e)

def ensure_vip_invite_column():
    try:
        with engine.begin() as conn:
            try: conn.execute(text("ALTER TABLE vip_memberships ADD COLUMN invite_link TEXT"))
            except Exception: pass
    except Exception as e:
        logging.warning("Falha ensure_vip_invite_column: %s", e)
def ensure_vip_plan_column():
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE vip_memberships ADD COLUMN plan VARCHAR"))
            except Exception:
                pass
            try:
                conn.execute(text("UPDATE vip_memberships SET plan='TRIMESTRAL' WHERE plan IS NULL"))
            except Exception:
                pass
    except Exception as e:
        logging.warning("Falha ensure_vip_plan_column: %s", e)

class VipPlan(str, Enum):
    TRIMESTRAL = "TRIMESTRAL"
    SEMESTRAL = "SEMESTRAL"
    ANUAL = "ANUAL"
    MENSAL = "MENSAL"

PLAN_DAYS = {
    VipPlan.TRIMESTRAL: 90,
    VipPlan.SEMESTRAL: 180,
    VipPlan.ANUAL: 365,
    VipPlan.MENSAL: 30,

}

def init_db():
    Base.metadata.create_all(bind=engine)
    initial_admin_id = os.getenv("INITIAL_ADMIN_ID")
    if initial_admin_id:
        with SessionLocal() as s:
            try:
                uid = int(initial_admin_id)
                if not s.query(Admin).filter(Admin.user_id == uid).first():
                    s.add(Admin(user_id=uid))
                    s.commit()
            except Exception:
                s.rollback()
                raise
    if not cfg_get("daily_pack_vip_hhmm"):  cfg_set("daily_pack_vip_hhmm", "09:00")
    if not cfg_get("daily_pack_free_hhmm"): cfg_set("daily_pack_free_hhmm", "09:30")

    env_prices = os.getenv("VIP_PLAN_PRICES_USD")
    if env_prices:
        cfg_set("vip_plan_prices_usd", env_prices)

def ensure_schema():
    Base.metadata.create_all(bind=engine)
    ensure_bigint_columns()
    ensure_pack_tier_column()
    ensure_packfile_src_columns()
    ensure_vip_invite_column()
    ensure_vip_plan_column()


# =========================
# Helpers
# =========================
# Quais comandos usuários comuns podem usar
ALLOWED_FOR_NON_ADM = {"pagar", "tx", "start", "cancel_tx" }

def esc(s): return html.escape(str(s) if s is not None else "")
def now_utc(): return dt.datetime.utcnow()


def wrap_ph(s: str) -> str:
    # Converte qualquer <algo> em <code>&lt;algo&gt;</code> para não quebrar o HTML
    return re.sub(r'<([^>\n]{1,80})>', r'<code>&lt;\1&gt;</code>', s)


from datetime import timedelta

import re
TX_RE = re.compile(r'^(0x)?[0-9a-fA-F]+$')
HASH64_RE = re.compile(r"0x[0-9a-fA-F]{64}")

def normalize_tx_hash(s: str) -> Optional[str]:
    if not s:
        return None
    s = s.strip()
    if not TX_RE.match(s):
        return None
    if s.startswith("0x"):
        # precisa ter 66 chars: 0x + 64 hex
        return s.lower() if len(s) == 66 else None
    else:
        # sem 0x: precisa ter 64 hex; adiciona 0x
        return ("0x" + s.lower()) if len(s) == 64 else None
    

def extract_tx_hashes(text: str) -> List[str]:
    """Return list of normalized transaction hashes found in text."""
    if not text:
        return []
    hashes = []
    for match in HASH64_RE.findall(text):
        h = normalize_tx_hash(match)
        if h:
            hashes.append(h)
    return hashes


# ----- Preço VIP (em nativo ou token) usando ConfigKV
def get_vip_price_native() -> Optional[float]:
    v = cfg_get("vip_price_native")
    try: return float(v) if v is not None else None
    except: return None

def set_vip_price_native(value: float):
    cfg_set("vip_price_native", str(value))

def get_vip_price_token() -> Optional[float]:
    v = cfg_get("vip_price_token")
    try: return float(v) if v is not None else None
    except: return None

def set_vip_price_token(value: float):
    cfg_set("vip_price_token", str(value))

# ----- Preços dos planos VIP (dias -> preço)
def get_vip_plan_prices_native() -> Dict[int, float]:
    v = cfg_get("vip_plan_prices_native")
    if not v:
        return {}
    try:
        data = json.loads(v)
        return {int(k): float(val) for k, val in data.items()}
    except Exception:
        logging.warning("vip_plan_prices_native inválido: %s", v)
        return {}

def get_vip_plan_prices_token() -> Dict[int, float]:
    v = cfg_get("vip_plan_prices_token")
    if not v:
        return {}
    try:
        data = json.loads(v)
        return {int(k): float(val) for k, val in data.items()}
    except Exception:
        logging.warning("vip_plan_prices_token inválido: %s", v)
        return {}

def infer_plan_days(
    amount_usd: Optional[float] = None,
    amount_wei: Optional[int] = None,
    amount_raw: Optional[int] = None,
) -> Optional[int]:
    if amount_usd is not None:
        plan = plan_from_amount(amount_usd)
        return PLAN_DAYS.get(plan) if plan else None
    if TOKEN_CONTRACT:
        plans = get_vip_plan_prices_token()
        if not plans:
            return None
        for days, price in plans.items():
            expected = int(round(price * (10 ** TOKEN_DECIMALS)))
            if amount_raw == expected:
                return days
    else:
        plans = get_vip_plan_prices_native()
        if not plans:
            return None
        for days, price in plans.items():
            if amount_wei == _to_wei(price, 18):
                return days
    return None



async def create_and_store_personal_invite(user_id: int) -> str:
    """
    Cria um link exclusivo (1 uso) que expira junto do VIP e salva em VipMembership.invite_link.
    Retorna a URL do convite.
    """
    m = vip_get(user_id)
    if not m or not m.active or not m.expires_at:
        # só cria se o VIP estiver ativo
        raise RuntimeError("VIP inativo ou sem data de expiração")

    expire_ts = int(m.expires_at.timestamp())

    invite = await application.bot.create_chat_invite_link(
        chat_id=GROUP_VIP_ID,
        expire_date=expire_ts,
        member_limit=1
    )

    with SessionLocal() as s:
        vm = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
        if vm:
            vm.invite_link = invite.invite_link
            s.commit()

    return invite.invite_link


# ----- Assinaturas
def vip_get(user_id: int) -> Optional['VipMembership']:
    with SessionLocal() as s:
        return s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
    
    


def vip_upsert_start_or_extend(user_id: int, username: Optional[str], tx_hash: Optional[str], plan: VipPlan) -> 'VipMembership':
    now = now_utc(); days = PLAN_DAYS.get(plan, 90)
    with SessionLocal() as s:
        m = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
        if not m:
            m = VipMembership(
                user_id=user_id,
                username=username,
                tx_hash=tx_hash,
                start_at=now,
                expires_at=now + timedelta(days=days),
                active=True,
                plan=plan.value,
            )
            s.add(m)
        else:
            # Se ainda ativo, soma dias a partir do expires_at; senão reinicia a partir de agora
            base = m.expires_at if m.active and m.expires_at and m.expires_at > now else now
            m.expires_at = base + timedelta(days=days)
            m.tx_hash = tx_hash or m.tx_hash
            m.active = True
            m.username = username or m.username
            m.plan = plan.value
        s.commit(); s.refresh(m)
        return m

def vip_adjust_days(user_id: int, delta_days: int) -> Optional['VipMembership']:
    with SessionLocal() as s:
        m = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
        if not m: return None
        base = m.expires_at or now_utc()
        m.expires_at = base + timedelta(days=delta_days)
        if m.expires_at <= now_utc():
            m.active = False
        s.commit(); s.refresh(m)
        return m

def vip_deactivate(user_id: int) -> bool:
    with SessionLocal() as s:
        m = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
        if not m: return False
        m.active = False
        m.expires_at = now_utc()
        s.commit()
        return True

def vip_list_active(limit: int = 200) -> List['VipMembership']:
    with SessionLocal() as s:
        now = now_utc()
        return (
            s.query(VipMembership)
             .filter(VipMembership.active == True, VipMembership.expires_at > now)
             .order_by(VipMembership.expires_at.asc())
             .limit(limit)
             .all()
        )

def human_left(dt_expires: dt.datetime) -> str:
    now = now_utc()
    if dt_expires <= now: return "expirado"
    delta = dt_expires - now
    days = delta.days
    hours = int(delta.seconds/3600)
    mins = int((delta.seconds%3600)/60)
    if days > 0: return f"{days}d {hours}h"
    if hours > 0: return f"{hours}h {mins}m"
    return f"{mins}m"


def parse_hhmm(s: str) -> Tuple[int, int]:
    s = (s or "").strip()
    if ":" not in s:
        raise ValueError("Formato inválido; use HH:MM")
    hh, mm = s.split(":", 1)
    h = int(hh); m = int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Hora fora do intervalo 00:00–23:59")
    return h, m

async def dm(user_id: int, text: str, parse_mode: Optional[str] = "HTML") -> bool:
    try:
        chat = await application.bot.get_chat(user_id)
        if getattr(chat, "is_bot", False):
            logging.warning(f"Tentativa de DM para bot {user_id}. Abortando.")
            return False
        await application.bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode)
        return True
    except Exception as e:
        logging.warning(f"Falha ao enviar DM para {user_id}: {e}")
        return False


# =========================
# ENV / CONFIG
# =========================
load_dotenv()
BOT_TOKEN   = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SELF_URL    = os.getenv("SELF_URL")

STORAGE_GROUP_ID       = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))
GROUP_VIP_ID           = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))
STORAGE_GROUP_FREE_ID  = int(os.getenv("STORAGE_GROUP_FREE_ID", "-1002509364079"))
GROUP_FREE_ID          = int(os.getenv("GROUP_FREE_ID", "-1002932075976"))

PORT = int(os.getenv("PORT", 8000))

# Pagamento / Cadeia
DEFAULT_CHAIN = CHAIN_CONFIGS[0] if CHAIN_CONFIGS else {}
WALLET_ADDRESS = DEFAULT_CHAIN.get("wallet_address", "")
CHAIN_NAME     = DEFAULT_CHAIN.get("chain_name", "")
CHAIN_SYMBOL   = DEFAULT_CHAIN.get("symbol", "")
RPC_URL        = DEFAULT_CHAIN.get("rpc_url", "")
TOKEN_CONTRACT = DEFAULT_CHAIN.get("token_contract")
TOKEN_DECIMALS = DEFAULT_CHAIN.get("decimals", 18)
# Accepts "1", "true", "yes", "y", "on" for True; "0", "false", "no", "n", "off" for False
AUTO_APPROVE_CRYPTO = bool(strtobool(os.getenv("AUTO_APPROVE_CRYPTO", "1")))
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "5"))

# ===== Multi-chain registry =====
# Adicione/edite redes conforme precisar. Mantenha o 'key' único.
INFURA_PROJECT_ID = os.getenv("INFURA_PROJECT_ID", "")
def make_rpc_url(network: str) -> str:
    return f"https://{network}.infura.io/v3/{INFURA_PROJECT_ID}"

NETWORKS = [
    # Redes Infura (MetaMask Developer)
    {"key": "eth", "name": "Ethereum", "rpc": make_rpc_url("mainnet"), "native_symbol": "ETH", "native_decimals": 18, "explorer": "https://etherscan.io"},
    {"key": "polygon", "name": "Polygon", "rpc": make_rpc_url("polygon-mainnet"), "native_symbol": "MATIC", "native_decimals": 18, "explorer": "https://polygonscan.com"},
    {"key": "base", "name": "Base", "rpc": make_rpc_url("base-mainnet"), "native_symbol": "ETH", "native_decimals": 18, "explorer": "https://basescan.org"},
    {"key": "arbitrum", "name": "Arbitrum One", "rpc": make_rpc_url("arbitrum-mainnet"), "native_symbol": "ETH", "native_decimals": 18, "explorer": "https://arbiscan.io"},
    {"key": "optimism", "name": "Optimism", "rpc": make_rpc_url("optimism-mainnet"), "native_symbol": "ETH", "native_decimals": 18, "explorer": "https://optimistic.etherscan.io"},
    {"key": "linea", "name": "Linea", "rpc": make_rpc_url("linea-mainnet"), "native_symbol": "ETH", "native_decimals": 18, "explorer": "https://lineascan.build"},
    {"key": "avax", "name": "Avalanche C-Chain", "rpc": make_rpc_url("avalanche-mainnet"), "native_symbol": "AVAX", "native_decimals": 18, "explorer": "https://snowtrace.io"},
    {"key": "palm", "name": "Palm", "rpc": make_rpc_url("palm-mainnet"), "native_symbol": "PALM", "native_decimals": 18, "explorer": "https://explorer.palm.io"},
     # Redes fora da Infura → usar RPC público confiável
    {"key": "bsc", "name": "BNB Smart Chain", "rpc": os.getenv("BSC_RPC", "https://bsc-dataseed.binance.org"), "native_symbol": "BNB", "native_decimals": 18, "explorer": "https://bscscan.com"},
    {"key": "fantom", "name": "Fantom Opera", "rpc": os.getenv("RPC_FANTOM", "https://rpc.ftm.tools/"), "native_symbol": "FTM", "native_decimals": 18, "explorer": "https://ftmscan.com"},
]

CHAINS = {
    # ... suas redes Infura (eth, polygon, arbitrum, optimism, base, linea) ...
    "bsc": {
        "name": "BNB Smart Chain",
        "rpc": os.getenv("BSC_RPC", "https://bsc-dataseed.binance.org"),
        "explorer_api": "https://api.bscscan.com/api",   # opcional
        "explorer_key": os.getenv("BSCSCAN_API_KEY", ""), # opcional
        "native_symbol": "BNB",
        "coingecko_id": "binancecoin",
    },
}



# Nativo
MIN_NATIVE_AMOUNT = float(os.getenv("MIN_NATIVE_AMOUNT", "0"))  # em moeda nativa

# ERC-20
# Quantidade mínima de tokens exigida para considerar um pagamento válido.
# Útil para evitar que valores residuais ou transferências equivocadas sejam
# processadas automaticamente.
MIN_TOKEN_AMOUNT = float(os.getenv("MIN_TOKEN_AMOUNT", "0"))

# ERC-20 (opcional)
TOKEN_SYMBOL    = os.getenv("TOKEN_SYMBOL", "TOKEN").strip()
COINGECKO_NATIVE_ID = os.getenv("COINGECKO_NATIVE_ID", CHAIN_NAME.lower()).strip().lower()
COINGECKO_PLATFORM = os.getenv("COINGECKO_PLATFORM", "ethereum").strip().lower()

FREE_PREVIEW_TEXT = os.getenv(
    "FREE_PREVIEW_TEXT",
    "🔓 Curtiu o preview? Assine o VIP para receber o pack completo! Digite /pagar para fazer parte do MELHOR grupo da Unreal 🚀"
).strip()

if not BOT_TOKEN:   raise RuntimeError("BOT_TOKEN não definido no .env")
if not WEBHOOK_URL: raise RuntimeError("WEBHOOK_URL não definido no .env")
if not WALLET_ADDRESS: logging.warning("WALLET_ADDRESS não definido — /pagar ficará limitado.")
if not RPC_URL:
    logging.warning("RPC_URL não definido — verificação on-chain ficará indisponível.")

# =========================
# FASTAPI + PTB
# =========================
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None
BOT_USERNAME = None


class ConfigKV(Base):
    __tablename__ = "config_kv"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)

def cfg_get(key: str, default: Optional[str] = None) -> Optional[str]:
    with SessionLocal() as s:
        row = s.query(ConfigKV).filter(ConfigKV.key == key).first()
        return row.value if row else default

def cfg_set(key: str, value: Optional[str]):
    with SessionLocal() as s:
        try:
            row = s.query(ConfigKV).filter(ConfigKV.key == key).first()
            if not row:
                s.add(ConfigKV(key=key, value=value))
            else:
                row.value = value
            s.commit()
        except Exception:
            s.rollback()
            raise
class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    added_at = Column(DateTime, default=now_utc)

class Pack(Base):
    __tablename__ = "packs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    header_message_id = Column(Integer, nullable=True, unique=True)
    created_at = Column(DateTime, default=now_utc)
    sent = Column(Boolean, default=False)
    tier = Column(String, default="vip")
    files = relationship("PackFile", back_populates="pack", cascade="all, delete-orphan")

class PackFile(Base):
    __tablename__ = "pack_files"
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(Integer, ForeignKey("packs.id", ondelete="CASCADE"))
    file_id = Column(String, nullable=False)
    file_unique_id = Column(String, nullable=True)
    file_type = Column(String, nullable=True)
    role = Column(String, nullable=True)        # preview | file
    file_name = Column(String, nullable=True)
    added_at = Column(DateTime, default=now_utc)
    src_chat_id = Column(BigInteger, nullable=True)
    src_message_id = Column(Integer, nullable=True)

    pack = relationship("Pack", back_populates="files")

    __table_args__ = (
        UniqueConstraint("pack_id", "file_unique_id", "file_type", name="uq_pack_file_unique"),
    )



class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)
    username = Column(String, nullable=True)
    tx_hash = Column(String, unique=True, index=True)
    chain = Column(String, default=CHAIN_NAME)
    amount = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | approved | rejected
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc)
    decided_at = Column(DateTime, nullable=True)

class VipMembership(Base):
    __tablename__ = "vip_memberships"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True, unique=True)
    username = Column(String, nullable=True)
    tx_hash = Column(String, nullable=True)
    start_at = Column(DateTime, nullable=False, default=now_utc)
    expires_at = Column(DateTime, nullable=False)
    active = Column(Boolean, default=True)
    plan = Column(String, default=VipPlan.TRIMESTRAL.value)
    created_at = Column(DateTime, default=now_utc)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)
    invite_link = Column(Text, nullable=True)  # << NOVO




class UserAddress(Base):
    __tablename__ = "user_addresses"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)
    address = Column(String, unique=True, index=True)
    added_at = Column(DateTime, default=now_utc)


def user_address_upsert(user_id: int, address: Optional[str]):
    if not address:
        return
    addr = address.lower()
    with SessionLocal() as s:
        try:
            row = s.query(UserAddress).filter(UserAddress.address == addr).first()
            if not row:
                s.add(UserAddress(user_id=user_id, address=addr))
            else:
                row.user_id = user_id
            s.commit()
        except Exception:
            s.rollback()
            raise


def user_id_by_address(address: Optional[str]) -> Optional[int]:
    if not address:
        return None
    addr = address.lower()
    with SessionLocal() as s:
        row = s.query(UserAddress).filter(UserAddress.address == addr).first()
        return row.user_id if row else None
    
def user_address_get_or_create(user_id: int) -> Optional[str]:
    """Retorna o endereço exclusivo do usuário, gerando se necessário.

    Esta função é um ponto de extensão para integrações com carteiras HD ou
    serviços custodiais. Se um endereço já existir para o usuário, ele é
    retornado; caso contrário, a geração deve ser implementada externamente.
    """
    with SessionLocal() as s:
        row = s.query(UserAddress).filter(UserAddress.user_id == user_id).first()
        if row:
            return row.address
        # TODO: integrar com serviço de geração de carteiras
        return None


class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    id = Column(Integer, primary_key=True)
    hhmm = Column(String, nullable=False)
    tz = Column(String, default="America/Sao_Paulo")
    text = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    tier = Column(String, default="vip")
    created_at = Column(DateTime, default=now_utc)
    __table_args__ = (UniqueConstraint('id', name='uq_scheduled_messages_id'),)


def scheduled_create(hhmm: str, text: str, tier: str = "vip", tz_name: str = "America/Sao_Paulo") -> 'ScheduledMessage':
    with SessionLocal() as s:
        try:
            m = ScheduledMessage(hhmm=hhmm, text=text, tz=tz_name, enabled=True, tier=tier)
            s.add(m); s.commit(); s.refresh(m)
            return m
        except Exception:
            s.rollback()
            raise

def scheduled_all(tier: Optional[str] = None) -> List['ScheduledMessage']:
    with SessionLocal() as s:
        q = s.query(ScheduledMessage)
        if tier:
            q = q.filter(ScheduledMessage.tier == tier)
        return q.order_by(ScheduledMessage.hhmm.asc(), ScheduledMessage.id.asc()).all()

def scheduled_get(sid: int) -> Optional['ScheduledMessage']:
    with SessionLocal() as s:
        return s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()

def scheduled_update(sid: int, hhmm: Optional[str], text: Optional[str]) -> bool:
    with SessionLocal() as s:
        try:
            m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
            if not m:
                return False
            if hhmm:
                m.hhmm = hhmm
            if text is not None:
                m.text = text
            s.commit()
            return True
        except Exception:
            s.rollback()
            raise

def scheduled_toggle(sid: int) -> Optional[bool]:
    with SessionLocal() as s:
        try:
            m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
            if not m:
                return None
            m.enabled = not m.enabled
            s.commit()
            return m.enabled
        except Exception:
            s.rollback()
            raise

def scheduled_delete(sid: int) -> bool:
    with SessionLocal() as s:
        try:
            m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
            if not m:
                return False
            s.delete(m)
            s.commit()
            return True
        except Exception:
            s.rollback()
            raise
# Garante que o esquema do banco esteja atualizado

ensure_schema()
init_db()


# =========================
# DB helpers
# =========================
def list_packs_by_tier(tier: str) -> List['Pack']:
    with SessionLocal() as s:
        return (
            s.query(Pack)
             .filter(Pack.tier == tier)
             .order_by(Pack.created_at.asc())
             .all()
        )




def list_admin_ids() -> List[int]:
    with SessionLocal() as s:
        return [a.user_id for a in s.query(Admin).order_by(Admin.added_at.asc()).all()]


# --- ADMIN helper com cache simples (evita ida ao banco toda hora)
_ADMIN_CACHE: set[int] = set()
_ADMIN_CACHE_TS: float = 0.0

def is_admin(user_id: int) -> bool:
    global _ADMIN_CACHE, _ADMIN_CACHE_TS
    now = dt.datetime.utcnow().timestamp()
    if now - _ADMIN_CACHE_TS > 60:
        with SessionLocal() as s:
            _ADMIN_CACHE = {a.user_id for a in s.query(Admin).all()}
        _ADMIN_CACHE_TS = now
    return int(user_id) in _ADMIN_CACHE


    
def add_admin_db(user_id: int) -> bool:
    with SessionLocal() as s:
        try:
            if s.query(Admin).filter(Admin.user_id == user_id).first():
                return False
            s.add(Admin(user_id=user_id))
            s.commit()
            # atualiza cache
            global _ADMIN_CACHE, _ADMIN_CACHE_TS
            _ADMIN_CACHE.add(user_id); _ADMIN_CACHE_TS = dt.datetime.utcnow().timestamp()
            return True
        except Exception:
            s.rollback()
            raise

def remove_admin_db(user_id: int) -> bool:
    with SessionLocal() as s:
        try:
            a = s.query(Admin).filter(Admin.user_id == user_id).first()
            if not a:
                return False
            s.delete(a)
            s.commit()
            # atualiza cache
            global _ADMIN_CACHE, _ADMIN_CACHE_TS
            _ADMIN_CACHE.discard(user_id); _ADMIN_CACHE_TS = dt.datetime.utcnow().timestamp()
            return True
        except Exception:
            s.rollback()
            raise


async def create_user_invite_link(user_id: int, validity_hours: int = 2, single_use: bool = True, join_request: bool = True) -> str:
    """
    Cria um link de convite para o grupo VIP:
    - expira em `validity_hours`
    - uso único (member_limit=1) se `single_use=True`
    - em modo 'pedido de entrada' se `join_request=True` (recomendado)
    """
    expire_ts = int((now_utc() + dt.timedelta(hours=validity_hours)).timestamp())
    try:
        link = await application.bot.create_chat_invite_link(
            chat_id=GROUP_VIP_ID,
            expire_date=expire_ts,
            member_limit=1 if single_use else None,
            creates_join_request=join_request
        )
        return link.invite_link
    except Exception as e:
        logging.exception("create_user_invite_link falhou")
        # fallback absoluto (não-recomendado): link geral
        return await application.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)

async def revoke_invite_link(invite_link: str):
    try:
        await application.bot.revoke_chat_invite_link(chat_id=GROUP_VIP_ID, invite_link=invite_link)
    except Exception as e:
        # se já expirou/foi revogado, ignoramos
        logging.debug(f"revoke_invite_link: {e}")

async def assign_and_send_invite(user_id: int, username: Optional[str], tx_hash: Optional[str]) -> str:
    """
    Gera um novo invite (expira em 2h, uso único, com join request),
    revoga o anterior (se houver) e salva no registro do VIP.
    Retorna o link para envio ao usuário.
    """
    with SessionLocal() as s:
        m = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
        if not m:

             # cria/renova plano trimestral por padrão e então gere link
            m = vip_upsert_start_or_extend(user_id, username, tx_hash, VipPlan.TRIMESTRAL)

        # revoga o anterior, se existir
        if m.invite_link:
            try:
                asyncio.create_task(revoke_invite_link(m.invite_link))
            except Exception:
                pass

        # cria novo
        new_link = await create_user_invite_link(user_id, validity_hours=2, single_use=True, join_request=True)
        m.invite_link = new_link
        s.commit()
        return new_link

async def vip_join_request_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa pedidos de entrada no grupo VIP via join request."""
    req = update.chat_join_request
    if not req or req.chat.id != GROUP_VIP_ID:
        return

    invite_link = req.invite_link.invite_link if req.invite_link else None
    user_id = req.from_user.id

    if not invite_link:
        await context.bot.decline_chat_join_request(chat_id=req.chat.id, user_id=user_id)
        return

    with SessionLocal() as s:
        vm = s.query(VipMembership).filter(VipMembership.invite_link == invite_link).first()
        valid = (
            vm is not None
            and vm.user_id == user_id
            and vm.active
            and vm.expires_at and vm.expires_at > now_utc()
        )

    if valid:
        await context.bot.approve_chat_join_request(chat_id=req.chat.id, user_id=user_id)
    else:
        await context.bot.decline_chat_join_request(chat_id=req.chat.id, user_id=user_id)

    try:
        await revoke_invite_link(invite_link)
    except Exception:
        pass

    with SessionLocal() as s:
        vm = s.query(VipMembership).filter(VipMembership.invite_link == invite_link).first()
        if vm:
            vm.invite_link = None
            if not valid and vm.expires_at and vm.expires_at <= now_utc():
                vm.active = False
            s.commit()

def create_pack(title: str, header_message_id: Optional[int] = None, tier: str = "vip") -> 'Pack':
    with SessionLocal() as s:
        try:
            p = Pack(title=title.strip(), header_message_id=header_message_id, tier=tier)
            s.add(p)
            s.commit()
            s.refresh(p)
            return p
        except Exception:
            s.rollback()
            raise


def get_pack_by_header(header_message_id: int) -> Optional['Pack']:
    with SessionLocal() as s:
        return s.query(Pack).filter(Pack.header_message_id == header_message_id).first()

def add_file_to_pack(
    pack_id: int, file_id: str, file_unique_id: Optional[str], file_type: str, role: str,
    file_name: Optional[str] = None, src_chat_id: Optional[int] = None, src_message_id: Optional[int] = None
):
    with SessionLocal() as s:
        try:
            pf = PackFile(
                pack_id=pack_id,
                file_id=file_id,
                file_unique_id=file_unique_id,
                file_type=file_type,
                role=role,
                file_name=file_name,
                src_chat_id=src_chat_id,
                src_message_id=src_message_id,
            )
            s.add(pf)
            s.commit()
            s.refresh(pf)
            return pf
        except Exception:
            s.rollback()
            raise


def get_next_unsent_pack(tier: str = "vip") -> Optional['Pack']:
    s = SessionLocal()
    try: return s.query(Pack).filter(Pack.sent == False, Pack.tier == tier).order_by(Pack.created_at.asc()).first()
    finally: s.close()

def mark_pack_sent(pack_id: int):
    with SessionLocal() as s:
        try:
            p = s.query(Pack).filter(Pack.id == pack_id).first()
            if p:
                p.sent = True
                s.commit()
        except Exception:
            s.rollback()
            raise

# =========================
# STORAGE GROUP handlers
# =========================

async def _block_non_admin_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bloqueia TODOS os comandos para não-admin, exceto a allowlist acima.
    Vale para privado e grupos."""
    user = update.effective_user
    if not user:
        return
    if is_admin(user.id):
        return  # admins passam

    text = (update.effective_message.text or "")
    cmd_raw = text.split()[0].lower()  # ex: "/comandos@SeuBot"
    if "@" in cmd_raw:
        cmd_raw = cmd_raw.split("@", 1)[0]  # tira @bot
    cmd = cmd_raw[1:] if cmd_raw.startswith("/") else cmd_raw

    if cmd in ALLOWED_FOR_NON_ADM:
        return  # /pagar e /tx liberados

    # Bloqueia o resto
    await update.effective_message.reply_text("🚫 Comando restrito. Use apenas /pagar ou /tx.")
    raise ApplicationHandlerStop

def header_key(chat_id: int, message_id: int) -> int:
    if chat_id == STORAGE_GROUP_ID: return int(message_id)
    if chat_id == STORAGE_GROUP_FREE_ID: return int(-message_id)
    return int(message_id)

async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.id not in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID}: return
    if msg.reply_to_message: return

    title = (msg.text or "").strip()
    if not title: return

    lower = title.lower()
    banned = {"sim", "não", "nao", "/proximo", "/finalizar", "/cancelar"}
    if lower in banned or title.startswith("/") or len(title) < 4: return

    words = title.split()
    looks_like_title = (
        len(words) >= 2 or lower.startswith(("pack ", "#pack ", "pack:", "[pack]"))
    )
    if not looks_like_title: return

    if update.effective_user and not is_admin(update.effective_user.id): return

    hkey = header_key(msg.chat.id, msg.message_id)
    if get_pack_by_header(hkey):
        await msg.reply_text("Pack já registrado."); return

    tier = "vip" if msg.chat.id == STORAGE_GROUP_ID else "free"
    p = create_pack(title=title, header_message_id=hkey, tier=tier)
    await msg.reply_text(
        f"Pack registrado: <b>{esc(p.title)}</b> (id {p.id}) — <i>{tier.upper()}</i>",
        parse_mode="HTML"
    )

async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.id not in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID}: return

    # Apenas admins podem anexar mídias aos packs
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return

    reply = msg.reply_to_message
    if not reply or not reply.message_id:
        await msg.reply_text("Envie este arquivo como <b>resposta</b> ao título do pack.", parse_mode="HTML")
        return

    hkey = header_key(update.effective_chat.id, reply.message_id)
    pack = get_pack_by_header(hkey)
    if not pack:
        await msg.reply_text("Cabeçalho do pack não encontrado. Responda à mensagem de título.")
        return

    file_id = None; file_unique_id = None; file_type = None; role = "file"; visible_name = None

    if msg.photo:
        biggest = msg.photo[-1]
        file_id = biggest.file_id; file_unique_id = getattr(biggest, "file_unique_id", None)
        file_type = "photo"; role = "preview"; visible_name = (msg.caption or "").strip() or None
    elif msg.video:
        file_id = msg.video.file_id; file_unique_id = getattr(msg.video, "file_unique_id", None)
        file_type = "video"; role = "preview"; visible_name = (msg.caption or "").strip() or None
    elif msg.animation:
        file_id = msg.animation.file_id; file_unique_id = getattr(msg.animation, "file_unique_id", None)
        file_type = "animation"; role = "preview"; visible_name = (msg.caption or "").strip() or None
    elif msg.document:
        file_id = msg.document.file_id; file_unique_id = getattr(msg.document, "file_unique_id", None)
        file_type = "document"; role = "file"; visible_name = getattr(msg.document, "file_name", None)
    elif msg.audio:
        file_id = msg.audio.file_id; file_unique_id = getattr(msg.audio, "file_unique_id", None)
        file_type = "audio"; role = "file"; visible_name = getattr(msg.audio, "file_name", None) or (msg.caption or "").strip() or None
    elif msg.voice:
        file_id = msg.voice.file_id; file_unique_id = getattr(msg.voice, "file_unique_id", None)
        file_type = "voice"; role = "file"; visible_name = (msg.caption or "").strip() or None
    else:
        await msg.reply_text("Tipo de mídia não suportado.", parse_mode="HTML"); return

    with SessionLocal() as s:
        q = s.query(PackFile).filter(PackFile.pack_id == pack.id)
        if file_unique_id:
            q = q.filter(PackFile.file_unique_id == file_unique_id)
        else:
            q = q.filter(PackFile.file_id == file_id)
        if q.first():
            await msg.reply_text("Este arquivo já foi adicionado a este pack.", parse_mode="HTML")
            return
    add_file_to_pack(
        pack_id=pack.id, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role,
        file_name=visible_name, src_chat_id=msg.chat.id, src_message_id=msg.message_id
    )
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b> — <i>{pack.tier.upper()}</i>.", parse_mode="HTML")
    



# =========================
# ENVIO DO PACK (JobQueue) com fallback copy_message
# =========================
# Evita envio concorrente do mesmo pack no mesmo processo
SENDING_PACKS = set()

async def _try_copy_message(context: ContextTypes.DEFAULT_TYPE, target_chat_id: int, pf: PackFile, caption: Optional[str] = None) -> bool:
    if not (pf.src_chat_id and pf.src_message_id): return False
    try:
        await context.application.bot.copy_message(
            chat_id=target_chat_id,
            from_chat_id=pf.src_chat_id,
            message_id=pf.src_message_id,
            caption=caption if caption else None,
            parse_mode="HTML" if caption else None
        ); return True
    except Exception as e:
        logging.warning(f"[copy_message] Falhou para item {pf.id}: {e}"); return False

async def _try_send_photo(context: ContextTypes.DEFAULT_TYPE, target_chat_id: int, pf: PackFile, caption: Optional[str] = None) -> bool:
    try:
        await context.application.bot.send_photo(chat_id=target_chat_id, photo=pf.file_id, caption=caption); return True
    except BadRequest as e:
        logging.warning(f"[send_photo] Falha {pf.id}: {e}. Tentando copy_message.")
        return await _try_copy_message(context, target_chat_id, pf, caption=caption)
    except Exception as e:
        logging.warning(f"[send_photo] Erro {pf.id}: {e}. Tentando copy_message.")
        return await _try_copy_message(context, target_chat_id, pf, caption=caption)

async def _try_send_video_or_animation(context: ContextTypes.DEFAULT_TYPE, target_chat_id: int, pf: PackFile, caption: Optional[str] = None) -> bool:
    try:
        if pf.file_type == "video":
            await context.application.bot.send_video(chat_id=target_chat_id, video=pf.file_id, caption=caption)
        else:
            await context.application.bot.send_animation(chat_id=target_chat_id, animation=pf.file_id, caption=caption)
        return True
    except Exception as e:
        logging.warning(f"[send_{pf.file_type}] Falha {pf.id}: {e}. Tentando copy_message.")
        return await _try_copy_message(context, target_chat_id, pf, caption=caption)

async def _try_send_document_like(context: ContextTypes.DEFAULT_TYPE, target_chat_id: int, pf: PackFile, caption: Optional[str] = None) -> bool:
    try:
        if pf.file_type == "document":
            await context.application.bot.send_document(chat_id=target_chat_id, document=pf.file_id, caption=caption)
        elif pf.file_type == "audio":
            await context.application.bot.send_audio(chat_id=target_chat_id, audio=pf.file_id, caption=caption)
        elif pf.file_type == "voice":
            await context.application.bot.send_voice(chat_id=target_chat_id, voice=pf.file_id, caption=caption)
        else:
            await context.application.bot.send_document(chat_id=target_chat_id, document=pf.file_id, caption=caption)
        return True
    except BadRequest as e:
        # Só faz fallback se for um erro típico de ID/arquivo – não para timeouts genéricos
        msg = str(e).lower()
        if any(x in msg for x in ["wrong file identifier", "failed to get http url content", "file not found"]):
            logging.warning(f"[send_{pf.file_type}] BadRequest {pf.id}: {e}. Tentando copy_message.")
            return await _try_copy_message(context, target_chat_id, pf, caption=caption)
        logging.warning(f"[send_{pf.file_type}] BadRequest {pf.id}: {e}. (Sem fallback)")
        return False
    except Exception as e:
        logging.warning(f"[send_{pf.file_type}] Erro {pf.id}: {e}. Tentando copy_message.")
        return await _try_copy_message(context, target_chat_id, pf, caption=caption)


async def _send_preview_media(context: ContextTypes.DEFAULT_TYPE, target_chat_id: int, previews: List[PackFile]) -> Dict[str, int]:
    counts = {"photos": 0, "videos": 0, "animations": 0}
    photo_items = [pf for pf in previews if pf.file_type == "photo"]
    if photo_items:
        media = []
        for pf in photo_items:
            try: media.append(InputMediaPhoto(media=pf.file_id))
            except Exception: media = []; break
        if media:
            try:
                await context.application.bot.send_media_group(chat_id=target_chat_id, media=media)
                counts["photos"] += len(photo_items)
            except Exception as e:
                logging.warning(f"[send_preview_media] Falha media_group: {e}. Enviando foto a foto.")
                for pf in photo_items:
                    if await _try_send_photo(context, target_chat_id, pf, caption=None):
                        counts["photos"] += 1
        else:
            for pf in photo_items:
                if await _try_send_photo(context, target_chat_id, pf, caption=None):
                    counts["photos"] += 1

    other_prev = [pf for pf in previews if pf.file_type in ("video", "animation")]
    for pf in other_prev:
        if await _try_send_video_or_animation(context, target_chat_id, pf, caption=None):
            counts["videos" if pf.file_type == "video" else "animations"] += 1
    return counts

async def enviar_pack_job(context: ContextTypes.DEFAULT_TYPE, tier: str, target_chat_id: int) -> str:
    try:
        pack = get_next_unsent_pack(tier=tier)
        if not pack:
            return f"Nenhum pack pendente para envio ({tier})."

        # Evita concorrência no mesmo processo
        if pack.id in SENDING_PACKS:
            return f"Pack #{pack.id} já está em envio ({tier})."
        SENDING_PACKS.add(pack.id)

        # Marca como "em envio" otimista (flag via DB: set sent=True provisoriamente)
        # Assim outro worker/processo que use get_next_unsent_pack não pega o mesmo.
        with SessionLocal() as s:
            p = s.query(Pack).filter(Pack.id == pack.id).first()
            if not p:
                SENDING_PACKS.discard(pack.id)
                return f"Pack desapareceu ({tier})."
            if p.sent:
                SENDING_PACKS.discard(pack.id)
                return f"Pack '{p.title}' já marcado como enviado ({tier})."
            p.sent = True  # reserva
            s.commit()

        # Agora recupere os arquivos
        with SessionLocal() as s:
            p = s.query(Pack).filter(Pack.id == pack.id).first()
            files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()

        if not files:
            # nada para enviar — mantemos sent=True
            return f"Pack '{p.title}' ({tier}) não possui arquivos. Marcado como enviado."

        # --- Dedupe defensivo
        seen = set()  # (file_unique_id, file_type) ou (file_id, file_type)
        previews = []
        docs = []
        for f in files:
            key = ((f.file_unique_id or f.file_id), f.file_type)
            if key in seen:
                continue
            seen.add(key)
            (previews if f.role == "preview" else docs).append(f)

        # Envia previews primeiro
        if previews:
            await _send_preview_media(context, target_chat_id, previews)

        # Envia título
        await context.application.bot.send_message(chat_id=target_chat_id, text=p.title)

        # Envia docs (com fallback controlado)
        for f in docs:
            await _try_send_document_like(context, target_chat_id, f, caption=None)

        # Crosspost de previews pro FREE (somente se VIP)
        if tier == "vip" and previews:
            try:
                await _send_preview_media(context, GROUP_FREE_ID, previews)
                await context.application.bot.send_message(chat_id=GROUP_FREE_ID, text=FREE_PREVIEW_TEXT)
            except Exception as e:
                logging.warning(f"Falha no crosspost VIP->FREE: {e}")

        return f"✅ Enviado pack '{p.title}' ({tier})."
    except Exception as e:
        logging.exception("Erro no enviar_pack_job")
        return f"❌ Erro no envio ({tier}): {e!r}"
    finally:
        SENDING_PACKS.discard(pack.id if 'pack' in locals() and pack else None)

async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE):
    return await enviar_pack_job(context, tier="vip", target_chat_id=GROUP_VIP_ID)


async def enviar_pack_free_job(context: ContextTypes.DEFAULT_TYPE):
    return await enviar_pack_job(context, tier="free", target_chat_id=GROUP_FREE_ID)

# =========================
# COMMANDS & ADMIN
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = ("Fala! Eu gerencio packs VIP/FREE, pagamentos via MetaMask e mensagens agendadas.\nUse /pagar para maiores")
    if msg: await msg.reply_text(text)

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Somente admin pode usar /comandos
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    base = [
        "📋 <b>Comandos</b>",
        "• /start — mensagem inicial",
        "• /comandos — esta lista",
        "• /listar_comandos — (alias)",
        "• /getid — mostra seus IDs",
        "",
        "💬 Envio imediato:",
        "• /say_vip <texto> — envia AGORA no VIP",
        "• /say_free <texto> — envia AGORA no FREE",
        "",
        "💸 Pagamento (MetaMask):",
        "• /pagar — instruções (vai pro seu privado)",
        "• /tx <hash> — valida e libera o VIP",
        "",
        "🧩 Packs:",
        "• /novopack (privado) — fluxo guiado (VIP/FREE)",
        "• /novopackvip (privado) — atalho",
        "• /novopackfree (privado) — atalho",
        "",
        "🕒 Mensagens agendadas:",
        "• /add_msg_vip HH:MM <texto>",
        "• /add_msg_free HH:MM <texto>",
        "• /list_msgs_vip | /list_msgs_free",
        "• /edit_msg_vip <id> [HH:MM] [texto]",
        "• /edit_msg_free <id> [HH:MM] [texto]",
        "• /toggle_msg_vip <id> | /toggle_msg_free <id>",
        "• /del_msg_vip <id> | /del_msg_free <id>",
        "",
        "🛠 <b>Admin</b>",
        "• /simularvip — envia o próximo pack VIP pendente",
        "• /simularfree — envia o próximo pack FREE pendente",
        "• /listar_packsvip | /listar_packvip | /listar_packs_vip | /listar_pack_vip — lista packs VIP",
        "• /listar_packsfree — lista packs FREE",
        "• /pack_info <id> — detalhes do pack",
        "• /excluir_item <id_item> — remove item do pack",
        "• /excluir_pack [<id>] — remove pack (com confirmação)",
        "• /set_pendentevip <id> — marca pack VIP como pendente",
        "• /set_pendentefree <id> — marca pack FREE como pendente",
        "• /set_enviadovip <id> — marca pack VIP como enviado",
        "• /set_enviadofree <id> — marca pack FREE como enviado",
        "• /set_pack_horario_vip HH:MM — define horário diário VIP",
        "• /set_pack_horario_free HH:MM — define horário diário FREE",
        "• /limpar_chat <N> — apaga últimas N mensagens",
        "• /mudar_nome <novo nome> — muda nome exibido do bot",
        "• /add_admin <user_id> | /rem_admin <user_id>",
        "• /listar_admins — lista admins",
        "• /listar_pendentes — pagamentos pendentes",
        "• /aprovar_tx <user_id> — aprova e envia convite VIP",
        "• /rejeitar_tx <user_id> [motivo] — rejeita pagamento",
        "",
        "🧩 Vip Pagamentos:",
        "• /valor — define preços",
        "• /vip_list — lista VIPs ativos",
        "• /vip_addtime <user_id> <dias>",
        "• /vip_set <user_id> <dias>",
        "• /vip_remove <user_id>",
    ]

    # sanear <> pra não quebrar HTML
    safe_lines = [wrap_ph(x) for x in base]
    await update.effective_message.reply_text("\n".join(safe_lines), parse_mode="HTML")


async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; chat = update.effective_chat; msg = update.effective_message
    if msg:
        await msg.reply_text(f"Seu nome: {esc(user.full_name)}\nSeu ID: {user.id}\nID deste chat: {chat.id}", parse_mode="HTML")

async def say_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    txt = (update.effective_message.text or "").split(maxsplit=1)
    if len(txt) < 2 or not txt[1].strip(): return await update.effective_message.reply_text("Uso: /say_vip <texto>")
    try:
        await application.bot.send_message(chat_id=GROUP_VIP_ID, text=txt[1].strip()); await update.effective_message.reply_text("✅ Enviado no VIP.")
    except Exception as e: await update.effective_message.reply_text(f"❌ Erro: {e}")

async def say_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    txt = (update.effective_message.text or "").split(maxsplit=1)
    if len(txt) < 2 or not txt[1].strip(): return await update.effective_message.reply_text("Uso: /say_free <texto>")
    try:
        await application.bot.send_message(chat_id=GROUP_FREE_ID, text=txt[1].strip()); await update.effective_message.reply_text("✅ Enviado no FREE.")
    except Exception as e: await update.effective_message.reply_text(f"❌ Erro: {e}")

async def mudar_nome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /mudar_nome <novo nome exibido do bot>")
    try:
        await application.bot.set_my_name(name=" ".join(context.args).strip()); await update.effective_message.reply_text("✅ Nome exibido alterado.")
    except Exception as e: await update.effective_message.reply_text(f"Erro: {e}")

async def limpar_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /limpar_chat <N>")
    try:
        n = int(context.args[0]); 
        if n <= 0 or n > 500: return await update.effective_message.reply_text("Escolha um N entre 1 e 500.")
    except: return await update.effective_message.reply_text("Número inválido.")
    chat_id = update.effective_chat.id; current_id = update.effective_message.message_id; deleted = 0
    for mid in range(current_id, current_id - n, -1):
        try:
            await application.bot.delete_message(chat_id=chat_id, message_id=mid); deleted += 1; await asyncio.sleep(0.03)
        except Exception: pass
    await application.bot.send_message(chat_id=chat_id, text=f"🧹 Apaguei ~{deleted} mensagens (melhor esforço).")

async def listar_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    ids = list_admin_ids()
    await update.effective_message.reply_text("👑 Admins:\n" + ("\n".join(f"- {i}" for i in ids) if ids else "Nenhum"), parse_mode="HTML")

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /add_admin <user_id>")
    try: uid = int(context.args[0])
    except: return await update.effective_message.reply_text("user_id inválido.")
    await update.effective_message.reply_text("✅ Admin adicionado." if add_admin_db(uid) else "Já era admin.")

async def rem_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /rem_admin <user_id>")
    try: uid = int(context.args[0])
    except: return await update.effective_message.reply_text("user_id inválido.")
    await update.effective_message.reply_text("✅ Admin removido." if remove_admin_db(uid) else "Este user não é admin.")

async def valor_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    msg = update.effective_message
    if not context.args:
        nat = get_vip_price_native()
        tok = get_vip_price_token()
        texto = (
            "💲 Preços atuais:\n"
            f"Nativo: {nat if nat is not None else 'não definido'}\n"
            f"Token: {tok if tok is not None else 'não definido'}"
        )
        return await msg.reply_text(texto)
    if len(context.args) < 2:
        return await msg.reply_text("Uso: /valor <nativo|token> <valor>")
    tipo = context.args[0].lower()
    try:
        valor = float(context.args[1].replace(',', '.'))
    except Exception:
        return await msg.reply_text("Valor inválido.")
    if tipo.startswith('n'):
        set_vip_price_native(valor)
        await msg.reply_text(f"✅ Preço nativo definido para {valor}")
    elif tipo.startswith('t'):
        set_vip_price_token(valor)
        await msg.reply_text(f"✅ Preço token definido para {valor}")
    else:
        await msg.reply_text("Uso: /valor <nativo|token> <valor>")

async def vip_list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    membros = vip_list_active()
    if not membros:
        return await update.effective_message.reply_text("Nenhum VIP ativo.")
    linhas = []
    for m in membros:
        hash_abrev = (m.tx_hash[:10] + '...') if m.tx_hash else '-'
        if m.username:
            if re.fullmatch(r"[A-Za-z0-9_]+", m.username):
                user = f"@{m.username}"
            else:
                user = m.username
        else:
            user = '-'
        linhas.append(f"{m.user_id} | {user} | {hash_abrev} | {human_left(m.expires_at)}")
    await update.effective_message.reply_text("\n".join(linhas))

async def vip_addtime_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    if len(context.args) < 2:
        return await update.effective_message.reply_text("Uso: /vip_addtime <user_id> <dias>")
    try:
        uid = int(context.args[0])
        dias = int(context.args[1])
    except Exception:
        return await update.effective_message.reply_text("Parâmetros inválidos.")
    m = vip_adjust_days(uid, dias)
    if not m:
        return await update.effective_message.reply_text("Usuário não encontrado.")
    await update.effective_message.reply_text(
        f"✅ Novo prazo: {m.expires_at.strftime('%d/%m/%Y')} ({human_left(m.expires_at)})"
    )

async def vip_set_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    if len(context.args) < 2:
        return await update.effective_message.reply_text("Uso: /vip_set <user_id> <dias>")
    try:
        uid = int(context.args[0])
        dias = int(context.args[1])
    except Exception:
        return await update.effective_message.reply_text("Parâmetros inválidos.")
    try:
        chat = await context.bot.get_chat(uid)
        username = chat.username or chat.full_name
    except Exception:
        username = None
    plan_map = {30: VipPlan.MENSAL, 90: VipPlan.TRIMESTRAL, 180: VipPlan.SEMESTRAL, 365: VipPlan.ANUAL}
    plan = plan_map.get(dias)
    if not plan:
        return await update.effective_message.reply_text("Dias devem ser 30, 90, 180 ou 365 dias.")
    m = vip_upsert_start_or_extend(uid, username, None, plan)
    await update.effective_message.reply_text(
        f"✅ VIP válido até {m.expires_at.strftime('%d/%m/%Y')} ({human_left(m.expires_at)})"
    )



async def vip_remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    if not context.args:
        return await update.effective_message.reply_text("Uso: /vip_remove <user_id>")

    try:
        uid = int(context.args[0])
    except Exception:
        return await update.effective_message.reply_text("user_id inválido.")

    # pega o registro antes de desativar (para ter o invite_link)
    m = vip_get(uid)

    # revoga link antigo se existir
    if m and getattr(m, "invite_link", None):
        try:
            await revoke_invite_link(m.invite_link)
        except Exception:
            pass
        # limpa o campo no banco
        with SessionLocal() as s:
            vm = s.query(VipMembership).filter(VipMembership.user_id == uid).first()
            if vm:
                vm.invite_link = None
                s.commit()

    # desativa VIP
    ok = vip_deactivate(uid)

    if ok:
        # “kick técnico” para remover acesso atual, mesmo se ainda tiver link antigo em conversas
        try:
            await application.bot.ban_chat_member(chat_id=GROUP_VIP_ID, user_id=uid)
            await application.bot.unban_chat_member(chat_id=GROUP_VIP_ID, user_id=uid)
        except Exception:
            pass
        return await update.effective_message.reply_text("✅ VIP removido/desativado.")
    else:
        return await update.effective_message.reply_text("Usuário não era VIP.")




async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    status = await enviar_pack_vip_job(context); await update.effective_message.reply_text(status)

async def simularfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    status = await enviar_pack_free_job(context); await update.effective_message.reply_text(status)

async def listar_packsvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    with SessionLocal() as s:
        packs = (
            s.query(Pack)
            .filter(Pack.tier == "vip", Pack.sent.is_(False))
            .order_by(Pack.created_at.asc())
            .all()
        )

        if not packs:
            await update.effective_message.reply_text("Nenhum pack VIP registrado.")
            raise ApplicationHandlerStop  # garante que nada mais responda

        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status  = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(
                f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}"
            )

    await update.effective_message.reply_text("\n".join(lines))
    # corta a propagação por segurança
    raise ApplicationHandlerStop




async def listar_packsfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    with SessionLocal() as s:
        packs = list_packs_by_tier("free")
        if not packs: return await update.effective_message.reply_text("Nenhum pack FREE registrado.")
        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        await update.effective_message.reply_text("\n".join(lines))
   

async def pack_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /pack_info <id>")
    try: pid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    with SessionLocal() as s:
        p = s.query(Pack).filter(Pack.id == pid).first()
        if not p: return await update.effective_message.reply_text("Pack não encontrado.")
        files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()
        if not files: return await update.effective_message.reply_text(f"Pack '{p.title}' não possui arquivos.")
        lines = [f"Pack [{p.id}] {esc(p.title)} — {'ENVIADO' if p.sent else 'PENDENTE'} — {p.tier.upper()}"]
        for f in files:
            name = f.file_name or ""
            src = f" src:{f.src_chat_id}/{f.src_message_id}" if f.src_chat_id and f.src_message_id else ""
            lines.append(f" - item #{f.id} | {f.file_type} ({f.role}) {name}{src}")
        await update.effective_message.reply_text("\n".join(lines))


async def excluir_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /excluir_item <id_item>")
    try: item_id = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido. Use: /excluir_item <id_item>")

    with SessionLocal() as s:
        try:
            item = s.query(PackFile).filter(PackFile.id == item_id).first()
            if not item:
                return await update.effective_message.reply_text("Item não encontrado.")
            pack = s.query(Pack).filter(Pack.id == item.pack_id).first()
            s.delete(item)
            s.commit()
            await update.effective_message.reply_text(f"✅ Item #{item_id} removido do pack '{pack.title if pack else '?'}'.")
        except Exception as e:
            s.rollback()
            logging.exception("Erro ao remover item")
            await update.effective_message.reply_text(f"❌ Erro ao remover item: {e}")

DELETE_PACK_CONFIRM = 1
async def excluir_pack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins."); return ConversationHandler.END
    if not context.args:
        packs = list_packs_by_tier("vip") + list_packs_by_tier("free")
        if not packs:
            await update.effective_message.reply_text("Nenhum pack registrado.")
            return ConversationHandler.END
        lines = ["🗑 <b>Excluir Pack</b>\n", "Envie: <code>/excluir_pack &lt;id&gt;</code> para escolher um."]
        for p in packs:
            lines.append(f"[{p.id}] {esc(p.title)} ({p.tier.upper()})")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
        return ConversationHandler.END
    try: pid = int(context.args[0])
    except: await update.effective_message.reply_text("Uso: /excluir_pack <id>"); return ConversationHandler.END
    context.user_data["delete_pid"] = pid
    await update.effective_message.reply_text(f"Confirma excluir o pack <b>#{pid}</b>? (sim/não)", parse_mode="HTML")
    return DELETE_PACK_CONFIRM

async def excluir_pack_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = (update.effective_message.text or "").strip().lower()
    if ans not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Responda <b>sim</b> para confirmar ou <b>não</b> para cancelar.", parse_mode="HTML")
        return DELETE_PACK_CONFIRM
    pid = context.user_data.get("delete_pid"); context.user_data.pop("delete_pid", None)
    if ans in ("não", "nao"): await update.effective_message.reply_text("Cancelado."); return ConversationHandler.END
    with SessionLocal() as s:
        try:
            p = s.query(Pack).filter(Pack.id == pid).first()
            if not p:
                await update.effective_message.reply_text("Pack não encontrado.")
                return ConversationHandler.END
            title = p.title
            s.delete(p)
            s.commit()
            await update.effective_message.reply_text(f"✅ Pack <b>{esc(title)}</b> (#{pid}) excluído.", parse_mode="HTML")
        except Exception as e:
            s.rollback()
            logging.exception("Erro ao excluir pack")
            await update.effective_message.reply_text(f"❌ Erro ao excluir: {e}")
    return ConversationHandler.END

async def _set_sent_by_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str, sent: bool):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins podem usar este comando.")
    if not context.args:
        return await update.effective_message.reply_text(f"Uso: /{'set_enviado' if sent else 'set_pendente'}{tier} <id_do_pack>")
    try: pid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    with SessionLocal() as s:
        try:
            p = s.query(Pack).filter(Pack.id == pid, Pack.tier == tier).first()
            if not p:
                return await update.effective_message.reply_text(f"Pack não encontrado para {tier.upper()}.")
            p.sent = sent
            s.commit()
            await update.effective_message.reply_text(
                f"✅ Pack #{p.id} — “{esc(p.title)}” marcado como <b>{'ENVIADO' if sent else 'PENDENTE'}</b> ({tier}).",
                parse_mode="HTML",
            )
        except Exception as e:
            s.rollback()
            await update.effective_message.reply_text(f"❌ Erro ao atualizar: {e}")

async def set_pendentefree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _set_sent_by_tier(update, context, tier="free", sent=False)
async def set_pendentevip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _set_sent_by_tier(update, context, tier="vip", sent=False)
async def set_enviadofree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _set_sent_by_tier(update, context, tier="free", sent=True)
async def set_enviadovip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _set_sent_by_tier(update, context, tier="vip", sent=True)

# =========================
# NOVOPACK (privado)
# =========================
CHOOSE_TIER, TITLE, CONFIRM_TITLE, PREVIEWS, FILES, CONFIRM_SAVE = range(6)

def _require_admin(update: Update) -> bool:
    return update.effective_user and is_admin(update.effective_user.id)

async def hint_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Agora envie PREVIEWS (📷 foto / 🎞 vídeo / 🎞 animação) ou use /proximo para ir aos ARQUIVOS.")

async def hint_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Agora envie ARQUIVOS (📄 documento / 🎵 áudio / 🎙 voice) ou use /finalizar para revisar e salvar.")

def _is_allowed_group(chat_id: int) -> bool:
    return chat_id in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID}

async def novopack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update):
        await update.effective_message.reply_text("Apenas admins podem usar este comando."); return ConversationHandler.END
    chat = update.effective_chat
    if chat.type != "private" and not _is_allowed_group(chat.id):
        try: username = BOT_USERNAME or (await application.bot.get_me()).username
        except Exception: username = None
        link = f"https://t.me/{username}?start=novopack" if username else ""
        await update.effective_message.reply_text(f"Use este comando no privado comigo, por favor.\n{link}")
        return ConversationHandler.END
    context.user_data.clear()
    await update.effective_message.reply_text("Quer cadastrar em qual tier? Responda <b>vip</b> ou <b>free</b>.", parse_mode="HTML")
    return CHOOSE_TIER

async def novopack_choose_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer in ("vip", "v"): context.user_data["tier"] = "vip"
    elif answer in ("free", "f", "gratis", "grátis"): context.user_data["tier"] = "free"
    else:
        await update.effective_message.reply_text("Não entendi. Responda <b>vip</b> ou <b>free</b> 🙂", parse_mode="HTML"); return CHOOSE_TIER
    await update.effective_message.reply_text(f"🧩 Novo pack <b>{context.user_data['tier'].upper()}</b> — envie o <b>título</b>.", parse_mode="HTML")
    return TITLE

async def novopackvip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update): await update.effective_message.reply_text("Apenas admins."); return ConversationHandler.END
    if update.effective_chat.type != "private": await update.effective_message.reply_text("Use este comando no privado comigo, por favor."); return ConversationHandler.END
    context.user_data.clear(); context.user_data["tier"] = "vip"
    await update.effective_message.reply_text("🧩 Novo pack VIP — envie o <b>título</b>.", parse_mode="HTML"); return TITLE

async def novopackfree_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update): await update.effective_message.reply_text("Apenas admins."); return ConversationHandler.END
    if update.effective_chat.type != "private": await update.effective_message.reply_text("Use este comando no privado comigo, por favor."); return ConversationHandler.END
    context.user_data.clear(); context.user_data["tier"] = "free"
    await update.effective_message.reply_text("🧩 Novo pack FREE — envie o <b>título</b>.", parse_mode="HTML"); return TITLE

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—"); previews = user_data.get("previews", []); files = user_data.get("files", []); tier = (user_data.get("tier") or "vip").upper()
    preview_names = []
    p_index = 1
    for it in previews:
        base = it.get("file_name")
        if base: preview_names.append(esc(base))
        else:
            label = "Foto" if it["file_type"] == "photo" else ("Vídeo" if it["file_type"] == "video" else "Animação")
            preview_names.append(f"{label} {p_index}"); p_index += 1
    file_names = []
    f_index = 1
    for it in files:
        base = it.get("file_name")
        if base: file_names.append(esc(base))
        else: file_names.append(f"{it['file_type'].capitalize()} {f_index}"); f_index += 1
    return "\n".join([
        f"📦 <b>Resumo do Pack</b> ({tier})",
        f"• Nome: <b>{esc(title)}</b>",
        f"• Previews ({len(previews)}): " + (", ".join(preview_names) if preview_names else "—"),
        f"• Arquivos ({len(files)}): " + (", ".join(file_names) if file_names else "—"),
        "", "Deseja salvar? (<b>sim</b>/<b>não</b>)"
    ])

async def novopack_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.effective_message.text or "").strip()
    if not title: await update.effective_message.reply_text("Título vazio. Envie um texto com o título do pack."); return TITLE
    context.user_data["title_candidate"] = title
    await update.effective_message.reply_text(f"Confirma o nome: <b>{esc(title)}</b>? (sim/não)", parse_mode="HTML"); return CONFIRM_TITLE

async def novopack_confirm_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Por favor, responda <b>sim</b> ou <b>não</b>.", parse_mode="HTML"); return CONFIRM_TITLE
    if answer in ("não", "nao"):
        await update.effective_message.reply_text("Ok! Envie o <b>novo título</b> do pack.", parse_mode="HTML"); return TITLE
    context.user_data["title"] = context.user_data.get("title_candidate"); context.user_data["previews"] = []; context.user_data["files"] = []
    await update.effective_message.reply_text(
        "2) Envie as <b>PREVIEWS</b> (📷 fotos / 🎞 vídeos / 🎞 animações).\nEnvie quantas quiser. Quando terminar, mande /proximo.",
        parse_mode="HTML"
    ); return PREVIEWS

async def novopack_collect_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; previews: List[Dict[str, Any]] = context.user_data.get("previews", [])
    if msg.photo:
        biggest = msg.photo[-1]
        previews.append({"file_id": biggest.file_id, "file_type": "photo", "file_name": (msg.caption or "").strip() or None, "src_chat_id": msg.chat.id, "src_message_id": msg.message_id})
        await msg.reply_text("✅ <b>Foto cadastrada</b>. Envie mais ou /proximo.", parse_mode="HTML")
    elif msg.video:
        previews.append({"file_id": msg.video.file_id, "file_type": "video", "file_name": (msg.caption or "").strip() or None, "src_chat_id": msg.chat.id, "src_message_id": msg.message_id})
        await msg.reply_text("✅ <b>Preview (vídeo) cadastrado</b>. Envie mais ou /proximo.", parse_mode="HTML")
    elif msg.animation:
        previews.append({"file_id": msg.animation.file_id, "file_type": "animation", "file_name": (msg.caption or "").strip() or None, "src_chat_id": msg.chat.id, "src_message_id": msg.message_id})
        await msg.reply_text("✅ <b>Preview (animação) cadastrado</b>. Envie mais ou /proximo.", parse_mode="HTML")
    else:
        await hint_previews(update, context); return PREVIEWS
    context.user_data["previews"] = previews; return PREVIEWS

async def novopack_next_to_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("title"):
        await update.effective_message.reply_text("Título não encontrado. Use /cancelar e recomece com /novopack."); return ConversationHandler.END
    await update.effective_message.reply_text(
        "3) Agora envie os <b>ARQUIVOS</b> (📄 documentos / 🎵 áudio / 🎙 voice).\nEnvie quantos quiser. Quando terminar, mande /finalizar.",
        parse_mode="HTML"
    ); return FILES

async def novopack_collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message; files: List[Dict[str, Any]] = context.user_data.get("files", [])
    if msg.document:
        files.append({"file_id": msg.document.file_id, "file_type": "document", "file_name": getattr(msg.document, "file_name", None) or (msg.caption or "").strip() or None, "src_chat_id": msg.chat.id, "src_message_id": msg.message_id})
        await msg.reply_text("✅ <b>Arquivo cadastrado</b>. Envie mais ou /finalizar.", parse_mode="HTML")
    elif msg.audio:
        files.append({"file_id": msg.audio.file_id, "file_type": "audio", "file_name": getattr(msg.audio, "file_name", None) or (msg.caption or "").strip() or None, "src_chat_id": msg.chat.id, "src_message_id": msg.message_id})
        await msg.reply_text("✅ <b>Áudio cadastrado</b>. Envie mais ou /finalizar.", parse_mode="HTML")
    elif msg.voice:
        files.append({"file_id": msg.voice.file_id, "file_type": "voice", "file_name": (msg.caption or "").strip() or None, "src_chat_id": msg.chat.id, "src_message_id": msg.message_id})
        await msg.reply_text("✅ <b>Voice cadastrado</b>. Envie mais ou /finalizar.", parse_mode="HTML")
    else:
        await hint_files(update, context); return FILES
    context.user_data["files"] = files; return FILES

async def novopack_finish_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(_summary_from_session(context.user_data), parse_mode="HTML"); return CONFIRM_SAVE

async def novopack_confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Responda <b>sim</b> para salvar ou <b>não</b> para cancelar.", parse_mode="HTML"); return CONFIRM_SAVE
    if answer in ("não", "nao"):
        context.user_data.clear(); await update.effective_message.reply_text("Operação cancelada. Nada foi salvo."); return ConversationHandler.END
    title = context.user_data.get("title"); previews = context.user_data.get("previews", []); files = context.user_data.get("files", []); tier = context.user_data.get("tier", "vip")
    p = create_pack(title=title, header_message_id=None, tier=tier)
    for it in previews:
        add_file_to_pack(p.id, it["file_id"], None, it["file_type"], "preview", it.get("file_name"), it.get("src_chat_id"), it.get("src_message_id"))
    for it in files:
        add_file_to_pack(p.id, it["file_id"], None, it["file_type"], "file", it.get("file_name"), it.get("src_chat_id"), it.get("src_message_id"))
    context.user_data.clear(); await update.effective_message.reply_text(f"🎉 <b>{esc(title)}</b> cadastrado com sucesso em <b>{tier.upper()}</b>!", parse_mode="HTML")
    return ConversationHandler.END

async def novopack_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear(); await update.effective_message.reply_text("Operação cancelada."); return ConversationHandler.END

# =========================
# Pagamento / Verificação on-chain (JSON-RPC)
# =========================
HEX_0X = "0x"
# keccak("Transfer(address,address,uint256)")
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# method selector for ERC-20 decimals()
ERC20_DECIMALS_SELECTOR = "0x313ce567"

def _hex_to_int(h: Optional[str]) -> int:
    if not h: return 0
    return int(h, 16) if h.startswith(HEX_0X) else int(h)

def _to_wei(amount_native: float, decimals: int = 18) -> int:
    return int(round(amount_native * (10 ** decimals)))

PRICE_TOLERANCE = float(os.getenv("PRICE_TOLERANCE", "0.01"))  # 1%

DEFAULT_PLAN_PRICE_USD = {
    VipPlan.TRIMESTRAL: 70.0,
    VipPlan.SEMESTRAL: 110.0,
    VipPlan.ANUAL: 1.0,
    VipPlan.MENSAL: 0.5,
}


def get_plan_prices_usd() -> Dict[VipPlan, float]:
    raw = cfg_get("vip_plan_prices_usd")
    if raw:
        try:
            data = json.loads(raw)
            return {
                plan: float(data.get(plan.name, DEFAULT_PLAN_PRICE_USD[plan]))
                for plan in VipPlan
            }
        except Exception:
            logging.warning("vip_plan_prices_usd inválido: %s", raw)
    return DEFAULT_PLAN_PRICE_USD.copy()


def plan_from_amount(amount_usd: float) -> Optional[VipPlan]:
    prices = get_plan_prices_usd()
    for plan, price in prices.items():
        if abs(amount_usd - price) <= price * PRICE_TOLERANCE:
            return plan
    return None

async def fetch_price_usd(cfg: Dict[str, Any]) -> Optional[float]:
    """Obtém o preço em USD do ativo nativo configurado."""
    chain_name = cfg.get("chain_name", "").lower()
    symbol = (cfg.get("symbol") or "").lower()
    asset_id = COINGECKO_NATIVE_ID or symbol or chain_name
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            url = (
                "https://api.coingecko.com/api/v3/simple/price?ids="
                f"{asset_id}&vs_currencies=usd"
            )
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            return data.get(asset_id, {}).get("usd")
    except Exception as e:
        logging.warning("Falha ao obter cotação USD: %s", e)
        return None
    
async def fetch_price_usd_for_contract(contract_addr: str) -> Optional[Tuple[float, int]]:
    """Retorna preço em USD e casas decimais para um contrato ERC-20.

    Busca o preço via ``simple/token_price`` e obtém os metadados do token
    em ``coins/{platform}/contract/{address}`` para descobrir as ``decimals``.
    Retorna ``(preço, decimals)`` quando ambos forem encontrados ou ``None``
    se qualquer chamada falhar.
    """
    if not contract_addr:
        return None
    contract_addr = contract_addr.lower()
    platform = COINGECKO_PLATFORM or "ethereum"
    price_url = (
        "https://api.coingecko.com/api/v3/simple/token_price/"
        f"{platform}?contract_addresses={contract_addr}&vs_currencies=usd"
    )
    meta_url = (
        "https://api.coingecko.com/api/v3/coins/"
        f"{platform}/contract/{contract_addr}?localization=false&tickers=false"
        "&market_data=false&community_data=false&developer_data=false&sparkline=false"
    )
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
            info = data.get(contract_addr)
            if not info:
                return None
            price = info.get("usd")
            decimals = info.get("decimals")
            if price is None or decimals is None:
                return None
            return price, decimals
    except Exception as e:
        logging.warning("Falha ao obter cotação USD (token): %s", e)
        return None
    

async def rpc_call(cfg: Dict[str, Any], method: str, params: list) -> Any:
    rpc_url = cfg.get("rpc_url")
    if not rpc_url:
        raise RuntimeError("RPC_URL ausente")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data.get("result")
    
    # Mapeamento de cadeias suportadas pela API pública da Blockchair
BLOCKCHAIR_SLUGS: Dict[str, int] = {
    "bitcoin": 8,
    "ethereum": 18,
    "binance-smart-chain": 18,
    "polygon": 18,
    "arbitrum": 18,
    "avalanche": 18,
    "fantom": 18,
    "base": 18,
    "optimism": 18,
    "gnosis": 18,
    "celo": 18,
}

async def verify_native_payment(cfg: Dict[str, Any], tx_hash: str) -> Dict[str, Any]:
    tx = await rpc_call(cfg, "eth_getTransactionByHash", [tx_hash])
    if not tx:
        return {"ok": False, "reason": "Transação não encontrada"}
    to_addr = (tx.get("to") or "").lower()
    wallet = cfg.get("wallet_address")
    if wallet and to_addr != wallet:
        return {"ok": False, "reason": "Destinatário diferente da carteira configurada"}
    value_wei = _hex_to_int(tx.get("value"))
    min_wei = _to_wei(MIN_NATIVE_AMOUNT, 18)
    if value_wei < min_wei:
        return {"ok": False, "reason": f"Valor abaixo do mínimo ({MIN_NATIVE_AMOUNT})"}
    receipt = await rpc_call(cfg, "eth_getTransactionReceipt", [tx_hash])
    if not receipt or receipt.get("status") != "0x1":
        return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
    current_block_hex = await rpc_call(cfg, "eth_blockNumber", [])
    confirmations = _hex_to_int(current_block_hex) - _hex_to_int(receipt.get("blockNumber", "0x0"))
    if confirmations < MIN_CONFIRMATIONS:
        return {"ok": False, "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})"}
    return {
        "ok": True,
        "type": "native",
        "from": (tx.get("from") or "").lower(),
        "to": to_addr,
        "amount_wei": value_wei,
        "confirmations": confirmations,
    }

def _topic_address(topic_hex: str) -> str:
    # topic é 32 bytes; endereço é os últimos 20 bytes
    if topic_hex.startswith(HEX_0X): topic_hex = topic_hex[2:]
    addr = "0x" + topic_hex[-40:]
    return addr.lower()

async def verify_erc20_payment(cfg: Dict[str, Any], tx_hash: str) -> Dict[str, Any]:
    
    receipt = await rpc_call(cfg, "eth_getTransactionReceipt", [tx_hash])
    if not receipt or receipt.get("status") != "0x1":
        return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
    
    logs = receipt.get("logs", [])
    wallet = cfg.get("wallet_address")
    found: Optional[Dict[str, Any]] = None
    reason = "Nenhum Transfer para a carteira" if wallet else "Nenhum evento Transfer encontrado"
    for lg in logs:
    
        topics = [t.lower() for t in lg.get("topics", [])]
        if not topics or topics[0] != TRANSFER_TOPIC:
            continue
        to_addr = _topic_address(topics[2]) if len(topics) >= 3 else ""
        from_addr = _topic_address(topics[1]) if len(topics) >= 2 else ""
        if wallet and to_addr != wallet:
            continue
        contract_addr = (lg.get("address") or "").lower()
        amount = _hex_to_int(lg.get("data"))
        try:
            decimals_hex = await rpc_call(
                cfg,
                "eth_call",
                [{"to": contract_addr, "data": ERC20_DECIMALS_SELECTOR}, "latest"],
            )
            decimals = _hex_to_int(decimals_hex)
        except Exception:
            decimals = cfg.get("decimals")
            if decimals is None:
                return {"ok": False, "reason": "Falha ao obter decimals do token"}
        min_units = int(round(MIN_TOKEN_AMOUNT * (10 ** decimals)))
        if amount < min_units:
            reason = f"Quantidade de token abaixo do mínimo ({MIN_TOKEN_AMOUNT})"
            continue
        found = {
            "amount_raw": amount,
            "from": from_addr,
            "to": to_addr,
            "contract": contract_addr,
            "decimals": decimals,
        }
        break
    if not found:
        return {"ok": False, "reason": reason}
    current_block_hex = await rpc_call(cfg, "eth_blockNumber", [])
    confirmations = _hex_to_int(current_block_hex) - _hex_to_int(receipt.get("blockNumber", "0x0"))
    if confirmations < MIN_CONFIRMATIONS:
        return {"ok": False, "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})"}
    return {
        "ok": True,
        "type": "erc20",
        "from": found["from"],
        "to": found["to"],
        "amount_raw": found["amount_raw"],
        "contract": found["contract"],
        "decimals": found["decimals"],
        "confirmations": confirmations,
    }
async def verify_erc20_payment_bscscan(cfg: Dict[str, Any], tx_hash: str) -> Dict[str, Any]:
    """Verifica transferência ERC-20 usando a API do BscScan."""
    api_key = cfg.get("bscscan_api_key")
    if not api_key:
        return {"ok": False, "reason": "BSCSCAN_API_KEY não definido"}
    base_url = "https://api.bscscan.com/api"
    wallet = cfg.get("wallet_address")
    contract_cfg = (cfg.get("token_contract") or "").lower()
    async with httpx.AsyncClient(timeout=10) as client:
        params = {
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
            "apikey": api_key,
        }
        r = await client.get(base_url, params=params)
        receipt = r.json().get("result")
        if not receipt or receipt.get("status") != "0x1":
            return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
        logs = receipt.get("logs", [])
        found = None
        reason = "Nenhum Transfer para a carteira" if wallet else "Nenhum evento Transfer encontrado"
        for lg in logs:
            topics = [t.lower() for t in lg.get("topics", [])]
            if not topics or topics[0] != TRANSFER_TOPIC:
                continue
            to_addr = _topic_address(topics[2]) if len(topics) >= 3 else ""
            from_addr = _topic_address(topics[1]) if len(topics) >= 2 else ""
            if wallet and to_addr != wallet:
                continue
            contract_addr = (lg.get("address") or "").lower()
            if contract_cfg and contract_addr != contract_cfg:
                continue
            amount = _hex_to_int(lg.get("data"))
            decimals = cfg.get("decimals")
            if decimals is None:
                p_dec = {
                    "module": "proxy",
                    "action": "eth_call",
                    "to": contract_addr,
                    "data": ERC20_DECIMALS_SELECTOR,
                    "tag": "latest",
                    "apikey": api_key,
                }
                r_dec = await client.get(base_url, params=p_dec)
                decimals = _hex_to_int(r_dec.json().get("result"))
            min_units = int(round(MIN_TOKEN_AMOUNT * (10 ** decimals)))
            if amount < min_units:
                reason = f"Quantidade de token abaixo do mínimo ({MIN_TOKEN_AMOUNT})"
                continue
            found = {
                "amount_raw": amount,
                "from": from_addr,
                "to": to_addr,
                "contract": contract_addr,
                "decimals": decimals,
            }
            break
        if not found:
            return {"ok": False, "reason": reason}
        r_block = await client.get(
            base_url,
            params={"module": "proxy", "action": "eth_blockNumber", "apikey": api_key},
        )
        current_block_hex = r_block.json().get("result")
        confirmations = _hex_to_int(current_block_hex) - _hex_to_int(
            receipt.get("blockNumber", "0x0")
        )
        if confirmations < MIN_CONFIRMATIONS:
            return {
                "ok": False,
                "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})",
            }
        return {
            "ok": True,
            "type": "erc20",
            "from": found["from"],
            "to": found["to"],
            "amount_raw": found["amount_raw"],
            "contract": found["contract"],
            "decimals": found["decimals"],
            "confirmations": confirmations,
        }

async def verify_tx_blockscan(cfg: Dict[str, Any], tx_hash: str) -> Dict[str, Any]:
    """Verifica transações usando a API Blockscan (Etherscan v2)."""
    api_key = cfg.get("etherscan_api_key") or cfg.get("bscscan_api_key")
    if not api_key:
        return {"ok": False, "reason": "ETHERSCAN_API_KEY não definido"}
    chain_id = cfg.get("chain_id")
    if not chain_id:
        return {"ok": False, "reason": "chain_id não definido"}
    wallet = (cfg.get("wallet_address") or "").lower()
    base_url = "https://api.etherscan.io/v2/api"
    async with httpx.AsyncClient(timeout=10) as client:
        params = {
            "chainid": chain_id,
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
            "apikey": api_key,
        }
        r = await client.get(base_url, params=params)
        receipt = r.json().get("result")
        if not receipt or receipt.get("status") != "0x1":
            return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
        r_block = await client.get(
            base_url,
            params={
                "chainid": chain_id,
                "module": "proxy",
                "action": "eth_blockNumber",
                "apikey": api_key,
            },
        )
        current_block_hex = r_block.json().get("result")
        confirmations = _hex_to_int(current_block_hex) - _hex_to_int(
            receipt.get("blockNumber", "0x0")
        )
        if confirmations < MIN_CONFIRMATIONS:
            return {
                "ok": False,
                "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})",
            }
        logs = receipt.get("logs", [])
        for lg in logs:
            topics = [t.lower() for t in lg.get("topics", [])]
            if not topics or topics[0] != TRANSFER_TOPIC or len(topics) < 3:
                continue
            to_addr = _topic_address(topics[2])
            from_addr = _topic_address(topics[1]) if len(topics) >= 2 else ""
            if wallet and to_addr != wallet:
                continue
            contract_addr = (lg.get("address") or "").lower()
            amount_raw = _hex_to_int(lg.get("data"))
            price_dec = await fetch_price_usd_for_contract(contract_addr)
            if not price_dec:
                return {"ok": False, "reason": "Falha ao obter cotação do ativo"}
            price, decimals = price_dec
            min_units = int(round(MIN_TOKEN_AMOUNT * (10 ** decimals)))
            if amount_raw < min_units:
                return {
                    "ok": False,
                    "reason": f"Quantidade de token abaixo do mínimo ({MIN_TOKEN_AMOUNT})",
                }
            amount_native = amount_raw / (10 ** decimals)
            amount_usd = amount_native * price
            plan_days = infer_plan_days(amount_usd=amount_usd)
            res = {
                "ok": True,
                "type": "erc20",
                "from": from_addr,
                "to": to_addr,
                "amount_raw": amount_raw,
                "contract": contract_addr,
                "decimals": decimals,
                "confirmations": confirmations,
                "amount_usd": amount_usd,
                "plan_days": plan_days,
                "chain_name": cfg.get("chain_name"),
                "symbol": cfg.get("symbol"),
            }
            if plan_days is None:
                res["reason"] = "Valor não corresponde a nenhum plano"
            return res
        r_tx = await client.get(
            base_url,
            params={
                "chainid": chain_id,
                "module": "proxy",
                "action": "eth_getTransactionByHash",
                "txhash": tx_hash,
                "apikey": api_key,
            },
        )
        tx = r_tx.json().get("result")
        if not tx:
            return {"ok": False, "reason": "Transação não encontrada"}
        to_addr = (tx.get("to") or "").lower()
        if wallet and to_addr != wallet:
            return {"ok": False, "reason": "Destinatário diferente da carteira configurada"}
        value_wei = _hex_to_int(tx.get("value"))
        price = await fetch_price_usd(cfg)
        if price is None:
            return {"ok": False, "reason": "Falha ao obter cotação do ativo"}
        amount_native = value_wei / (10 ** 18)
        amount_usd = amount_native * price
        plan_days = infer_plan_days(amount_usd=amount_usd)
        res = {
            "ok": True,
            "type": "native",
            "from": (tx.get("from") or "").lower(),
            "to": to_addr,
            "amount_wei": value_wei,
            "confirmations": confirmations,
            "amount_usd": amount_usd,
            "plan_days": plan_days,
            "chain_name": cfg.get("chain_name"),
            "symbol": cfg.get("symbol"),
        }
        if plan_days is None:
            res["reason"] = "Valor não corresponde a nenhum plano"
        return res

async def verify_tx_bscscan(cfg: Dict[str, Any], tx_hash: str) -> Dict[str, Any]:
    """Verifica transações (BNB ou BEP-20) usando apenas a API do BscScan."""
    api_key = cfg.get("bscscan_api_key")
    if not api_key:
        return {"ok": False, "reason": "BSCSCAN_API_KEY não definido"}
    wallet = (cfg.get("wallet_address") or "").lower()
    base_url = "https://api.bscscan.com/api"
    async with httpx.AsyncClient(timeout=10) as client:
        params = {
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
            "apikey": api_key,
        }
        r = await client.get(base_url, params=params)
        receipt = r.json().get("result")
        if not receipt or receipt.get("status") != "0x1":
            return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
        r_block = await client.get(
            base_url, params={"module": "proxy", "action": "eth_blockNumber", "apikey": api_key}
        )
        current_block_hex = r_block.json().get("result")
        confirmations = _hex_to_int(current_block_hex) - _hex_to_int(
            receipt.get("blockNumber", "0x0")
        )
        if confirmations < MIN_CONFIRMATIONS:
            return {
                "ok": False,
                "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})",
            }
        logs = receipt.get("logs", [])
        for lg in logs:
            topics = [t.lower() for t in lg.get("topics", [])]
            if not topics or topics[0] != TRANSFER_TOPIC or len(topics) < 3:
                continue
            to_addr = _topic_address(topics[2])
            from_addr = _topic_address(topics[1]) if len(topics) >= 2 else ""
            if wallet and to_addr != wallet:
                continue
            contract_addr = (lg.get("address") or "").lower()
            amount_raw = _hex_to_int(lg.get("data"))
            price_dec = await fetch_price_usd_for_contract(contract_addr)
            if not price_dec:
                return {"ok": False, "reason": "Falha ao obter cotação do ativo"}
            price, decimals = price_dec
            min_units = int(round(MIN_TOKEN_AMOUNT * (10 ** decimals)))
            if amount_raw < min_units:
                return {
                    "ok": False,
                    "reason": f"Quantidade de token abaixo do mínimo ({MIN_TOKEN_AMOUNT})",
                }
            amount_native = amount_raw / (10 ** decimals)
            amount_usd = amount_native * price
            plan_days = infer_plan_days(amount_usd=amount_usd)
            res = {
                "ok": True,
                "type": "erc20",
                "from": from_addr,
                "to": to_addr,
                "amount_raw": amount_raw,
                "contract": contract_addr,
                "decimals": decimals,
                "confirmations": confirmations,
                "amount_usd": amount_usd,
                "plan_days": plan_days,
                "chain_name": cfg.get("chain_name"),
                "symbol": cfg.get("symbol"),
            }
            if plan_days is None:
                res["reason"] = "Valor não corresponde a nenhum plano"
            return res
        # Se nenhum evento Transfer para a carteira, trata como transferência nativa
        r_tx = await client.get(
            base_url,
            params={"module": "proxy", "action": "eth_getTransactionByHash", "txhash": tx_hash, "apikey": api_key},
        )
        tx = r_tx.json().get("result")
        if not tx:
            return {"ok": False, "reason": "Transação não encontrada"}
        to_addr = (tx.get("to") or "").lower()
        if wallet and to_addr != wallet:
            return {"ok": False, "reason": "Destinatário diferente da carteira configurada"}
        value_wei = _hex_to_int(tx.get("value"))
        price = await fetch_price_usd(cfg)
        if price is None:
            return {"ok": False, "reason": "Falha ao obter cotação do ativo"}
        amount_native = value_wei / (10 ** 18)
        amount_usd = amount_native * price
        plan_days = infer_plan_days(amount_usd=amount_usd)
        res = {
            "ok": True,
            "type": "native",
            "from": (tx.get("from") or "").lower(),
            "to": to_addr,
            "amount_wei": value_wei,
            "confirmations": confirmations,
            "amount_usd": amount_usd,
            "plan_days": plan_days,
            "chain_name": cfg.get("chain_name"),
            "symbol": cfg.get("symbol"),
        }
        if plan_days is None:
            res["reason"] = "Valor não corresponde a nenhum plano"
        return res
    

async def verify_tx_etherscan(cfg: Dict[str, Any], tx_hash: str) -> Dict[str, Any]:
    """Verifica transações (ETH ou ERC-20) usando apenas a API do Etherscan."""
    api_key = cfg.get("etherscan_api_key")
    if not api_key:
        return {"ok": False, "reason": "ETHERSCAN_API_KEY não definido"}
    wallet = (cfg.get("wallet_address") or "").lower()
    base_url = "https://api.etherscan.io/api"
    async with httpx.AsyncClient(timeout=10) as client:
        params = {
            "module": "proxy",
            "action": "eth_getTransactionReceipt",
            "txhash": tx_hash,
            "apikey": api_key,
        }
        r = await client.get(base_url, params=params)
        receipt = r.json().get("result")
        if not receipt or receipt.get("status") != "0x1":
            return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
        r_block = await client.get(
            base_url, params={"module": "proxy", "action": "eth_blockNumber", "apikey": api_key}
        )
        current_block_hex = r_block.json().get("result")
        confirmations = _hex_to_int(current_block_hex) - _hex_to_int(
            receipt.get("blockNumber", "0x0")
        )
        if confirmations < MIN_CONFIRMATIONS:
            return {
                "ok": False,
                "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})",
            }
        logs = receipt.get("logs", [])
        for lg in logs:
            topics = [t.lower() for t in lg.get("topics", [])]
            if not topics or topics[0] != TRANSFER_TOPIC or len(topics) < 3:
                continue
            to_addr = _topic_address(topics[2])
            if wallet and to_addr != wallet:
                continue
            contract_addr = (lg.get("address") or "").lower()
            amount_raw = _hex_to_int(lg.get("data"))
            price_dec = await fetch_price_usd_for_contract(contract_addr)
            if not price_dec:
                return {"ok": False, "reason": "Falha ao obter cotação do ativo"}
            price, decimals = price_dec
            min_units = int(round(MIN_TOKEN_AMOUNT * (10 ** decimals)))
            if amount_raw < min_units:
                return {
                    "ok": False,
                    "reason": f"Quantidade de token abaixo do mínimo ({MIN_TOKEN_AMOUNT})",
                }
            amount_native = amount_raw / (10 ** decimals)
            amount_usd = amount_native * price
            plan_days = infer_plan_days(amount_usd=amount_usd)
            res = {
                "ok": True,
                "type": "erc20",
                "to": to_addr,
                "amount_raw": amount_raw,
                "contract": contract_addr,
                "decimals": decimals,
                "confirmations": confirmations,
                "amount_usd": amount_usd,
                "plan_days": plan_days,
                "chain_name": cfg.get("chain_name"),
                "symbol": cfg.get("symbol"),
            }
            if plan_days is None:
                res["reason"] = "Valor não corresponde a nenhum plano"
            return res
        r_tx = await client.get(
            base_url,
            params={"module": "proxy", "action": "eth_getTransactionByHash", "txhash": tx_hash, "apikey": api_key},
        )
        tx = r_tx.json().get("result")
        if not tx:
            return {"ok": False, "reason": "Transação não encontrada"}
        to_addr = (tx.get("to") or "").lower()
        if wallet and to_addr != wallet:
            return {"ok": False, "reason": "Destinatário diferente da carteira configurada"}
        value_wei = _hex_to_int(tx.get("value"))
        price = await fetch_price_usd(cfg)
        if price is None:
            return {"ok": False, "reason": "Falha ao obter cotação do ativo"}
        amount_native = value_wei / (10 ** 18)
        amount_usd = amount_native * price
        plan_days = infer_plan_days(amount_usd=amount_usd)
        res = {
            "ok": True,
            "type": "native",
            "from": (tx.get("from") or "").lower(),
            "to": to_addr,
            "amount_wei": value_wei,
            "confirmations": confirmations,
            "amount_usd": amount_usd,
            "plan_days": plan_days,
            "chain_name": cfg.get("chain_name"),
            "symbol": cfg.get("symbol"),
        }
        if plan_days is None:
            res["reason"] = "Valor não corresponde a nenhum plano"
        return res

async def detect_chain_from_hash(tx_hash: str) -> Optional[str]:
    """Tenta identificar em qual cadeia a transação existe usando APIs públicas."""
    async with httpx.AsyncClient(timeout=10) as client:
        for slug in BLOCKCHAIR_SLUGS.keys():
            try:
                url = f"https://api.blockchair.com/{slug}/dashboards/transaction/{tx_hash}"
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                data = r.json().get("data", {})
                if tx_hash in data and data[tx_hash].get("transaction"):
                    return slug
            except Exception as e:
                logging.warning("Erro detectando cadeia %s: %s", slug, e)
                continue
    return None

async def verify_tx_any_chain(tx_hash: str) -> Dict[str, Any]:
    if not is_valid_tx_hash(tx_hash):
        return {"ok": False, "reason": "Hash inválida (precisa ter 66 chars com 0x + 64 hex)"}

    for net in NETWORKS:
        try:
            tx = await rpc_call_net(net, "eth_getTransactionByHash", [tx_hash])
        except Exception:
            continue  # tenta próxima rede

        if not tx:
            continue

        # Achamos a rede!
        try:
            receipt = await rpc_call_net(net, "eth_getTransactionReceipt", [tx_hash])
        except Exception as e:
            return {"ok": False, "reason": f"Falha ao obter receipt em {net['name']}: {e}"}

        status_ok = (receipt and receipt.get("status") == "0x1")
        if not status_ok:
            return {"ok": False, "chain": net["key"], "reason": "Transação ainda não sucessful/confirmada"}

        # Confirmações
        try:
            blk_hex = await rpc_call_net(net, "eth_blockNumber", [])
        except Exception as e:
            return {"ok": False, "chain": net["key"], "reason": f"Falha ao obter bloco atual: {e}"}
        confs = hex_to_int(blk_hex) - hex_to_int(receipt.get("blockNumber","0x0"))
        min_confs = int(net.get("min_confirmations", 5))
        if confs < min_confs:
            return {"ok": False, "chain": net["key"], "reason": f"Confirmações insuficientes ({confs}/{min_confs})"}

        # Detectar tipo: nativo ou ERC-20
        wallet = WALLET_ADDRESS
        native_symbol = net["native_symbol"]
        native_dec = net["native_decimals"]

        # Checa ERC-20 nos logs primeiro
        logs = receipt.get("logs", []) or []
        for lg in logs:
            if (lg.get("topics") or [None])[0] == TRANSFER_TOPIC:
                to_addr = ("0x" + (lg["topics"][2][26:] if lg["topics"][2].startswith("0x") else lg["topics"][2])[-40:]).lower()
                if to_addr == wallet:
                    contract = (lg.get("address") or "").lower()
                    amount_raw = hex_to_int(lg.get("data"))
                    sym, dec = await erc20_symbol_decimals(net, contract)
                    amount = to_units(amount_raw, dec)

                    # USD
                    usd_unit = await price_usd_token(net["key"], contract)
                    usd_total = (usd_unit * amount) if usd_unit else None

                    return {
                        "ok": True, "chain": net["key"], "chain_name": net["name"],
                        "type": "erc20", "contract": contract,
                        "symbol": sym, "decimals": dec,
                        "amount_raw": amount_raw, "amount": amount,
                        "usd": usd_total, "confirmations": confs
                    }

        # Se não achou token, tenta nativo (to == WALLET_ADDRESS)
        to_addr = (tx.get("to") or "").lower()
        if to_addr == wallet:
            value_wei = hex_to_int(tx.get("value"))
            amount = to_units(value_wei, native_dec)

            usd_unit = await price_usd_native(native_symbol)
            usd_total = (usd_unit * amount) if usd_unit else None

            return {
                "ok": True, "chain": net["key"], "chain_name": net["name"],
                "type": "native",
                "symbol": native_symbol, "decimals": native_dec,
                "amount_wei": value_wei, "amount": amount,
                "usd": usd_total, "confirmations": confs
            }

        # Achou a tx nessa rede mas não é depósito para sua carteira
        return {"ok": False, "chain": net["key"], "reason": "Transação não foi enviada para a sua carteira."}

    # Não encontrou em nenhuma rede
    return {"ok": False, "reason": "Transação não encontrada nas redes configuradas."}


# Alias para compatibilidade externa e testes
verify_tx_any = verify_tx_any_chain


async def process_incoming_payment(tx_info: Dict[str, Any]) -> None:
    cfg = tx_info.get("cfg", {})
    tx_hash = tx_info.get("tx_hash")
    uid = tx_info.get("user_id")
    if not uid or not tx_hash:
        return
    amount_usd: Optional[float] = None
    if tx_info.get("type") == "native":
        price = await fetch_price_usd(cfg)
        if price is None:
            logging.warning("Falha ao obter cotação USD para pagamento")
            return
        amount_native = tx_info.get("amount_wei", 0) / (10 ** 18)
        amount_usd = amount_native * price
    elif tx_info.get("type") == "erc20":
        price_dec = await fetch_price_usd_for_contract(tx_info.get("contract"))
        if not price_dec:
            logging.warning(
                "Falha ao obter cotação do token %s", tx_info.get("contract")
            )
            return
        price, dec = price_dec
        decimals = tx_info.get("decimals") or dec
        amount_token = tx_info.get("amount_raw", 0) / (10 ** decimals)
        amount_usd = amount_token * price
    if amount_usd is None:
        return
    plan = plan_from_amount(amount_usd)
    if not plan:
        logging.warning("Valor %s não corresponde a nenhum plano", amount_usd)
        return
    m = vip_upsert_start_or_extend(uid, tx_info.get("username"), tx_hash, plan)

    def _store():
        with SessionLocal() as s:
            p = Payment(
                user_id=uid,
                username=tx_info.get("username"),
                tx_hash=tx_hash,
                chain=cfg.get("chain_name"),
                amount=str(amount_usd),
                status="approved",
                decided_at=now_utc(),
            )
            s.add(p)
            s.commit()

    await asyncio.to_thread(_store)
    invite_link = await create_and_store_personal_invite(uid)
    msg = (
        f"✅ Pagamento confirmado na rede {cfg.get('chain_name')}\n"
        f"VIP válido até {m.expires_at:%d/%m/%Y}.\nEntre no grupo: {invite_link}"
    )
    try:
        await dm(uid, msg, parse_mode=None)

    except Exception as e:
        logging.warning("Falha ao enviar DM de convite: %s", e)
        for aid in list_admin_ids():
            try:
                await dm(aid, f"Falha ao enviar convite para {uid}: {invite_link}", parse_mode=None)
            except Exception:
                pass


async def monitor_wallet(cfg: Dict[str, Any]) -> None:
    wallet = (cfg.get("wallet_address") or "").lower()
    if not wallet:
        logging.warning("wallet_address não configurado")
        return
    last_block: Optional[int] = None
    while True:
        try:
            latest_hex = await rpc_call(cfg, "eth_blockNumber", [])
            latest = _hex_to_int(latest_hex)
            if last_block is None:
                last_block = latest
            for bn in range(last_block + 1, latest + 1):
                block = await rpc_call(cfg, "eth_getBlockByNumber", [hex(bn), True])
                if not block:
                    continue
                for tx in block.get("transactions", []):
                    if (tx.get("to") or "").lower() != wallet:
                        continue
                    tx_hash = tx.get("hash")
                    if not tx_hash:
                        continue
                    try:
                        info = await verify_native_payment(cfg, tx_hash)
                        if not info.get("ok"):
                            info = await verify_erc20_payment(cfg, tx_hash)
                    except Exception as e:
                        logging.warning("Erro verificando transação %s: %s", tx_hash, e)
                        continue
                    if info.get("ok"):
                        uid = user_id_by_address(info.get("from"))
                        if uid:
                            info.update({"user_id": uid, "cfg": cfg, "tx_hash": tx_hash})
                            await process_incoming_payment(info)
            last_block = latest
        except Exception as e:
            logging.warning("Erro no monitor_wallet: %s", e)
        await asyncio.sleep(5)


async def monitor_wallet_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    cfg = (context.job.data or {}).get("cfg") or {}
    await monitor_wallet(cfg)


async def verify_tx_blockchair(tx_hash: str, slug: Optional[str] = None) -> Dict[str, Any]:
    """Tentativa de verificação genérica usando a API pública do Blockchair."""
    slugs = {slug: BLOCKCHAIR_SLUGS.get(slug, 18)} if slug else BLOCKCHAIR_SLUGS
    wallets = {
        cfg.get("wallet_address", "").lower()
        for cfg in CHAIN_CONFIGS
        if cfg.get("wallet_address")
    }
    tx_clean = tx_hash[2:] if tx_hash.startswith("0x") else tx_hash
    async with httpx.AsyncClient(timeout=10) as client:
        for sl, decimals in slugs.items():
            try:
                url = f"https://api.blockchair.com/{sl}/dashboards/transaction/{tx_clean}"
                r = await client.get(url)
                if r.status_code != 200:
                    continue
                resp = r.json()
                if not isinstance(resp, dict):
                    logging.warning("Resposta inválida da Blockchair para %s: %r", sl, resp)
                    continue
                data_obj = resp.get("data")
                if not isinstance(data_obj, dict):
                    logging.warning("Resposta inválida da Blockchair para %s: %r", sl, data_obj)
                    continue
                data = data_obj.get(tx_clean, {})
                tx = data.get("transaction")
                if not tx:
                    continue
                to_addr = (tx.get("recipient") or "").lower()
                if to_addr.startswith("0x") and wallets and to_addr not in wallets:
                    continue
                value = int(tx.get("value", 0))
                price_usd = resp.get("context", {}).get("state", {}).get("market_price_usd") or 0
                amount_native = value / (10 ** decimals)
                amount_usd = amount_native * float(price_usd)
                plan_days = infer_plan_days(amount_usd=amount_usd)
                chain_name = sl.replace("-", " ").title()
                symbol = next(
                    (
                        c.get("symbol")
                        for c in CHAIN_CONFIGS
                        if c.get("chain_name") == chain_name
                    ),
                    "",
                )
                res = {
                    "ok": bool(plan_days),
                    "type": "native",
                    "amount_wei": value,
                    "amount_usd": amount_usd,
                    "plan_days": plan_days,
                    "chain_name": chain_name,
                    "symbol": symbol,
                }
                if not plan_days:
                    res["reason"] = "Valor não corresponde a nenhum plano"
                return res
            except Exception as e:
                logging.warning("Erro verificando Blockchair %s: %s", sl, e)
                continue
    return {"ok": False, "reason": "Transação não encontrada em nenhuma cadeia."}
    



# =========================
# Pagamento – comandos
# =========================

async def simular_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins podem simular TX.")

    user = update.effective_user
    tx_hash = "0x" + "deadbeef"*8  # hash fictício, 66 chars

    # grava como aprovado direto
    with SessionLocal() as s:
        try:
            p = Payment(
                user_id=user.id,
                username=user.username,
                tx_hash=tx_hash,
                chain="TESTNET",
                status="approved",
                amount="1000000000000000000",  # 1 ETH fictício
                decided_at=now_utc(),
            )
            s.add(p)
            s.commit()
        except Exception:
            s.rollback()
            return await update.effective_message.reply_text("❌ Erro ao simular pagamento.")

     # cria/renova VIP no plano trimestral
    m = vip_upsert_start_or_extend(user.id, user.username, tx_hash, VipPlan.TRIMESTRAL)

    try:
        invite_link = await create_and_store_personal_invite(user.id)
        sent = await dm(
            user.id,
            f"""✅ Pagamento confirmado na rede {p.chain}!
VIP válido até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).
Entre no VIP: {invite_link}""",
            parse_mode=None,
        )
        if sent:
            await update.effective_message.reply_text("✅ Pagamento simulado com sucesso. Veja seu privado.")
        else:
            await update.effective_message.reply_text(
                "Simulado OK, mas não consegui te enviar o convite no privado."
            )
    except Exception as e:
        await update.effective_message.reply_text(f"Simulado OK, mas falhou enviar convite: {e}")
        invite = await assign_and_send_invite(user.id, user.username, tx_hash)
        await dm(
    user.id,f"""✅ Pagamento confirmado na rede {p.chain}!
VIP válido até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).
Convite (válido 2h, uso único): {invite}""",
            parse_mode=None,
        )
async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not CHAIN_CONFIGS:
        return await update.effective_message.reply_text(
            "Método de pagamento não configurado. (CHAIN_CONFIGS ausente)"
        )

    user = update.effective_user
    chat = update.effective_chat
    msg  = update.effective_message
    wallets = []
    for cfg in CHAIN_CONFIGS:
        label = cfg.get("chain_name", "")
        symbol = cfg.get("symbol")
        if symbol:
            label = f"{label} ({symbol})"
        wallets.append(f"{label}: <code>{esc(cfg['wallet_address'])}</code>")

    texto = (
        f"💸 <b>Pagamento via Cripto</b>\n"
        f"1) Abra seu banco de cripto.\n"
        f"2) Envie o valor para <b>uma</b> das carteiras abaixo:\n" + "\n".join(wallets) + "\n"
        f"3) Depois me mande aqui: <code>/tx &lt;hash_da_transacao&gt;</code>\n\n"
        f"⚙️ Válido on-chain (mín. {MIN_CONFIRMATIONS} confirmações).\n"
        f"✅ Aprovando, te envio o convite do VIP no privado."
    )

    prices = get_plan_prices_usd()
    plan_lines = [
        f"- {plan.name.title()}: {PLAN_DAYS[plan]} dias por ${prices[plan]:.2f} USD"
        for plan in VipPlan
    ]
    texto += "\n\nPlanos:\n" + "\n".join(plan_lines)

    sent = await dm(user.id, texto)

    if chat.type != "private":
        # apaga a mensagem do usuário
        try:
            await msg.delete()
        except Exception:
            pass

        bot_msg = None
        if sent:
            try:
                bot_msg = await chat.send_message("Te enviei o passo a passo no privado. 👌")
            except Exception:
                pass
        else:
            # usuário não iniciou o bot
            try:
                username = BOT_USERNAME or (await application.bot.get_me()).username
            except Exception:
                username = None
            link = f"https://t.me/{username}?start=pagamento" if username else ""
            bot_msg = await chat.send_message(
                ("⚠️ Não consegui te chamar no privado.\n"
                 "Toque em <b>Start</b> no meu perfil e tente /pagar de novo.\n"
                 f"{esc(link) if link else ''}"),
                parse_mode="HTML"
            )

        # apaga o aviso depois de 5s (ajuste aqui se quiser outro tempo)
        if bot_msg:
            async def _delete_later(mid: int):
                await asyncio.sleep(20)  # <<< mude 5 para o número de segundos que quiser
                try:
                    await application.bot.delete_message(chat_id=chat.id, message_id=mid)
                except Exception:
                    pass
            asyncio.create_task(_delete_later(bot_msg.message_id))
    else:
        await msg.reply_text("Qualquer dúvida, me mande a hash com /tx <hash> 😉")


async def listar_pendentes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    with SessionLocal() as s:
        pend = s.query(Payment).filter(Payment.status == "pending").order_by(Payment.created_at.asc()).all()
        if not pend:
            return await update.effective_message.reply_text("Sem pagamentos pendentes.")
        lines = ["⏳ <b>Pendentes</b>"] + [
            f"- user_id:{p.user_id} @{p.username or '-'} | {p.tx_hash} | {p.chain} | {p.created_at.strftime('%d/%m %H:%M')}" for p in pend
        ]
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
    
# --- /tx -------------------------------------------------------------
from decimal import Decimal
import logging

def _fmt_usd(x) -> str:
    try:
        return f"${Decimal(x):,.2f}"
    except Exception:
        return f"${x}"

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message

    # validação de uso
    if not context.args:
        return await msg.reply_text("Uso: /tx <hash>\nEx.: /tx 0xabc123...")

    txhash = context.args[0].strip()
    if not txhash.startswith("0x"):
        txhash = "0x" + txhash
    if len(txhash) != 66:  # 0x + 64 hex
        return await msg.reply_text("Hash inválida. Deve ter 64 hex (começando com 0x).")

    # feedback
    await msg.reply_text("🔎 Verificando a transação em múltiplas redes...")

    try:
        # ✅ IMPORTANTE: find_tx_any_chain é async → precisa de await
        info = await find_tx_any_chain(txhash)

        if not info:
            return await msg.reply_text("❌ Não encontrei essa hash em nenhuma rede configurada.")

        # Esperado de `info`:
        # {
        #   "chain": "Polygon" | "BSC" | "Ethereum" | ...,
        #   "tx": "0x...",
        #   "from": "0x...",
        #   "to": "0x...",
        #   "symbol": "MATIC" | "BNB" | "ETH" | ...,
        #   "value_native": Decimal | float | str,
        #   "usd_total": Decimal | float | str,
        #   "confirmations": int | None,
        #   "status": "success" | "pending" | "failed",
        #   "explorer": "https://.../tx/0x..."
        # }

        chain   = info.get("chain", "?")
        symbol  = info.get("symbol", "?")
        v_nat   = info.get("value_native", 0)
        usd     = info.get("usd_total") or info.get("value_usd") or 0
        confs   = info.get("confirmations")
        status  = info.get("status", "desconhecido")
        expl    = info.get("explorer")

        # mensagem detalhada
        txt = (
            "✅ Transação encontrada!\n\n"
            f"• Rede: {chain}\n"
            f"• Hash: <code>{txhash}</code>\n"
            f"• De: <code>{info.get('from','')}</code>\n"
            f"• Para: <code>{info.get('to','')}</code>\n"
            f"• Valor: {v_nat} {symbol} (~ {_fmt_usd(usd)})\n"
            f"• Confirmações: {confs if confs is not None else 'n/d'}\n"
            f"• Status: {status}\n"
        )
        if expl:
            txt += f"\n🔗 Explorer: {expl}"

        await msg.reply_html(txt)

        # (opcional) classificar VIP automático pelo USD
        try:
            tier = pick_vip_tier(Decimal(str(usd)))  # sua função que decide o nível
        except Exception:
            tier = pick_vip_tier(usd)  # fallback se já for Decimal

        if tier:
            await msg.reply_text(f"🎁 Valor {_fmt_usd(usd)} → nível {tier}")
            # Se quiser ativar automaticamente:
            # await grant_vip(update.effective_user.id, tier, txhash, chain)

    except Exception as e:
        logging.exception("Falha no /tx")
        await msg.reply_text(f"❌ Erro ao verificar on-chain: {e}")
# --------------------------------------------------------------------


    # …daqui você já decide qual VIP aplicar com base em usd_total
    days = {
        "basic": int(os.getenv("VIP_DAYS_BASIC", "30")),
        "pro":   int(os.getenv("VIP_DAYS_PRO", "60")),
        "ultra": int(os.getenv("VIP_DAYS_ULTRA", "120")),
    }.get(tier, 30)

    # >>>>>>>>>> chame sua função real de conceder/renovar VIP >>>>>>>>>>
    # ok = grant_or_extend_vip(user_id=update.effective_user.id, days=days, tier=tier, usd=usd, chain=chain, tx=info["hash"])
    ok = True  # placeholder — use sua função existente

    if ok:
        return await msg.reply_text(
            f"✅ Confirmei sua transação na **{chain}** (≈ ${usd:.2f}, {confs} confs).\n"
            f"Você recebeu o VIP **{tier.upper()}** por **{days} dias**. Bem-vindo! 🎉",
            parse_mode="Markdown"
        )
    else:
        return await msg.reply_text("❌ Não consegui registrar seu VIP. Tente novamente ou fale com um admin.")


    # Já existe?
    def _fetch_existing():
        with SessionLocal() as s:
            return s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
    existing = await asyncio.to_thread(_fetch_existing)
    if existing and existing.user_id != user.id:
        return await msg.reply_text("Esse hash já foi usado por outro usuário.")


    if existing and existing.status == "approved":
        if existing.user_id == user.id:
            m = vip_get(user.id)
            try:
                invite_link = (m.invite_link if m else None) or await create_and_store_personal_invite(user.id)
                await dm(
                    user.id,
                    f"✅ Seu pagamento já estava aprovado!\n"
                    f"VIP até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).\n"
                    f"Entre no VIP: {invite_link}",
                    parse_mode=None,
                )
                return await msg.reply_text("Esse hash já estava aprovado. Reenviei o convite no seu privado. ✅")
            except Exception as e:
                return await msg.reply_text(f"Hash aprovado, mas falhou ao reenviar o convite: {e}")
        else:
            return await msg.reply_text("Esse hash já foi usado por outro usuário.")
    elif existing and existing.status == "pending":
        return await msg.reply_text("Esse hash já foi registrado e está pendente. Aguarde a validação.")
    elif existing and existing.status == "rejected":
        return await msg.reply_text("Esse hash já foi rejeitado. Fale com um administrador.")

    # Verificação on-chain
    try:
        res = await verify_tx_any(tx_hash)
    except Exception as e:
        logging.exception("Erro verificando transação")
        return await msg.reply_text(f"❌ Erro ao verificar on-chain: {e}")
    if res is None:
        return await msg.reply_text("❌ Transação não encontrada em nenhuma cadeia.")

    # Checagem de plano
    paid_ok = res.get("ok", False)
    plan_days = None
    if paid_ok:
        plan_days = res.get("plan_days") or infer_plan_days(amount_usd=res.get("amount_usd"))
        if not plan_days:
            logging.warning(
                "Valor da transação não corresponde a nenhum plano: %s",
                res.get("amount_usd"),
            )
            paid_ok = False
            res["reason"] = res.get("reason") or "Valor não corresponde a nenhum plano"

    status = "approved" if (AUTO_APPROVE_CRYPTO and paid_ok) else "pending"

    sender_addr = res.get("from")
    if sender_addr:
        await asyncio.to_thread(user_address_upsert, user.id, sender_addr)

    def _store_payment():
        with SessionLocal() as s:
            try:
                p = Payment(
                    user_id=user.id,
                    username=user.username,
                    tx_hash=tx_hash,
                    chain=res.get("chain_name", CHAIN_NAME),
                    status=status,
                    amount=str(res.get("amount_usd") or ""),
                    decided_at=now_utc() if status == "approved" else None,
                    notes=res.get("reason") if status == "pending" else None,
                )
                s.add(p)
                s.commit()
            except Exception:
                s.rollback()
                raise
    await asyncio.to_thread(_store_payment)
    if status == "pending" and (res.get("reason") or "").startswith("Transação não encontrada"):
        schedule_pending_tx_recheck()
        

    if status == "approved":
        try:
            plan = plan_from_amount(float(res.get("amount_usd") or 0)) or VipPlan.TRIMESTRAL
            m = vip_upsert_start_or_extend(user.id, user.username, tx_hash, plan)
            invite_link = await create_and_store_personal_invite(user.id)
            await dm(
                user.id,
                f"✅ Pagamento confirmado na rede {res.get('chain_name', CHAIN_NAME)}"
                f" ({res.get('symbol', CHAIN_SYMBOL)})!\n",
                f"VIP válido até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).\n",
                f"Entre no VIP: {invite_link}",
                parse_mode=None
            )
            return await msg.reply_text("✅ Verifiquei sua transação e já liberei seu acesso. Confira seu privado.")
        except Exception as e:
            logging.exception("Erro enviando invite auto-approve")
            return await msg.reply_text(f"Pagamento OK, mas falhou ao enviar o convite: {e}")
    else:
        human = res.get("reason", "Aguardando confirmações.")
        await msg.reply_text(f"⏳ Hash recebido. Status: {human}")
        try:
            for aid in list_admin_ids():
                txt = (
                    "📥 Pagamento pendente:\n"
                    f"user_id:{user.id} @{user.username or '-'}\n"
                    f"hash:{tx_hash}\nrede:{res.get('chain_name')} ({res.get('symbol', CHAIN_SYMBOL)})\ninfo:{human}"
                )
                await dm(aid, txt, parse_mode=None)
        except Exception:
            pass

async def cancel_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not context.args:
        return await msg.reply_text("Uso: /cancel_tx <hash_da_transacao>")

    tx_raw = context.args[0]
    tx_hash = normalize_tx_hash(tx_raw)
    if not tx_hash:
        return await msg.reply_text("Hash inválida.")

    with SessionLocal() as s:
        p = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if not p:
            return await msg.reply_text("Nenhum registro encontrado para essa hash.")
        if p.status != "pending":
            return await msg.reply_text("Apenas transações pendentes podem ser canceladas.")
        if p.user_id != user.id and not is_admin(user.id):
            return await msg.reply_text("Você não pode cancelar essa transação.")
        try:
            s.delete(p)
            s.commit()
            return await msg.reply_text("Registro removido. Envie a hash novamente com /tx.")
        except Exception as e:
            s.rollback()
            return await msg.reply_text(f"Erro ao remover: {e}")
        
async def clear_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not (user and is_admin(user.id)):
        await msg.reply_text("Apenas admins.")
        raise ApplicationHandlerStop
    if not context.args:
        return await msg.reply_text("Uso: /clear_tx <hash_da_transacao>")

    tx_raw = context.args[0]
    tx_hash = normalize_tx_hash(tx_raw)
    if not tx_hash:
        return await msg.reply_text("Hash inválida.")
    
    def _clear():
        with SessionLocal() as s:
            pay_q = s.query(Payment).filter(func.lower(Payment.tx_hash) == tx_hash)
            vm_q = s.query(VipMembership).filter(func.lower(VipMembership.tx_hash) == tx_hash)
            pays = pay_q.all()
            vms = vm_q.all()
            if not pays and not vms:
                return False
            try:
                if pays:
                    pay_q.delete(synchronize_session=False)
                    if vms:
                        vm_q.update({VipMembership.tx_hash: None}, synchronize_session=False)
                    s.commit()
                return True
            except Exception as e:
                s.rollback()
                raise e
    tx_hash = tx_hash.lower()
    try:
        removed = await asyncio.to_thread(_clear)
    except Exception as e:
           return await msg.reply_text(f"Erro ao remover: {e}")
    if not removed:
        return await msg.reply_text("Nenhum registro encontrado para essa hash.")
    return await msg.reply_text("Registro removido.")


async def aprovar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        raise ApplicationHandlerStop
    if not context.args:
        return await update.effective_message.reply_text("Uso: /aprovar_tx <user_id>")
    try:
        uid = int(context.args[0])
    except:
        return await update.effective_message.reply_text("user_id inválido.")

    with SessionLocal() as s:
        try:
            p = (
                s.query(Payment)
                 .filter(Payment.user_id == uid, Payment.status == "pending")
                 .order_by(Payment.created_at.asc())
                 .first()
            )
            if not p:
                return await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")

            p.status = "approved"
            p.decided_at = now_utc()
            s.commit()
            plan = plan_from_amount(float(p.amount or 0)) or VipPlan.TRIMESTRAL
            vip_upsert_start_or_extend(uid, None, p.tx_hash, plan)
            txh = p.tx_hash
        except Exception as e:
            s.rollback()
            return await update.effective_message.reply_text(f"❌ Erro ao aprovar: {e}")

       # cria/renova VIP conforme plano e vincula a hash aprovada
    try:
        # tenta pegar username atual via bot (se conseguir)
        try:
            u = await application.bot.get_chat(uid)
            username = u.username
        except Exception:
            username = None

        m = vip_upsert_start_or_extend(uid, username, txh, plan)

        invite_link = await create_and_store_personal_invite(uid)
        await application.bot.send_message(
    chat_id=uid,
            text=(
                f"✅ Pagamento aprovado na rede {p.chain}!\n"
                f"Seu VIP vai até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).\n"
                f"Entre no VIP: {invite_link}"
            ),
        )


        await update.effective_message.reply_text(f"Aprovado e convite enviado para {uid}.")
    except Exception as e:
        logging.exception("Erro enviando invite")
        await update.effective_message.reply_text(f"Aprovado, mas falhou ao enviar convite: {e}")


async def rejeitar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /rejeitar_tx <user_id> [motivo]")
    try: uid = int(context.args[0])
    except: return await update.effective_message.reply_text("user_id inválido.")
    motivo = " ".join(context.args[1:]).strip() if len(context.args) > 1 else "Não especificado"
    with SessionLocal() as s:
        try:
            p = s.query(Payment).filter(Payment.user_id == uid, Payment.status == "pending").order_by(Payment.created_at.asc()).first()
            if not p:
                return await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
            p.status = "rejected"
            p.notes = motivo
            p.decided_at = now_utc()
            s.commit()
            try:
                await application.bot.send_message(chat_id=uid, text=f"❌ Pagamento rejeitado. Motivo: {motivo}")
            except Exception:
                pass
            await update.effective_message.reply_text("Pagamento rejeitado.")
        except Exception as e:
            s.rollback()
            await update.effective_message.reply_text(f"❌ Erro ao rejeitar: {e}")
# ----- Auto TX handler -----

async def _hashes_from_photo(photo) -> List[str]:
    try:
        file = await photo.get_file()
        buf = BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore
            text = pytesseract.image_to_string(Image.open(buf))
        except Exception:
            text = ""
        return extract_tx_hashes(text)
    except Exception:
        return []


async def _hashes_from_pdf(document) -> List[str]:
    try:
        file = await document.get_file()
        buf = BytesIO()
        await file.download_to_memory(buf)
        buf.seek(0)
        try:
            from pdfminer.high_level import extract_text  # type: ignore
            text = extract_text(buf)
        except Exception:
            text = ""
        return extract_tx_hashes(text)
    except Exception:
        return []


async def auto_tx_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (msg.text or "") + (" " + msg.caption if msg.caption else "")
    hashes = extract_tx_hashes(text)
    if not hashes:
        if getattr(msg, "photo", None):
            hashes = await _hashes_from_photo(msg.photo[-1])
        elif getattr(msg, "document", None) and msg.document.mime_type == "application/pdf":
            hashes = await _hashes_from_pdf(msg.document)
    if hashes:
        await tx_cmd(update, SimpleNamespace(args=[hashes[0]]))

# ----- Verificação periódica de TX não encontradas
JOB_PENDING_TX = "pending_tx_recheck"

async def pending_tx_recheck_job(context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as s:
        pendings = (
            s.query(Payment)
             .filter(Payment.status == "pending", Payment.notes.ilike("%Transação não encontrada%"))
             .all()
        )
    if not pendings:
        return
    for p in pendings:
        try:
            res = await verify_tx_any(p.tx_hash)
        except Exception:
            logging.exception("Erro rechecando tx %s", p.tx_hash)
            continue
        if res.get("ok"):
            plan = plan_from_amount(float(p.amount or 0)) or VipPlan.TRIMESTRAL
            with SessionLocal() as s2:
                try:
                    pay = s2.query(Payment).filter(Payment.id == p.id).first()
                    if pay:
                        pay.status = "approved"
                        pay.decided_at = now_utc()
                        pay.notes = None
                        s2.commit()
                except Exception:
                    s2.rollback()
                    logging.exception("Erro atualizando pagamento %s", p.id)
                    continue
            try:
                m = vip_upsert_start_or_extend(p.user_id, p.username, p.tx_hash, plan)
                invite_link = await create_and_store_personal_invite(p.user_id)
                await dm(
                    p.user_id,
                    f"✅ Pagamento confirmado na rede {res.get('chain_name', CHAIN_NAME)} ({res.get('symbol', CHAIN_SYMBOL)})!\n",
                    f"VIP válido até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).\n",
                    f"Entre no VIP: {invite_link}",
                    parse_mode=None,
                )
            except Exception:
                logging.exception("Erro enviando invite recheck")
        else:
            with SessionLocal() as s2:
                try:
                    pay = s2.query(Payment).filter(Payment.id == p.id).first()
                    if pay:
                        pay.notes = res.get("reason")
                        s2.commit()
                except Exception:
                    s2.rollback()
                    logging.exception("Erro atualizando motivo pendente")
    with SessionLocal() as s:
        remaining = (
            s.query(Payment)
             .filter(Payment.status == "pending", Payment.notes.ilike("%Transação não encontrada%"))
             .count()
        )
    if remaining:
        context.job_queue.run_once(pending_tx_recheck_job, dt.timedelta(minutes=5), name=JOB_PENDING_TX)

def schedule_pending_tx_recheck():
    jq = application.job_queue
    if jq.get_jobs_by_name(JOB_PENDING_TX):
        return
    with SessionLocal() as s:
        exists = (
            s.query(Payment)
             .filter(Payment.status == "pending", Payment.notes.ilike("%Transação não encontrada%"))
             .count()
        )
    if exists:
        jq.run_once(pending_tx_recheck_job, dt.timedelta(minutes=5), name=JOB_PENDING_TX)
# =========================
# Mensagens agendadas / Jobs
# =========================
JOB_PREFIX_SM = "schmsg_"
def _tz(tz_name: str):
    try: return pytz.timezone(tz_name)
    except Exception: return pytz.timezone("America/Sao_Paulo")

async def _scheduled_message_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job; sid = int(job.name.replace(JOB_PREFIX_SM, "")) if job and job.name else None
    if sid is None: return
    m = scheduled_get(sid); 
    if not m or not m.enabled: return
    try:
        target_chat = GROUP_VIP_ID if m.tier == "vip" else GROUP_FREE_ID
        await context.application.bot.send_message(chat_id=target_chat, text=m.text)
    except Exception as e: logging.warning(f"Falha ao enviar scheduled_message id={sid}: {e}")

def _register_all_scheduled_messages(job_queue: JobQueue):
    for j in list(job_queue.jobs()):
        if j.name and (j.name.startswith(JOB_PREFIX_SM) or j.name in {"daily_pack_vip", "daily_pack_free", "keepalive"}):
            j.schedule_removal()
    msgs = scheduled_all()
    for m in msgs:
        try: h, k = parse_hhmm(m.hhmm)
        except Exception: continue
        tz = _tz(m.tz)
        job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")

async def _reschedule_daily_packs():
    for j in list(application.job_queue.jobs()):
        if j.name in {"daily_pack_vip", "daily_pack_free"}: j.schedule_removal()
    tz = pytz.timezone("America/Sao_Paulo")
    hhmm_vip  = cfg_get("daily_pack_vip_hhmm")  or "09:00"
    hhmm_free = cfg_get("daily_pack_free_hhmm") or "09:30"
    hv, mv = parse_hhmm(hhmm_vip); hf, mf = parse_hhmm(hhmm_free)
    application.job_queue.run_daily(enviar_pack_vip_job,  time=dt.time(hour=hv, minute=mv, tzinfo=tz), name="daily_pack_vip")
    application.job_queue.run_daily(enviar_pack_free_job, time=dt.time(hour=hf, minute=mf, tzinfo=tz), name="daily_pack_free")
    logging.info(f"Job VIP agendado para {hhmm_vip}; FREE para {hhmm_free} (America/Sao_Paulo)")

async def _add_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args or len(context.args) < 2: return await update.effective_message.reply_text(f"Uso: /add_msg_{tier} HH:MM <texto>")
    hhmm = context.args[0]
    try: parse_hhmm(hhmm)
    except Exception as e: return await update.effective_message.reply_text(f"Hora inválida: {e}")
    texto = " ".join(context.args[1:]).strip()
    if not texto: return await update.effective_message.reply_text("Texto vazio.")
    m = scheduled_create(hhmm, texto, tier=tier)
    tz = _tz(m.tz); h, k = parse_hhmm(m.hhmm)
    application.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")
    await update.effective_message.reply_text(f"✅ Mensagem #{m.id} ({tier.upper()}) criada para {m.hhmm} (diária).")

async def add_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):  await _add_msg_tier(update, context, "vip")
async def add_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _add_msg_tier(update, context, "free")

async def _list_msgs_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    msgs = scheduled_all(tier=tier)
    if not msgs: return await update.effective_message.reply_text(f"Não há mensagens agendadas ({tier.upper()}).")
    lines = [f"🕒 <b>Mensagens agendadas — {tier.upper()}</b>"]
    for m in msgs:
        status = "ON" if m.enabled else "OFF"
        preview = (m.text[:80] + "…") if len(m.text) > 80 else m.text
        lines.append(f"#{m.id} — {m.hhmm} ({m.tz}) [{status}] — {esc(preview)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def list_msgs_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):  await _list_msgs_tier(update, context, "vip")
async def list_msgs_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _list_msgs_tier(update, context, "free")

async def _edit_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text(f"Uso: /edit_msg_{tier} <id> [HH:MM] [novo texto]")
    try: sid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    hhmm = None; new_text = None
    if len(context.args) >= 2:
        candidate = context.args[1]
        if ":" in candidate and len(candidate) <= 5:
            try: parse_hhmm(candidate); hhmm = candidate; new_text = " ".join(context.args[2:]).strip() if len(context.args) > 2 else None
            except Exception as e: return await update.effective_message.reply_text(f"Hora inválida: {e}")
        else: new_text = " ".join(context.args[1:]).strip()
    if hhmm is None and new_text is None: return await update.effective_message.reply_text("Nada para alterar. Informe HH:MM e/ou novo texto.")
    m_current = scheduled_get(sid)
    if not m_current or m_current.tier != tier: return await update.effective_message.reply_text(f"Mensagem não encontrada no tier {tier.upper()}.")
    ok = scheduled_update(sid, hhmm, new_text)
    if not ok: return await update.effective_message.reply_text("Mensagem não encontrada.")
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}": j.schedule_removal()
    m = scheduled_get(sid)
    if m:
        tz = _tz(m.tz); h, k = parse_hhmm(m.hhmm)
        context.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")
    await update.effective_message.reply_text("✅ Mensagem atualizada.")

async def edit_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):  await _edit_msg_tier(update, context, "vip")
async def edit_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _edit_msg_tier(update, context, "free")

async def _toggle_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text(f"Uso: /toggle_msg_{tier} <id>")
    try: sid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    m_current = scheduled_get(sid)
    if not m_current or m_current.tier != tier: return await update.effective_message.reply_text(f"Mensagem não encontrado no tier {tier.upper()}.")
    new_state = scheduled_toggle(sid)
    if new_state is None: return await update.effective_message.reply_text("Mensagem não encontrada.")
    await update.effective_message.reply_text(f"✅ Mensagem #{sid} ({tier.upper()}) agora está {'ON' if new_state else 'OFF'}.")

async def toggle_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):  await _toggle_msg_tier(update, context, "vip")
async def toggle_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _toggle_msg_tier(update, context, "free")

async def _del_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text(f"Uso: /del_msg_{tier} <id>")
    try: sid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    m_current = scheduled_get(sid)
    if not m_current or m_current.tier != tier: return await update.effective_message.reply_text(f"Mensagem não encontrada no tier {tier.upper()}.")
    ok = scheduled_delete(sid)
    if not ok: return await update.effective_message.reply_text("Mensagem não encontrada.")
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}": j.schedule_removal()
    await update.effective_message.reply_text("✅ Mensagem removida.")

async def del_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):  await _del_msg_tier(update, context, "vip")
async def del_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE): await _del_msg_tier(update, context, "free")

async def set_pack_horario_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /set_pack_horario_vip HH:MM")
    try:
        hhmm = context.args[0]; parse_hhmm(hhmm); cfg_set("daily_pack_vip_hhmm", hhmm); await _reschedule_daily_packs()
        await update.effective_message.reply_text(f"✅ Horário diário dos packs VIP definido para {hhmm}.")
    except Exception as e: await update.effective_message.reply_text(f"Hora inválida: {e}")

async def set_pack_horario_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /set_pack_horario_free HH:MM")
    try:
        hhmm = context.args[0]; parse_hhmm(hhmm); cfg_set("daily_pack_free_hhmm", hhmm); await _reschedule_daily_packs()
        await update.effective_message.reply_text(f"✅ Horário diário dos packs FREE definido para {hhmm}.")
    except Exception as e: await update.effective_message.reply_text(f"Hora inválida: {e}")

# =========================
# Error handler global
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Erro não tratado", exc_info=context.error)

# =========================
# Webhooks + Keepalive
# =========================
@app.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    data   = await request.json()
    uid    = data.get("telegram_user_id")
    tx_hash= (data.get("tx_hash") or "").strip().lower()
    amount = data.get("amount")
    chain  = data.get("chain") or CHAIN_NAME
    symbol = data.get("symbol") or CHAIN_SYMBOL

    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)

    try:
        res = await verify_tx_any(tx_hash)
        chain = res.get("chain_name", chain)
        symbol = res.get("symbol", symbol)
    except Exception as e:
        logging.exception("Erro verificando no webhook")
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

    approved = bool(res.get("ok"))
    plan_days = None
    if approved:
        plan_days = res.get("plan_days") or infer_plan_days(amount_usd=res.get("amount_usd"))
        if not plan_days:
            logging.warning(
                "Webhook: valor da transação não corresponde a nenhum plano: %s",
                res.get("amount_usd"),
            )
            approved = False
            res["reason"] = res.get("reason") or "Valor não corresponde a nenhum plano"
            amt_val = float(res.get('amount_usd') or amount or 0)
    plan = plan_from_amount(amt_val) or VipPlan.TRIMESTRAL
    
    with SessionLocal() as s:
        try:
            pay = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
            if not pay:
                pay = Payment(
                    user_id=int(uid),
                    tx_hash=tx_hash,
                    amount=str(res.get('amount_usd') or amount or ""),
                    chain=chain,
                    status="approved" if approved else "pending",
                    decided_at=now_utc() if approved else None,
                    notes=res.get("reason") if not approved else None,
                )
                s.add(pay)
            else:
                pay.status = "approved" if approved else "pending"
                pay.decided_at = now_utc() if approved else None
                if not approved:
                    pay.notes = res.get("reason")
            s.commit()
        except Exception:
            s.rollback()
            raise

    if not approved and (res.get("reason") or "").startswith("Transação não encontrada"):
        schedule_pending_tx_recheck()

    # Se aprovado, renova VIP conforme plano e manda convite
    if approved:
        try:
            # melhor esforço para obter username atual
            try:
                u = await application.bot.get_chat(int(uid))
                username = u.username
            except Exception:
                username = None

            vip_upsert_start_or_extend(int(uid), username, tx_hash, plan)
            invite_link = await create_and_store_personal_invite(int(uid))
            await application.bot.send_message(
    chat_id=int(uid),
    text=(f"✅ Pagamento confirmado na rede {chain} ({symbol})!\n"
          f"Seu VIP foi ativado por {PLAN_DAYS[plan]} dias.\n"
          f"Entre no VIP: {invite_link}")
)

        except Exception:
            logging.exception("Erro enviando invite")

    return JSONResponse({"ok": True, "verified": approved, "reason": res.get("reason")})



@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception:
        logging.exception("Erro processando update Telegram")
        raise HTTPException(status_code=400, detail="Invalid update")
    return PlainTextResponse("", status_code=200)

@app.get("/")
async def root(): return {"status": "online", "message": "Bot ready (crypto + schedules + VIP/FREE)"}

@app.get("/keepalive")
async def keepalive(): return {"ok": True, "ts": now_utc().isoformat()}


async def vip_expiration_warn_job(context: ContextTypes.DEFAULT_TYPE):
    now = now_utc()
    with SessionLocal() as s:
        membros = (
            s.query(VipMembership)
             .filter(VipMembership.active == True, VipMembership.expires_at > now)
             .all()
        )
    for m in membros:
        dias = (m.expires_at - now).days
        if dias in (3, 1):
            texto = f"⚠️ Seu VIP expira em {dias} dia{'s' if dias > 1 else ''}. Use /pagar para renovar."
            await dm(m.user_id, texto)


async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    if not SELF_URL: return
    url = SELF_URL.rstrip("/") + "/keepalive"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url); logging.info(f"[keepalive] GET {url} -> {r.status_code}")
    except Exception as e: logging.warning(f"[keepalive] erro: {e}")

# ===== Guard global: só permite /pagar e /tx para não-admin (em qualquer chat)
ALLOWED_NON_ADMIN = {"pagar", "tx", "status"}

async def _block_non_admin_everywhere(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not msg or not user:
        return
    text = (msg.text or "").strip().lower()
    if not text.startswith("/"):
        return
    # extrai comando base sem @bot
    cmd = text.split()[0]
    base = cmd[1:].split("@", 1)[0]  # ex: "/pagar@MeuBot" -> "pagar"

    if is_admin(user.id):
        return  # admin passa

    if base not in ALLOWED_NON_ADMIN:
        # opcional: responder algo curto só no privado
        if update.effective_chat.type == "private":
            await msg.reply_text("Comando disponível apenas para administradores.")
        # corta a propagação
        raise ApplicationHandlerStop



# =========================
# Startup
# =========================
@app.on_event("startup")
async def on_startup():
    global bot, BOT_USERNAME
    logging.basicConfig(level=logging.INFO)
    await application.initialize(); await application.start()
    bot = application.bot
    try: await bot.set_webhook(url=WEBHOOK_URL)
    except Exception as e: logging.warning(f"set_webhook falhou: {e}")
    me = await bot.get_me(); BOT_USERNAME = me.username
    logging.info("Bot iniciado (cripto + schedules + VIP/FREE).")

    # ==== Error handler
    application.add_error_handler(error_handler)


    application.add_handler(CommandHandler("simular_tx", simular_tx_cmd), group=1)
    application.add_handler(ChatJoinRequestHandler(vip_join_request_handler), group=1)


    # ===== Guard GLOBAL para não-admin (vem BEM cedo)
    application.add_handler(MessageHandler(filters.COMMAND, _block_non_admin_commands), group=-2)


    # ==== Guard (tem que vir ANTES)
    application.add_handler(MessageHandler(filters.COMMAND, _block_non_admin_everywhere), group=-100)

    # ==== Conversas do NOVOPACK
    states_map = {
        TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_title)],
        CONFIRM_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_confirm_title)],
        PREVIEWS: [
            CommandHandler("proximo", novopack_next_to_files),
            MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION, novopack_collect_previews),
            MessageHandler(filters.TEXT & ~filters.COMMAND, hint_previews),
        ],
        FILES: [
            CommandHandler("finalizar", novopack_finish_review),
            MessageHandler(filters.Document.ALL | filters.AUDIO | filters.VOICE, novopack_collect_files),
            MessageHandler(filters.TEXT & ~filters.COMMAND, hint_files),
        ],
        CONFIRM_SAVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_confirm_save)],
    }

    conv_main = ConversationHandler(
        entry_points=[
            CommandHandler("novopack", novopack_start),
            CommandHandler("start", novopack_start, filters=filters.ChatType.PRIVATE & filters.Regex(r"^/start\s+novopack(\s|$)")),
        ],
        states={CHOOSE_TIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_choose_tier)], **states_map},
        fallbacks=[CommandHandler("cancelar", novopack_cancel)], allow_reentry=True,
    )
    application.add_handler(conv_main, group=0)

    conv_vip = ConversationHandler(
        entry_points=[CommandHandler("novopackvip", novopackvip_start, filters=filters.ChatType.PRIVATE)],
        states=states_map, fallbacks=[CommandHandler("cancelar", novopack_cancel)], allow_reentry=True,
    )
    application.add_handler(conv_vip, group=0)

    conv_free = ConversationHandler(
        entry_points=[CommandHandler("novopackfree", novopackfree_start, filters=filters.ChatType.PRIVATE)],
        states=states_map, fallbacks=[CommandHandler("cancelar", novopack_cancel)], allow_reentry=True,
    )
    application.add_handler(conv_free, group=0)

    # ===== Conversa /excluir_pack
    excluir_conv = ConversationHandler(
        entry_points=[CommandHandler("excluir_pack", excluir_pack_cmd)],
        states={DELETE_PACK_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, excluir_pack_confirm)]},
        fallbacks=[], allow_reentry=True,
    )
    application.add_handler(excluir_conv, group=0)

    # ===== Handlers de storage
    application.add_handler(
        MessageHandler(
            (filters.Chat(STORAGE_GROUP_ID) | filters.Chat(STORAGE_GROUP_FREE_ID)) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler
        ),
        group=1,
    )
    media_filter = (
        (filters.Chat(STORAGE_GROUP_ID) | filters.Chat(STORAGE_GROUP_FREE_ID)) &
        (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.AUDIO | filters.Document.ALL | filters.VOICE)
    )
    application.add_handler(MessageHandler(media_filter, storage_media_handler), group=1)

    # ===== Comandos gerais (group=1)
    application.add_handler(CommandHandler("start", start_cmd), group=1)
    application.add_handler(CommandHandler("comandos", comandos_cmd), group=5)
    application.add_handler(CommandHandler("listar_comandos", comandos_cmd), group=5)
    application.add_handler(CommandHandler("getid", getid_cmd), group=1)

    application.add_handler(CommandHandler("say_vip", say_vip_cmd), group=1)
    application.add_handler(CommandHandler("say_free", say_free_cmd), group=1)

    application.add_handler(CommandHandler("simularvip", simularvip_cmd), group=1)
    application.add_handler(
        CommandHandler(
            [
                "listar_packsvip",
                "listar_packvip",
                "listar_packs_vip",
                "listar_pack_vip",
            ],
            listar_packsvip_cmd,
            block=True,
        ),
        group=1,
    )
    application.add_handler(CommandHandler("listar_packsfree", listar_packsfree_cmd), group=1,)
    application.add_handler(CommandHandler("pack_info", pack_info_cmd), group=1)
    application.add_handler(CommandHandler("excluir_item", excluir_item_cmd), group=1)
    application.add_handler(CommandHandler("set_pendentevip", set_pendentevip_cmd), group=1)
    application.add_handler(CommandHandler("set_pendentefree", set_pendentefree_cmd), group=1)
    application.add_handler(CommandHandler("set_enviadovip", set_enviadovip_cmd), group=1)
    application.add_handler(CommandHandler("set_enviadofree", set_enviadofree_cmd), group=1)

    application.add_handler(CommandHandler("listar_admins", listar_admins_cmd), group=1)
    application.add_handler(CommandHandler("add_admin", add_admin_cmd), group=1)
    application.add_handler(CommandHandler("rem_admin", rem_admin_cmd), group=1)
    application.add_handler(CommandHandler("mudar_nome", mudar_nome_cmd), group=1)
    application.add_handler(CommandHandler("limpar_chat", limpar_chat_cmd), group=1)

    application.add_handler(CommandHandler("valor", valor_cmd), group=1)
    application.add_handler(CommandHandler("vip_list", vip_list_cmd), group=1)
    application.add_handler(CommandHandler("vip_addtime", vip_addtime_cmd), group=1)
    application.add_handler(CommandHandler("vip_set", vip_set_cmd), group=1)
    application.add_handler(CommandHandler("vip_remove", vip_remove_cmd), group=1)



    application.add_handler(CommandHandler("pagar", pagar_cmd), group=1)
    application.add_handler(CommandHandler("tx", tx_cmd), group=1)
    application.add_handler(CommandHandler("cancel_tx", cancel_tx_cmd), group=1)
    application.add_handler(CommandHandler("clear_tx", clear_tx_cmd), group=1)
    application.add_handler(CommandHandler("listar_pendentes", listar_pendentes_cmd), group=1)
    application.add_handler(CommandHandler("aprovar_tx", aprovar_tx_cmd), group=1)
    application.add_handler(CommandHandler("rejeitar_tx", rejeitar_tx_cmd), group=1)

    application.add_handler(CommandHandler("add_msg_vip", add_msg_vip_cmd), group=1)
    application.add_handler(CommandHandler("add_msg_free", add_msg_free_cmd), group=1)
    application.add_handler(CommandHandler("list_msgs_vip", list_msgs_vip_cmd), group=1)
    application.add_handler(CommandHandler("list_msgs_free", list_msgs_free_cmd), group=1)
    application.add_handler(CommandHandler("edit_msg_vip", edit_msg_vip_cmd), group=1)
    application.add_handler(CommandHandler("edit_msg_free", edit_msg_free_cmd), group=1)
    application.add_handler(CommandHandler("toggle_msg_vip", toggle_msg_vip_cmd), group=1)
    application.add_handler(CommandHandler("toggle_msg_free", toggle_msg_free_cmd), group=1)
    application.add_handler(CommandHandler("del_msg_vip", del_msg_vip_cmd), group=1)
    application.add_handler(CommandHandler("del_msg_free", del_msg_free_cmd), group=1)

    application.add_handler(CommandHandler("set_pack_horario_vip", set_pack_horario_vip_cmd), group=1)
    application.add_handler(CommandHandler("set_pack_horario_free", set_pack_horario_free_cmd), group=1)
    
    hash_filter = (
        (filters.TEXT | filters.PHOTO | filters.Document.PDF)
        & ~filters.COMMAND
        & ~filters.Chat(STORAGE_GROUP_ID)
        & ~filters.Chat(STORAGE_GROUP_FREE_ID)
    )
    application.add_handler(MessageHandler(hash_filter, auto_tx_handler), group=1)

    # Jobs
    await _reschedule_daily_packs()
    _register_all_scheduled_messages(application.job_queue)
    schedule_pending_tx_recheck()
    for cfg in CHAIN_CONFIGS:
        application.job_queue.run_once(
            monitor_wallet_job,
            when=dt.timedelta(seconds=0),
            data={"cfg": cfg},
            name=f"monitor_{cfg.get('chain_name', '')}",
        )

    application.job_queue.run_daily(vip_expiration_warn_job, time=dt.time(hour=9, minute=0, tzinfo=pytz.timezone("America/Sao_Paulo")), name="vip_warn")
    application.job_queue.run_repeating(
        keepalive_job,
        interval=dt.timedelta(minutes=4),
        first=dt.timedelta(seconds=20),
        name="keepalive",
        job_kwargs={"misfire_grace_time": 10},
    )
    logging.info("Handlers e jobs registrados.")

# =========================
# Run
# =========================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
