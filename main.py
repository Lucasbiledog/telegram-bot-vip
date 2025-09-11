# main.py
import os
import logging
import asyncio
import datetime as dt
from enum import Enum
from typing import Optional, List, Dict, Any, Tuple

import html
import json
import pytz
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
import httpx

from telegram import Update, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
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
    CallbackQueryHandler,
)
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
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.engine import make_url

from models import Pack
from config import WEBAPP_URL

from payments import (
    resolve_payment_usd_autochain,              # já está funcionando
    WALLET_ADDRESS,                             # sua carteira destino
    tx_cmd, listar_pendentes_cmd,               # comandos de pagamento
    aprovar_tx_cmd, rejeitar_tx_cmd,           # comandos admin de pagamento
)
from utils import (
    choose_plan_from_usd,                       # mapeia USD -> dias
    create_one_time_invite,                     # função de convite p/ o grupo VIP
    vip_upsert_and_get_until,                   # centralizado
    make_link_sig,                              # assinatura de link compartilhada
    send_with_retry,
    reply_with_retry,
)


# === Config DB ===
# Use /tmp for SQLite on cloud platforms, fallback to in-memory if filesystem is read-only
import tempfile

def get_database_url():
    """
    Get database URL with fallback handling for cloud deployments.
    
    For production deployments (Render, Heroku, etc.):
    - ALWAYS configure DATABASE_URL environment variable with PostgreSQL
    - SQLite in /tmp is TEMPORARY and data will be LOST on redeploy
    - In-memory database is for emergency fallback only
    
    To setup persistent PostgreSQL on Render.com:
    1. Add database to render.yaml:
       databases:
         - name: telegram-bot-db
           databaseName: telegram_bot
           user: telegram_user
           plan: free
    2. DATABASE_URL will be automatically provided
    3. Redeploy the application
    """
    env_db_url = os.getenv("DATABASE_URL")
    
    if env_db_url:
        # Render provides PostgreSQL URLs, use them directly
        if env_db_url.startswith("postgresql://") or env_db_url.startswith("postgres://"):
            # Remover print desnecessário para melhor performance
            # print("Using PostgreSQL database from DATABASE_URL") 
            # Convert postgres:// to postgresql:// if needed (SQLAlchemy requirement)
            return env_db_url.replace("postgres://", "postgresql://", 1)
        return env_db_url
    
    # Check if we're in a cloud environment (Render, Heroku, etc.)
    if any(os.getenv(var) for var in ["RENDER", "HEROKU", "DYNO"]):
        print("Detected cloud environment without DATABASE_URL.")
        print("CRITICAL: No DATABASE_URL configured!")
        print("To fix this permanently:")
        print("   1. Create a PostgreSQL database on your cloud platform")
        print("   2. Set DATABASE_URL environment variable")
        print("   3. Redeploy the application")
        print("")
        print("For Render.com:")
        print("   - Add a PostgreSQL database to your service")
        print("   - The DATABASE_URL will be automatically provided")
        print("")
        
        # Try to use /tmp SQLite as temporary fallback
        tmp_db = "/tmp/telegram_bot.db"
        try:
            # Test if we can write to /tmp
            with open(tmp_db + ".test", 'w') as f:
                f.write("test")
            os.remove(tmp_db + ".test")
            print(f"TEMPORARY FALLBACK: Using SQLite database: {tmp_db}")
            print("WARNING: Data will be lost on restart/redeploy!")
            print("Configure PostgreSQL immediately for data persistence!")
            return f"sqlite:///{tmp_db}"
        except Exception as e:
            print(f"Cannot write to /tmp ({e})")
            print("FALLING BACK TO IN-MEMORY DATABASE!")
            print("ALL DATA WILL BE LOST ON RESTART!")
            print("CONFIGURE PostgreSQL DATABASE IMMEDIATELY!")
            return "sqlite:///:memory:"
    
    # Try different SQLite paths in order of preference (for local development)
    # Use absolute path based on script location
    script_dir = os.path.dirname(os.path.abspath(__file__))
    sqlite_paths = [
        os.path.join(script_dir, "bot.db"),  # Same directory as script
        os.path.join(tempfile.gettempdir(), "bot.db"),  # /tmp/bot.db
        os.path.join(os.getcwd(), "bot.db"),  # ./bot.db
        ":memory:"  # In-memory as last resort
    ]
    
    for path in sqlite_paths:
        if path == ":memory:":
            print("Warning: Using in-memory SQLite database. Data will not persist between restarts.")
            return "sqlite:///:memory:"
        
        try:
            # Test if we can create the directory and write to it
            db_dir = os.path.dirname(path)
            if db_dir and not os.path.exists(db_dir):
                os.makedirs(db_dir, exist_ok=True)
            
            # Test write permissions by creating a temporary file
            test_path = path + ".test"
            with open(test_path, 'w') as f:
                f.write("test")
            os.remove(test_path)
            
            print(f"Using SQLite database: {path}")
            # Use pathlib for cross-platform path handling
            from pathlib import Path
            path_obj = Path(path)
            return f"sqlite:///{path_obj.as_posix()}"
            
        except (OSError, PermissionError) as e:
            print(f"Warning: Cannot use database path {path}: {e}")
            # Try to fix permissions if it's the bot.db file
            if path.endswith("bot.db") and os.path.exists(path):
                try:
                    import stat
                    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
                    print(f"Fixed permissions for {path}, retrying...")
                    # Test again after fixing permissions
                    test_path = path + ".test"
                    with open(test_path, 'w') as f:
                        f.write("test")
                    os.remove(test_path)
                    print(f"Using SQLite database: {path}")
                    # Use pathlib for cross-platform path handling
                    from pathlib import Path
                    path_obj = Path(path)
                    return f"sqlite:///{path_obj.as_posix()}"
                except Exception as perm_e:
                    print(f"Could not fix permissions: {perm_e}")
            continue
    
    # This shouldn't be reached, but just in case
    return "sqlite:///:memory:"

DB_URL = get_database_url()
url = make_url(DB_URL)

# Force synchronous SQLite dialect if using SQLite
if url.get_backend_name() == "sqlite":
    # Replace aiosqlite driver with synchronous sqlite driver
    if "aiosqlite" in str(url):
        DB_URL = DB_URL.replace("sqlite+aiosqlite://", "sqlite:///")
        url = make_url(DB_URL)
    # Ensure we're using synchronous SQLite
    if not url.drivername:
        url = url.set(drivername="sqlite")
    elif url.drivername == "sqlite+aiosqlite":
        url = url.set(drivername="sqlite")

engine = create_engine(
    url, 
    pool_pre_ping=True, 
    future=True,
    pool_size=5,  # Conexões simultâneas
    max_overflow=10,  # Conexões extras quando necessário
    pool_timeout=30,  # Timeout para obter conexão
    pool_recycle=3600,  # Reciclar conexões a cada hora
)
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

def ensure_pack_scheduled_for_column():
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE packs ADD COLUMN scheduled_for TIMESTAMP"))
            except Exception:
                pass
    except Exception as e:
        logging.warning("Falha em ensure_pack_scheduled_for_column: %s", e)
        
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

def ensure_payment_fields():
    """Garantir que a tabela payments possua os novos campos utilizados pelo bot"""
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE payments ADD COLUMN token_symbol VARCHAR"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE payments ADD COLUMN usd_value VARCHAR"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE payments ADD COLUMN vip_days INTEGER"))
            except Exception:
                pass
    except Exception as e:
        logging.warning("Falha ensure_payment_fields: %s", e)

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

def ensure_schema():
    global engine, SessionLocal, url, DB_URL
    
    try:
        Base.metadata.create_all(bind=engine)
        ensure_bigint_columns()
        ensure_pack_tier_column()
        ensure_pack_scheduled_for_column()
        ensure_packfile_src_columns()
        ensure_vip_invite_column()
        ensure_vip_plan_column()
        ensure_payment_fields()
        
        # Show appropriate success message based on database type
        db_type = "PostgreSQL" if url.get_backend_name() == "postgresql" else "SQLite"
        if ":memory:" in str(url):
            print("Database schema initialized successfully (IN-MEMORY)")
            print("WARNING: All data will be lost on restart!")
            print("Configure PostgreSQL for production use!")
        elif "/tmp/" in str(url):
            print("Database schema initialized successfully (TEMPORARY SQLite)")
            print("WARNING: Data will be lost on redeploy!")
            print("Configure PostgreSQL for production use!")
            print(f"Database URL: {DB_URL}")
        elif db_type == "PostgreSQL":
            print("Database schema initialized successfully (PostgreSQL)")
            print("Data persistence ENABLED - safe for production!")
            # Don't print the full URL for security (contains password)
            try:
                parsed_url = url
                safe_url = f"postgresql://{parsed_url.username}@{parsed_url.host}:{parsed_url.port}/{parsed_url.database}"
                print(f"Database: {safe_url}")
            except:
                print("Database: PostgreSQL configured")
        else:
            print(f"Database schema initialized successfully ({db_type})")
            print(f"Database URL: {DB_URL}")
            
    except Exception as e:
        # Only try fallback if we're not already using in-memory
        if ":memory:" not in str(DB_URL):
            print(f"Warning: Database schema initialization failed: {e}")
            print("Attempting to use in-memory database as fallback...")
            # Try to reinitialize with in-memory database
            DB_URL = "sqlite:///:memory:"
            url = make_url(DB_URL)
            engine = create_engine(
    url, 
    pool_pre_ping=True, 
    future=True,
    pool_size=5,  # Conexões simultâneas
    max_overflow=10,  # Conexões extras quando necessário
    pool_timeout=30,  # Timeout para obter conexão
    pool_recycle=3600,  # Reciclar conexões a cada hora
)
            SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
            
            try:
                Base.metadata.create_all(bind=engine)
                ensure_bigint_columns()
                ensure_pack_tier_column()
                ensure_packfile_src_columns()
                ensure_vip_invite_column()
                ensure_vip_plan_column()
                print("Successfully initialized with in-memory database")
            except Exception as fallback_error:
                print(f"Critical error: Even in-memory database failed: {fallback_error}")
                raise
        else:
            print(f"Critical error: Database initialization failed: {e}")
            raise


# =========================
# Helpers
# =========================
# Quais comandos usuários comuns podem usar
ALLOWED_FOR_NON_ADM = {"pagar", "tx", "start", "novopack" }

def esc(s): return html.escape(str(s) if s is not None else "")
def now_utc(): return dt.datetime.now(dt.timezone.utc)

def _reorganize_payment_ids(session):
    """Reorganiza IDs dos payments para preencher lacunas após exclusões"""
    try:
        # Buscar todos os payments ordenados por created_at (mais antigo primeiro)
        payments = session.query(Payment).order_by(Payment.created_at.asc()).all()
        
        if not payments:
            # Se não há payments, resetar sequência para 1
            from sqlalchemy import text
            session.execute(text("ALTER SEQUENCE payments_id_seq RESTART WITH 1;"))
            return
        
        # Reorganizar IDs sequenciais começando de 1
        for new_id, payment in enumerate(payments, 1):
            if payment.id != new_id:
                payment.id = new_id
        
        # Resetar sequência para próximo ID disponível
        next_id = len(payments) + 1
        from sqlalchemy import text
        session.execute(text(f"ALTER SEQUENCE payments_id_seq RESTART WITH {next_id};"))
        
    except Exception as e:
        logging.error(f"Erro ao reorganizar IDs dos payments: {e}")
        # Em caso de erro, apenas tentar resetar a sequência
        try:
            payments_count = session.query(Payment).count()
            if payments_count == 0:
                from sqlalchemy import text
                session.execute(text("ALTER SEQUENCE payments_id_seq RESTART WITH 1;"))
            else:
                max_id = session.query(Payment.id).order_by(Payment.id.desc()).first()
                if max_id:
                    from sqlalchemy import text
                    session.execute(text(f"ALTER SEQUENCE payments_id_seq RESTART WITH {max_id[0] + 1};"))
        except:
            pass

def _reorganize_pack_ids(session):
    """Reorganiza IDs dos packs para preencher lacunas após exclusões"""
    try:
        # Buscar todos os packs ordenados por created_at (mais antigo primeiro)
        packs = session.query(Pack).order_by(Pack.created_at.asc()).all()
        
        if not packs:
            # Se não há packs, resetar sequência para 1
            from sqlalchemy import text
            session.execute(text("ALTER SEQUENCE packs_id_seq RESTART WITH 1;"))
            return
        
        # Verificar se precisa reorganizar (se os IDs já são sequenciais, não faz nada)
        needs_reorganization = False
        for i, pack in enumerate(packs, 1):
            if pack.id != i:
                needs_reorganization = True
                break
        
        if not needs_reorganization:
            # IDs já estão sequenciais, apenas resetar sequência
            next_id = len(packs) + 1
            from sqlalchemy import text
            session.execute(text(f"ALTER SEQUENCE packs_id_seq RESTART WITH {next_id};"))
            return
        
        # Approach: reconstruir os packs com IDs corretos
        # Primeiro coletar todos os dados
        pack_data = []
        for i, pack in enumerate(packs, 1):
            if pack.id != i:
                # Coletar dados do pack e seus arquivos
                pack_files = session.query(PackFile).filter(PackFile.pack_id == pack.id).all()
                pack_data.append({
                    'old_id': pack.id,
                    'new_id': i,
                    'pack': pack,
                    'files': pack_files
                })
        
        # Executar reorganização apenas se necessário
        if pack_data:
            # Criar novos packs com IDs corretos
            temp_packs = {}  # old_id -> new_pack
            for data in pack_data:
                # Criar novo pack com ID correto
                new_pack = Pack(
                    title=data['pack'].title,
                    header_message_id=data['pack'].header_message_id,
                    tier=data['pack'].tier,
                    sent=data['pack'].sent,
                    created_at=data['pack'].created_at,
                    scheduled_for=data['pack'].scheduled_for
                )
                new_pack.id = data['new_id']
                session.add(new_pack)
                session.flush()  # Para garantir que o ID seja atribuído
                temp_packs[data['old_id']] = new_pack
            
            # Criar novos PackFiles apontando para os novos packs
            for data in pack_data:
                new_pack = temp_packs[data['old_id']]
                for pf in data['files']:
                    new_file = PackFile(
                        pack_id=new_pack.id,
                        file_id=pf.file_id,
                        file_unique_id=pf.file_unique_id,
                        file_type=pf.file_type,
                        role=pf.role,
                        file_name=pf.file_name,
                        added_at=pf.added_at,
                        src_chat_id=pf.src_chat_id,
                        src_message_id=pf.src_message_id
                    )
                    session.add(new_file)
            
            # Deletar packs antigos (isso deletará automaticamente os PackFiles por cascade)
            for data in pack_data:
                session.delete(data['pack'])
        
        # Resetar sequência para próximo ID disponível
        next_id = len(packs) + 1
        from sqlalchemy import text
        session.execute(text(f"ALTER SEQUENCE packs_id_seq RESTART WITH {next_id};"))
        
    except Exception as e:
        logging.error(f"Erro ao reorganizar IDs dos packs: {e}")
        raise  # Re-raise para reverter transação


def wrap_ph(s: str) -> str:
    # Converte qualquer <algo> em <code>&lt;algo&gt;</code> para não quebrar o HTML
    return re.sub(r'<([^>\n]{1,80})>', r'<code>&lt;\1&gt;</code>', s)


from datetime import timedelta

import re
TX_RE = re.compile(r'^(0x)?[0-9a-fA-F]+$')

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

# Payment Configuration
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
BOT_SECRET = os.getenv("BOT_SECRET", "")

STORAGE_GROUP_ID       = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))
GROUP_VIP_ID           = int(os.getenv("Group_VIP_ID", os.getenv("GROUP_VIP_ID", "-1002791988432")))
STORAGE_GROUP_FREE_ID  = int(os.getenv("STORAGE_GROUP_FREE_ID", "-1002509364079"))
GROUP_FREE_ID          = int(os.getenv("GROUP_FREE_ID", "-1002932075976"))

PORT = int(os.getenv("PORT", 8000))

# Job prefixes
JOB_PREFIX_SM = "scheduled_msg_"

# =========================
# FASTAPI + PTB
# =========================
app = FastAPI()

# Montar arquivos estáticos da webapp
import os
webapp_dir = os.path.join(os.path.dirname(__file__), "webapp")
if os.path.exists(webapp_dir):
    app.mount("/webapp", StaticFiles(directory=webapp_dir), name="webapp")
# Configure timeouts para produção (mais tolerantes para cloud)
from telegram.request import HTTPXRequest
request = HTTPXRequest(
    connection_pool_size=8,
    read_timeout=120,  # Increased from 60
    write_timeout=120, # Increased from 60  
    connect_timeout=60, # Increased from 30
    pool_timeout=60,   # Increased from 30
)

application = ApplicationBuilder().token(BOT_TOKEN).request(request).build()
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
    scheduled_for = Column(DateTime, nullable=True)
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
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, nullable=False)
    username = Column(String, nullable=True)
    tx_hash = Column(String, unique=True, index=True)
    chain = Column(String, default="unknown")
    amount = Column(String, nullable=True)  # Quantidade do token
    token_symbol = Column(String, nullable=True)  # Símbolo do token (ETH, USDC, etc)
    usd_value = Column(String, nullable=True)  # Valor em USD na época do pagamento
    vip_days = Column(Integer, nullable=True)  # Dias de VIP atribuídos
    status = Column(String, default="pending")  # pending, approved, rejected
    created_at = Column(DateTime, nullable=False)

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
# Garante que o esquema do banco esteja atualizado - apenas uma vez por sessão
_schema_initialized = False

def ensure_schema_once():
    """Executa ensure_schema apenas uma vez por sessão para melhor performance"""
    global _schema_initialized
    if not _schema_initialized:
        # Versão otimizada para melhor performance
        try:
            Base.metadata.create_all(bind=engine)
            # Pular verificações de schema que são demoradas em produção
            # ensure_bigint_columns()
            # ensure_pack_tier_column()
            # ensure_pack_scheduled_for_column()
            # ensure_packfile_src_columns()
            # ensure_vip_invite_column()
            # ensure_vip_plan_column()
            # ensure_payment_fields()
            
            # Configurações básicas
            init_db()
            _schema_initialized = True
        except Exception as e:
            logging.error(f"Schema initialization failed: {e}")
            # Fallback para versão completa se necessário
            ensure_schema()
            init_db()
            _schema_initialized = True

# Executar apenas quando necessário (na inicialização do bot)
if __name__ == "__main__":
    ensure_schema_once()


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

def create_pack(
    title: str,
    header_message_id: Optional[int] = None,
    tier: str = "vip",
    scheduled_for: Optional[dt.datetime] = None,
    reorganize_ids: bool = False,  # Novo parâmetro para controlar reorganização
) -> 'Pack':
    with SessionLocal() as s:
        try:
            p = Pack(
                title=title.strip(),
                header_message_id=header_message_id,
                tier=tier,
                scheduled_for=scheduled_for,
            )
            s.add(p)
            s.flush()  # Para obter o ID temporário
            
            # Reorganizar IDs apenas quando explicitamente solicitado
            if reorganize_ids:
                _reorganize_pack_ids(s)
            
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
    try: 
        return s.query(Pack).filter(Pack.sent == False, Pack.tier == tier).order_by(Pack.created_at.asc()).first()
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
        return  # /tx liberado

    # Bloqueia o resto
    await update.effective_message.reply_text("🚫 Comando restrito. Comandos permitidos: /tx, /novopack")
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
    tier = "vip" if msg.chat.id == STORAGE_GROUP_ID else "free"
    
    # Otimização: usar uma única sessão de database para verificar e criar
    with SessionLocal() as s:
        # Verificar se já existe
        existing = s.query(Pack).filter(Pack.header_message_id == hkey).first()
        if existing:
            await msg.reply_text("Pack já registrado.")
            return
            
        # Criar novo pack na mesma sessão
        p = Pack(
            title=title.strip(),
            header_message_id=hkey,
            tier=tier,
            scheduled_for=None,
        )
        s.add(p)
        s.commit()
        s.refresh(p)
        
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

    # Otimização: usar uma única sessão para todas as operações de database
    with SessionLocal() as s:
        # Buscar pack
        pack = s.query(Pack).filter(Pack.header_message_id == hkey).first()
        if not pack:
            await msg.reply_text("Cabeçalho do pack não encontrado. Responda à mensagem de título.")
            return

        # Verificar se arquivo já existe
        q = s.query(PackFile).filter(PackFile.pack_id == pack.id)
        if file_unique_id:
            q = q.filter(PackFile.file_unique_id == file_unique_id)
        else:
            q = q.filter(PackFile.file_id == file_id)
        if q.first():
            await msg.reply_text("Este arquivo já foi adicionado a este pack.", parse_mode="HTML")
            return
            
        # Adicionar arquivo na mesma sessão
        pf = PackFile(
            pack_id=pack.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_type=file_type,
            role=role,
            file_name=visible_name,
            src_chat_id=msg.chat.id,
            src_message_id=msg.message_id,
        )
        s.add(pf)
        s.commit()
        
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

async def _create_checkout_keyboard():
    """Cria o teclado inline com botão de checkout"""
    if not WEBAPP_URL or not WALLET_ADDRESS:
        return None
    
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
    import time
    import os
    
    # Gerar parâmetros de segurança genéricos para o grupo FREE
    ts = int(time.time())
    sig = make_link_sig(os.getenv("BOT_SECRET", "default"), 0, ts)  # uid=0 para genérico
    
    # URL com parâmetros de segurança
    secure_url = f"{WEBAPP_URL}?uid=0&ts={ts}&sig={sig}"
    
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💳 Assinar VIP - Pagar com Crypto",
            web_app=WebAppInfo(url=secure_url)
        )]
    ])

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
        # Agrupar fotos para uma melhor apresentação visual
        media = []
        for pf in photo_items:
            try: media.append(InputMediaPhoto(media=pf.file_id))
            except Exception: media = []; break
        
        if media and len(media) > 1:
            # Para múltiplas fotos, sempre enviar como media_group para agrupamento
            try:
                await context.application.bot.send_media_group(chat_id=target_chat_id, media=media[:10])  # Telegram limita a 10
                counts["photos"] += len(media[:10])
                # Se houver mais de 10 fotos, enviar o resto em grupos separados
                if len(media) > 10:
                    remaining = media[10:]
                    while remaining:
                        batch = remaining[:10]
                        remaining = remaining[10:]
                        try:
                            await context.application.bot.send_media_group(chat_id=target_chat_id, media=batch)
                            counts["photos"] += len(batch)
                        except Exception as e:
                            logging.warning(f"[send_preview_media] Falha media_group adicional: {e}")
                            for item in batch:
                                try:
                                    await context.application.bot.send_photo(chat_id=target_chat_id, photo=item.media)
                                    counts["photos"] += 1
                                except Exception:
                                    pass
            except Exception as e:
                logging.warning(f"[send_preview_media] Falha media_group: {e}. Enviando foto a foto.")
                for pf in photo_items:
                    if await _try_send_photo(context, target_chat_id, pf, caption=None):
                        counts["photos"] += 1
        elif media:
            # Para uma única foto, enviar normalmente
            if await _try_send_photo(context, target_chat_id, photo_items[0], caption=None):
                counts["photos"] += 1
        else:
            # Fallback para envio individual
            for pf in photo_items:
                if await _try_send_photo(context, target_chat_id, pf, caption=None):
                    counts["photos"] += 1

    other_prev = [pf for pf in previews if pf.file_type in ("video", "animation")]
    for pf in other_prev:
        if await _try_send_video_or_animation(context, target_chat_id, pf, caption=None):
            counts["videos" if pf.file_type == "video" else "animations"] += 1
    
    # Adicionar botão de checkout automaticamente após as imagens (apenas no grupo FREE)
    if target_chat_id == GROUP_FREE_ID and (counts["photos"] > 0 or counts["videos"] > 0 or counts["animations"] > 0):
        # Enviar apenas o botão simples - a mensagem completa virá do callback
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💳 Abrir Página de Pagamento",
                callback_data="checkout_callback"
            )]
        ])
        
        # Mensagem completa com informações de pagamento
        checkout_msg = (
            "💸 <b>Quer ver o conteúdo completo?</b>\n\n"
            "✅ Clique no botão abaixo para abrir a página de pagamento\n"
            "🔒 Pague com qualquer criptomoeda\n"
            "⚡ Ativação automática\n\n"
            "💰 <b>Planos:</b>\n"
            "• 30 dias: $0.05\n"
            "• 60 dias: $1.00\n"
            "• 180 dias: $1.50\n"
            "• 365 dias: $2.00"
        )
        
        try:
            await context.application.bot.send_message(
                chat_id=target_chat_id,
                text=checkout_msg,
                reply_markup=keyboard,
                parse_mode="HTML"
            )
        except Exception as e:
            logging.warning(f"[send_preview_media] Erro ao enviar checkout: {e}")
    
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

        if tier == "free":
            # GRUPO FREE: Apenas previews + botão de pagamento (sem título, sem docs)
            if previews:
                try:
                    await _send_preview_media(context, target_chat_id, previews)
                except Exception as e:
                    if "Chat not found" in str(e):
                        logging.error(f"Chat {target_chat_id} não encontrado durante envio de previews.")
                        return f"❌ Erro: Chat {target_chat_id} não encontrado. Bot não está no grupo?"
                    raise
            
        elif tier == "vip":
            # GRUPO VIP: Tudo (previews + título + docs)
            
            # Envia previews primeiro
            if previews:
                try:
                    await _send_preview_media(context, target_chat_id, previews)
                except Exception as e:
                    if "Chat not found" in str(e):
                        logging.error(f"Chat {target_chat_id} não encontrado durante envio de previews.")
                        return f"❌ Erro: Chat {target_chat_id} não encontrado. Bot não está no grupo?"
                    raise

            # Envia título
            try:
                await context.application.bot.send_message(chat_id=target_chat_id, text=p.title)
            except Exception as e:
                if "Chat not found" in str(e):
                    logging.error(f"Chat {target_chat_id} não encontrado. Verifique se o bot está no grupo.")
                    return f"❌ Erro: Chat {target_chat_id} não encontrado. Bot não está no grupo?"
                raise

            # Envia docs (com fallback controlado)
            for f in docs:
                await _try_send_document_like(context, target_chat_id, f, caption=None)

        # Crosspost: Enviar previews do VIP também para o grupo FREE
        if tier == "vip" and previews:
            try:
                logging.info(f"Enviando previews do pack VIP '{p.title}' também para o grupo FREE")
                await _send_preview_media(context, GROUP_FREE_ID, previews)
                logging.info(f"✅ Previews enviadas com sucesso para o grupo FREE")
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

# Função removida - agendamento individual não é mais usado

# =========================
# CALLBACK QUERY HANDLER
# =========================
async def checkout_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o botão de checkout"""
    query = update.callback_query
    if not query or query.data != "checkout_callback":
        return
    
    await query.answer()  # Responde ao callback
    
    user = query.from_user
    if not user:
        return
    
    # Implementa a lógica de checkout diretamente para callback queries
    try:
        import time
        import os
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
        from utils import send_with_retry, make_link_sig
        
        # Verificar se o WALLET_ADDRESS está configurado
        WALLET_ADDRESS = os.getenv("WALLET_ADDRESS")
        if not WALLET_ADDRESS or WALLET_ADDRESS == "your_crypto_wallet_address_here":
            await query.message.reply_text(
                "❌ Sistema de pagamento em configuração. Use o comando /pagar para instruções.",
                parse_mode="HTML"
            )
            return
        
        # Obter WEBAPP_URL diretamente das variáveis de ambiente
        WEBAPP_URL = os.getenv("WEBAPP_URL")
        if not WEBAPP_URL:
            SELF_URL = os.getenv("SELF_URL", "")
            WEBAPP_URL = f"{SELF_URL.rstrip('/')}/pay/" if SELF_URL else None
        elif WEBAPP_URL and not WEBAPP_URL.endswith('/pay/'):
            # Garantir que o path /pay/ esteja presente
            WEBAPP_URL = f"{WEBAPP_URL.rstrip('/')}/pay/"
        
        # Capturar dados do usuário automaticamente - SEMPRE usar o ID do usuário que clicou
        uid = user.id  # ID do usuário que clicou no botão
        username = user.username or user.first_name or f"user_{uid}"
        ts = int(time.time())
        sig = make_link_sig(os.getenv("BOT_SECRET", "default"), uid, ts)
        
        # Criar URL com parâmetros do usuário CORRETO (quem clicou)
        base_url = os.getenv("WEBAPP_URL", "https://telegram-bot-vip-hfn7.onrender.com")
        if base_url.endswith('/pay/') or base_url.endswith('/pay'):
            checkout_url = f"{base_url.rstrip('/')}/?uid={uid}&username={username}&ts={ts}&sig={sig}"
        else:
            checkout_url = f"{base_url.rstrip('/')}/pay/?uid={uid}&username={username}&ts={ts}&sig={sig}"
        
        logging.info(f"[CHECKOUT] Usuário que clicou - ID: {uid}, Username: {username}, URL: {checkout_url[:100]}...")
        
        # Botão que abre diretamente com o user ID capturado
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💳 Abrir Página de Pagamento",
                url=checkout_url
            )]
        ])

        checkout_msg = (
            f"💸 <b>Pagamento VIP via Cripto</b>\n\n"
            f"👤 <b>Usuário:</b> {username} (ID: {uid})\n"
            f"✅ Link personalizado para SEU ID\n"
            f"🔒 Pague com qualquer criptomoeda\n"
            f"⚡ Ativação automática do VIP\n\n"
            f"💰 <b>Planos:</b>\n"
            f"• 30 dias: $0.05\n"
            f"• 60 dias: $1.00\n"
            f"• 180 dias: $1.50\n"
            f"• 365 dias: $2.00"
        )

        # Editar a mensagem existente para trocar o callback por URL
        try:
            await query.edit_message_reply_markup(reply_markup=keyboard)
        except Exception:
            # Se falhar ao editar, enviar nova mensagem
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="💳 Link de pagamento:",
                parse_mode="HTML",
                reply_markup=keyboard
            )
                
    except Exception as e:
        logging.error(f"Erro no checkout_callback_handler: {e}")
        logging.error(f"Tipo do erro: {type(e).__name__}")
        import traceback
        logging.error(f"Traceback: {traceback.format_exc()}")
        try:
            await query.message.reply_text(
                "❌ Erro ao processar pagamento. Tente usar o comando /pagar diretamente.",
                parse_mode="HTML"
            )
        except Exception as reply_error:
            logging.error(f"Erro ao enviar mensagem de erro: {reply_error}")

# =========================
# COMMANDS & ADMIN
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = ("Fala! Eu gerencio packs VIP/FREE, pagamentos via MetaMask e mensagens agendadas.\nOs pagamentos são automáticos quando as imagens são enviadas.")
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
        "• Pagamentos automáticos junto às imagens",
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
        "• /listar_packs — lista todos os packs (VIP e FREE)",
        "• /pack_info <id> — detalhes do pack",
        "• /excluir_item <id_item> — remove item do pack",
        "• /excluir_pack [<id>] — remove pack (com confirmação)",
        "• /excluir_todos_packs — remove TODOS os packs (CUIDADO!)",
        "• /set_pendentevip <id> — marca pack VIP como pendente",
        "• /set_pendentefree <id> — marca pack FREE como pendente",
        "• /set_enviadovip <id> — marca pack VIP como enviado",
        "• /set_enviadofree <id> — marca pack FREE como enviado",
        "• /set_pack_horario_vip HH:MM — define horário diário VIP",
        "• /set_pack_horario_free HH:MM — define horário diário FREE",
        "• /listar_jobs — lista jobs de agendamento ativos",
        "• /enviar_pack_agora <vip|free> — força envio imediato",
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

async def debug_grupos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para debug dos grupos configurados"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return await update.effective_message.reply_text("Apenas admins.")
    
    info = f"""🔧 Debug dos Grupos

Grupos configurados:
• GROUP_VIP_ID: {GROUP_VIP_ID}
• GROUP_FREE_ID: {GROUP_FREE_ID}
• STORAGE_GROUP_ID: {STORAGE_GROUP_ID}
• STORAGE_GROUP_FREE_ID: {STORAGE_GROUP_FREE_ID}

Chat atual: {update.effective_chat.id}

Variáveis ENV:
• Group_VIP_ID: {os.getenv('Group_VIP_ID', 'não definido')}
• GROUP_VIP_ID: {os.getenv('GROUP_VIP_ID', 'não definido')}"""
    
    await update.effective_message.reply_text(info)

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

# =========================
# COMANDOS DE GERENCIAMENTO DE PAGAMENTOS E VIP
# =========================

async def listar_hashes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todas as hashes de pagamento cadastradas"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    with SessionLocal() as s:
        try:
            payments = s.query(Payment).order_by(Payment.created_at.desc()).all()
            
            if not payments:
                # Resetar sequence do auto-increment quando não houver payments
                try:
                    from sqlalchemy import text
                    s.execute(text("ALTER SEQUENCE payments_id_seq RESTART WITH 1;"))
                    s.commit()
                except Exception:
                    pass  # Ignorar se não conseguir resetar
                
                return await update.effective_message.reply_text("📋 Nenhuma hash cadastrada.")
            
            # Paginar resultados (máximo 10 por página)
            page = 1
            if context.args:
                try:
                    page = int(context.args[0])
                    if page < 1: page = 1
                except:
                    page = 1
            
            per_page = 10
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_payments = payments[start_idx:end_idx]
            
            total_pages = (len(payments) + per_page - 1) // per_page
            
            msg_lines = [f"📋 <b>HASHES CADASTRADAS</b> (Página {page}/{total_pages})\n"]
            
            for p in page_payments:
                status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(p.status, "❓")
                username_info = f"@{p.username}" if p.username else f"ID:{p.user_id}"
                
                # Converter UTC para horário local brasileiro
                if p.created_at:
                    # Assumir que created_at está em UTC e converter para BRT (UTC-3)
                    import pytz
                    utc_dt = p.created_at.replace(tzinfo=pytz.UTC)
                    brt_dt = utc_dt.astimezone(pytz.timezone('America/Sao_Paulo'))
                    created = brt_dt.strftime("%d/%m/%Y %H:%M BRT")
                else:
                    created = "N/A"
                
                # Buscar VIP associado a esta hash ou usuário
                vip_info = ""
                if p.status == "approved":
                    # Primeiro tentar por hash
                    vip = s.query(VipMembership).filter(VipMembership.tx_hash == p.tx_hash).first()
                    
                    # Se não encontrar por hash, tentar por user_id (VIP pode existir sem hash vinculada)
                    if not vip and p.user_id:
                        vip = s.query(VipMembership).filter(
                            VipMembership.user_id == p.user_id,
                            VipMembership.active == True
                        ).order_by(VipMembership.expires_at.desc()).first()
                    
                    if vip:
                        now = now_utc()
                        if vip.expires_at and vip.active:
                            # Garantir que ambas as datas tenham timezone
                            expires_at = vip.expires_at
                            if expires_at.tzinfo is None:
                                expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
                            
                            if expires_at > now:
                                days_left = (expires_at - now).days
                                hours_left = ((expires_at - now).total_seconds() / 3600) % 24
                                expires_brt = expires_at.astimezone(pytz.timezone('America/Sao_Paulo'))
                            
                            # Mostrar tempo mais preciso
                            if days_left > 0:
                                time_left = f"{days_left} dias restantes"
                            elif hours_left > 0:
                                time_left = f"{int(hours_left)} horas restantes"
                            else:
                                time_left = "expira em breve"
                                
                            vip_info = f"\n👑 VIP Ativo: {time_left}\n📅 Expira: {expires_brt.strftime('%d/%m/%Y às %H:%M BRT')}"
                            
                            # Usar informações do pagamento se disponível
                            if p.vip_days:
                                vip_info += f"\n🎯 VIP atribuído: {p.vip_days} dias"
                            else:
                                # Fallback para plano salvo no VIP
                                plan_names = {
                                    "mensal": "30 dias",
                                    "bimestral": "60 dias", 
                                    "trimestral": "180 dias",
                                    "anual": "365 dias"
                                }
                                plan_desc = plan_names.get(vip.plan, vip.plan or "indefinido")
                                vip_info += f"\n🎯 Plano: {plan_desc}"
                        else:
                            # VIP expirado - mostrar quando expirou
                            if vip.expires_at:
                                expires_brt = vip.expires_at.replace(tzinfo=pytz.UTC).astimezone(pytz.timezone('America/Sao_Paulo'))
                                vip_info = f"\n👑 VIP Expirado\n📅 Expirou: {expires_brt.strftime('%d/%m/%Y às %H:%M BRT')}"
                            else:
                                vip_info = f"\n👑 VIP Expirado (sem data)"
                    else:
                        # Payment aprovado mas VIP não encontrado
                        vip_info = f"\n⚠️ VIP não encontrado para este usuário"
                
                # Informações sobre pagamento (usar dados salvos)
                payment_info = ""
                chain_names = {
                    "0x1": "Ethereum", "0x38": "BSC", "0x89": "Polygon",
                    "ethereum": "Ethereum", "bsc": "BSC", "polygon": "Polygon"
                }
                chain_desc = chain_names.get(p.chain, p.chain or "unknown")
                
                if p.status == "approved" and p.token_symbol and p.usd_value:
                    # Usar informações salvas durante aprovação
                    try:
                        usd_val = float(p.usd_value)
                        # Calcular quantidade baseada no valor USD e símbolo
                        if p.amount and p.amount != "N/A":
                            amount_display = p.amount
                        else:
                            # Recalcular quantidade aproximada baseada no valor USD
                            try:
                                import asyncio
                                from payments import resolve_payment_usd_autochain
                                # Para display, usar estimativa baseada no valor salvo
                                if p.token_symbol == "BTCB":
                                    # Estimar quantidade BTCB baseada no USD salvo
                                    btc_price = 110000  # Preço aproximado
                                    amount_display = f"{usd_val/btc_price:.6f}"
                                else:
                                    amount_display = "~"
                            except:
                                amount_display = "~"
                        
                        payment_info = f"\n💰 Pago: {amount_display} {p.token_symbol} (${usd_val:.2f} USD) | {chain_desc}"
                    except:
                        payment_info = f"\n💰 {p.token_symbol or 'Token'} | {chain_desc}"
                elif p.amount:
                    payment_info = f"\n💰 Valor: {p.amount} | Rede: {chain_desc}"
                else:
                    payment_info = f"\n🔗 Rede: {chain_desc}"
                
                msg_lines.append(
                    f"{status_emoji} <b>Hash #{p.id}</b> | Status: <b>{p.status.upper()}</b>\n"
                    f"👤 {username_info}\n"
                    f"📅 {created}{payment_info}{vip_info}\n"
                    f"💳 <code>{p.tx_hash}</code>"
                )
            
            if total_pages > 1:
                msg_lines.append(f"\n📄 Use /listar_hashes {page+1} para próxima página")
            
            msg_text = "\n\n".join(msg_lines)
            await update.effective_message.reply_text(msg_text, parse_mode="HTML")
            
        except Exception as e:
            logging.exception("Erro ao listar hashes")
            await update.effective_message.reply_text(f"❌ Erro ao listar hashes: {e}")

async def excluir_hash_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Exclui uma hash específica do sistema"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    if not context.args:
        return await update.effective_message.reply_text(
            "❌ Uso: /excluir_hash <hash_ou_id>\n"
            "💡 Exemplos:\n"
            "   /excluir_hash 0x1a2b3c4d... (hash completa ou parcial)\n"
            "   /excluir_hash 5 (ID da hash)\n"
            "💡 Use /listar_hashes para ver as hashes disponíveis"
        )
    
    identifier = context.args[0].strip()
    
    with SessionLocal() as s:
        try:
            payment = None
            
            # Primeiro, tentar como ID numérico
            if identifier.isdigit():
                payment_id = int(identifier)
                payment = s.query(Payment).filter(Payment.id == payment_id).first()
            
            # Se não encontrou por ID, tentar por hash
            if not payment:
                payment = s.query(Payment).filter(
                    Payment.tx_hash.ilike(f"%{identifier}%")
                ).first()
            
            if not payment:
                return await update.effective_message.reply_text(
                    f"❌ Hash/ID não encontrado: <code>{identifier}</code>\n"
                    "💡 Use /listar_hashes para ver as hashes disponíveis",
                    parse_mode="HTML"
                )
            
            # Confirmar exclusão
            username_info = f"@{payment.username}" if payment.username else f"ID:{payment.user_id}"
            
            # Converter horário para BRT
            if payment.created_at:
                import pytz
                utc_dt = payment.created_at.replace(tzinfo=pytz.UTC)
                brt_dt = utc_dt.astimezone(pytz.timezone('America/Sao_Paulo'))
                created_time = brt_dt.strftime('%d/%m/%Y %H:%M BRT')
            else:
                created_time = 'N/A'
            
            confirm_msg = (
                f"⚠️ <b>CONFIRMAR EXCLUSÃO DE HASH</b>\n\n"
                f"🆔 ID: <b>#{payment.id}</b>\n"
                f"👤 Usuário: {username_info}\n"
                f"📅 Criado: {created_time}\n"
                f"🔗 Chain: {payment.chain or 'unknown'}\n"
                f"⚡ Status: <b>{payment.status.upper()}</b>\n"
                f"💳 Hash completa:\n<code>{payment.tx_hash}</code>\n\n"
                f"⚠️ Esta ação é <b>IRREVERSÍVEL</b>!\n"
                f"Responda <b>CONFIRMAR</b> para excluir ou <b>CANCELAR</b> para abortar."
            )
            
            # Salvar dados para confirmação
            context.user_data["delete_hash_id"] = payment.id
            context.user_data["delete_hash_value"] = payment.tx_hash
            context.user_data["awaiting_delete_confirm"] = True
            
            await update.effective_message.reply_text(confirm_msg, parse_mode="HTML")
            
        except Exception as e:
            logging.exception("Erro ao buscar hash para exclusão")
            await update.effective_message.reply_text(f"❌ Erro ao buscar hash: {e}")

async def listar_vips_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos os VIPs cadastrados com detalhes"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    with SessionLocal() as s:
        try:
            vips = s.query(VipMembership).order_by(VipMembership.expires_at.desc()).all()
            
            if not vips:
                return await update.effective_message.reply_text("👑 Nenhum VIP cadastrado.")
            
            # Paginar resultados
            page = 1
            if context.args:
                try:
                    page = int(context.args[0])
                    if page < 1: page = 1
                except:
                    page = 1
            
            per_page = 8
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            page_vips = vips[start_idx:end_idx]
            
            total_pages = (len(vips) + per_page - 1) // per_page
            
            msg_lines = [f"👑 <b>MEMBROS VIP</b> (Página {page}/{total_pages})\n"]
            
            now = now_utc()
            active_count = 0
            expired_count = 0
            
            for vip in page_vips:
                username_info = f"@{vip.username}" if vip.username else f"ID:{vip.user_id}"
                
                # Converter horários para BRT
                if vip.expires_at:
                    import pytz
                    utc_expires = vip.expires_at.replace(tzinfo=pytz.UTC)
                    brt_expires = utc_expires.astimezone(pytz.timezone('America/Sao_Paulo'))
                    expires_str = brt_expires.strftime("%d/%m/%Y %H:%M BRT")
                else:
                    expires_str = "N/A"
                    
                if vip.created_at:
                    import pytz
                    utc_created = vip.created_at.replace(tzinfo=pytz.UTC)
                    brt_created = utc_created.astimezone(pytz.timezone('America/Sao_Paulo'))
                    created_str = brt_created.strftime("%d/%m/%Y BRT")
                else:
                    created_str = "N/A"
                
                # Verificar status
                is_active = vip.active and vip.expires_at and vip.expires_at > now
                if is_active:
                    active_count += 1
                    status_emoji = "✅"
                    status_text = "ATIVO"
                else:
                    expired_count += 1
                    status_emoji = "❌" if vip.expires_at and vip.expires_at <= now else "⏸️"
                    status_text = "EXPIRADO" if vip.expires_at and vip.expires_at <= now else "INATIVO"
                
                # Calcular dias restantes
                if is_active:
                    # Garantir que ambas as datas tenham timezone
                    expires_at = vip.expires_at
                    if expires_at.tzinfo is None:
                        expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
                    days_left = (expires_at - now).days
                    time_info = f"⏰ {days_left} dias restantes"
                else:
                    time_info = "⏰ Expirado"
                
                msg_lines.append(
                    f"{status_emoji} <b>VIP #{vip.id}</b> | {status_text}\n"
                    f"👤 {username_info}\n"
                    f"📅 Expira: {expires_str}\n"
                    f"🎯 Criado: {created_str}\n"
                    f"⏰ {time_info}"
                )
            
            # Estatísticas
            msg_lines.insert(1, f"📊 Total: {len(vips)} | ✅ Ativos: {active_count} | ❌ Expirados: {expired_count}\n")
            
            if total_pages > 1:
                msg_lines.append(f"\n📄 Use /listar_vips {page+1} para próxima página")
            
            msg_text = "\n\n".join(msg_lines)
            await update.effective_message.reply_text(msg_text, parse_mode="HTML")
            
        except Exception as e:
            logging.exception("Erro ao listar VIPs")
            await update.effective_message.reply_text(f"❌ Erro ao listar VIPs: {e}")

async def processar_confirmacao_exclusao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Processa confirmação de exclusão de hash quando usuário responde CONFIRMAR/CANCELAR"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return
    
    # Verificar se está aguardando confirmação de exclusão
    if not context.user_data.get("awaiting_delete_confirm"):
        return
    
    msg_text = (update.effective_message.text or "").strip().upper()
    
    if msg_text == "CONFIRMAR":
        payment_id = context.user_data.get("delete_hash_id")
        hash_value = context.user_data.get("delete_hash_value")
        
        if not payment_id or not hash_value:
            await update.effective_message.reply_text("❌ Sessão expirada. Tente novamente.")
        else:
            with SessionLocal() as s:
                try:
                    payment = s.query(Payment).filter(Payment.id == payment_id).first()
                    
                    if not payment:
                        await update.effective_message.reply_text("❌ Payment não encontrado.")
                    else:
                        # Salvar info antes de excluir
                        payment_id = payment.id
                        username = payment.username
                        user_id = payment.user_id
                        
                        # Excluir payment
                        s.delete(payment)
                        
                        # Reorganizar IDs para preencher lacunas
                        _reorganize_payment_ids(s)
                        
                        s.commit()
                        
                        username_info = f"@{username}" if username else f"ID:{user_id}"
                        await update.effective_message.reply_text(
                            f"✅ <b>HASH EXCLUÍDA COM SUCESSO</b>\n\n"
                            f"🆔 ID: #{payment_id}\n"
                            f"👤 Usuário: {username_info}\n"
                            f"💳 Hash: <code>{hash_value}</code>\n\n"
                            f"A hash foi removida permanentemente do sistema.",
                            parse_mode="HTML"
                        )
                        
                except Exception as e:
                    s.rollback()
                    logging.exception("Erro ao excluir hash")
                    await update.effective_message.reply_text(f"❌ Erro ao excluir hash: {e}")
        
        # Limpar dados da sessão
        context.user_data.pop("delete_hash_id", None)
        context.user_data.pop("delete_hash_value", None)
        context.user_data.pop("awaiting_delete_confirm", None)
    
    elif msg_text == "CANCELAR":
        context.user_data.pop("delete_hash_id", None)
        context.user_data.pop("delete_hash_value", None)
        context.user_data.pop("awaiting_delete_confirm", None)
        await update.effective_message.reply_text("❌ Exclusão cancelada.")

async def chat_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra informações do chat atual para diagnóstico"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    chat = update.effective_chat
    if not chat:
        return await update.effective_message.reply_text("❌ Não foi possível obter informações do chat.")
    
    # Verificar se é grupo permitido
    is_allowed = _is_allowed_group(chat.id)
    
    # Obter configurações dos grupos
    storage_vip = STORAGE_GROUP_ID
    storage_free = STORAGE_GROUP_FREE_ID
    
    info_msg = (
        f"📊 <b>INFORMAÇÕES DO CHAT</b>\n\n"
        f"🆔 ID do Chat: <code>{chat.id}</code>\n"
        f"📝 Tipo: {chat.type}\n"
        f"🏷️ Título: {chat.title or 'N/A'}\n\n"
        f"⚙️ <b>CONFIGURAÇÕES DOS GRUPOS:</b>\n"
        f"📦 Storage VIP: <code>{storage_vip}</code>\n"
        f"📦 Storage FREE: <code>{storage_free}</code>\n\n"
        f"✅ <b>STATUS:</b>\n"
        f"{'✅ Permitido para /novopack' if is_allowed else '❌ NÃO permitido para /novopack'}\n\n"
        f"💡 <b>DIAGNÓSTICO:</b>\n"
        f"Para permitir /novopack neste grupo, configure:\n"
        f"<code>STORAGE_GROUP_ID={chat.id}</code>\n"
        f"ou\n"
        f"<code>STORAGE_GROUP_FREE_ID={chat.id}</code>"
    )
    
    await update.effective_message.reply_text(info_msg, parse_mode="HTML")

async def atualizar_comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Atualiza a lista de comandos do bot"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    try:
        from telegram import BotCommand
        
        # Lista completa de comandos organizados por categoria
        comandos = [
            # Comandos básicos
            BotCommand("start", "Iniciar o bot e ver instruções"),
            BotCommand("help", "Ajuda e lista de comandos"),
            BotCommand("status", "Ver seu status VIP atual"),
            
            # Comandos de pagamento
            BotCommand("checkout", "Acessar página de pagamento"),
            BotCommand("tx", "Verificar status de uma transação"),
            
            # Comandos de conteúdo (admins)
            BotCommand("novopack", "Criar novo pack VIP/FREE"),
            BotCommand("novopacvip", "Criar novo pack VIP rapidamente"),
            BotCommand("novopackfree", "Criar novo pack FREE rapidamente"),
            BotCommand("listar", "Listar todos os packs"),
            BotCommand("listar_pendentes", "Listar packs pendentes de envio"),
            BotCommand("excluir_pack", "Excluir um pack específico"),
            
            # Comandos administrativos
            BotCommand("listar_admins", "Listar todos os administradores"),
            BotCommand("add_admin", "Adicionar novo administrador"),
            BotCommand("rem_admin", "Remover administrador"),
            
            # Comandos de gerenciamento VIP
            BotCommand("vip_list", "Listar todos os membros VIP"),
            BotCommand("vip_addtime", "Adicionar tempo VIP para usuário"),
            BotCommand("vip_set", "Definir VIP para usuário"),
            BotCommand("vip_remove", "Remover VIP de usuário"),
            BotCommand("listar_vips", "Listar VIPs com detalhes completos"),
            
            # Comandos de pagamentos e hashes
            BotCommand("listar_hashes", "Listar todas as hashes cadastradas"),
            BotCommand("excluir_hash", "Excluir hash específica do sistema"),
            BotCommand("aprovar_tx", "Aprovar transação manualmente"),
            BotCommand("rejeitar_tx", "Rejeitar transação"),
            
            # Comandos de mensagens automáticas
            BotCommand("add_msg_vip", "Adicionar mensagem automática VIP"),
            BotCommand("add_msg_free", "Adicionar mensagem automática FREE"),
            BotCommand("list_msgs_vip", "Listar mensagens VIP"),
            BotCommand("list_msgs_free", "Listar mensagens FREE"),
            
            # Comandos utilitários
            BotCommand("chat_info", "Ver informações do chat atual"),
            BotCommand("limpar_chat", "Limpar mensagens do chat"),
            BotCommand("valor", "Configurar preços de pagamento"),
        ]
        
        # Atualizar comandos
        await context.bot.set_my_commands(comandos)
        
        total_comandos = len(comandos)
        await update.effective_message.reply_text(
            f"✅ <b>COMANDOS ATUALIZADOS</b>\n\n"
            f"📋 Total de comandos: <b>{total_comandos}</b>\n"
            f"🔄 Lista de comandos atualizada com sucesso!\n\n"
            f"💡 Os usuários agora verão todos os comandos disponíveis "
            f"quando digitarem / no chat.",
            parse_mode="HTML"
        )
        
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Erro ao atualizar comandos: {e}"
        )

async def reavaliar_pagamentos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reavalia pagamentos antigos com preços atuais da blockchain"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    try:
        with SessionLocal() as s:
            # Buscar pagamentos aprovados dos últimos 30 dias
            from datetime import datetime, timedelta
            import pytz
            
            thirty_days_ago = datetime.now(pytz.UTC) - timedelta(days=30)
            payments = s.query(Payment).filter(
                Payment.status == "approved",
                Payment.created_at >= thirty_days_ago
            ).order_by(Payment.created_at.desc()).all()
            
            if not payments:
                return await update.effective_message.reply_text(
                    "📋 Nenhum pagamento aprovado encontrado nos últimos 30 dias."
                )
            
            total_payments = len(payments)
            await update.effective_message.reply_text(
                f"🔄 <b>REAVALIAÇÃO DE PAGAMENTOS</b>\n\n"
                f"📊 Encontrados: {total_payments} pagamentos\n"
                f"⏳ Reavaliando com preços atuais...\n\n"
                f"💡 Isso pode levar alguns segundos...",
                parse_mode="HTML"
            )
            
            upgraded_count = 0
            unchanged_count = 0
            error_count = 0
            results = []
            
            for payment in payments[:10]:  # Limitar a 10 por vez para não sobrecarregar
                try:
                    from payments import resolve_payment_usd_autochain
                    from utils import choose_plan_from_usd
                    
                    # Obter valor atual na blockchain
                    ok, msg, current_usd, details = await resolve_payment_usd_autochain(
                        payment.tx_hash, force_refresh=True
                    )
                    
                    if ok and current_usd:
                        # Calcular VIP atual vs VIP que seria atribuído agora
                        current_days = payment.vip_days or 0
                        new_days = choose_plan_from_usd(current_usd)
                        old_usd = float(payment.usd_value) if payment.usd_value else 0
                        
                        short_hash = payment.tx_hash[:12] + "..."
                        username_info = f"@{payment.username}" if payment.username else f"ID:{payment.user_id}"
                        
                        if new_days and new_days > current_days:
                            # Upgrade disponível!
                            upgrade_days = new_days - current_days
                            results.append({
                                'type': 'upgrade',
                                'hash': short_hash,
                                'user': username_info,
                                'old_usd': old_usd,
                                'new_usd': current_usd,
                                'old_days': current_days,
                                'new_days': new_days,
                                'upgrade_days': upgrade_days,
                                'payment_id': payment.id
                            })
                            upgraded_count += 1
                        else:
                            # Sem mudança ou preço menor
                            results.append({
                                'type': 'unchanged',
                                'hash': short_hash,
                                'user': username_info,
                                'old_usd': old_usd,
                                'new_usd': current_usd,
                                'days': current_days
                            })
                            unchanged_count += 1
                    else:
                        error_count += 1
                        
                except Exception as e:
                    error_count += 1
                    logging.warning(f"Erro ao reavaliar payment {payment.id}: {e}")
            
            # Mostrar resultados
            msg_lines = [
                f"📊 <b>RESULTADOS DA REAVALIAÇÃO</b>\n",
                f"✅ Podem ser upgradados: {upgraded_count}",
                f"➖ Sem mudança: {unchanged_count}", 
                f"❌ Erros: {error_count}\n"
            ]
            
            if upgraded_count > 0:
                msg_lines.append(f"🚀 <b>UPGRADES DISPONÍVEIS:</b>\n")
                for result in [r for r in results if r['type'] == 'upgrade'][:5]:
                    msg_lines.append(
                        f"💰 {result['hash']} ({result['user']})\n"
                        f"   ${result['old_usd']:.2f} → ${result['new_usd']:.2f} USD\n"
                        f"   {result['old_days']} → {result['new_days']} dias (+{result['upgrade_days']})\n"
                    )
                
                if upgraded_count > 5:
                    msg_lines.append(f"... e mais {upgraded_count - 5} upgrades\n")
                    
                msg_lines.append(f"💡 Use /aplicar_upgrades para aplicar os upgrades")
            
            msg_text = "\n".join(msg_lines)
            await update.effective_message.reply_text(msg_text, parse_mode="HTML")
            
    except Exception as e:
        logging.exception("Erro ao reavaliar pagamentos")
        await update.effective_message.reply_text(f"❌ Erro ao reavaliar pagamentos: {e}")

async def aplicar_upgrades_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Aplica upgrades de VIP baseados em preços atuais"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    await update.effective_message.reply_text(
        f"⚠️ <b>APLICAR UPGRADES</b>\n\n"
        f"⚠️ Esta funcionalidade atualiza VIPs existentes com base em preços atuais.\n"
        f"💡 Use /reavaliar_pagamentos primeiro para ver quais upgrades estão disponíveis.\n\n"
        f"🔧 Funcionalidade em desenvolvimento...",
        parse_mode="HTML"
    )

async def atualizar_precos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Força atualização manual dos preços de fallback"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")
    
    await update.effective_message.reply_text(
        "🔄 <b>ATUALIZANDO PREÇOS</b>\n\n"
        "⏳ Buscando preços atuais no CoinGecko...",
        parse_mode="HTML"
    )
    
    try:
        from payments import _update_fallback_prices, FALLBACK_PRICES
        
        # Capturar preços antes
        old_btc = FALLBACK_PRICES.get("bitcoin", 0)
        old_eth = FALLBACK_PRICES.get("ethereum", 0)
        old_bnb = FALLBACK_PRICES.get("binancecoin", 0)
        
        # Atualizar
        _update_fallback_prices()
        
        # Verificar mudanças
        new_btc = FALLBACK_PRICES.get("bitcoin", 0)
        new_eth = FALLBACK_PRICES.get("ethereum", 0)  
        new_bnb = FALLBACK_PRICES.get("binancecoin", 0)
        
        result_lines = [
            "✅ <b>PREÇOS ATUALIZADOS</b>\n",
            f"₿ Bitcoin/BTCB: ${old_btc:,.0f} → ${new_btc:,.0f}",
            f"Ξ Ethereum: ${old_eth:,.0f} → ${new_eth:,.0f}",
            f"🔸 BNB: ${old_bnb:,.0f} → ${new_bnb:,.0f}",
            "",
            "💡 Próxima atualização automática em 30 minutos."
        ]
        
        await update.effective_message.reply_text(
            "\n".join(result_lines),
            parse_mode="HTML"
        )
        
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ <b>ERRO AO ATUALIZAR</b>\n\n"
            f"🔍 Detalhes: {str(e)}\n\n"
            f"💡 Tente novamente em alguns minutos.",
            parse_mode="HTML"
        )

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
        user = f"@{m.username}" if m.username else '-'
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
    plan_map = {30: VipPlan.MENSAL, 90: VipPlan.TRIMESTRAL, 180: VipPlan.SEMESTRAL, 365: VipPlan.ANUAL}
    plan = plan_map.get(dias)
    if not plan:
        return await update.effective_message.reply_text("Dias devem ser 30, 90, 180 ou 365 dias.")
    m = vip_upsert_start_or_extend(uid, None, None, plan)
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
    """Simulação REAL: Envia pack completo para VIP + preview para FREE"""
    if not (update.effective_user and is_admin(update.effective_user.id)): 
        return await update.effective_message.reply_text("Apenas admins.")
    
    # Enviar pack completo para VIP
    status_vip = await enviar_pack_vip_job(context)
    
    # Enviar preview + botão para FREE
    status_free = await enviar_pack_free_job(context)
    
    # Resposta combinada
    resultado = f"🎯 **Simulação Real Concluída**\n\n**VIP:** {status_vip}\n**FREE:** {status_free}"
    await update.effective_message.reply_text(resultado, parse_mode="Markdown")

async def simularfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia apenas preview + botão para grupo FREE"""
    if not (update.effective_user and is_admin(update.effective_user.id)): 
        return await update.effective_message.reply_text("Apenas admins.")
    
    status = await enviar_pack_free_job(context)
    await update.effective_message.reply_text(f"📱 **Free:** {status}", parse_mode="Markdown")

async def listar_packsvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    with SessionLocal() as s:
        # Debug: contar todos os packs (incluindo enviados)
        total_packs_vip = s.query(Pack).filter(Pack.tier == "vip").count()
        total_packs_all = s.query(Pack).count()
        
        logging.info(f"[listar_packsvip] Total VIP packs: {total_packs_vip}, Total all packs: {total_packs_all}")
        
        # Buscar todos os packs VIP (não apenas pendentes)
        all_vip_packs = (
            s.query(Pack)
            .filter(Pack.tier == "vip")
            .order_by(Pack.created_at.asc())
            .all()
        )
        
        # Buscar apenas os pendentes
        packs = (
            s.query(Pack)
            .filter(Pack.tier == "vip", Pack.sent.is_(False))
            .order_by(Pack.created_at.asc())
            .all()
        )

        # Se não há packs pendentes, mas há packs VIP, mostrar informação
        if not packs and all_vip_packs:
            lines = [f"📊 <b>Todos os packs VIP ({len(all_vip_packs)} total):</b>"]
            for p in all_vip_packs:
                previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
                docs = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
                status = "ENVIADO" if p.sent else "PENDENTE"
                ag = (
                    f" (agendado para {p.scheduled_for.strftime('%d/%m %H:%M')})"
                    if p.scheduled_for else ""
                )
                lines.append(
                    f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}{ag}"
                )
            await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
            raise ApplicationHandlerStop

        if not packs:
            await update.effective_message.reply_text(f"Nenhum pack VIP pendente.\n\n📊 Total VIP packs no banco: {total_packs_vip}\n📊 Total de todos os packs: {total_packs_all}")
            raise ApplicationHandlerStop  # garante que nada mais responda

        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status  = "ENVIADO" if p.sent else "PENDENTE"
            ag = (
                f" (agendado para {p.scheduled_for.strftime('%d/%m %H:%M')})"
                if p.scheduled_for else ""
            )
            lines.append(
                f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}{ag}"
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
            ag = (
                f" (agendado para {p.scheduled_for.strftime('%d/%m %H:%M')})"
                if p.scheduled_for else ""
            )
            lines.append(
                f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}{ag}"
            )
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
            
            # Reorganizar IDs dos packs para preencher lacunas
            _reorganize_pack_ids(s)
            
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
CHOOSE_TIER, TITLE, CONFIRM_TITLE, PREVIEWS, FILES, SCHEDULE, CONFIRM_SAVE = range(7)


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
    # Permitir uso no privado ou nos grupos de storage
    if chat.type != "private" and not _is_allowed_group(chat.id):
        try: username = BOT_USERNAME or (await application.bot.get_me()).username
        except Exception: username = None
        link = f"https://t.me/{username}?start=novopack" if username else ""
        await reply_with_retry(update.effective_message, f"Use este comando no privado comigo, por favor.\n{link}")
        return ConversationHandler.END
    context.user_data.clear()
    await reply_with_retry(update.effective_message, "Quer cadastrar em qual tier? Responda <b>vip</b> ou <b>free</b>.", parse_mode="HTML")
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
    if not _require_admin(update): await reply_with_retry(update.effective_message, "Apenas admins."); return ConversationHandler.END
    if update.effective_chat.type != "private": await reply_with_retry(update.effective_message, "Use este comando no privado comigo, por favor."); return ConversationHandler.END
    context.user_data.clear(); context.user_data["tier"] = "vip"
    await update.effective_message.reply_text("🧩 Novo pack VIP — envie o <b>título</b>.", parse_mode="HTML"); return TITLE

async def novopackfree_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update): await reply_with_retry(update.effective_message, "Apenas admins."); return ConversationHandler.END
    if update.effective_chat.type != "private": await reply_with_retry(update.effective_message, "Use este comando no privado comigo, por favor."); return ConversationHandler.END
    context.user_data.clear(); context.user_data["tier"] = "free"
    await update.effective_message.reply_text("🧩 Novo pack FREE — envie o <b>título</b>.", parse_mode="HTML"); return TITLE

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—"); previews = user_data.get("previews", []); files = user_data.get("files", []); tier = (user_data.get("tier") or "vip").upper(); scheduled_for = user_data.get("scheduled_for")
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
    
    # Obter dados da sessão
    title = context.user_data.get("title")
    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])
    tier = context.user_data.get("tier", "vip")
    
    # Debug: log dos dados
    logging.info(f"[novopack_confirm_save] Salvando pack: title='{title}', tier='{tier}', previews={len(previews)}, files={len(files)}")
    
    try:
        # Verificar se title não está vazio
        if not title:
            await update.effective_message.reply_text("❌ Erro: título do pack não encontrado. Use /cancelar e tente novamente.")
            return ConversationHandler.END
        
        # Criar o pack no banco
        logging.info(f"[novopack_confirm_save] Criando pack no banco...")
        p = create_pack(title=title, header_message_id=None, tier=tier, reorganize_ids=True)
        logging.info(f"[novopack_confirm_save] Pack criado com ID: {p.id}")
        
        # Adicionar previews
        for idx, it in enumerate(previews):
            logging.info(f"[novopack_confirm_save] Adicionando preview {idx+1}/{len(previews)}: {it.get('file_type')}")
            add_file_to_pack(
                p.id, it["file_id"], None, it["file_type"], "preview", 
                it.get("file_name"), it.get("src_chat_id"), it.get("src_message_id")
            )
        
        # Adicionar arquivos
        for idx, it in enumerate(files):
            logging.info(f"[novopack_confirm_save] Adicionando arquivo {idx+1}/{len(files)}: {it.get('file_type')}")
            add_file_to_pack(
                p.id, it["file_id"], None, it["file_type"], "file", 
                it.get("file_name"), it.get("src_chat_id"), it.get("src_message_id")
            )
        
        # Sucesso
        logging.info(f"[novopack_confirm_save] Pack '{title}' salvo com sucesso! ID: {p.id}")
        context.user_data.clear()
        await update.effective_message.reply_text(
            f"🎉 <b>{esc(title)}</b> cadastrado com sucesso em <b>{tier.upper()}</b>!\n\nID do pack: <code>{p.id}</code>", 
            parse_mode="HTML"
        )
        return ConversationHandler.END
        
    except Exception as e:
        # Log do erro completo
        logging.exception(f"[novopack_confirm_save] Erro ao salvar pack: {e}")
        await update.effective_message.reply_text(
            f"❌ Erro ao salvar o pack: {str(e)}\n\nTente novamente ou use /cancelar.", 
            parse_mode="HTML"
        )
        return CONFIRM_SAVE  # Volta para o estado de confirmação

async def novopack_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear(); await update.effective_message.reply_text("Operação cancelada."); return ConversationHandler.END

# =========================
# Pagamento / Verificação on-chain (JSON-RPC)
# =========================
HEX_0X = "0x"
TRANSFER_TOPIC = "0x40dDBD27F878d07808339F9965f013F1CBc2F812"  # keccak("Transfer(address,address,uint256)")

def _hex_to_int(h: Optional[str]) -> int:
    if not h: return 0
    return int(h, 16) if h.startswith(HEX_0X) else int(h)

def _to_wei(amount_native: float, decimals: int = 18) -> int:
    return int(round(amount_native * (10 ** decimals)))

PRICE_TOLERANCE = float(os.getenv("PRICE_TOLERANCE", "0.01"))  # 1%

PLAN_PRICE_USD = {
    VipPlan.TRIMESTRAL: 70.0,
    VipPlan.SEMESTRAL: 110.0,
    VipPlan.ANUAL: 179.0,
    VipPlan.MENSAL: 30.0,
}

def plan_from_amount(amount_usd: float) -> Optional[VipPlan]:
    for plan, price in PLAN_PRICE_USD.items():
        if abs(amount_usd - price) <= price * PRICE_TOLERANCE:
            return plan
    return None

async def fetch_price_usd() -> Optional[float]:
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            if TOKEN_CONTRACT:
                platform = COINGECKO_PLATFORM or "ethereum"
                url = (
                    "https://api.coingecko.com/api/v3/simple/token_price/"
                    f"{platform}?contract_addresses={TOKEN_CONTRACT}&vs_currencies=usd"
                )
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
                return data.get(TOKEN_CONTRACT.lower(), {}).get("usd")
            else:
                asset_id = COINGECKO_NATIVE_ID or CHAIN_NAME.lower()
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
async def rpc_call(method: str, params: list) -> Any:
    if not RPC_URL:
        raise RuntimeError("RPC_URL ausente")
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(RPC_URL, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        data = r.json()
        if "error" in data:
            raise RuntimeError(f"RPC error: {data['error']}")
        return data.get("result")

async def verify_native_payment(tx_hash: str) -> Dict[str, Any]:
    tx = await rpc_call("eth_getTransactionByHash", [tx_hash])
    if not tx: return {"ok": False, "reason": "Transação não encontrada"}
    to_addr = (tx.get("to") or "").lower()
    if to_addr != WALLET_ADDRESS:
        return {"ok": False, "reason": "Destinatário diferente da carteira configurada"}
    value_wei = _hex_to_int(tx.get("value"))
    min_wei = _to_wei(MIN_NATIVE_AMOUNT, 18)
    if value_wei < min_wei:
        return {"ok": False, "reason": f"Valor abaixo do mínimo ({MIN_NATIVE_AMOUNT})"}
    receipt = await rpc_call("eth_getTransactionReceipt", [tx_hash])
    if not receipt or receipt.get("status") != "0x1":
        return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
    current_block_hex = await rpc_call("eth_blockNumber", [])
    confirmations = _hex_to_int(current_block_hex) - _hex_to_int(receipt.get("blockNumber", "0x0"))
    if confirmations < MIN_CONFIRMATIONS:
        return {"ok": False, "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})"}
    return {
        "ok": True, "type": "native", "from": (tx.get("from") or "").lower(),
        "to": to_addr, "amount_wei": value_wei, "confirmations": confirmations
    }

def _topic_address(topic_hex: str) -> str:
    # topic é 32 bytes; endereço é os últimos 20 bytes
    if topic_hex.startswith(HEX_0X): topic_hex = topic_hex[2:]
    addr = "0x" + topic_hex[-40:]
    return addr.lower()

async def verify_erc20_payment(tx_hash: str) -> Dict[str, Any]:
    if not TOKEN_CONTRACT:
        return {"ok": False, "reason": "TOKEN_CONTRACT não configurado"}
    receipt = await rpc_call("eth_getTransactionReceipt", [tx_hash])
    if not receipt or receipt.get("status") != "0x1":
        return {"ok": False, "reason": "Transação não confirmada/sucesso ainda"}
    logs = receipt.get("logs", [])
    found = None
    for lg in logs:
        if (lg.get("address") or "").lower() != TOKEN_CONTRACT: continue
        topics = [t.lower() for t in lg.get("topics", [])]
        if not topics or topics[0] != TRANSFER_TOPIC: continue
        to_addr = _topic_address(topics[2]) if len(topics) >= 3 else ""
        if to_addr == WALLET_ADDRESS:
            amount = _hex_to_int(lg.get("data"))
            found = {"amount_raw": amount, "to": to_addr}; break
    if not found:
        return {"ok": False, "reason": "Nenhum Transfer para a carteira no contrato informado"}
    min_units = int(round(MIN_TOKEN_AMOUNT * (10 ** TOKEN_DECIMALS)))
    if found["amount_raw"] < min_units:
        return {"ok": False, "reason": f"Quantidade de token abaixo do mínimo ({MIN_TOKEN_AMOUNT})"}
    current_block_hex = await rpc_call("eth_blockNumber", [])
    confirmations = _hex_to_int(current_block_hex) - _hex_to_int(receipt.get("blockNumber", "0x0"))
    if confirmations < MIN_CONFIRMATIONS:
        return {"ok": False, "reason": f"Confirmações insuficientes ({confirmations}/{MIN_CONFIRMATIONS})"}
    return {"ok": True, "type": "erc20", "to": found["to"], "amount_raw": found["amount_raw"], "confirmations": confirmations}

# Cache simples para evitar validações repetidas
_HASH_CACHE = {}

async def verify_tx_any(tx_hash: str) -> Dict[str, Any]:
    # Verificar cache primeiro
    if tx_hash in _HASH_CACHE:
        logging.info(f"Hash {tx_hash[:10]}... encontrada em cache")
        return _HASH_CACHE[tx_hash]
    
    if TOKEN_CONTRACT:
        res = await verify_erc20_payment(tx_hash)
    else:
        res = await verify_native_payment(tx_hash)
    if res.get("ok"):
        price = await fetch_price_usd()
        if price is None:
            res["ok"] = False
            res["reason"] = "Falha ao obter cotação do ativo"
            return res
        if "amount_raw" in res:
            amount_native = res.get("amount_raw", 0) / (10 ** TOKEN_DECIMALS)
        else:
            amount_native = res.get("amount_wei", 0) / (10 ** 18)
        res["amount_usd"] = amount_native * price
        plan_days = infer_plan_days(amount_usd=res["amount_usd"])
        res["plan_days"] = plan_days
        if plan_days is None:
            res["reason"] = res.get("reason") or "Valor não corresponde a nenhum plano"
    
    # Cachear resultado
    _HASH_CACHE[tx_hash] = res
    # Limitar tamanho do cache
    if len(_HASH_CACHE) > 100:
        # Remover metade das entradas mais antigas
        old_keys = list(_HASH_CACHE.keys())[:50]
        for key in old_keys:
            del _HASH_CACHE[key]
    
    return res
    

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
        await dm(
    user.id,
    f"✅ Pagamento confirmado na rede {CHAIN_NAME}!\n"
    f"VIP válido até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).\n"
    f"Entre no VIP: {invite_link}",
    parse_mode=None
)

        await update.effective_message.reply_text("✅ Pagamento simulado com sucesso. Veja seu privado.")
    except Exception as e:
        await update.effective_message.reply_text(f"Simulado OK, mas falhou enviar convite: {e}")
        invite = await assign_and_send_invite(user.id, user.username, tx_hash)
        await dm(
    user.id,
    f"✅ Pagamento confirmado na rede {CHAIN_NAME}!\n"
    f"VIP válido até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).\n"
    f"Convite (válido 2h, uso único): {invite}",
    parse_mode=None
)



# helper: apagar mensagem depois de alguns segundos
async def delete_later(chat_id: int, message_id: int, seconds: int = 5):
    await asyncio.sleep(seconds)
    try:
        await application.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

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
    if not application or not application.job_queue:
        logging.warning("Application ou job_queue não disponível para reagendar packs")
        return
    
    # Remover jobs existentes
    removed_jobs = []
    for j in list(application.job_queue.jobs()):
        if j.name in {"daily_pack_vip", "daily_pack_free"}: 
            j.schedule_removal()
            removed_jobs.append(j.name)
    
    if removed_jobs:
        logging.info(f"Jobs removidos: {removed_jobs}")
    
    tz = pytz.timezone("America/Sao_Paulo")
    hhmm_vip  = cfg_get("daily_pack_vip_hhmm")  or "09:00"
    hhmm_free = cfg_get("daily_pack_free_hhmm") or "09:30"
    
    try:
        hv, mv = parse_hhmm(hhmm_vip); hf, mf = parse_hhmm(hhmm_free)
        
        # Agendar novos jobs
        application.job_queue.run_daily(enviar_pack_vip_job,  time=dt.time(hour=hv, minute=mv, tzinfo=tz), name="daily_pack_vip")
        application.job_queue.run_daily(enviar_pack_free_job, time=dt.time(hour=hf, minute=mf, tzinfo=tz), name="daily_pack_free")
        
        logging.info(f"✅ Jobs reagendados - VIP: {hhmm_vip}, FREE: {hhmm_free} (America/Sao_Paulo)")
    except Exception as e:
        logging.error(f"Erro ao reagendar jobs: {e}")

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
    if not (update.effective_user and is_admin(update.effective_user.id)): 
        return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: 
        return await update.effective_message.reply_text("Uso: /set_pack_horario_vip HH:MM")
    
    try:
        hhmm = context.args[0]
        parse_hhmm(hhmm)  # Validar formato
        
        # Salvar configuração
        cfg_set("daily_pack_vip_hhmm", hhmm)
        
        # Reagendar jobs
        await _reschedule_daily_packs()
        
        # Verificar se foi salvo corretamente
        saved_time = cfg_get("daily_pack_vip_hhmm")
        
        await update.effective_message.reply_text(
            f"✅ Horário diário dos packs VIP atualizado!\n"
            f"🕒 Novo horário: {saved_time}\n"
            f"📅 Próximo envio: Hoje às {saved_time} (Horário de Brasília)\n"
            f"🔄 Jobs reagendados com sucesso!"
        )
        
        logging.info(f"Horário VIP alterado para {hhmm} pelo usuário {update.effective_user.id}")
        
    except Exception as e: 
        await update.effective_message.reply_text(f"❌ Hora inválida: {e}")
        logging.error(f"Erro ao alterar horário VIP: {e}")

async def set_pack_horario_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): 
        return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: 
        return await update.effective_message.reply_text("Uso: /set_pack_horario_free HH:MM")
    
    try:
        hhmm = context.args[0]
        parse_hhmm(hhmm)  # Validar formato
        
        # Salvar configuração
        cfg_set("daily_pack_free_hhmm", hhmm)
        
        # Reagendar jobs
        await _reschedule_daily_packs()
        
        # Verificar se foi salvo corretamente
        saved_time = cfg_get("daily_pack_free_hhmm")
        
        await update.effective_message.reply_text(
            f"✅ Horário diário dos packs FREE atualizado!\n"
            f"🕒 Novo horário: {saved_time}\n"
            f"📅 Próximo envio: Hoje às {saved_time} (Horário de Brasília)\n"
            f"🔄 Jobs reagendados com sucesso!"
        )
        
        logging.info(f"Horário FREE alterado para {hhmm} pelo usuário {update.effective_user.id}")
        
    except Exception as e: 
        await update.effective_message.reply_text(f"❌ Hora inválida: {e}")
        logging.error(f"Erro ao alterar horário FREE: {e}")

async def listar_jobs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos os jobs ativos para debug"""
    if not (update.effective_user and is_admin(update.effective_user.id)): 
        return await update.effective_message.reply_text("Apenas admins.")
    
    if not application or not application.job_queue:
        return await update.effective_message.reply_text("❌ Job queue não disponível.")
    
    jobs = list(application.job_queue.jobs())
    
    if not jobs:
        return await update.effective_message.reply_text("📋 Nenhum job ativo.")
    
    # Separar jobs por tipo
    pack_jobs = []
    other_jobs = []
    
    for job in jobs:
        if job.name and ("daily_pack" in job.name):
            pack_jobs.append(job)
        else:
            other_jobs.append(job)
    
    lines = ["📋 <b>JOBS ATIVOS</b>\n"]
    
    # Jobs de packs
    if pack_jobs:
        lines.append("📦 <b>JOBS DE PACKS:</b>")
        for job in pack_jobs:
            if hasattr(job, 'next_t') and job.next_t:
                next_run = job.next_t.astimezone(pytz.timezone("America/Sao_Paulo")).strftime("%d/%m %H:%M BRT")
                lines.append(f"• {job.name}: próximo em {next_run}")
            else:
                lines.append(f"• {job.name}: horário não definido")
        lines.append("")
    
    # Outros jobs
    if other_jobs:
        lines.append("🔧 <b>OUTROS JOBS:</b>")
        for job in other_jobs[:10]:  # Limitar para não enviar mensagem muito longa
            if hasattr(job, 'next_t') and job.next_t:
                next_run = job.next_t.astimezone(pytz.timezone("America/Sao_Paulo")).strftime("%d/%m %H:%M")
                lines.append(f"• {job.name or 'unnamed'}: {next_run}")
        if len(other_jobs) > 10:
            lines.append(f"• ... e mais {len(other_jobs) - 10} jobs")
    
    lines.append(f"\n📊 Total: {len(jobs)} jobs ativos")
    
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def enviar_pack_agora_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Força envio imediato de pack VIP ou FREE"""
    if not (update.effective_user and is_admin(update.effective_user.id)): 
        return await update.effective_message.reply_text("Apenas admins.")
    
    if not context.args or context.args[0].lower() not in ["vip", "free"]:
        return await update.effective_message.reply_text(
            "Uso: /enviar_pack_agora <vip|free>\n"
            "Exemplo: /enviar_pack_agora vip"
        )
    
    tier = context.args[0].lower()
    
    try:
        await update.effective_message.reply_text(f"🚀 Enviando packs {tier.upper()} agora...")
        
        if tier == "vip":
            result = await enviar_pack_vip_job(context)
            target = "VIP"
        else:
            result = await enviar_pack_free_job(context)
            target = "FREE"
        
        await update.effective_message.reply_text(
            f"✅ Envio manual concluído para {target}!\n"
            f"📄 Resultado: {result}"
        )
        
        logging.info(f"Envio manual de pack {tier} executado pelo usuário {update.effective_user.id}")
        
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Erro no envio manual: {e}")
        logging.error(f"Erro no envio manual de pack {tier}: {e}")

async def listar_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando unificado para listar todos os packs (VIP e FREE)"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    with SessionLocal() as s:
        # Buscar todos os packs ordenados por tier e created_at
        all_packs = (
            s.query(Pack)
            .order_by(Pack.tier.desc(), Pack.created_at.asc())  # VIP primeiro, depois FREE
            .all()
        )
        
        if not all_packs:
            await update.effective_message.reply_text("Nenhum pack cadastrado.")
            return

        # Separar por tier
        vip_packs = [p for p in all_packs if p.tier == "vip"]
        free_packs = [p for p in all_packs if p.tier == "free"]
        
        lines = [f"📦 <b>PACKS CADASTRADOS</b>\n"]
        
        # Seção VIP
        if vip_packs:
            lines.append(f"👑 <b>VIP ({len(vip_packs)} packs):</b>")
            for idx, p in enumerate(vip_packs, 1):
                previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
                docs = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
                status = "✅ ENVIADO" if p.sent else "⏳ PENDENTE"
                
                lines.append(
                    f"[{idx}] {esc(p.title)} — {status}\n"
                    f"    📷 {previews} previews | 📄 {docs} arquivos\n"
                    f"    📅 {p.created_at.strftime('%d/%m %H:%M')}"
                )
        else:
            lines.append("👑 <b>VIP:</b> Nenhum pack")
            
        lines.append("")  # Linha em branco
        
        # Seção FREE
        if free_packs:
            lines.append(f"🆓 <b>FREE ({len(free_packs)} packs):</b>")
            vip_count = len(vip_packs)  # Para continuar numeração após VIP
            for idx, p in enumerate(free_packs, vip_count + 1):
                previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
                docs = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
                status = "✅ ENVIADO" if p.sent else "⏳ PENDENTE"
                
                lines.append(
                    f"[{idx}] {esc(p.title)} — {status}\n"
                    f"    📷 {previews} previews | 📄 {docs} arquivos\n"
                    f"    📅 {p.created_at.strftime('%d/%m %H:%M')}"
                )
        else:
            lines.append("🆓 <b>FREE:</b> Nenhum pack")
        
        # Informações de agendamento
        lines.append(f"\n⏰ <b>HORÁRIOS DE ENVIO:</b>")
        vip_horario = cfg_get("daily_pack_vip_hhmm") or "09:00"
        free_horario = cfg_get("daily_pack_free_hhmm") or "09:30"
        lines.append(f"👑 VIP: {vip_horario} (diário)")
        lines.append(f"🆓 FREE: {free_horario} (diário)")
        
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

EXCLUIR_TODOS_CONFIRM = 2

async def excluir_todos_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para excluir TODOS os packs (VIP e FREE) com confirmação"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    
    with SessionLocal() as session:
        # Contar packs
        all_packs = session.query(Pack).all()
        vip_count = session.query(Pack).filter(Pack.tier == "vip").count()
        free_count = session.query(Pack).filter(Pack.tier == "free").count()
        total = len(all_packs)
        
        if total == 0:
            return await update.effective_message.reply_text("❌ Não há packs para excluir.")
        
        # Solicitar confirmação
        await update.effective_message.reply_text(
            f"⚠️ <b>ATENÇÃO - EXCLUSÃO EM MASSA</b>\n\n"
            f"Você está prestes a excluir <b>TODOS</b> os packs:\n"
            f"👑 VIP: {vip_count} packs\n"
            f"🆓 FREE: {free_count} packs\n"
            f"📦 Total: {total} packs\n\n"
            f"⚠️ <b>Esta ação é IRREVERSÍVEL!</b>\n"
            f"Todos os arquivos e previews serão perdidos.\n\n"
            f"Para confirmar, digite: <code>EXCLUIR TUDO</code>",
            parse_mode="HTML"
        )
        
        # Salvar dados para confirmação
        context.user_data["excluir_todos_count"] = total
        context.user_data["excluir_todos_vip"] = vip_count
        context.user_data["excluir_todos_free"] = free_count
        
        return EXCLUIR_TODOS_CONFIRM

async def excluir_todos_packs_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirmação para exclusão de todos os packs"""
    resposta = (update.effective_message.text or "").strip()
    
    if resposta != "EXCLUIR TUDO":
        await update.effective_message.reply_text(
            "❌ Confirmação incorreta. Operação cancelada.\n"
            "Para confirmar, digite exatamente: <code>EXCLUIR TUDO</code>",
            parse_mode="HTML"
        )
        return ConversationHandler.END
    
    # Dados da confirmação
    total = context.user_data.get("excluir_todos_count", 0)
    vip_count = context.user_data.get("excluir_todos_vip", 0)
    free_count = context.user_data.get("excluir_todos_free", 0)
    
    try:
        with SessionLocal() as session:
            # Excluir todos os packs (cascade deleta PackFiles automaticamente)
            all_packs = session.query(Pack).all()
            
            if not all_packs:
                await update.effective_message.reply_text("❌ Nenhum pack encontrado para excluir.")
                return ConversationHandler.END
            
            # Excluir todos
            for pack in all_packs:
                session.delete(pack)
            
            # Reorganizar IDs (resetar sequência)
            _reorganize_pack_ids(session)
            
            session.commit()
            
            await update.effective_message.reply_text(
                f"✅ <b>EXCLUSÃO CONCLUÍDA!</b>\n\n"
                f"📊 Excluídos:\n"
                f"👑 VIP: {vip_count} packs\n"
                f"🆓 FREE: {free_count} packs\n"
                f"📦 Total: {total} packs\n\n"
                f"🔄 IDs reorganizados - próximo pack será #1",
                parse_mode="HTML"
            )
            
    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Erro ao excluir packs: {str(e)}\n"
            f"Operação cancelada por segurança."
        )
    
    # Limpar dados temporários
    context.user_data.pop("excluir_todos_count", None)
    context.user_data.pop("excluir_todos_vip", None)
    context.user_data.pop("excluir_todos_free", None)
    
    return ConversationHandler.END

# =========================
# Error handler global
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle errors gracefully, especially timeout errors in production"""
    error = context.error
    
    # Handle timeout errors more gracefully
    if isinstance(error, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)):
        logging.warning(f"Timeout error (expected in production): {error}")
        return
    
    # Handle Telegram timeout errors
    from telegram.error import TimedOut, NetworkError
    if isinstance(error, (TimedOut, NetworkError)):
        logging.warning(f"Telegram network error (expected in production): {error}")
        return
    
    # For other errors, log with full traceback
    logging.exception("Erro não tratado", exc_info=error)

# =========================
# Webhooks + Keepalive
# =========================
@app.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    data   = await request.json()
    uid    = data.get("telegram_user_id") or data.get("uid")  # Fallback para 'uid'
    tx_hash= (data.get("tx_hash") or data.get("hash", "")).strip().lower()  # Fallback para 'hash'
    amount = data.get("amount")
    chain  = data.get("chain") or CHAIN_NAME
    
    # Log detalhado para debug
    logging.info(f"Webhook recebido - UID: {uid}, Hash: {tx_hash[:10] if tx_hash else 'None'}..., Amount: {amount}")

    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)

    try:
        res = await verify_tx_any(tx_hash)
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
                )
                s.add(pay)
            else:
                pay.status = "approved" if approved else "pending"
                pay.decided_at = now_utc() if approved else None
            s.commit()
        except Exception:
            s.rollback()
            raise

     # Se aprovado, renova VIP conforme plano e manda convite
    if approved:
        try:
            # melhor esforço para obter username atual do usuário específico
            username = None
            try:
                u = await application.bot.get_chat(int(uid))
                username = u.username or u.first_name or f"user_{uid}"
                logging.info(f"Username obtido para UID {uid}: {username}")
            except Exception as e:
                logging.warning(f"Erro ao obter dados do usuário {uid}: {e}")
                username = f"user_{uid}"

            # Garantir que VIP seja atribuído ao usuário correto
            user_id_final = int(uid)
            vip_upsert_start_or_extend(user_id_final, username, tx_hash, plan)
            invite_link = await create_and_store_personal_invite(user_id_final)
            
            # Notificar o usuário específico que fez o pagamento
            await application.bot.send_message(
                chat_id=user_id_final,
                text=(f"✅ Pagamento confirmado para {username}!\n"
                      f"Seu VIP foi ativado por {PLAN_DAYS[plan]} dias.\n"
                      f"Entre no VIP: {invite_link}")
            )
            logging.info(f"[WEBHOOK] VIP ativado para usuário {user_id_final} ({username})")
            
            # Log adicional para debug
            logging.info(f"[WEBHOOK] Pagamento processado - Hash: {tx_hash[:10]}..., Usuario: {user_id_final}, Plano: {PLAN_DAYS[plan]} dias")

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

@app.get("/pay/")
async def checkout_page():
    """Serve the checkout page"""
    try:
        import os
        webapp_path = os.path.join(os.path.dirname(__file__), "webapp", "index.html")
        if os.path.exists(webapp_path):
            return FileResponse(webapp_path, media_type="text/html")
        else:
            return HTMLResponse("""
                <html><head><title>Checkout</title></head>
                <body><h1>Checkout page not found</h1>
                <p>Please ensure webapp/index.html exists</p></body></html>
            """, status_code=404)
    except Exception as e:
        return HTMLResponse(f"<html><body><h1>Error: {e}</h1></body></html>", status_code=500)

@app.get("/vip_pricing")
async def get_vip_pricing():
    """Endpoint para webapp obter informações de preços VIP - agora usa faixas dinâmicas"""
    # Retorna faixas de valor em vez de preços fixos
    return {
        "wallet_address": WALLET_ADDRESS,
        "value_tiers": {
            "$0.10 - $0.99": "30 dias",
            "$1.00 - $4.99": "60 dias", 
            "$5.00 - $14.99": "180 dias",
            "$15.00+": "365 dias"
        },
        "min_confirmations": 3
    }

@app.post("/process_payment")
async def process_payment(request: Request):
    """API endpoint para processar pagamentos do checkout"""
    try:
        data = await request.json()
        tx_hash = data.get("tx_hash", "").strip()
        user_id = data.get("user_id")
        
        if not tx_hash or not user_id:
            return JSONResponse({"error": "tx_hash and user_id required"}, status_code=400)
        
        # Aqui você pode adicionar a lógica de processamento
        # Por enquanto, retorna sucesso básico
        return JSONResponse({
            "status": "received", 
            "message": "Transaction hash received and will be processed"
        })
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)

@app.get("/api/config")
async def api_config(uid: str = None, ts: str = None, sig: str = None):
    """Endpoint /api/config para webapp obter configurações de pagamento"""
    try:
        from utils import make_link_sig
        
        # Permitir acesso sem autenticação
        if uid and ts and sig:
            # Se parâmetros fornecidos, validar
            try:
                uid_int = int(uid)
                ts_int = int(ts)
            except ValueError:
                raise HTTPException(status_code=400, detail="uid/ts devem ser números")
                
            # Verificar se o timestamp não é muito antigo (ex: máximo 1 hora)
            import time
            now = int(time.time())
            if abs(now - ts_int) > 3600:  # 1 hora
                raise HTTPException(status_code=400, detail="Link expirado")
                
            # Validar assinatura
            expected_sig = make_link_sig(BOT_SECRET or "default", uid_int, ts_int)
            if sig != expected_sig:
                raise HTTPException(status_code=403, detail="Assinatura inválida")
        
        # Obter configurações (sempre disponível) - preços mínimos para compatibilidade com webapp
        value_tiers = {
            "30": 0.10,   # Mínimo para 1 mês
            "60": 1.00,   # Mínimo para 2 meses
            "180": 5.00,  # Mínimo para 6 meses
            "365": 15.00  # Mínimo para 1 ano
        }
        
        return {
            "wallet": WALLET_ADDRESS,
            "plans_usd": value_tiers,
            "networks": ["ETH", "BSC", "POLYGON", "ARBITRUM", "BASE"],
            "confirmations_min": 1
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erro em /api/config: {e}")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")

@app.post("/api/validate")
async def api_validate(request: Request):
    """Endpoint /api/validate para validar pagamentos"""
    try:
        from payments import approve_by_usd_and_invite
        
        data = await request.json()
        uid = data.get("uid")
        username = data.get("username")
        hash = data.get("hash", "").strip()
        
        # Log detalhado do que foi recebido
        logging.info(f"[API-VALIDATE] Recebido - UID: {uid}, Username: {username}, Hash: {hash[:10] if hash else 'None'}...")
        
        if not uid or not hash:
            raise HTTPException(status_code=400, detail="uid e hash são obrigatórios")
        
        # Garantir que UID seja numérico
        try:
            uid_int = int(uid)
        except (ValueError, TypeError):
            logging.error(f"[API-VALIDATE] UID inválido: {uid}")
            raise HTTPException(status_code=400, detail="UID deve ser um número válido")
        
        # Validação do hash
        if len(hash) < 40:
            return {"ok": False, "message": "Hash de transação inválido"}
        
        try:
            # Usar a função completa de aprovação com UID validado
            logging.info(f"[API-VALIDATE] Processando pagamento para UID: {uid_int}")
            ok, msg, payload = await approve_by_usd_and_invite(uid_int, username, hash, notify_user=False)
            
            if ok:
                return {
                    "ok": True,
                    "message": msg,
                    **payload  # Inclui invite, until, usd
                }
            else:
                return {
                    "ok": False,
                    "message": msg
                }
            
        except Exception as validation_error:
            logging.error(f"Erro na validação: {validation_error}")
            return {
                "ok": False, 
                "message": f"Erro na validação do pagamento: {str(validation_error)}"
            }
        
    except HTTPException:
        raise
    except Exception as e:
        logging.error(f"Erro em /api/validate: {e}")
        raise HTTPException(status_code=500, detail="Erro interno do servidor")


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
            texto = f"⚠️ Seu VIP expira em {dias} dia{'s' if dias > 1 else ''}. Renove através do botão de pagamento que aparece junto às imagens."
            await dm(m.user_id, texto)


async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    if not SELF_URL: return
    url = SELF_URL.rstrip("/") + "/keepalive"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url); logging.info(f"[keepalive] GET {url} -> {r.status_code}")
    except Exception as e: logging.warning(f"[keepalive] erro: {e}")

# ===== Guard global: só permite /tx para não-admin (em qualquer chat)
ALLOWED_NON_ADMIN = {"tx", "status", "novopack"}

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
    base = cmd[1:].split("@", 1)[0]  # ex: "/tx@MeuBot" -> "tx"

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
    
    # Inicializar esquema do banco apenas uma vez
    ensure_schema_once()
    
    # Debug das variáveis de ambiente críticas
    logging.info(f"🔧 Environment Debug:")
    logging.info(f"   BOT_TOKEN: {'✅ Set' if BOT_TOKEN and BOT_TOKEN != 'test_token' else '❌ Missing/Invalid'}")
    logging.info(f"   WEBHOOK_URL: {'✅ Set' if WEBHOOK_URL else '❌ Missing'}")
    logging.info(f"   DATABASE_URL: {'✅ Set' if os.getenv('DATABASE_URL') else '❌ Missing'}")
    logging.info(f"   WALLET_ADDRESS: {'✅ Set' if WALLET_ADDRESS else '❌ Missing'}")
    
    # Verificar se BOT_TOKEN está configurado
    if not BOT_TOKEN or BOT_TOKEN == "test_token":
        logging.error("❌ BOT_TOKEN não está configurado corretamente!")
        logging.error("   Configure BOT_TOKEN no Render com o token do seu bot do Telegram")
        return
    
    # Retry logic for bot initialization (common on cloud platforms)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            logging.info(f"Tentativa {attempt + 1}/{max_retries} de inicializar o bot...")
            logging.info(f"Token configurado: {'Sim' if BOT_TOKEN else 'Não'} (primeiros 10 chars: {BOT_TOKEN[:10] if BOT_TOKEN else 'N/A'}...)")
            
            # Inicializar o application primeiro com timeout estendido
            logging.info("Inicializando application...")
            await asyncio.wait_for(application.initialize(), timeout=60.0)
            
            # Depois inicializar o bot
            logging.info("Obtendo bot instance...")
            bot = application.bot
            
            # Por último, iniciar o application
            logging.info("Iniciando application...")
            await asyncio.wait_for(application.start(), timeout=60.0)
            
            logging.info("✅ Bot inicializado com sucesso!")
            break
            
        except asyncio.TimeoutError:
            logging.warning(f"Bot initialization attempt {attempt + 1}/{max_retries} timed out after 60 seconds")
        except Exception as e:
            logging.warning(f"Bot initialization attempt {attempt + 1}/{max_retries} failed: {e}")
            
        if attempt == max_retries - 1:
            logging.error("Falha na inicialização do bot após todas as tentativas.")
            # Não fazer raise para não quebrar o servidor
            return
        
        logging.info(f"Aguardando 10 segundos antes da próxima tentativa...")
        await asyncio.sleep(10)  # Wait longer between retries
    
    # Só configurar webhook se bot foi inicializado com sucesso
    if bot:
        # Set webhook with retry
        try:
            await bot.set_webhook(url=WEBHOOK_URL)
            logging.info(f"Webhook configurado: {WEBHOOK_URL}")
        except Exception as e:
            logging.warning(f"set_webhook falhou: {e}")
        
        # Get bot info with retry
        for attempt in range(3):
            try:
                me = await bot.get_me()
                BOT_USERNAME = me.username
                logging.info(f"Bot conectado: @{BOT_USERNAME}")
                break
            except Exception as e:
                logging.warning(f"get_me attempt {attempt + 1}/3 failed: {e}")
                if attempt == 2:
                    BOT_USERNAME = "UnknownBot"  # fallback
                else:
                    await asyncio.sleep(2)
        
        logging.info("Bot iniciado com sucesso (cripto + schedules + VIP/FREE).")
        
        # ==== Error handler (só se bot inicializou)
        application.add_error_handler(error_handler)
        
        # ==== TODOS OS HANDLERS SÓ EXECUTAM SE O BOT FOI INICIALIZADO COM SUCESSO ====
        
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

        # ===== Conversa /excluir_todos_packs
        excluir_todos_conv = ConversationHandler(
            entry_points=[CommandHandler("excluir_todos_packs", excluir_todos_packs_cmd)],
            states={EXCLUIR_TODOS_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, excluir_todos_packs_confirm)]},
            fallbacks=[CommandHandler("cancelar", lambda u, c: ConversationHandler.END)], 
            allow_reentry=True,
        )
        application.add_handler(excluir_todos_conv, group=0)

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
        application.add_handler(CommandHandler("debug_grupos", debug_grupos_cmd), group=1)

        application.add_handler(CommandHandler("say_vip", say_vip_cmd), group=1)
        application.add_handler(CommandHandler("say_free", say_free_cmd), group=1)

        application.add_handler(CommandHandler("simularvip", simularvip_cmd), group=1)
        application.add_handler(CommandHandler("simularfree", simularfree_cmd), group=1)
        application.add_handler(CommandHandler("listar_packs", listar_packs_cmd), group=1)
        application.add_handler(CommandHandler("pack_info", pack_info_cmd), group=1)
        application.add_handler(CommandHandler("excluir_item", excluir_item_cmd), group=1)
        application.add_handler(CommandHandler("set_pendentevip", set_pendentevip_cmd), group=1)
        application.add_handler(CommandHandler("set_pendentefree", set_pendentefree_cmd), group=1)
        application.add_handler(CommandHandler("set_enviadovip", set_enviadovip_cmd), group=1)
        application.add_handler(CommandHandler("set_enviadofree", set_enviadofree_cmd), group=1)

        application.add_handler(CommandHandler("listar_admins", listar_admins_cmd), group=1)
        application.add_handler(CommandHandler("add_admin", add_admin_cmd), group=1)
        application.add_handler(CommandHandler("rem_admin", rem_admin_cmd), group=1)
        
        # Comandos de gerenciamento de pagamentos e VIP
        application.add_handler(CommandHandler("listar_hashes", listar_hashes_cmd), group=1)
        application.add_handler(CommandHandler("excluir_hash", excluir_hash_cmd), group=1)
        application.add_handler(CommandHandler("listar_vips", listar_vips_cmd), group=1)
        application.add_handler(CommandHandler("chat_info", chat_info_cmd), group=1)
        application.add_handler(CommandHandler("atualizar_comandos", atualizar_comandos_cmd), group=1)
        application.add_handler(CommandHandler("reavaliar_pagamentos", reavaliar_pagamentos_cmd), group=1)
        application.add_handler(CommandHandler("aplicar_upgrades", aplicar_upgrades_cmd), group=1)
        application.add_handler(CommandHandler("atualizar_precos", atualizar_precos_cmd), group=1)
        
        # Handler para confirmações de exclusão de hash
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, processar_confirmacao_exclusao), group=2)
        application.add_handler(CommandHandler("mudar_nome", mudar_nome_cmd), group=1)
        application.add_handler(CommandHandler("limpar_chat", limpar_chat_cmd), group=1)

        application.add_handler(CommandHandler("valor", valor_cmd), group=1)
        application.add_handler(CommandHandler("vip_list", vip_list_cmd), group=1)
        application.add_handler(CommandHandler("vip_addtime", vip_addtime_cmd), group=1)
        application.add_handler(CommandHandler("vip_set", vip_set_cmd), group=1)
        application.add_handler(CommandHandler("vip_remove", vip_remove_cmd), group=1)

        # Comandos de pagamento crypto
        from payments import pagar_cmd, tx_cmd, listar_pendentes_cmd, aprovar_tx_cmd, rejeitar_tx_cmd
        application.add_handler(CommandHandler("pagar", pagar_cmd), group=1)
        application.add_handler(CommandHandler("checkout", pagar_cmd), group=1)  # alias
        application.add_handler(CommandHandler("tx", tx_cmd), group=1)
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
        application.add_handler(CommandHandler("listar_jobs", listar_jobs_cmd), group=1)
        application.add_handler(CommandHandler("enviar_pack_agora", enviar_pack_agora_cmd), group=1)

        # ===== Callback Query Handler
        application.add_handler(CallbackQueryHandler(checkout_callback_handler, pattern="checkout_callback"), group=1)

        # Jobs
        await _reschedule_daily_packs()
        _register_all_scheduled_messages(application.job_queue)

        application.job_queue.run_daily(vip_expiration_warn_job, time=dt.time(hour=9, minute=0, tzinfo=pytz.timezone("America/Sao_Paulo")), name="vip_warn")
        application.job_queue.run_repeating(keepalive_job, interval=dt.timedelta(minutes=4), first=dt.timedelta(seconds=20), name="keepalive")
        logging.info("Handlers e jobs registrados.")
    else:
        logging.error("Bot não foi inicializado - funcionalidades do Telegram não estarão disponíveis.")

# =========================
# Signal Handling para manter bot ativo
# =========================
import signal
import sys

def signal_handler(signum, frame):
    logging.warning(f"Recebido sinal {signum}. Bot continuará executando...")
    # Não fazer sys.exit() - ignorar sinais de interrupção
    pass

# =========================
# Run
# =========================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    # Configurar handlers de sinal para manter o bot ativo
    signal.signal(signal.SIGINT, signal_handler)   # Ctrl+C
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, signal_handler)  # Terminação
    
    logging.info("🤖 Bot configurado para ficar sempre ativo - ignorando sinais de interrupção")
    logging.info("📱 Para parar o bot, feche o terminal ou use Task Manager")
    
    try:
        # Usar configuração que reinicia automaticamente em caso de falha
        uvicorn.run(
            "main:app", 
            host="0.0.0.0", 
            port=PORT,
            access_log=True,
            reload=False,  # Desabilitar reload automático
            log_level="info"
        )
    except Exception as e:
        logging.error(f"Erro crítico no servidor: {e}")
        logging.info("Tentando reiniciar em 5 segundos...")
        import time
        time.sleep(5)
        # Tentar reiniciar
        os.system(f"python {sys.argv[0]}")  # Reiniciar o próprio script
