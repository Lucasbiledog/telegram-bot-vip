# main.py
import os
import logging
import asyncio
import datetime as dt
import time
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

from telegram import Update, Bot, InputMediaPhoto, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.error import BadRequest, RetryAfter, TimedOut, NetworkError
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
    MessageHandler,
    ChatMemberHandler,
)

from auto_sender import (
    SourceFile,
    SentFile,
    setup_auto_sender,
    setup_catalog,
    index_message_file,
    send_daily_vip_file,
    send_weekly_free_file,
    send_or_update_vip_catalog,
    get_stats,
    reset_sent_history,
    SOURCE_CHAT_ID
)
# === Imports ===
# Comandos de monitoramento para admin
try:
    from admin_stress_commands import (
        system_check_cmd,
        connectivity_check_cmd,
        stress_test_tokens_cmd,
        stress_test_status_cmd,
        register_monitoring_commands
    )
    MONITORING_COMMANDS_AVAILABLE = True
except ImportError:
    MONITORING_COMMANDS_AVAILABLE = False
    logging.warning("Comandos de monitoramento não disponíveis")

# Comandos de validação de pagamentos para admin
try:
    from vip_payment_stress_test import (
        vip_payment_test_cmd,
        vip_payment_quick_cmd
    )
    PAYMENT_VALIDATION_AVAILABLE = True
except ImportError:
    PAYMENT_VALIDATION_AVAILABLE = False
    logging.warning("Sistema de validação de pagamentos não disponível")

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

# Importar apenas User, PendingNotification e MemberLog do models.py
# Pack e Payment já estão definidos no main.py com mais campos
from models import User, PendingNotification, MemberLog, SupportTicket, Base as ModelsBase

# === Funções de Retry Automático ===
async def send_with_retry(func, *args, max_retries=3, **kwargs):
    """Executa função com retry automático para rate limits e erros de rede"""
    last_exception = None

    for attempt in range(max_retries):
        try:
            return await func(*args, **kwargs)
        except RetryAfter as e:
            if attempt < max_retries - 1:
                wait_time = e.retry_after + 1
                logging.warning(f"Rate limit hit, waiting {wait_time}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                last_exception = e
                continue
            else:
                raise e
        except (TimedOut, NetworkError) as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff
                logging.warning(f"Network error, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries}): {e}")
                await asyncio.sleep(wait_time)
                last_exception = e
                continue
            else:
                raise e
        except Exception as e:
            # Para outros erros, não fazer retry
            raise e

    # Se chegou aqui, todos os retries falharam
    raise last_exception
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

# Cache system para alta performance
from cache import (
    cache,
    cache_admin_list,
    get_cached_admin_list,
    cache_price,
    get_cached_price,
    cache_user_vip_status,
    get_cached_vip_status,
    invalidate_user_cache,
)

# Sistema de filas assíncronas para alta concorrência
from queue_system import (
    queue_manager,
    init_queue_system,
    queue_payment_validation,
    queue_pack_sending,
    queue_vip_notification,
    QueuePriority,
)

# Rate limiting inteligente para alta performance
from rate_limiter import (
    telegram_limiter,
    api_limiter,
    with_telegram_rate_limit,
    with_api_rate_limit,
    smart_delay,
    batch_with_rate_limit,
)

# Circuit Breaker pattern para proteção contra cascata de falhas
from circuit_breaker import (
    breaker_manager,
    get_database_breaker,
    get_telegram_api_breaker,
    get_coingecko_breaker,
    get_blockchain_rpc_breaker,
    get_payment_validation_breaker,
    with_circuit_breaker,
    with_database_protection,
    with_api_protection,
    health_check_with_breakers,
    CircuitBreakerError,
)

# Operações otimizadas em batch para alta performance
from batch_operations import (
    batch_processor,
    batch_send_messages,
    batch_validate_payments,
    batch_update_vip_status,
    bulk_notify_vip_expiration,
    bulk_process_pending_payments,
    bulk_cleanup_expired_data,
    get_batch_processor_stats,
    DatabaseBatchProcessor,
)

# Sistema de envio automático de arquivos
from auto_sender import (
    SourceFile,
    SentFile,
    setup_auto_sender,
    index_message_file,
    send_daily_vip_file,
    send_weekly_free_file,
    get_stats,
    reset_sent_history,
    SOURCE_CHAT_ID,
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
        
        # Try to use persistent volume (Render /opt/render/project/.data)
        # or fallback to /tmp
        persistent_paths = [
            "/opt/render/project/.data/telegram_bot.db",
            "/data/telegram_bot.db",
            "/tmp/telegram_bot.db"
        ]

        for db_path in persistent_paths:
            try:
                # Ensure directory exists
                db_dir = os.path.dirname(db_path)
                os.makedirs(db_dir, exist_ok=True)

                # Test write permission
                with open(db_path + ".test", 'w') as f:
                    f.write("test")
                os.remove(db_path + ".test")

                if db_path.startswith("/tmp"):
                    print(f"⚠️  TEMPORARY: Using SQLite at {db_path}")
                    print("   Data will be LOST on restart/redeploy!")
                else:
                    print(f"✅ PERSISTENT: Using SQLite at {db_path}")
                    print("   Data will be PRESERVED between restarts!")

                return f"sqlite:///{db_path}"
            except Exception as e:
                continue

        # Last resort: in-memory
        print("❌ CRITICAL: Cannot write to any persistent location!")
        print("   Falling back to IN-MEMORY database")
        print("   ALL DATA WILL BE LOST ON RESTART!")
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

# Configuração condicional baseada no tipo de banco
if url.drivername.startswith("sqlite"):
    # SQLite não suporta pool_size, max_overflow, pool_timeout
    engine = create_engine(
        url,
        future=True,
        echo=False,
        connect_args={"check_same_thread": False}
    )
else:
    # PostgreSQL e outros bancos suportam pooling
    connect_args = {}
    if url.drivername.startswith("postgresql"):
        connect_args = {
            "application_name": "telegram_bot",
            "connect_timeout": 30,  # Aumentado para 30s
            "sslmode": "prefer",  # prefer é mais tolerante que require
            "keepalives": 1,
            "keepalives_idle": 30,
            "keepalives_interval": 10,
            "keepalives_count": 5
        }

    engine = create_engine(
        url,
        pool_pre_ping=True,  # Testa conexão antes de usar
        future=True,
        pool_size=20,  # Reduzido de 50 para 20
        max_overflow=40,  # Reduzido de 100 para 40
        pool_timeout=30,  # Aumentado de 5 para 30s
        pool_recycle=3600,  # 1 hora (aumentado de 30min)
        echo=False,
        connect_args=connect_args,
        execution_options={
            "isolation_level": "READ COMMITTED"
        }
    )
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
# Usar a Base do models.py para que todas as tabelas sejam criadas juntas
Base = ModelsBase
# Configurar metadata do models.py para usar este engine
Base.metadata.bind = engine

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

def ensure_vip_notification_columns():
    """Adiciona colunas de notificação e remoção se não existirem"""
    try:
        logging.info("Iniciando migração das colunas de notificação VIP...")
        with engine.begin() as conn:
            # Adicionar coluna first_name se não existir
            try:
                conn.execute(text("ALTER TABLE vip_memberships ADD COLUMN first_name VARCHAR"))
                logging.info("✅ Coluna first_name adicionada")
            except Exception as e:
                logging.debug(f"Coluna first_name já existe ou erro: {e}")

            # Adicionar colunas de notificação
            try:
                conn.execute(text("ALTER TABLE vip_memberships ADD COLUMN notified_7_days BOOLEAN DEFAULT FALSE"))
                logging.info("✅ Coluna notified_7_days adicionada")
            except Exception as e:
                logging.debug(f"Coluna notified_7_days já existe ou erro: {e}")

            try:
                conn.execute(text("ALTER TABLE vip_memberships ADD COLUMN notified_3_days BOOLEAN DEFAULT FALSE"))
                logging.info("✅ Coluna notified_3_days adicionada")
            except Exception as e:
                logging.debug(f"Coluna notified_3_days já existe ou erro: {e}")

            try:
                conn.execute(text("ALTER TABLE vip_memberships ADD COLUMN notified_1_day BOOLEAN DEFAULT FALSE"))
                logging.info("✅ Coluna notified_1_day adicionada")
            except Exception as e:
                logging.debug(f"Coluna notified_1_day já existe ou erro: {e}")

            try:
                conn.execute(text("ALTER TABLE vip_memberships ADD COLUMN removal_scheduled BOOLEAN DEFAULT FALSE"))
                logging.info("✅ Coluna removal_scheduled adicionada")
            except Exception as e:
                logging.debug(f"Coluna removal_scheduled já existe ou erro: {e}")
            
            # Garantir que valores NULL sejam FALSE
            try:
                conn.execute(text("UPDATE vip_memberships SET notified_7_days = FALSE WHERE notified_7_days IS NULL"))
                conn.execute(text("UPDATE vip_memberships SET notified_3_days = FALSE WHERE notified_3_days IS NULL"))
                conn.execute(text("UPDATE vip_memberships SET notified_1_day = FALSE WHERE notified_1_day IS NULL"))
                conn.execute(text("UPDATE vip_memberships SET removal_scheduled = FALSE WHERE removal_scheduled IS NULL"))
                logging.info("✅ Valores NULL de notificação atualizados para FALSE")
            except Exception as e:
                logging.debug(f"Erro ao atualizar valores NULL (pode ser normal): {e}")
        
        logging.info("✅ Migração das colunas VIP concluída com sucesso!")
                
    except Exception as e:
        logging.error("❌ Falha ensure_vip_notification_columns: %s", e)
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
    VipPlan.MENSAL: 30,
    VipPlan.TRIMESTRAL: 90,     # Trimestral agora é 90 dias
    VipPlan.SEMESTRAL: 180,
    VipPlan.ANUAL: 365,
}

def init_db():
    """Inicializa banco de dados com retry automático"""
    import time
    max_retries = 3
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            logging.info(f"[DB] Tentativa {attempt + 1}/{max_retries} de conectar ao banco...")
            Base.metadata.create_all(bind=engine)
            logging.info(f"[DB] ✅ Conexão estabelecida com sucesso!")
            break
        except Exception as e:
            if attempt < max_retries - 1:
                logging.warning(f"[DB] ⚠️ Falha na tentativa {attempt + 1}: {e}")
                logging.info(f"[DB] 🔄 Aguardando {retry_delay}s antes de tentar novamente...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Exponential backoff
            else:
                logging.error(f"[DB] ❌ Todas as tentativas falharam!")
                raise

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
    if not cfg_get("daily_pack_free_hhmm"): cfg_set("daily_pack_free_hhmm", "10:00")

def ensure_critical_indexes():
    """Criar índices críticos para performance em larga escala"""
    try:
        with engine.begin() as conn:
            # Índices críticos para alta performance
            indexes = [
                # Payments - busca por hash é muito frequente
                "CREATE INDEX IF NOT EXISTS idx_payments_tx_hash ON payments(tx_hash)",
                "CREATE INDEX IF NOT EXISTS idx_payments_user_status ON payments(user_id, status)",
                "CREATE INDEX IF NOT EXISTS idx_payments_temp_user_id ON payments(temp_user_id)",
                "CREATE INDEX IF NOT EXISTS idx_payments_created_at ON payments(created_at DESC)",

                # VIP memberships - consultas frequentes por usuário e expiração
                "CREATE INDEX IF NOT EXISTS idx_vip_user_expires ON vip_memberships(user_id, expires_at)",
                "CREATE INDEX IF NOT EXISTS idx_vip_active_expires ON vip_memberships(active, expires_at) WHERE active = true",
                "CREATE INDEX IF NOT EXISTS idx_vip_expires_at ON vip_memberships(expires_at DESC)",

                # Packs - envio por tier e status
                "CREATE INDEX IF NOT EXISTS idx_packs_tier_sent ON packs(tier, sent, created_at)",
                "CREATE INDEX IF NOT EXISTS idx_packs_header_message ON packs(header_message_id)",
                "CREATE INDEX IF NOT EXISTS idx_packs_scheduled_for ON packs(scheduled_for)",

                # Pack files - busca por pack
                "CREATE INDEX IF NOT EXISTS idx_pack_files_pack_id ON pack_files(pack_id, id)",
                "CREATE INDEX IF NOT EXISTS idx_pack_files_src_msg ON pack_files(src_chat_id, src_message_id)",

                # Admins - verificação de admin é muito frequente
                "CREATE INDEX IF NOT EXISTS idx_admins_user_id ON admins(user_id)",

                # VIP notifications - evitar duplicatas
                "CREATE INDEX IF NOT EXISTS idx_vip_notifications_user_type ON vip_notifications(user_id, notification_type, created_at)",

                # Scheduled messages - execução por horário
                "CREATE INDEX IF NOT EXISTS idx_scheduled_messages_enabled_tier ON scheduled_messages(enabled, tier, hhmm)",
            ]

            for index_sql in indexes:
                try:
                    conn.execute(text(index_sql))
                    logging.debug(f"Índice criado/verificado: {index_sql.split('idx_')[1].split(' ')[0] if 'idx_' in index_sql else 'unknown'}")
                except Exception as idx_error:
                    # Índice já existe ou erro, continuar
                    logging.debug(f"Erro ao criar índice: {idx_error}")
                    pass

    except Exception as e:
        logging.warning(f"Erro ao criar índices críticos: {e}")
        pass

def ensure_schema():
    global engine, SessionLocal, url, DB_URL

    try:
        Base.metadata.create_all(bind=engine)
        ensure_bigint_columns()
        ensure_pack_tier_column()
        ensure_pack_scheduled_for_column()
        ensure_packfile_src_columns()
        ensure_vip_invite_column()
        ensure_vip_notification_columns()
        ensure_vip_plan_column()
        ensure_payment_fields()
        ensure_critical_indexes()  # Criar índices para alta performance
        
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
            # Configuração condicional baseada no tipo de banco
            if url.drivername.startswith("sqlite"):
                # SQLite não suporta pool_size, max_overflow, pool_timeout
                engine = create_engine(
                    url,
                    future=True,
                    echo=False,
                    connect_args={"check_same_thread": False}
                )
            else:
                # PostgreSQL e outros bancos suportam pooling
                connect_args = {}
                if url.drivername.startswith("postgresql"):
                    connect_args = {
                        "application_name": "telegram_bot",
                        "connect_timeout": 30,  # Aumentado para 30s
                        "sslmode": "prefer",  # prefer é mais tolerante que require
                        "keepalives": 1,
                        "keepalives_idle": 30,
                        "keepalives_interval": 10,
                        "keepalives_count": 5
                    }

                engine = create_engine(
                    url,
                    pool_pre_ping=True,  # Testa conexão antes de usar
                    future=True,
                    pool_size=20,  # Reduzido de 50 para 20
                    max_overflow=40,  # Reduzido de 100 para 40
                    pool_timeout=30,  # Aumentado de 5 para 30s
                    pool_recycle=3600,  # 1 hora (aumentado de 30min)
                    echo=False,
                    connect_args=connect_args,
                    execution_options={
                        "isolation_level": "READ COMMITTED"
                    }
                )
            SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

            try:
                Base.metadata.create_all(bind=engine)
                ensure_bigint_columns()
                ensure_pack_tier_column()
                ensure_packfile_src_columns()
                ensure_vip_invite_column()
                ensure_vip_notification_columns()
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
ALLOWED_FOR_NON_ADM = {"pagar", "tx", "start", "novopack", "novopackvip", "novopackfree", "getid", "comandos", "listar_comandos" }

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
    logging.info(f"[INVITE-DEBUG] Iniciando criação de convite para user_id: {user_id}")
    
    m = vip_get(user_id)
    if not m:
        raise RuntimeError(f"VIP não encontrado para user_id: {user_id}")
    if not m.active:
        raise RuntimeError(f"VIP inativo para user_id: {user_id}")
    if not m.expires_at:
        raise RuntimeError(f"VIP sem data de expiração para user_id: {user_id}")

    # Corrigir timezone se necessário antes de converter para timestamp
    expires_at = m.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
        # Atualizar no banco para evitar o problema no futuro
        with SessionLocal() as update_session:
            vm_update = update_session.query(VipMembership).filter(VipMembership.user_id == user_id).first()
            if vm_update:
                vm_update.expires_at = expires_at
                update_session.commit()
    
    expire_ts = int(expires_at.timestamp())
    logging.info(f"[INVITE-DEBUG] VIP válido até: {expires_at} (timestamp: {expire_ts})")

    try:
        # Verificar se o bot tem permissões no grupo
        try:
            chat_member = await application.bot.get_chat_member(GROUP_VIP_ID, application.bot.id)
            if not chat_member.can_invite_users:
                logging.error(f"[INVITE-DEBUG] Bot não tem permissão para convidar usuários no grupo {GROUP_VIP_ID}")
        except Exception as perm_error:
            logging.warning(f"[INVITE-DEBUG] Não foi possível verificar permissões: {perm_error}")
        
        invite = await application.bot.create_chat_invite_link(
            chat_id=GROUP_VIP_ID,
            expire_date=expires_at,  # Usar o datetime corrigido
            member_limit=1
        )
        logging.info(f"[INVITE-DEBUG] Convite criado: {invite.invite_link}")
    except Exception as e:
        logging.error(f"[INVITE-DEBUG] Erro ao criar convite no Telegram: {e}")
        logging.error(f"[INVITE-DEBUG] GROUP_VIP_ID: {GROUP_VIP_ID}")
        raise RuntimeError(f"Falha ao criar convite no Telegram: {e}")

    # Salvar no banco
    try:
        with SessionLocal() as s:
            vm = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
            if vm:
                vm.invite_link = invite.invite_link
                s.commit()
                logging.info(f"[INVITE-DEBUG] Convite salvo no banco para user_id: {user_id}")
            else:
                logging.warning(f"[INVITE-DEBUG] VipMembership não encontrado no banco para user_id: {user_id}")
    except Exception as e:
        logging.error(f"[INVITE-DEBUG] Erro ao salvar convite no banco: {e}")
        # Não falhar aqui, o convite já foi criado

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


async def log_to_group(text: str, parse_mode: Optional[str] = "HTML") -> bool:
    """
    Envia mensagem de log para o grupo de logs configurado.
    """
    if not LOGS_GROUP_ID or LOGS_GROUP_ID == 0:
        return False

    try:
        timestamp = dt.datetime.now().strftime("%d/%m/%Y %H:%M:%S")
        message = f"🤖 <b>Log do Sistema</b>\n📅 {timestamp}\n\n{text}"

        await application.bot.send_message(
            chat_id=LOGS_GROUP_ID,
            text=message,
            parse_mode=parse_mode
        )
        return True
    except Exception as e:
        logging.warning(f"Falha ao enviar log para grupo {LOGS_GROUP_ID}: {e}")
        return False


# =========================
# ENV / CONFIG
# =========================
load_dotenv()
BOT_TOKEN   = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SELF_URL    = os.getenv("SELF_URL")
LOCAL_MODE  = os.getenv("LOCAL_MODE", "false").lower() == "true"

# Payment Configuration
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "")
BOT_SECRET = os.getenv("BOT_SECRET", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
WEBAPP_LINK_SECRET = os.getenv("WEBAPP_LINK_SECRET", "")

STORAGE_GROUP_ID       = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))
GROUP_VIP_ID           = int(os.getenv("Group_VIP_ID", os.getenv("GROUP_VIP_ID", "-1003255098941")))
STORAGE_GROUP_FREE_ID  = int(os.getenv("STORAGE_GROUP_FREE_ID", "-1002509364079"))
GROUP_FREE_ID          = int(os.getenv("GROUP_FREE_ID", "-1002932075976"))
PACK_ADMIN_CHAT_ID     = int(os.getenv("PACK_ADMIN_CHAT_ID", "-1003080645605"))

# Novos IDs para sistema de envio automático
VIP_CHANNEL_ID         = int(os.getenv("VIP_CHANNEL_ID", str(GROUP_VIP_ID)))  # Usa mesmo ID do grupo VIP
FREE_CHANNEL_ID        = int(os.getenv("FREE_CHANNEL_ID", str(GROUP_FREE_ID)))  # Usa mesmo ID do grupo FREE
LOGS_GROUP_ID          = int(os.getenv("LOGS_GROUP_ID", "-5028443973"))  # Grupo para postar logs do sistema

PORT = int(os.getenv("PORT", 8000))

# Job prefixes
JOB_PREFIX_SM = "scheduled_msg_"

# =========================
# FASTAPI + PTB
# =========================

# Timestamp de início para métricas de uptime
start_time = time.time()

app = FastAPI(
    title="Telegram VIP Bot API",
    description="Bot Telegram para gerenciamento de conteúdo VIP com pagamentos em cripto",
    version="2.0.0"
)

@app.on_event("startup")
async def startup_event():
    """Inicialização de sistemas críticos"""
    global start_time
    start_time = time.time()

    try:
        # Inicializar cache Redis
        await cache.init_redis()

        # Inicializar sistema de filas
        await init_queue_system()

        # Atualizar cache de admins
        await refresh_admin_cache()

        # Configurar sistema de envio automático (passar classes de modelo)
        setup_auto_sender(VIP_CHANNEL_ID, FREE_CHANNEL_ID, SourceFile, SentFile)
        setup_catalog(cfg_get, cfg_set)
        logging.info(f"📤 Sistema de envio automático configurado - VIP: {VIP_CHANNEL_ID}, FREE: {FREE_CHANNEL_ID}")

        # Iniciar sistema keep-alive para manter bot ativo 24/7
        from keep_alive import keep_alive_ping
        SELF_URL = os.getenv("SELF_URL", "")
        if SELF_URL:
            logging.info("🔄 Sistema Keep-Alive iniciado (ping a cada 10 minutos)")
            asyncio.create_task(keep_alive_ping())
        else:
            logging.warning("⚠️ SELF_URL não configurada - sistema keep-alive não será iniciado")

        logging.info("✅ Sistemas de alta performance inicializados com sucesso")

    except Exception as e:
        logging.error(f"❌ Erro na inicialização dos sistemas: {e}")

@app.on_event("shutdown")
async def shutdown_event():
    """Limpeza na finalização"""
    try:
        # Parar sistema de filas
        await queue_manager.stop()
        logging.info("✅ Sistemas finalizados com sucesso")
    except Exception as e:
        logging.error(f"❌ Erro na finalização: {e}")

# Montar arquivos estáticos da webapp
import os
webapp_dir = os.path.join(os.path.dirname(__file__), "webapp")
if os.path.exists(webapp_dir):
    app.mount("/webapp", StaticFiles(directory=webapp_dir), name="webapp")

# =========================
# HEALTH CHECK & MONITORING ENDPOINTS
# =========================

@app.get("/health")
async def health_check():
    """Health check endpoint para load balancers e monitoring"""
    try:
        # Verificar conectividade do banco
        db_status = "healthy"
        try:
            with SessionLocal() as s:
                s.execute(text("SELECT 1"))
        except Exception as e:
            db_status = f"unhealthy: {str(e)[:100]}"

        # Verificar cache Redis
        cache_status = "healthy"
        try:
            await cache.set("health_check", "ok", 10)
            cache_result = await cache.get("health_check")
            if cache_result != "ok":
                cache_status = "unhealthy: cache test failed"
        except Exception as e:
            cache_status = f"fallback: {str(e)[:50]}"

        # Status do bot
        bot_status = "healthy" if application and application.bot else "unhealthy"

        # Status das filas
        queue_stats = queue_manager.get_stats() if queue_manager else {"error": "not initialized"}

        health_data = {
            "status": "healthy",
            "timestamp": dt.datetime.now().isoformat(),
            "version": "2.0.0",
            "services": {
                "database": db_status,
                "cache": cache_status,
                "bot": bot_status,
                "queues": queue_stats
            },
            "uptime_seconds": time.time() - start_time if 'start_time' in globals() else 0
        }

        # Se qualquer serviço crítico estiver down, retornar erro
        if db_status != "healthy" or bot_status != "healthy":
            health_data["status"] = "degraded"
            return JSONResponse(content=health_data, status_code=503)

        return JSONResponse(content=health_data, status_code=200)

    except Exception as e:
        return JSONResponse(
            content={
                "status": "unhealthy",
                "error": str(e),
                "timestamp": dt.datetime.now().isoformat()
            },
            status_code=503
        )

@app.get("/metrics")
async def metrics_endpoint():
    """Endpoint de métricas para Prometheus/monitoring"""
    try:
        with SessionLocal() as s:
            # Estatísticas do banco
            total_users = s.query(VipMembership).count()
            active_vips = s.query(VipMembership).filter(
                VipMembership.active == True,
                VipMembership.expires_at > now_utc()
            ).count()
            total_packs = s.query(Pack).count()
            pending_packs = s.query(Pack).filter(Pack.sent == False).count()
            total_payments = s.query(Payment).count()
            pending_payments = s.query(Payment).filter(Payment.status == 'pending').count()

        # Métricas das filas
        queue_stats = queue_manager.get_stats() if queue_manager else {}

        # Métricas do pool de conexões
        pool_stats = {
            "pool_size": engine.pool.size(),
            "checked_in": engine.pool.checkedin(),
            "checked_out": engine.pool.checkedout(),
            "overflow": engine.pool.overflow(),
            "invalid": engine.pool.invalid(),
        } if engine and hasattr(engine.pool, 'size') else {}

        metrics = {
            "database": {
                "total_users": total_users,
                "active_vips": active_vips,
                "total_packs": total_packs,
                "pending_packs": pending_packs,
                "total_payments": total_payments,
                "pending_payments": pending_payments
            },
            "queues": queue_stats,
            "connection_pool": pool_stats,
            "system": {
                "timestamp": datetime.now().isoformat(),
                "uptime_seconds": time.time() - start_time if 'start_time' in globals() else 0
            }
        }

        return JSONResponse(content=metrics, status_code=200)

    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.now().isoformat()},
            status_code=500
        )

@app.get("/ready")
async def readiness_check():
    """Readiness check para Kubernetes"""
    try:
        # Verificar se todos os sistemas críticos estão prontos
        ready = True
        services = {}

        # Banco de dados
        try:
            with SessionLocal() as s:
                s.execute(text("SELECT 1"))
            services["database"] = "ready"
        except Exception as e:
            services["database"] = f"not ready: {e}"
            ready = False

        # Bot
        if application and application.bot:
            services["bot"] = "ready"
        else:
            services["bot"] = "not ready"
            ready = False

        # Queue system
        if queue_manager and queue_manager.running:
            services["queues"] = "ready"
        else:
            services["queues"] = "not ready"
            ready = False

        response_data = {
            "ready": ready,
            "services": services,
            "timestamp": datetime.now().isoformat()
        }

        status_code = 200 if ready else 503
        return JSONResponse(content=response_data, status_code=status_code)

    except Exception as e:
        return JSONResponse(
            content={
                "ready": False,
                "error": str(e),
                "timestamp": datetime.now().isoformat()
            },
            status_code=503
        )

@app.get("/stats")
async def stats_endpoint():
    """Endpoint de estatísticas detalhadas"""
    try:
        with SessionLocal() as s:
            # Estatísticas mais detalhadas
            stats = {
                "vip_members": {
                    "total": s.query(VipMembership).count(),
                    "active": s.query(VipMembership).filter(
                        VipMembership.active == True,
                        VipMembership.expires_at > now_utc()
                    ).count(),
                    "expired": s.query(VipMembership).filter(
                        VipMembership.expires_at <= now_utc()
                    ).count(),
                },
                "packs": {
                    "total": s.query(Pack).count(),
                    "vip": s.query(Pack).filter(Pack.tier == "vip").count(),
                    "free": s.query(Pack).filter(Pack.tier == "free").count(),
                    "pending": s.query(Pack).filter(Pack.sent == False).count(),
                },
                "payments": {
                    "total": s.query(Payment).count(),
                    "approved": s.query(Payment).filter(Payment.status == 'approved').count(),
                    "pending": s.query(Payment).filter(Payment.status == 'pending').count(),
                    "rejected": s.query(Payment).filter(Payment.status == 'rejected').count(),
                },
                "system": {
                    "uptime_seconds": time.time() - start_time if 'start_time' in globals() else 0,
                    "queue_stats": queue_manager.get_stats() if queue_manager else {},
                    "timestamp": datetime.now().isoformat()
                }
            }

        return JSONResponse(content=stats, status_code=200)

    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.now().isoformat()},
            status_code=500
        )

@app.get("/circuit-breakers")
async def circuit_breakers_status():
    """Status de todos os circuit breakers"""
    try:
        breaker_stats = await health_check_with_breakers()
        return JSONResponse(content=breaker_stats, status_code=200)
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.now().isoformat()},
            status_code=500
        )

@app.post("/circuit-breakers/{breaker_name}/reset")
async def reset_circuit_breaker(breaker_name: str):
    """Reseta um circuit breaker específico"""
    try:
        success = breaker_manager.reset_breaker(breaker_name)
        if success:
            return JSONResponse(
                content={
                    "message": f"Circuit breaker '{breaker_name}' resetado com sucesso",
                    "timestamp": datetime.now().isoformat()
                },
                status_code=200
            )
        else:
            return JSONResponse(
                content={
                    "error": f"Circuit breaker '{breaker_name}' não encontrado",
                    "timestamp": datetime.now().isoformat()
                },
                status_code=404
            )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.now().isoformat()},
            status_code=500
        )

@app.get("/batch-operations/stats")
async def batch_operations_stats():
    """Estatísticas das operações em batch"""
    try:
        stats = get_batch_processor_stats()
        return JSONResponse(content=stats, status_code=200)
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.now().isoformat()},
            status_code=500
        )

@app.post("/batch-operations/cleanup")
async def run_batch_cleanup(days_old: int = 30):
    """Executa limpeza em lote de dados antigos"""
    try:
        if days_old < 7:
            return JSONResponse(
                content={"error": "days_old deve ser pelo menos 7 dias"},
                status_code=400
            )

        result = await bulk_cleanup_expired_data(days_old)
        return JSONResponse(
            content={
                "message": "Limpeza concluída com sucesso",
                "results": result,
                "timestamp": datetime.now().isoformat()
            },
            status_code=200
        )
    except Exception as e:
        return JSONResponse(
            content={"error": str(e), "timestamp": datetime.now().isoformat()},
            status_code=500
        )

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
    first_name = Column(String, nullable=True)  # Nome real do usuário
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
    first_name = Column(String, nullable=True)  # Nome real do usuário
    tx_hash = Column(String, nullable=True)
    start_at = Column(DateTime, nullable=False, default=now_utc)
    expires_at = Column(DateTime, nullable=False)
    active = Column(Boolean, default=True)
    plan = Column(String, default=VipPlan.TRIMESTRAL.value)
    created_at = Column(DateTime, default=now_utc)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)
    invite_link = Column(Text, nullable=True)
    # Campos para controle de notificações
    notified_7_days = Column(Boolean, default=False)
    notified_3_days = Column(Boolean, default=False) 
    notified_1_day = Column(Boolean, default=False)
    removal_scheduled = Column(Boolean, default=False)

class VipNotification(Base):
    __tablename__ = "vip_notifications"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, nullable=False)
    notification_type = Column(String, nullable=False)  # '7_days', '3_days', '1_day', 'expired', 'removed'
    sent_at = Column(DateTime, nullable=False, default=now_utc)
    vip_expires_at = Column(DateTime, nullable=False)  # Para histórico


# === Classes do Sistema de Envio Automático ===
class SourceFile(Base):
    """
    Indexa todos os arquivos disponíveis no grupo fonte.
    Populada automaticamente quando mensagens são enviadas no grupo fonte.
    """
    __tablename__ = "source_files"

    id = Column(Integer, primary_key=True)
    file_id = Column(String, nullable=False)
    file_unique_id = Column(String, nullable=False, unique=True, index=True)
    file_type = Column(String, nullable=False)  # photo, video, document, etc
    message_id = Column(Integer, nullable=False, index=True)
    source_chat_id = Column(BigInteger, nullable=False)
    caption = Column(Text, nullable=True)
    file_name = Column(String, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    indexed_at = Column(DateTime(timezone=True), nullable=False, default=now_utc)
    active = Column(Boolean, default=True)  # Pode ser desativado manualmente


class SentFile(Base):
    """Rastreia arquivos já enviados para evitar repetição"""
    __tablename__ = "sent_files"

    id = Column(Integer, primary_key=True)
    file_unique_id = Column(String, nullable=False, index=True)
    file_type = Column(String, nullable=False)  # photo, video, document, etc
    message_id = Column(Integer, nullable=False)
    source_chat_id = Column(BigInteger, nullable=False)
    sent_to_tier = Column(String, nullable=False, index=True)  # 'vip' ou 'free'
    sent_at = Column(DateTime(timezone=True), nullable=False, default=now_utc)
    caption = Column(String, nullable=True)


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
        # Versão otimizada para melhor performance com retry
        import time
        max_retries = 3

        for attempt in range(max_retries):
            try:
                logging.info(f"[SCHEMA] Inicializando schema (tentativa {attempt + 1}/{max_retries})...")
                Base.metadata.create_all(bind=engine)

                # Pular verificações de schema que são demoradas em produção
                # ensure_bigint_columns()
                # ensure_pack_tier_column()
                # ensure_pack_scheduled_for_column()
                # ensure_packfile_src_columns()
                # ensure_vip_invite_column()
                # ensure_vip_plan_column()
                # ensure_payment_fields()

                # MIGRAÇÃO CRÍTICA: Colunas de notificação VIP (necessárias para funcionamento)
                ensure_vip_notification_columns()

                # Configurações básicas
                init_db()
                _schema_initialized = True
                logging.info("[SCHEMA] ✅ Schema inicializado com sucesso!")
                break

            except Exception as e:
                if attempt < max_retries - 1:
                    logging.warning(f"[SCHEMA] ⚠️ Tentativa {attempt + 1} falhou: {e}")
                    logging.info(f"[SCHEMA] 🔄 Aguardando 3s antes de tentar novamente...")
                    time.sleep(3)
                else:
                    logging.warning(f"[SCHEMA] ⚠️ Fast path falhou após {max_retries} tentativas")
                    logging.info(f"[SCHEMA] 🔄 Usando fallback para ensure_schema() completo...")
                    # Fallback para versão completa se necessário
                    try:
                        ensure_schema()
                        init_db()
                        _schema_initialized = True
                        logging.info("[SCHEMA] ✅ Schema inicializado via fallback!")
                    except Exception as fallback_error:
                        logging.error(f"[SCHEMA] ❌ Fallback também falhou: {fallback_error}")
                        raise

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


# --- ADMIN helper com cache Redis + fallback local para alta performance
_ADMIN_CACHE: set[int] = set()
_ADMIN_CACHE_TS: float = 0.0

async def refresh_admin_cache():
    """Atualiza cache de admins no Redis e localmente"""
    with SessionLocal() as s:
        admin_ids = [a.user_id for a in s.query(Admin).all()]
        await cache_admin_list(admin_ids)
        global _ADMIN_CACHE, _ADMIN_CACHE_TS
        _ADMIN_CACHE = set(admin_ids)
        _ADMIN_CACHE_TS = dt.datetime.utcnow().timestamp()

def is_admin(user_id: int) -> bool:
    """Verifica se usuário é admin usando cache (síncrono para compatibilidade)"""
    global _ADMIN_CACHE, _ADMIN_CACHE_TS
    now = dt.datetime.utcnow().timestamp()

    # Se cache local está válido (< 5min), usar ele
    if now - _ADMIN_CACHE_TS < 300 and _ADMIN_CACHE:
        return int(user_id) in _ADMIN_CACHE

    # Cache local expirado, consultar banco e atualizar
    with SessionLocal() as s:
        admin_ids = [a.user_id for a in s.query(Admin).all()]
        _ADMIN_CACHE = set(admin_ids)
        _ADMIN_CACHE_TS = now
        # Atualizar Redis em background (não bloquear)
        try:
            asyncio.create_task(cache_admin_list(admin_ids))
        except RuntimeError:
            pass  # Loop não existe ainda

    return int(user_id) in _ADMIN_CACHE

async def is_admin_async(user_id: int) -> bool:
    """Versão assíncrona otimizada com Redis"""
    cached_admins = await get_cached_admin_list()
    if cached_admins:
        return int(user_id) in cached_admins

    # Cache miss, buscar do banco e cachear
    await refresh_admin_cache()
    cached_admins = await get_cached_admin_list()
    return int(user_id) in cached_admins if cached_admins else False


    
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

async def vip_member_joined_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para quando usuário realmente ENTRA no grupo VIP (após aprovação)"""
    # Verificar se é no grupo VIP
    if not update.effective_chat or update.effective_chat.id != GROUP_VIP_ID:
        return
    
    # Handler para novos membros que entraram
    if update.message and update.message.new_chat_members:
        for new_member in update.message.new_chat_members:
            await process_vip_member_entry(new_member, "new_member")
    
    # Handler para chat_member update (quando status muda para 'member')
    elif update.chat_member:
        chat_member_update = update.chat_member
        # Verificar se o status mudou para 'member' (entrou no grupo)
        if (chat_member_update.new_chat_member.status == 'member' and 
            chat_member_update.old_chat_member.status in ['restricted', 'left', 'kicked']):
            await process_vip_member_entry(chat_member_update.new_chat_member.user, "status_change")

async def process_vip_member_entry(user, entry_type: str):
    """Processa entrada real de um membro no grupo VIP e associa com pagamento pendente"""
    user_id = user.id
    username = user.username or user.first_name
    
    logging.info(f"[VIP-ENTRY] Usuário {username} (ID: {user_id}) entrou no grupo VIP via {entry_type}")
    
    # Buscar pagamentos pendentes sem ID associado (UID temporário)
    with SessionLocal() as s:
        # Payment está definido no main.py, não no payments.py

        # Procurar pagamentos aprovados recentes sem user_id válido ou com user_id = 0
        recent_payments = s.query(Payment).filter(
            Payment.status == "approved",
            Payment.user_id.in_([0, None]),  # Pagamentos sem ID válido
            Payment.created_at >= now_utc() - dt.timedelta(hours=24)  # Últimas 24h
        ).order_by(Payment.created_at.desc()).all()

        logging.info(f"[VIP-ENTRY] Buscando pagamentos pendentes para {user_id} - encontrados {len(recent_payments)} pagamentos")
        
        if recent_payments:
            # Pegar o pagamento mais recente
            payment = recent_payments[0]
            
            # Verificar se é um pagamento de renovação
            is_renewal = payment.temp_user_id and payment.temp_user_id.startswith("RENEW_")
            
            if is_renewal:
                logging.info(f"[VIP-ENTRY] Processando renovação para usuário {user_id}")
                
                # Para renovações: desativar VIP atual e criar novo período completo
                current_vip = s.query(VipMembership).filter(
                    VipMembership.user_id == user_id,
                    VipMembership.active == True
                ).first()
                
                if current_vip:
                    # Desativar VIP atual
                    current_vip.active = False
                    current_vip.notes = f"Substituído por renovação em {now_utc().strftime('%d/%m/%Y %H:%M')}"
                    logging.info(f"[VIP-RENEWAL] VIP anterior desativado - expirava em {current_vip.expires_at}")
                
                # Criar novo VIP com período completo (a partir de agora, não somando ao anterior)
                new_expires = now_utc() + dt.timedelta(days=payment.days_vip)
                
                new_vip = VipMembership(
                    user_id=user_id,
                    username=username,
                    active=True,
                    expires_at=new_expires,
                    created_at=now_utc(),
                    plan=payment.plan,
                    notes=f"Renovação - substitui VIP anterior"
                )
                s.add(new_vip)
                vip = new_vip
                
                logging.info(f"[VIP-RENEWAL] Novo VIP criado - expira em {new_expires}")
                
            else:
                # Lógica normal para novos VIPs
                # Criar VipMembership com ID real baseado nos dados do pagamento
                vip_expires = now_utc() + dt.timedelta(days=payment.vip_days)

                vip = VipMembership(
                    user_id=user_id,
                    username=username,
                    active=True,
                    expires_at=vip_expires,
                    created_at=now_utc(),
                    plan=f"{payment.vip_days}d"
                )
                s.add(vip)
                logging.info(f"[VIP-ENTRY] VIP criado para {user_id} - expira em {vip_expires.strftime('%d/%m/%Y %H:%M')}")
            
            # Associar pagamento ao usuário que entrou
            payment.user_id = user_id
            payment.username = username
            
            s.commit()
            
            # Enviar comprovante completo no privado
            try:
                # Calcular data de expiração do VIP
                vip_expires = vip.expires_at if vip else None
                expires_str = vip_expires.strftime("%d/%m/%Y às %H:%M") if vip_expires else "N/A"
                
                # Criar comprovante detalhado
                comprovante = (
                    f"📜 <b>COMPROVANTE DE PAGAMENTO VIP</b> 📜\n"
                    f"{'='*35}\n\n"
                    
                    f"📅 <b>Data:</b> {now_utc().strftime('%d/%m/%Y às %H:%M')}\n"
                    f"👤 <b>Usuário:</b> {username}\n"
                    f"🆔 <b>ID Telegram:</b> <code>{user_id}</code>\n\n"
                    
                    f"💰 <b>DETALHES DO PAGAMENTO</b>\n"
                    f"• <b>Valor Pago:</b> ${payment.usd_value}\n"
                    f"• <b>Criptomoeda:</b> {payment.token_symbol or 'N/A'}\n"
                    f"• <b>Quantidade:</b> {payment.amount}\n"
                    f"• <b>Hash:</b> <code>{payment.tx_hash[:16]}...{payment.tx_hash[-8:]}</code>\n\n"
                    
                    f"👑 <b>VIP ATIVADO</b>\n"
                    f"• <b>Duração:</b> {payment.vip_days} dias\n"
                    f"• <b>Válido até:</b> {expires_str}\n"
                    f"• <b>Status:</b> ✅ Ativo\n\n"
                    
                    f"📁 <b>REGRAS DO GRUPO VIP</b>\n"
                    f"• Respeite todos os membros\n"
                    f"• Proibido spam ou conteúdo inapropriado\n"
                    f"• Não compartilhe links de convite\n"
                    f"• Mantenha conversa relevante ao tema\n"
                    f"• Proibido revenda de conteúdo\n"
                    f"• Respeite os administradores\n\n"
                    
                    f"⚠️ <b>IMPORTANTE:</b>\n"
                    f"• Seu VIP expira automaticamente na data indicada\n"
                    f"• Você receberá avisos 3 e 1 dia antes do vencimento\n"
                    f"• Para renovar, use o mesmo botão de pagamento\n"
                    f"• Em caso de dúvidas, contate o suporte\n\n"
                    
                    f"🎉 <b>Bem-vindo ao grupo VIP!</b>\n"
                    f"Aproveite o conteúdo exclusivo!"
                )
                
                await application.bot.send_message(
                    chat_id=user_id,
                    text=comprovante,
                    parse_mode="HTML"
                )
                logging.info(f"[VIP-ENTRY] Comprovante enviado para {user_id}")
            except Exception as e:
                logging.error(f"[VIP-ENTRY] Erro ao enviar comprovante: {e}")
        else:
            logging.info(f"[VIP-ENTRY] Nenhum pagamento pendente encontrado para associar ao usuário {user_id}")

            # Verificar se usuário já tem VIP ativo (pagamento antigo)
            existing_vip = s.query(VipMembership).filter(
                VipMembership.user_id == user_id,
                VipMembership.active == True
            ).first()

            # PROTEÇÃO: Se não tem pagamento pendente NEM VIP ativo, remover do grupo
            if not existing_vip:
                try:
                    logging.warning(f"[LINK-PROTECTION] ⚠️ Usuário {user_id} tentou entrar sem pagamento válido - REMOVENDO")
                    await application.bot.ban_chat_member(
                        chat_id=GROUP_VIP_ID,
                        user_id=user_id
                    )
                    # Desbanir imediatamente para permitir entrada futura com pagamento
                    await application.bot.unban_chat_member(
                        chat_id=GROUP_VIP_ID,
                        user_id=user_id
                    )

                    # Notificar no privado
                    try:
                        await application.bot.send_message(
                            chat_id=user_id,
                            text=(
                                "⚠️ <b>Acesso Negado ao Grupo VIP</b>\n\n"
                                "Você foi removido do grupo VIP porque não encontramos "
                                "um pagamento válido associado ao seu ID.\n\n"
                                "💳 <b>Para acessar o grupo VIP:</b>\n"
                                "1. Faça o pagamento através do link oficial\n"
                                "2. Aguarde a confirmação\n"
                                "3. Use o link de convite enviado no seu privado\n\n"
                                "🔐 Cada link é único e só funciona para quem fez o pagamento.\n\n"
                                "Dúvidas? Entre em contato com o suporte."
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as notify_error:
                        logging.error(f"[LINK-PROTECTION] Erro ao notificar usuário removido: {notify_error}")

                    # Log no grupo de administração
                    from main import LOGS_GROUP_ID
                    try:
                        await application.bot.send_message(
                            chat_id=LOGS_GROUP_ID,
                            text=(
                                f"🚫 <b>ACESSO NEGADO - PROTEÇÃO DE LINK</b>\n\n"
                                f"👤 User: <code>{user_id}</code> (@{username})\n"
                                f"⚠️ Tentou entrar sem pagamento válido\n"
                                f"✅ Removido automaticamente do grupo\n\n"
                                f"💡 O link de convite é protegido e só funciona para quem fez o pagamento."
                            ),
                            parse_mode="HTML"
                        )
                    except Exception as log_error:
                        logging.error(f"[LINK-PROTECTION] Erro ao enviar log: {log_error}")

                    return  # Não continuar processamento

                except Exception as e:
                    logging.error(f"[LINK-PROTECTION] Erro ao remover usuário não autorizado: {e}")

            if existing_vip:
                # Debug: verificar por que a data está tão longe no futuro
                logging.info(f"[VIP-ENTRY] VIP existente para {user_id}:")
                logging.info(f"  - Created: {existing_vip.created_at}")
                logging.info(f"  - Expires: {existing_vip.expires_at}")
                logging.info(f"  - Plan: {existing_vip.plan}")
                logging.info(f"  - Active: {existing_vip.active}")
                
                # Enviar mensagem de boas-vindas para VIP existente
                expires_str = existing_vip.expires_at.strftime("%d/%m/%Y às %H:%M") if existing_vip.expires_at else "N/A"
                
                welcome_msg = (
                    f"🎉 <b>Bem-vindo de volta ao grupo VIP!</b>\n\n"
                    f"👤 <b>Usuário:</b> {username}\n"
                    f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
                    f"👑 <b>Seu VIP está ativo até:</b> {expires_str}\n\n"
                    
                    f"📁 <b>LEMBRE-SE DAS REGRAS:</b>\n"
                    f"• Respeite todos os membros\n"
                    f"• Proibido spam ou conteúdo inapropriado\n"
                    f"• Não compartilhe links de convite\n"
                    f"• Mantenha conversa relevante ao tema\n"
                    f"• Proibido revenda de conteúdo\n"
                    f"• Respeite os administradores\n\n"
                    
                    f"Aproveite o conteúdo exclusivo!"
                )
                
                try:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=welcome_msg,
                        parse_mode="HTML"
                    )
                    logging.info(f"[VIP-ENTRY] Mensagem de boas-vindas enviada para VIP existente {user_id}")
                except Exception as e:
                    logging.error(f"[VIP-ENTRY] Erro ao enviar boas-vindas: {e}")
            
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
    await update.effective_message.reply_text("🚫 Comando restrito. Comandos permitidos: /tx, /novopack, /novopackvip, /novopackfree, /getid, /comandos")
    raise ApplicationHandlerStop

def header_key(chat_id: int, message_id: int) -> int:
    if chat_id == STORAGE_GROUP_ID: return int(message_id)
    if chat_id == STORAGE_GROUP_FREE_ID: return int(-message_id)
    return int(message_id)


async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.id not in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID, PACK_ADMIN_CHAT_ID}: return
    if msg.reply_to_message: return

    if update.effective_user and not is_admin(update.effective_user.id): return

    title = (msg.text or "").strip()
    if not title: return

    lower = title.lower()
    banned = {"sim", "não", "nao", "/proximo", "/finalizar", "/cancelar", "excluir tudo"}
    if lower in banned or title.startswith("/") or len(title) < 4: return

    words = title.split()
    looks_like_title = (
        len(words) >= 2 or lower.startswith(("pack ", "#pack ", "pack:", "[pack]"))
    )
    if not looks_like_title: return

    hkey = header_key(msg.chat.id, msg.message_id)

    # Determinar tier baseado no chat
    if msg.chat.id == STORAGE_GROUP_ID:
        tier = "vip"
    elif msg.chat.id == STORAGE_GROUP_FREE_ID:
        tier = "free"
    elif msg.chat.id == PACK_ADMIN_CHAT_ID:
        # No chat de administração, permitir especificar o tier no título
        title_lower = title.lower()
        if "[vip]" in title_lower or "#vip" in title_lower:
            tier = "vip"
            title = title.replace("[vip]", "").replace("[VIP]", "").replace("#vip", "").replace("#VIP", "").strip()
        elif "[free]" in title_lower or "#free" in title_lower:
            tier = "free"
            title = title.replace("[free]", "").replace("[FREE]", "").replace("#free", "").replace("#FREE", "").strip()
        else:
            # Padrão: VIP se não especificado
            tier = "vip"
    else:
        tier = "free"  # Fallback
    
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
    if not msg or msg.chat.id not in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID, PACK_ADMIN_CHAT_ID}: return

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


async def _send_preview_media(context: ContextTypes.DEFAULT_TYPE, target_chat_id: int, previews: List[PackFile], is_crosspost: bool = False) -> Dict[str, int]:
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
    
    # Adicionar botão de checkout apenas quando é crosspost VIP->FREE (não em envios diretos do pack FREE)
    if target_chat_id == GROUP_FREE_ID and is_crosspost and (counts["photos"] > 0 or counts["videos"] > 0 or counts["animations"] > 0):
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
            "• 30 dias: $30.00 USD (Mensal)\n"
            "• 90 dias: $70.00 USD (Trimestral)\n"
            "• 180 dias: $110.00 USD (Semestral)\n"
            "• 365 dias: $179.00 USD (Anual)"
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
            # GRUPO FREE: Pack completo como bonificação semanal

            # Enviar mensagem personalizada
            now = datetime.now()
            data_formatada = now.strftime("%d/%m")
            hora_formatada = now.strftime("%H:%M")

            # Calcular próxima quarta-feira baseada no horário configurado
            def proximo_envio_free():
                free_hhmm = cfg_get("daily_pack_free_hhmm") or "10:00"
                try:
                    hora, minuto = map(int, free_hhmm.split(":"))
                except:
                    hora, minuto = 10, 0

                # Calcular próxima quarta-feira (dia 2 = Wednesday)
                dias_ate_quarta = (2 - now.weekday()) % 7
                if dias_ate_quarta == 0 and now.hour >= hora:
                    # Se hoje é quarta e já passou do horário, próxima quarta
                    dias_ate_quarta = 7

                proxima_quarta = now + timedelta(days=dias_ate_quarta)
                return proxima_quarta

            proximo_pack = proximo_envio_free()
            proxima_data = proximo_pack.strftime("%d/%m")

            # Buscar próximo pack FREE para spoiler (excluindo o atual)
            with SessionLocal() as s:
                proximo_free = (
                    s.query(Pack)
                    .filter(Pack.tier == "free", Pack.sent == False, Pack.id != p.id)
                    .order_by(Pack.created_at.asc())
                    .first()
                )
                spoiler_titulo = proximo_free.title if proximo_free else "Surpresa Especial"

            # Obter URL de checkout
            checkout_url = WEBAPP_URL or "https://telegram-bot-vip-hfn7.onrender.com/pay/"

            mensagem_free = (
                f"🔥 **PACK FREE DA SEMANA** 🔥\n\n"
                f"📦 **{p.title}**\n\n"
                f"💥 Pack completo liberado AGORA!\n"
                f"👑 **QUER MAIS?** Entre no VIP e receba packs DIÁRIOS!\n\n"
                f"🗓️ **Próximo Pack FREE:** {proxima_data}\n"
                f"👀 **Spoiler:** ||{spoiler_titulo}||\n\n"
                f"💎 **[ASSINAR VIP AGORA]({checkout_url})** 💎"
            )

            try:
                await context.application.bot.send_message(
                    chat_id=target_chat_id,
                    text=mensagem_free,
                    parse_mode="Markdown"
                )
            except Exception as e:
                if "Chat not found" in str(e):
                    logging.error(f"Chat {target_chat_id} não encontrado durante envio da mensagem.")
                    return f"❌ Erro: Chat {target_chat_id} não encontrado. Bot não está no grupo?"
                raise

            # Enviar previews primeiro
            if previews:
                try:
                    await _send_preview_media(context, target_chat_id, previews)
                except Exception as e:
                    if "Chat not found" in str(e):
                        logging.error(f"Chat {target_chat_id} não encontrado durante envio de previews.")
                        return f"❌ Erro: Chat {target_chat_id} não encontrado. Bot não está no grupo?"
                    raise

            # Enviar todos os arquivos (pack completo)
            for f in docs:
                await _try_send_document_like(context, target_chat_id, f, caption=None)
            
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
                await _send_preview_media(context, GROUP_FREE_ID, previews, is_crosspost=True)
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
    """Job agendado para envio automático de packs VIP com notificações de falha"""
    try:
        hhmm_vip = cfg_get("daily_pack_vip_hhmm") or "09:00"
        logging.info(f"[SCHEDULE-VIP] Executando envio automático às {hhmm_vip}")

        result = await send_with_retry(
            enviar_pack_job,
            context,
            tier="vip",
            target_chat_id=VIP_CHANNEL_ID
        )

        if result and ("Nenhum pack" in result or "já marcado" in result):
            logging.info(f"[SCHEDULE-VIP] {result}")
        elif result and "❌" in result:
            # Falha no envio - notificar admin
            logging.error(f"[SCHEDULE-VIP] Falha no envio: {result}")
            admin_ids = get_admin_ids()
            for admin_id in admin_ids:
                try:
                    await send_with_retry(
                        context.bot.send_message,
                        chat_id=admin_id,
                        text=f"⚠️ **Falha no envio automático do pack VIP**\n\n"
                             f"🕒 Horário: {hhmm_vip}\n"
                             f"❌ Erro: {result}\n\n"
                             f"Verifique o bot e tente novamente com /enviar_pack_agora vip"
                    )
                except Exception as e:
                    logging.error(f"Erro ao notificar admin {admin_id}: {e}")
        else:
            logging.info(f"[SCHEDULE-VIP] Pack VIP enviado com sucesso: {result}")

        return result

    except Exception as e:
        error_msg = f"Erro crítico no envio automático VIP: {e}"
        logging.exception(f"[SCHEDULE-VIP] {error_msg}")

        # Notificar todos os admins sobre erro crítico
        admin_ids = get_admin_ids()
        for admin_id in admin_ids:
            try:
                await send_with_retry(
                    context.bot.send_message,
                    chat_id=admin_id,
                    text=f"🚨 **ERRO CRÍTICO - Pack VIP**\n\n"
                         f"❌ {error_msg}\n\n"
                         f"O envio automático falhou completamente. "
                         f"Verifique os logs e tente manualmente com /enviar_pack_agora vip"
                )
            except Exception as notify_error:
                logging.error(f"Falha ao notificar admin {admin_id}: {notify_error}")

        return f"❌ {error_msg}"


async def enviar_pack_free_job(context: ContextTypes.DEFAULT_TYPE):
    """Job agendado para envio automático de packs FREE com notificações de falha"""
    try:
        hhmm_free = cfg_get("daily_pack_free_hhmm") or "09:30"
        logging.info(f"[SCHEDULE-FREE] Executando envio automático às {hhmm_free} (quartas-feiras)")

        result = await send_with_retry(
            enviar_pack_job,
            context,
            tier="free",
            target_chat_id=GROUP_FREE_ID
        )

        if result and ("Nenhum pack" in result or "já marcado" in result):
            logging.info(f"[SCHEDULE-FREE] {result}")
        elif result and "❌" in result:
            # Falha no envio - notificar admin
            logging.error(f"[SCHEDULE-FREE] Falha no envio: {result}")
            admin_ids = get_admin_ids()
            for admin_id in admin_ids:
                try:
                    await send_with_retry(
                        context.bot.send_message,
                        chat_id=admin_id,
                        text=f"⚠️ **Falha no envio automático do pack FREE**\n\n"
                             f"🕒 Horário: {hhmm_free} (quartas-feiras)\n"
                             f"❌ Erro: {result}\n\n"
                             f"Verifique o bot e tente novamente com /enviar_pack_agora free"
                    )
                except Exception as e:
                    logging.error(f"Erro ao notificar admin {admin_id}: {e}")
        else:
            logging.info(f"[SCHEDULE-FREE] Pack FREE enviado com sucesso: {result}")

        return result

    except Exception as e:
        error_msg = f"Erro crítico no envio automático FREE: {e}"
        logging.exception(f"[SCHEDULE-FREE] {error_msg}")

        # Notificar todos os admins sobre erro crítico
        admin_ids = get_admin_ids()
        for admin_id in admin_ids:
            try:
                await send_with_retry(
                    context.bot.send_message,
                    chat_id=admin_id,
                    text=f"🚨 **ERRO CRÍTICO - Pack FREE**\n\n"
                         f"❌ {error_msg}\n\n"
                         f"O envio automático falhou completamente. "
                         f"Verifique os logs e tente manualmente com /enviar_pack_agora free"
                )
            except Exception as notify_error:
                logging.error(f"Falha ao notificar admin {admin_id}: {notify_error}")

        return f"❌ {error_msg}"

# Função removida - agendamento individual não é mais usado

# =========================
# CALLBACK QUERY HANDLER
# =========================
async def checkout_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para o botão de checkout e renovações temporárias"""
    query = update.callback_query
    if not query:
        return
    
    # Permitir checkout_callback normal e checkout_temp_ para renovações
    if query.data != "checkout_callback" and not query.data.startswith("checkout_temp_"):
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
        
        # Verificar se é renovação ou checkout normal
        is_renewal = query.data.startswith("checkout_temp_")
        temp_uid = None
        username = user.username or user.first_name or "user"
        ts = int(time.time())
        
        if is_renewal:
            # Extrair temp_payment_id do callback_data
            temp_payment_id = query.data.replace("checkout_temp_", "")
            logging.info(f"[CHECKOUT-RENEW] Processando renovação para temp_payment_id: {temp_payment_id}")
            
            # Buscar pagamento de renovação existente
            with SessionLocal() as s:
                renewal_payment = s.query(Payment).filter(
                    Payment.temp_user_id == temp_payment_id,
                    Payment.status == "pending_payment"
                ).first()
                
                if not renewal_payment:
                    await query.edit_message_text(
                        "❌ Pagamento de renovação não encontrado ou expirado.\n"
                        "Tente iniciar uma nova renovação.",
                        parse_mode="HTML"
                    )
                    return
                
                temp_uid = temp_payment_id
                logging.info(f"[CHECKOUT-RENEW] Renovação encontrada: {renewal_payment.days_vip} dias, ${renewal_payment.amount_usd}")
        else:
            # Gerar ID temporário para checkout normal - ID real será capturado quando entrar no grupo
            import uuid
            # Usar apenas números para compatibilidade com validação de UID
            temp_uid = int(time.time())
            logging.info(f"[CHECKOUT] Sessão de pagamento - Temp ID: {temp_uid}, Telegram User: {user.id} ({username})")
        
        # Criar URL com ID temporário - ID real será associado quando entrar no grupo VIP
        sig = make_link_sig(os.getenv("BOT_SECRET", "default"), temp_uid, ts)
        base_url = os.getenv("WEBAPP_URL", "https://telegram-bot-vip-hfn7.onrender.com")
        if base_url.endswith('/pay/') or base_url.endswith('/pay'):
            checkout_url = f"{base_url.rstrip('/')}/?uid={temp_uid}&username={username}&ts={ts}&sig={sig}"
        else:
            checkout_url = f"{base_url.rstrip('/')}/pay/?uid={temp_uid}&username={username}&ts={ts}&sig={sig}"
        
        logging.info(f"[CHECKOUT] URL gerada com temp ID: {checkout_url[:100]}...")
        
        # Botão que abre diretamente com o user ID capturado
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "💳 Abrir Página de Pagamento",
                url=checkout_url
            )]
        ])

        if is_renewal:
            # Mensagem específica para renovação
            with SessionLocal() as s:
                renewal_payment = s.query(Payment).filter(
                    Payment.temp_user_id == temp_payment_id,
                    Payment.status == "pending_payment"
                ).first()
                
                plan_name = renewal_payment.plan.replace("_", " ").title() if renewal_payment.plan else f"{renewal_payment.days_vip} dias"
                
                checkout_msg = (
                    f"🔄 <b>Renovação VIP via Cripto</b>\n\n"
                    f"👤 <b>Usuário:</b> {username}\n"
                    f"📦 <b>Plano:</b> {plan_name} ({renewal_payment.days_vip} dias)\n"
                    f"💰 <b>Valor:</b> ${renewal_payment.amount_usd:.2f} USD\n"
                    f"🔄 <b>Tipo:</b> Renovação (substitui VIP atual)\n\n"
                    f"✅ Pagamento será processado automaticamente\n"
                    f"🔒 Pague com qualquer criptomoeda\n"
                    f"⚡ Ativação imediata após confirmação\n\n"
                    f"📋 <b>Como funciona:</b>\n"
                    f"1. Clique no botão abaixo para pagar\n"
                    f"2. Após o pagamento, aguarde a confirmação\n"
                    f"3. Seu VIP será renovado automaticamente\n"
                    f"4. Você receberá um novo período completo!"
                )
        else:
            # Mensagem padrão para checkout normal
            checkout_msg = (
                f"💸 <b>Pagamento VIP via Cripto</b>\n\n"
                f"👤 <b>Usuário:</b> {username}\n"
                f"✅ Pagamento será associado quando você entrar no grupo VIP\n"
                f"🔒 Pague com qualquer criptomoeda\n"
                f"⚡ Ativação automática do VIP\n\n"
                f"💰 <b>Planos:</b>\n"
                f"• 30 dias: $30.00 USD (Mensal)\n"
                f"• 90 dias: $70.00 USD (Trimestral)\n"
                f"• 180 dias: $110.00 USD (Semestral)\n"
                f"• 365 dias: $179.00 USD (Anual)\n\n"
                f"📋 <b>Como funciona:</b>\n"
                f"1. Clique no botão abaixo para pagar\n"
                f"2. Após o pagamento, aguarde a confirmação\n"
                f"3. Use o link de convite que será enviado\n"
                f"4. Entre no grupo VIP - seu pagamento será automaticamente associado!"
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
    chat = update.effective_chat
    user = update.effective_user

    # Enviar mensagens pendentes (se houver)
    try:
        from vip_manager import send_pending_notifications
        await send_pending_notifications(update, context)
    except Exception as e:
        logging.warning(f"Erro ao enviar mensagens pendentes: {e}")

    # Verificar se há argumentos (deep link)
    if context.args:
        arg = context.args[0]

        # Deep link para página de pagamento: /start vip ou /start payment
        if arg in ['vip', 'payment', 'acesso']:
            # Capturar ID real do usuário
            user_id = user.id
            # Garantir que sempre temos um username válido
            username = user.username if user.username else (user.first_name if user.first_name else f"user_{user_id}")

            logging.info(f"[DEEP-LINK] Usuário {user_id} (@{username}) solicitou acesso VIP via deep link")

            # Gerar link de pagamento personalizado com assinatura
            from utils import make_link_sig
            import time

            ts = int(time.time())
            sig = make_link_sig(WEBAPP_LINK_SECRET, user_id, ts)

            # URL da página de pagamento com parâmetros de autenticação
            payment_url = f"{WEBAPP_URL}?uid={user_id}&ts={ts}&sig={sig}&username={username}"

            # Mensagem com link de pagamento
            payment_msg = (
                f"👋 <b>Olá, {user.first_name}!</b>\n\n"
                f"🎯 <b>Quer ter acesso ao conteúdo completo?</b>\n\n"
                f"💎 <b>Benefícios VIP:</b>\n"
                f"• Acesso a conteúdo exclusivo premium\n"
                f"• Atualizações diárias de novos arquivos\n"
                f"• Suporte prioritário\n"
                f"• Sem anúncios ou spam\n\n"
                f"💰 <b>Planos disponíveis:</b>\n"
                f"• Mensal (30 dias): $30.00\n"
                f"• Trimestral (90 dias): $70.00\n"
                f"• Semestral (180 dias): $110.00\n"
                f"• Anual (365 dias): $179.00\n\n"
                f"🔐 <b>Pagamento seguro via blockchain</b>\n"
                f"Aceitamos diversas criptomoedas em múltiplas redes.\n\n"
                f"👇 <b>Clique no botão abaixo para pagar:</b>"
            )

            # Criar botão com link de pagamento
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 Fazer Pagamento", url=payment_url)],
                [InlineKeyboardButton("📩 Suporte", callback_data="support_start")],
            ])

            await msg.reply_text(payment_msg, parse_mode='HTML', reply_markup=keyboard)

            logging.info(f"[DEEP-LINK] Link de pagamento enviado para {user_id}: {payment_url}")
            return

        # Deep link para acesso VIP: /start vip_CODIGO
        if arg.startswith('vip_'):
            vip_code = arg[4:]  # Remove 'vip_' prefix

            # Buscar link VIP salvo
            saved_link = cfg_get(f"vip_link_{vip_code}")

            if saved_link:
                # Verificar se o código pertence a este usuário
                user_code = cfg_get(f"vip_code_{user.id}")

                if user_code == vip_code:
                    # Enviar link VIP
                    welcome_msg = (
                        f"✅ <b>Bem-vindo ao VIP, {user.first_name}!</b>\n\n"
                        f"🎉 Seu pagamento foi confirmado com sucesso!\n\n"
                        f"📲 <b>Entre no grupo VIP agora:</b>\n"
                        f"{saved_link}\n\n"
                        f"💡 <b>Este link é exclusivo e expira em 2 horas.</b>"
                    )
                    await msg.reply_text(welcome_msg, parse_mode='HTML')

                    # Limpar códigos usados
                    cfg_set(f"vip_link_{vip_code}", None)
                    cfg_set(f"vip_code_{user.id}", None)

                    logging.info(f"[DEEP-LINK] Link VIP entregue via deep link para {user.id}")
                    return
                else:
                    await msg.reply_text(
                        "❌ Este link não pertence a você.\n\n"
                        "Se você fez um pagamento, aguarde a confirmação ou "
                        "entre em contato com o suporte."
                    )
                    return
            else:
                await msg.reply_text(
                    "❌ Link expirado ou inválido.\n\n"
                    "Se você fez um pagamento recente, entre em contato com o suporte."
                )
                return

    # Mensagem especial para o chat de administração de packs
    if chat and chat.id == PACK_ADMIN_CHAT_ID:
        text = (
            "🎯 **Chat de Administração de Packs**\n\n"
            "📝 **Como usar:**\n"
            "1. Envie o título do pack\n"
            "2. Adicione `[VIP]` ou `[FREE]` no título para especificar o tier\n"
            "3. Envie os arquivos (fotos, vídeos, documentos)\n\n"
            "📋 **Exemplos:**\n"
            "• `Pack Especial [VIP]` - Criará um pack VIP\n"
            "• `Pack Grátis [FREE]` - Criará um pack FREE\n"
            "• `Meu Pack` - Criará um pack VIP (padrão)\n\n"
            "✅ **Chat configurado e funcionando!**"
        )
        if msg: await msg.reply_text(text, parse_mode="Markdown")
        return

    # Mensagem padrão: enviar mensagem VIP para todos os usuários
    user_id = user.id
    username = user.username if user.username else (user.first_name if user.first_name else f"user_{user_id}")

    logging.info(f"[START] Usuário {user_id} (@{username}) enviou /start")

    # Gerar link de pagamento personalizado com assinatura
    from utils import make_link_sig
    import time

    ts = int(time.time())
    sig = make_link_sig(WEBAPP_LINK_SECRET, user_id, ts)

    # URL da página de pagamento com parâmetros de autenticação
    payment_url = f"{WEBAPP_URL}?uid={user_id}&ts={ts}&sig={sig}&username={username}"

    # Mensagem com link de pagamento
    payment_msg = (
        f"👋 <b>Olá, {user.first_name}!</b>\n\n"
        f"🎯 <b>Quer ter acesso ao conteúdo completo?</b>\n\n"
        f"💎 <b>Benefícios VIP:</b>\n"
        f"• Acesso a conteúdo exclusivo premium\n"
        f"• Atualizações diárias de novos arquivos\n"
        f"• Suporte prioritário\n"
        f"• Sem anúncios ou spam\n\n"
        f"💰 <b>Planos disponíveis:</b>\n"
        f"• Mensal (30 dias): $30.00\n"
        f"• Trimestral (90 dias): $70.00\n"
        f"• Semestral (180 dias): $110.00\n"
        f"• Anual (365 dias): $179.00\n\n"
        f"🔐 <b>Pagamento seguro via blockchain</b>\n"
        f"Aceitamos diversas criptomoedas em múltiplas redes.\n\n"
        f"👇 <b>Clique no botão abaixo para pagar:</b>"
    )

    # Criar botão com link de pagamento
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Fazer Pagamento", url=payment_url)],
        [InlineKeyboardButton("📩 Suporte", callback_data="support_start")],
    ])

    await msg.reply_text(payment_msg, parse_mode='HTML', reply_markup=keyboard)

    logging.info(f"[START] Link de pagamento enviado para {user_id}: {payment_url}")


async def index_files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Comando /index_files - Indexa arquivos do grupo fonte automaticamente.
    USA SESSÃO PERSISTENTE - Pede código SMS apenas na primeira vez!
    """
    # Verificar se é admin
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("⛔ Apenas admins podem indexar arquivos.")
        return

    msg = update.effective_message

    # Importar auto_indexer
    try:
        from auto_indexer import index_full_history_command, get_pyrogram_client
        from config import SOURCE_CHAT_ID
    except ImportError as e:
        logging.error(f"Erro ao importar auto_indexer: {e}")
        await msg.reply_text(
            "❌ Erro ao carregar módulo de indexação.\n\n"
            f"Detalhes: {e}"
        )
        return

    if not SOURCE_CHAT_ID:
        await msg.reply_text("❌ SOURCE_CHAT_ID não configurado no .env!")
        return

    # Enviar mensagem inicial
    status_msg = await msg.reply_text(
        "🔍 <b>Iniciando Indexação Automática</b>\n\n"
        "⏳ Conectando ao Telegram...\n\n"
        "💡 <b>Primeira vez?</b> Você receberá um código SMS.\n"
        "📱 Digite o código aqui no chat quando receber.",
        parse_mode='HTML'
    )

    try:
        # Função para atualizar mensagem com progresso
        async def update_progress(text):
            try:
                await status_msg.edit_text(text, parse_mode='HTML')
            except:
                pass

        # Executar indexação
        with SessionLocal() as session:
            stats = await index_full_history_command(
                session=session,
                source_chat_id=SOURCE_CHAT_ID,
                update_message_func=update_progress
            )

        # Relatório final
        final_msg = (
            "✅ <b>Indexação Concluída!</b>\n\n"
            f"📨 Mensagens processadas: {stats['total_processed']}\n"
            f"✅ Novas indexadas: {stats['newly_indexed']}\n"
            f"⏭️ Já existentes: {stats['duplicated']}\n"
            f"❌ Erros: {stats['errors']}\n\n"
        )

        if stats['file_types']:
            final_msg += "📁 <b>Tipos encontrados:</b>\n"
            for file_type, count in stats['file_types'].items():
                final_msg += f"   • {file_type}: {count}\n"

        final_msg += f"\n💾 <b>Total no banco:</b> {stats['total_processed'] - stats['duplicated']} arquivos"

        await status_msg.edit_text(final_msg, parse_mode='HTML')

        logging.info(f"[INDEX_CMD] Indexação concluída por {update.effective_user.id}: {stats}")

    except Exception as e:
        logging.error(f"[INDEX_CMD] Erro na indexação: {e}")
        import traceback
        logging.error(traceback.format_exc())

        await status_msg.edit_text(
            f"❌ <b>Erro na indexação:</b>\n\n"
            f"<code>{type(e).__name__}: {str(e)}</code>\n\n"
            f"💡 Verifique:\n"
            f"• TELEGRAM_API_ID configurado\n"
            f"• TELEGRAM_API_HASH configurado\n"
            f"• DATABASE_URL conectado\n"
            f"• Você está no grupo fonte",
            parse_mode='HTML'
        )


async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Somente admin pode usar /comandos
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    base = [
        "📋 <b>Comandos</b>",
        "• /start — mensagem inicial",
        "• /index_files — indexar arquivos do grupo fonte (NOVO!)",
        "• /comandos — esta lista",
        "• /listar_comandos — (alias)",
        "• /getid — mostra seus IDs",
        "• /debug_grupos — debug grupos configurados",
        "• /debug_packs — debug packs no banco",
        "• /limpar_packs_problematicos — remove packs com títulos inválidos",
        "• /comprovante — ver comprovante do VIP",
        "• /status — ver status do VIP",
        "",
        "💬 Envio imediato:",
        "• /say_vip <texto> — envia AGORA no VIP",
        "• /say_free <texto> — envia AGORA no FREE",
        "• /test_mensagem_free [titulo] — testa mensagem do pack FREE",
        "",
        "💸 Pagamento (MetaMask):",
        "• Pagamentos automáticos junto às imagens",
        "• /tx <hash> — valida e libera o VIP",
        "• /pagar_vip — envia mensagem com link de pagamento (admin)",
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
        response = f"👤 Seu nome: {esc(user.full_name)}\n🆔 Seu ID: {user.id}\n💬 ID deste chat: {chat.id}"

        # Detectar se é uma mensagem encaminhada de canal
        if msg.forward_from_chat:
            forward_chat = msg.forward_from_chat
            response += f"\n\n📢 <b>Mensagem encaminhada de:</b>\n"
            response += f"📛 Nome: {esc(forward_chat.title or 'Sem nome')}\n"
            response += f"🆔 ID: <code>{forward_chat.id}</code>\n"
            response += f"📊 Tipo: {forward_chat.type}"

            if forward_chat.username:
                response += f"\n🔗 Username: @{forward_chat.username}"
        elif msg.reply_to_message and msg.reply_to_message.forward_from_chat:
            forward_chat = msg.reply_to_message.forward_from_chat
            response += f"\n\n📢 <b>Resposta a mensagem encaminhada de:</b>\n"
            response += f"📛 Nome: {esc(forward_chat.title or 'Sem nome')}\n"
            response += f"🆔 ID: <code>{forward_chat.id}</code>\n"
            response += f"📊 Tipo: {forward_chat.type}"

            if forward_chat.username:
                response += f"\n🔗 Username: @{forward_chat.username}"

        await msg.reply_text(response, parse_mode="HTML")

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
• PACK_ADMIN_CHAT_ID: {PACK_ADMIN_CHAT_ID}

Chat atual: {update.effective_chat.id}

Variáveis ENV:
• Group_VIP_ID: {os.getenv('Group_VIP_ID', 'não definido')}
• GROUP_VIP_ID: {os.getenv('GROUP_VIP_ID', 'não definido')}"""
    
    await update.effective_message.reply_text(info)


async def debug_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para debug detalhado dos packs no banco"""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return await update.effective_message.reply_text("Apenas admins.")

    with SessionLocal() as s:
        all_packs = s.query(Pack).order_by(Pack.id.asc()).all()

        if not all_packs:
            await update.effective_message.reply_text("Nenhum pack no banco.")
            return

        lines = ["🔧 **DEBUG PACKS NO BANCO:**\n"]

        for p in all_packs:
            files_count = s.query(PackFile).filter(PackFile.pack_id == p.id).count()
            lines.append(
                f"**Pack ID {p.id}:**\n"
                f"• Título: {esc(p.title)}\n"
                f"• Tier: {p.tier}\n"
                f"• Status: {'ENVIADO' if p.sent else 'PENDENTE'}\n"
                f"• Header ID: {p.header_message_id}\n"
                f"• Arquivos: {files_count}\n"
                f"• Criado: {p.created_at.strftime('%d/%m %H:%M')}\n"
            )

        text = "\n".join(lines)
        if len(text) > 4000:  # Telegram limit
            # Split into chunks
            chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for chunk in chunks:
                await update.effective_message.reply_text(chunk, parse_mode="Markdown")
        else:
            await update.effective_message.reply_text(text, parse_mode="Markdown")

async def limpar_packs_problematicos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para limpar packs com títulos problemáticos"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    problematicos = ["EXCLUIR TUDO", "excluir tudo", "Excluir Tudo"]

    with SessionLocal() as s:
        packs_removidos = []
        for titulo in problematicos:
            packs = s.query(Pack).filter(Pack.title == titulo).all()
            for pack in packs:
                # Remover arquivos do pack
                s.query(PackFile).filter(PackFile.pack_id == pack.id).delete()
                # Remover o pack
                s.delete(pack)
                packs_removidos.append(f"#{pack.id} - {pack.title}")

        s.commit()

        if packs_removidos:
            await update.effective_message.reply_text(
                f"✅ Packs problemáticos removidos:\n" + "\n".join(packs_removidos)
            )
        else:
            await update.effective_message.reply_text("ℹ️ Nenhum pack problemático encontrado.")

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

async def test_mensagem_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para testar como ficará a mensagem do pack FREE"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")

    # Simular um pack de exemplo
    titulo_exemplo = context.args[0] if context.args else "Pack Exemplo"

    now = datetime.now()
    data_formatada = now.strftime("%d/%m")
    hora_formatada = now.strftime("%H:%M")

    # Calcular próxima quarta-feira baseada no horário configurado
    def proximo_envio_free():
        free_hhmm = cfg_get("daily_pack_free_hhmm") or "10:00"
        try:
            hora, minuto = map(int, free_hhmm.split(":"))
        except:
            hora, minuto = 10, 0

        # Calcular próxima quarta-feira (dia 2 = Wednesday)
        dias_ate_quarta = (2 - now.weekday()) % 7
        if dias_ate_quarta == 0 and now.hour >= hora:
            # Se hoje é quarta e já passou do horário, próxima quarta
            dias_ate_quarta = 7

        proxima_quarta = now + timedelta(days=dias_ate_quarta)
        return proxima_quarta

    proximo_pack = proximo_envio_free()
    proxima_data = proximo_pack.strftime("%d/%m")

    # Obter URL de checkout
    checkout_url = WEBAPP_URL or "https://telegram-bot-vip-hfn7.onrender.com/pay/"

    mensagem_free = (
        f"🔥 **PACK FREE DA SEMANA** 🔥\n\n"
        f"📦 **{titulo_exemplo}**\n\n"
        f"💥 Pack completo liberado AGORA!\n"
        f"👑 **QUER MAIS?** Entre no VIP e receba packs DIÁRIOS!\n\n"
        f"🗓️ **Próximo Pack FREE:** {proxima_data}\n"
        f"👀 **Spoiler:** ||Próximo Pack Surpresa||\n\n"
        f"💎 **[ASSINAR VIP AGORA]({checkout_url})** 💎"
    )

    await update.effective_message.reply_text(
        f"📱 **Preview da mensagem FREE:**\n\n{mensagem_free}",
        parse_mode="Markdown"
    )

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
                # Prioridade: nome > @username > ID
                if hasattr(p, 'first_name') and p.first_name:
                    username_info = p.first_name
                elif p.username:
                    username_info = f"@{p.username}"
                else:
                    username_info = f"ID:{p.user_id}"
                
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
                    # Primeiro tentar por hash com tratamento de erro para colunas inexistentes
                    vip = None
                    try:
                        vip = s.query(VipMembership).filter(VipMembership.tx_hash == p.tx_hash).first()
                    except Exception as e:
                        # Se erro for sobre colunas inexistentes, usar query básica
                        if "does not exist" in str(e):
                            logging.warning("Colunas VIP não encontradas, usando query básica")
                            # Query raw sem as colunas problemáticas
                            result = s.execute(text("""
                                SELECT id, user_id, username, tx_hash, expires_at, active, plan 
                                FROM vip_memberships 
                                WHERE tx_hash = :hash
                                LIMIT 1
                            """), {"hash": p.tx_hash}).fetchone()
                            
                            if result:
                                # Criar objeto mock com dados básicos
                                class MockVip:
                                    def __init__(self, row):
                                        self.id, self.user_id, self.username, self.tx_hash, self.expires_at, self.active, self.plan = row
                                vip = MockVip(result)
                        else:
                            logging.error(f"Erro inesperado na query VIP: {e}")
                            raise
                    
                    # Se não encontrar por hash, tentar por user_id (VIP pode existir sem hash vinculada)
                    if not vip and p.user_id:
                        try:
                            vip = s.query(VipMembership).filter(
                                VipMembership.user_id == p.user_id,
                                VipMembership.active == True
                            ).order_by(VipMembership.expires_at.desc()).first()
                        except Exception as e:
                            if "does not exist" in str(e):
                                # Query raw para user_id também
                                result = s.execute(text("""
                                    SELECT id, user_id, username, tx_hash, expires_at, active, plan 
                                    FROM vip_memberships 
                                    WHERE user_id = :user_id AND active = true
                                    ORDER BY expires_at DESC
                                    LIMIT 1
                                """), {"user_id": p.user_id}).fetchone()
                                
                                if result:
                                    class MockVip:
                                        def __init__(self, row):
                                            self.id, self.user_id, self.username, self.tx_hash, self.expires_at, self.active, self.plan = row
                                    vip = MockVip(result)
                            else:
                                logging.error(f"Erro inesperado na segunda query VIP: {e}")
                                raise
                    
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
                                    "trimestral": "90 dias",
                                    "semestral": "180 dias",
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
            # Prioridade: nome > @username > ID
            if hasattr(payment, 'first_name') and payment.first_name:
                username_info = payment.first_name
            elif payment.username:
                username_info = f"@{payment.username}"
            else:
                username_info = f"ID:{payment.user_id}"
            
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
                # Prioridade: nome > @username > ID
                if hasattr(vip, 'first_name') and vip.first_name:
                    username_info = vip.first_name
                elif vip.username:
                    username_info = f"@{vip.username}"
                else:
                    username_info = f"ID:{vip.user_id}"
                
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
                        
                        # Prioridade: nome real > @username > ID
                        if hasattr(payment, 'first_name') and payment.first_name:
                            username_info = payment.first_name
                        elif username:
                            username_info = f"@{username}"
                        else:
                            username_info = f"ID:{user_id}"
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
            BotCommand("comprovante", "Ver comprovante detalhado do VIP"),
            
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
                        # Prioridade: nome > @username > ID
                        if hasattr(payment, 'first_name') and payment.first_name:
                            username_info = payment.first_name
                        elif payment.username:
                            username_info = f"@{payment.username}"
                        else:
                            username_info = f"ID:{payment.user_id}"
                        
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
    return chat_id in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID, PACK_ADMIN_CHAT_ID}

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
    VipPlan.MENSAL: 30.00,      # 30 dias
    VipPlan.TRIMESTRAL: 70.00,  # 90 dias
    VipPlan.SEMESTRAL: 110.00,  # 180 dias
    VipPlan.ANUAL: 179.00,      # 365 dias
}

def plan_from_amount(amount_usd: float) -> Optional[VipPlan]:
    """
    Determina o plano VIP baseado no valor pago usando ranges ao invés de valores exatos.

    ====== VALORES DE PRODUÇÃO ======
    Ranges de PRODUÇÃO:
    - $30.00 - $69.99: VIP 30 dias (MENSAL)
    - $70.00 - $109.99: VIP 90 dias (TRIMESTRAL)
    - $110.00 - $178.99: VIP 180 dias (SEMESTRAL)
    - $179.00+: VIP 365 dias (ANUAL)
    """
    # ====== MODO TESTE - VALORES REDUZIDOS ======
    # Descomente abaixo para usar valores de teste
    # if amount_usd < 1.00:
    #     return None  # Valor muito baixo
    # elif amount_usd < 2.00:
    #     return VipPlan.MENSAL      # 30 dias
    # elif amount_usd < 3.00:
    #     return VipPlan.TRIMESTRAL  # 90 dias
    # elif amount_usd < 4.00:
    #     return VipPlan.SEMESTRAL   # 180 dias
    # else:
    #     return VipPlan.ANUAL       # 365 dias

    # ====== VALORES DE PRODUÇÃO ======
    # Valores atualizados: Mensal $30 | Trimestral $70 | Semestral $110 | Anual $179
    if amount_usd < 30.00:
        return None  # Valor insuficiente
    elif amount_usd < 70.00:
        return VipPlan.MENSAL      # 30 dias
    elif amount_usd < 110.00:
        return VipPlan.TRIMESTRAL  # 90 dias
    elif amount_usd < 179.00:
        return VipPlan.SEMESTRAL   # 180 dias
    else:
        return VipPlan.ANUAL       # 365 dias
    #
    # if amount_usd < 30.00:
    #     return None  # Valor muito baixo
    # elif amount_usd < 70.00:
    #     return VipPlan.MENSAL      # 30 dias
    # elif amount_usd < 110.00:
    #     return VipPlan.TRIMESTRAL  # 90 dias
    # elif amount_usd < 179.00:
    #     return VipPlan.SEMESTRAL   # 180 dias
    # else:
    #     return VipPlan.ANUAL       # 365 dias

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
    logging.info(f"[SIMULAR_TX] ==================== INICIO ====================")
    logging.info(f"[SIMULAR_TX] User ID: {update.effective_user.id if update.effective_user else 'None'}")

    if not update.effective_user:
        logging.error("[SIMULAR_TX] ERROR: update.effective_user é None")
        return await update.effective_message.reply_text("❌ Erro: Usuário não identificado.")

    user_id = update.effective_user.id
    is_admin_user = is_admin(user_id)
    logging.info(f"[SIMULAR_TX] Verificando admin para {user_id}: {is_admin_user}")

    if not is_admin_user:
        logging.warning(f"[SIMULAR_TX] ACESSO NEGADO para user_id={user_id}")
        return await update.effective_message.reply_text("Apenas admins podem simular TX.")

    user = update.effective_user
    tx_hash = "0x" + "deadbeef"*8  # hash fictício, 66 chars
    logging.info(f"[SIMULAR_TX] Criando tx_hash simulado: {tx_hash[:16]}...")

    # grava como aprovado direto
    logging.info(f"[SIMULAR_TX] Iniciando transação no banco de dados...")
    with SessionLocal() as s:
        try:
            logging.info(f"[SIMULAR_TX] Criando objeto Payment...")
            p = Payment(
                user_id=user.id,
                username=user.username,
                tx_hash=tx_hash,
                chain="TESTNET",
                status="approved",
                amount="1000000000000000000",  # 1 ETH fictício
                created_at=now_utc(),
            )
            logging.info(f"[SIMULAR_TX] Adicionando à sessão...")
            s.add(p)
            logging.info(f"[SIMULAR_TX] Fazendo commit...")
            s.commit()
            logging.info(f"[SIMULAR_TX] ✅ Payment criado: id={p.id}")
        except Exception as e:
            s.rollback()
            logging.error(f"[SIMULAR_TX] ❌ ERRO ao gravar pagamento: {e}", exc_info=True)
            return await update.effective_message.reply_text(f"❌ Erro ao simular pagamento: {e}")

    # cria/renova VIP no plano trimestral
    logging.info(f"[SIMULAR_TX] Criando/renovando VIP membership...")
    try:
        m = vip_upsert_start_or_extend(user.id, user.username, tx_hash, VipPlan.TRIMESTRAL)
        logging.info(f"[SIMULAR_TX] ✅ VIP membership criado: expires_at={m.expires_at}")
    except Exception as e:
        logging.error(f"[SIMULAR_TX] ❌ ERRO ao criar VIP: {e}", exc_info=True)
        return await update.effective_message.reply_text(f"❌ Erro ao criar VIP: {e}")

    try:
        logging.info(f"[SIMULAR_TX] Gerando convite pessoal para user_id={user.id}...")
        invite_link = await create_and_store_personal_invite(user.id)
        logging.info(f"[SIMULAR_TX] ✅ Convite gerado: {invite_link[:50]}...")

        message_text = (
            f"✅ Pagamento confirmado na rede {CHAIN_NAME}!\n"
            f"VIP válido até {m.expires_at:%d/%m/%Y} ({human_left(m.expires_at)}).\n"
            f"Entre no VIP: {invite_link}"
        )

        # Tentar enviar mensagem privada
        logging.info(f"[SIMULAR_TX] Tentando enviar mensagem privada para {user.id}...")
        try:
            await application.bot.send_message(
                chat_id=user.id,
                text=message_text
            )
            logging.info(f"[SIMULAR_TX] ✅ Mensagem privada enviada com sucesso!")
            await update.effective_message.reply_text("✅ Pagamento simulado com sucesso. Veja seu privado.")
            logging.info(f"[SIMULAR_TX] ==================== FIM (SUCESSO) ====================")

        except Exception as dm_error:
            # Se falhar (usuário não iniciou conversa), criar deep link
            logging.warning(f"[SIMULAR_TX] ⚠️ Falha ao enviar privado: {dm_error}")

            if invite_link:
                # Salvar o link VIP temporariamente com código único
                logging.info(f"[SIMULAR_TX] Criando deep link...")
                import hashlib
                vip_code = hashlib.md5(f"{user.id}{tx_hash}".encode()).hexdigest()[:8]
                cfg_set(f"vip_link_{vip_code}", invite_link)
                cfg_set(f"vip_code_{user.id}", vip_code)
                logging.info(f"[SIMULAR_TX] VIP code salvo: {vip_code}")

                # Obter username do bot
                bot_info = await application.bot.get_me()
                bot_username = bot_info.username

                # Criar deep link
                deep_link = f"https://t.me/{bot_username}?start=vip_{vip_code}"
                logging.info(f"[SIMULAR_TX] Deep link criado: {deep_link}")

                # Enviar mensagem no grupo de logs
                logging.info(f"[SIMULAR_TX] Enviando notificação para grupo de logs...")
                await log_to_group(
                    f"💳 <b>Pagamento Simulado (TESTE)</b>\n\n"
                    f"👤 Usuário: @{user.username} (ID: {user.id})\n"
                    f"📦 Plano: 90 dias (TRIMESTRAL)\n"
                    f"🔗 Hash: <code>{tx_hash}</code>\n\n"
                    f"⚠️ <b>Usuário não iniciou conversa com o bot!</b>\n\n"
                    f"📲 Envie este link para o usuário:\n"
                    f"<code>{deep_link}</code>\n\n"
                    f"Ou peça para ele enviar /start para @{bot_username}"
                )

                # Informar admin
                logging.info(f"[SIMULAR_TX] Informando admin sobre deep link...")
                await update.effective_message.reply_text(
                    f"✅ <b>Pagamento simulado!</b>\n\n"
                    f"⚠️ <b>Não foi possível enviar mensagem privada.</b>\n"
                    f"(Você precisa iniciar conversa com o bot primeiro)\n\n"
                    f"📲 <b>Deep link criado:</b>\n"
                    f"<code>{deep_link}</code>\n\n"
                    f"📋 Envie /start para o bot e ele entregará seu link VIP.\n"
                    f"📢 Uma notificação foi enviada ao grupo de logs.",
                    parse_mode='HTML'
                )

                logging.info(f"[SIMULAR_TX] ==================== FIM (DEEP LINK) ====================")
            else:
                logging.error(f"[SIMULAR_TX] ❌ Nenhum invite_link disponível")
                await update.effective_message.reply_text(
                    "⚠️ Erro: Não foi possível gerar link VIP nem enviar mensagem privada."
                )

    except Exception as e:
        logging.error(f"[SIMULAR_TX] ❌ ERRO GERAL: {e}", exc_info=True)
        await update.effective_message.reply_text(f"❌ Erro ao simular pagamento: {e}")



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
        target_chat = VIP_CHANNEL_ID if m.tier == "vip" else FREE_CHANNEL_ID

        # Enviar mensagem de texto
        await context.application.bot.send_message(chat_id=target_chat, text=m.text)

        # Verificar se deve enviar pack junto com a mensagem
        if "[ENVIAR_PACK]" in m.text.upper():
            if m.tier == "vip":
                pack_result = await enviar_pack_vip_job(context)
                logging.info(f"Pack VIP enviado via agendamento (msg id={sid}): {pack_result}")
            elif m.tier == "free":
                pack_result = await enviar_pack_free_job(context)
                logging.info(f"Pack FREE enviado via agendamento (msg id={sid}): {pack_result}")

    except Exception as e: logging.warning(f"Falha ao enviar scheduled_message id={sid}: {e}")

def _register_all_scheduled_messages(job_queue: JobQueue):
    for j in list(job_queue.jobs()):
        if j.name and (j.name.startswith(JOB_PREFIX_SM) or j.name in {"daily_pack_vip", "daily_pack_free", "weekly_pack_free", "keepalive"}):
            j.schedule_removal()
    msgs = scheduled_all()
    for m in msgs:
        try: h, k = parse_hhmm(m.hhmm)
        except Exception: continue
        tz = _tz(m.tz)
        job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")

async def _reschedule_daily_packs():
    """Reagenda jobs diários de packs com logs detalhados"""
    if not application or not application.job_queue:
        logging.warning("[RESCHEDULE] Application ou job_queue não disponível para reagendar packs")
        return False

    # Remover jobs existentes
    removed_jobs = []
    for j in list(application.job_queue.jobs()):
        if j.name in {"daily_pack_vip", "daily_pack_free", "weekly_pack_free"}:
            j.schedule_removal()
            removed_jobs.append(j.name)

    if removed_jobs:
        logging.info(f"[RESCHEDULE] Jobs removidos: {removed_jobs}")
    else:
        logging.info("[RESCHEDULE] Nenhum job anterior para remover")

    tz = pytz.timezone("America/Sao_Paulo")
    hhmm_vip  = cfg_get("daily_pack_vip_hhmm")  or "09:00"
    hhmm_free = cfg_get("daily_pack_free_hhmm") or "09:30"

    try:
        hv, mv = parse_hhmm(hhmm_vip)
        hf, mf = parse_hhmm(hhmm_free)

        # Calcular próximas execuções
        now = dt.datetime.now(tz)
        next_vip = now.replace(hour=hv, minute=mv, second=0, microsecond=0)
        if next_vip <= now:
            next_vip += dt.timedelta(days=1)

        next_free = now.replace(hour=hf, minute=mf, second=0, microsecond=0)
        while next_free.weekday() != 2:  # 2 = quarta-feira
            next_free += dt.timedelta(days=1)
        if next_free <= now:
            next_free += dt.timedelta(weeks=1)

        # Agendar novos jobs
        application.job_queue.run_daily(enviar_pack_vip_job,  time=dt.time(hour=hv, minute=mv, tzinfo=tz), name="daily_pack_vip")
        application.job_queue.run_daily(enviar_pack_free_job, time=dt.time(hour=hf, minute=mf, tzinfo=tz), days=(2,), name="weekly_pack_free")

        logging.info(f"[RESCHEDULE] ✅ Jobs reagendados com sucesso:")
        logging.info(f"[RESCHEDULE]   📧 VIP: {hhmm_vip} (diário) - próximo: {next_vip.strftime('%d/%m %H:%M')}")
        logging.info(f"[RESCHEDULE]   📧 FREE: {hhmm_free} (quartas) - próximo: {next_free.strftime('%d/%m %H:%M')}")
        logging.info(f"[RESCHEDULE]   🌍 Timezone: {tz}")

        return True

    except Exception as e:
        logging.error(f"[RESCHEDULE] ❌ Erro ao reagendar jobs: {e}")
        return False

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
        old_time = cfg_get("daily_pack_vip_hhmm") or "09:00"
        parse_hhmm(hhmm)  # Validar formato

        # Salvar configuração
        cfg_set("daily_pack_vip_hhmm", hhmm)

        # Reagendar jobs com feedback
        reschedule_success = await _reschedule_daily_packs()

        # Verificar se foi salvo corretamente
        saved_time = cfg_get("daily_pack_vip_hhmm")

        # Calcular próximo envio
        tz = pytz.timezone("America/Sao_Paulo")
        now = dt.datetime.now(tz)
        hv, mv = parse_hhmm(saved_time)
        next_send = now.replace(hour=hv, minute=mv, second=0, microsecond=0)
        if next_send <= now:
            next_send += dt.timedelta(days=1)

        if reschedule_success:
            status_emoji = "✅"
            status_text = "Jobs reagendados com sucesso!"
            log_level = "INFO"
        else:
            status_emoji = "⚠️"
            status_text = "Horário salvo, mas houve problema no reagendamento. Reinicie o bot."
            log_level = "WARNING"

        await send_with_retry(
            update.effective_message.reply_text,
            f"{status_emoji} **Horário VIP atualizado!**\n\n"
            f"🕒 Horário anterior: {old_time}\n"
            f"🕒 Novo horário: {saved_time}\n"
            f"📅 Próximo envio: {next_send.strftime('%d/%m às %H:%M')} (Brasília)\n"
            f"🔄 {status_text}"
        )

        logging.log(
            getattr(logging, log_level),
            f"[CONFIG] Horário VIP alterado: {old_time} → {hhmm} pelo usuário {update.effective_user.id}"
        )

    except Exception as e:
        logging.error(f"[CONFIG] Erro ao alterar horário VIP: {e}")
        await send_with_retry(
            update.effective_message.reply_text,
            f"❌ **Erro ao alterar horário:**\n\n{e}\n\n"
            f"Formato correto: HH:MM (ex: 10:30)"
        )

async def set_pack_horario_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        return await update.effective_message.reply_text("Uso: /set_pack_horario_free HH:MM")

    try:
        hhmm = context.args[0]
        old_time = cfg_get("daily_pack_free_hhmm") or "09:30"
        parse_hhmm(hhmm)  # Validar formato

        # Salvar configuração
        cfg_set("daily_pack_free_hhmm", hhmm)

        # Reagendar jobs com feedback
        reschedule_success = await _reschedule_daily_packs()

        # Verificar se foi salvo corretamente
        saved_time = cfg_get("daily_pack_free_hhmm")

        # Calcular próximo envio (quarta-feira)
        tz = pytz.timezone("America/Sao_Paulo")
        now = dt.datetime.now(tz)
        hf, mf = parse_hhmm(saved_time)
        next_send = now.replace(hour=hf, minute=mf, second=0, microsecond=0)
        while next_send.weekday() != 2:  # 2 = quarta-feira
            next_send += dt.timedelta(days=1)
        if next_send <= now:
            next_send += dt.timedelta(weeks=1)

        if reschedule_success:
            status_emoji = "✅"
            status_text = "Jobs reagendados com sucesso!"
            log_level = "INFO"
        else:
            status_emoji = "⚠️"
            status_text = "Horário salvo, mas houve problema no reagendamento. Reinicie o bot."
            log_level = "WARNING"

        await send_with_retry(
            update.effective_message.reply_text,
            f"{status_emoji} **Horário FREE atualizado!**\n\n"
            f"🕒 Horário anterior: {old_time}\n"
            f"🕒 Novo horário: {saved_time}\n"
            f"📅 Próximo envio: {next_send.strftime('%d/%m às %H:%M')} (quarta-feira)\n"
            f"🔄 {status_text}"
        )

        logging.log(
            getattr(logging, log_level),
            f"[CONFIG] Horário FREE alterado: {old_time} → {hhmm} pelo usuário {update.effective_user.id}"
        )

    except Exception as e:
        logging.error(f"[CONFIG] Erro ao alterar horário FREE: {e}")
        await send_with_retry(
            update.effective_message.reply_text,
            f"❌ **Erro ao alterar horário:**\n\n{e}\n\n"
            f"Formato correto: HH:MM (ex: 14:30)"
        )

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
            for p in vip_packs:
                previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
                docs = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
                status = "✅ ENVIADO" if p.sent else "⏳ PENDENTE"

                lines.append(
                    f"[{p.id}] {esc(p.title)} — {status}\n"
                    f"    📷 {previews} previews | 📄 {docs} arquivos\n"
                    f"    📅 {p.created_at.strftime('%d/%m %H:%M')}"
                )
        else:
            lines.append("👑 <b>VIP:</b> Nenhum pack")
            
        lines.append("")  # Linha em branco
        
        # Seção FREE
        if free_packs:
            lines.append(f"🆓 <b>FREE ({len(free_packs)} packs):</b>")
            for p in free_packs:
                previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
                docs = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
                status = "✅ ENVIADO" if p.sent else "⏳ PENDENTE"

                lines.append(
                    f"[{p.id}] {esc(p.title)} — {status}\n"
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
        lines.append(f"🆓 FREE: {free_horario} (quartas-feiras)")
        
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
        # Usar SQL direto para evitar conflitos de ORM
        with SessionLocal() as session:
            from sqlalchemy import text
            
            # Verificar se existem packs
            pack_count = session.query(Pack).count()
            if pack_count == 0:
                await update.effective_message.reply_text("❌ Nenhum pack encontrado para excluir.")
                return ConversationHandler.END
            
            # Excluir usando SQL direto (mais seguro para evitar conflitos)
            # Primeiro PackFiles (FK constraint)
            session.execute(text("DELETE FROM pack_files"))
            
            # Depois Packs
            session.execute(text("DELETE FROM packs"))
            
            # Resetar sequências
            session.execute(text("ALTER SEQUENCE packs_id_seq RESTART WITH 1"))
            session.execute(text("ALTER SEQUENCE pack_files_id_seq RESTART WITH 1"))
            
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

async def comprovante_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para mostrar comprovante de pagamento VIP do usuário"""
    user = update.effective_user
    if not user:
        return
    
    user_id = user.id
    username = user.username or user.first_name or f"user_{user_id}"
    
    with SessionLocal() as s:
        # Buscar VIP ativo do usuário
        vip = s.query(VipMembership).filter(
            VipMembership.user_id == user_id,
            VipMembership.active == True
        ).first()
        
        if not vip:
            return await update.effective_message.reply_text(
                "❌ Você não possui VIP ativo.\n"
                "Use o botão de pagamento para adquirir seu VIP!"
            )
        
        # Buscar pagamento mais recente associado
        # Payment está definido no main.py, não no payments.py
        payment = s.query(Payment).filter(
            Payment.user_id == user_id,
            Payment.status == "approved"
        ).order_by(Payment.created_at.desc()).first()
        
        # Dados do VIP
        expires_str = vip.expires_at.strftime("%d/%m/%Y às %H:%M") if vip.expires_at else "N/A"
        created_str = vip.created_at.strftime("%d/%m/%Y às %H:%M") if vip.created_at else "N/A"
        
        # Criar comprovante
        comprovante = (
            f"📜 <b>SEU COMPROVANTE VIP</b> 📜\n"
            f"{'='*30}\n\n"
            
            f"👤 <b>Usuário:</b> {username}\n"
            f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
        )
        
        if payment:
            comprovante += (
                f"💰 <b>Último Pagamento:</b>\n"
                f"• <b>Valor:</b> ${payment.usd_value}\n"
                f"• <b>Cripto:</b> {payment.token_symbol or 'N/A'}\n"
                f"• <b>Quantidade:</b> {payment.amount}\n"
                f"• <b>Data:</b> {payment.created_at.strftime('%d/%m/%Y') if payment.created_at else 'N/A'}\n\n"
            )
        
        comprovante += (
            f"👑 <b>STATUS VIP</b>\n"
            f"• <b>Ativo desde:</b> {created_str}\n"
            f"• <b>Válido até:</b> {expires_str}\n"
            f"• <b>Status:</b> ✅ Ativo\n\n"
            
            f"📁 <b>REGRAS DO GRUPO VIP</b>\n"
            f"• Respeite todos os membros\n"
            f"• Proibido spam ou conteúdo inapropriado\n"
            f"• Não compartilhe links de convite\n"
            f"• Mantenha conversa relevante\n"
            f"• Proibido revenda de conteúdo\n"
            f"• Respeite os administradores\n\n"
            
            f"📱 <b>Comandos Úteis:</b>\n"
            f"• /comprovante - Ver este comprovante\n"
            f"• /status - Ver status do VIP\n\n"
            
            f"🎉 Obrigado por ser VIP!"
        )
        
        await update.effective_message.reply_text(
            comprovante,
            parse_mode="HTML"
        )

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para ver status do VIP (alias para comprovante)"""
    await comprovante_cmd(update, context)

async def pagar_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para enviar mensagem de pagamento VIP"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins podem usar este comando.")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "💳 Abrir Página de Pagamento",
            callback_data="checkout_callback"
        )]
    ])

    checkout_msg = (
        "💸 <b>Quer ver o conteúdo completo?</b>\n\n"
        "✅ Clique no botão abaixo para abrir a página de pagamento\n"
        "🔒 Pague com qualquer criptomoeda\n"
        "⚡ Ativação automática\n\n"
        "💰 <b>Planos:</b>\n"
        "• 30 dias: $30.00 USD (Mensal)\n"
        "• 90 dias: $70.00 USD (Trimestral)\n"
        "• 180 dias: $110.00 USD (Semestral)\n"
        "• 365 dias: $179.00 USD (Anual)"
    )

    await update.effective_message.reply_text(
        checkout_msg,
        parse_mode="HTML",
        reply_markup=keyboard
    )

async def migrate_vip_columns_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando para executar migração das colunas de notificação VIP"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("❌ Apenas admins.")

    try:
        await update.effective_message.reply_text("🔄 Executando migração das colunas VIP...")

        # Executar migração
        ensure_vip_notification_columns()

        await update.effective_message.reply_text(
            "✅ Migração concluída!\n"
            "Colunas verificadas/adicionadas:\n"
            "• first_name\n"
            "• notified_7_days\n"
            "• notified_3_days\n"
            "• notified_1_day\n"
            "• removal_scheduled\n\n"
            "⚠️ Agora o comando /vip_list deve funcionar corretamente!"
        )

    except Exception as e:
        await update.effective_message.reply_text(f"❌ Erro na migração: {e}")
        logging.error(f"Erro na migração VIP: {e}")

async def fix_vip_dates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Corrigir VIPs com datas muito longas no futuro"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    
    now = now_utc()
    max_future = now + dt.timedelta(days=400)  # Máximo 400 dias no futuro
    
    with SessionLocal() as s:
        # Buscar VIPs com datas muito longas
        problematic_vips = s.query(VipMembership).filter(
            VipMembership.expires_at > max_future
        ).all()
        
        if not problematic_vips:
            return await update.effective_message.reply_text("✅ Nenhum VIP com data problemática encontrado.")
        
        report = []
        for vip in problematic_vips:
            old_date = vip.expires_at
            # Resetar para 30 dias a partir de agora
            new_date = now + dt.timedelta(days=30)
            vip.expires_at = new_date
            
            report.append(
                f"ID {vip.user_id}: {old_date.strftime('%Y-%m-%d')} \u2192 {new_date.strftime('%Y-%m-%d')}"
            )
        
        s.commit()
        
        report_text = (
            f"🔧 <b>VIPs corrigidos: {len(problematic_vips)}</b>\n\n" +
            "\n".join(report[:10])  # Mostrar apenas os primeiros 10
        )
        
        if len(report) > 10:
            report_text += f"\n\n... e mais {len(report) - 10}"
        
        await update.effective_message.reply_text(report_text, parse_mode="HTML")

async def debug_convite_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando de debug para testar criação de convites"""
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    
    user_id = update.effective_user.id
    try:
        # Primeiro verificar se o usuário tem VIP
        m = vip_get(user_id)
        if not m:
            return await update.effective_message.reply_text(
                f"❌ Você não tem VIP cadastrado. Use /vip_set {user_id} 30 primeiro."
            )
        
        if not m.active:
            return await update.effective_message.reply_text(
                f"❌ Seu VIP está inativo. Status: {m.active}, Expira: {m.expires_at}"
            )
        
        # Tentar criar convite
        await update.effective_message.reply_text("🔄 Testando criação de convite...")
        
        invite_link = await create_and_store_personal_invite(user_id)
        
        await update.effective_message.reply_text(
            f"✅ Convite criado com sucesso!\n\n"
            f"🔗 Link: {invite_link}\n"
            f"👤 Para: {update.effective_user.first_name} (ID: {user_id})\n"
            f"⏰ Expira em: {m.expires_at}\n"
            f"📁 GROUP_VIP_ID: {GROUP_VIP_ID}"
        )
        
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        await update.effective_message.reply_text(
            f"❌ Erro ao criar convite:\n\n"
            f"<code>{str(e)}</code>\n\n"
            f"Detalhes nos logs.", 
            parse_mode="HTML"
        )
        logging.error(f"[DEBUG-CONVITE] Erro completo: {error_details}")


# ===========================
# COMANDOS - Sistema de Envio Automático
# ===========================

async def auto_index_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler que indexa automaticamente arquivos do grupo fonte"""
    with SessionLocal() as session:
        indexed = await index_message_file(update, session)
        if indexed:
            # Enviar confirmação silenciosa no grupo de logs
            file_type = "arquivo"
            if update.effective_message.photo:
                file_type = "foto"
            elif update.effective_message.video:
                file_type = "vídeo"
            elif update.effective_message.document:
                file_type = "documento"

            await log_to_group(
                f"📁 <b>Arquivo Indexado</b>\n"
                f"🎯 Tipo: {file_type}\n"
                f"📝 Caption: {update.effective_message.caption or '(sem legenda)'}\n"
                f"🆔 Message ID: {update.effective_message.message_id}"
            )


async def stats_auto_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra estatísticas do sistema de envio automático"""
    if not is_admin(update.effective_user.id):
        return

    await update.effective_message.reply_text("🔄 Carregando estatísticas...")

    with SessionLocal() as session:
        stats = await get_stats(session)

    if not stats:
        await update.effective_message.reply_text("❌ Erro ao carregar estatísticas")
        return

    msg = (
        f"📊 <b>Estatísticas do Sistema de Envio Automático</b>\n\n"
        f"📁 Arquivos indexados: <b>{stats['indexed_files']}</b>\n\n"
        f"👑 <b>VIP:</b>\n"
        f"  • Enviados: {stats['vip']['total_sent']}\n"
        f"  • Disponíveis: {stats['vip']['available']}\n"
        f"  • Último envio: {stats['vip']['last_sent'].strftime('%d/%m/%Y %H:%M') if stats['vip']['last_sent'] else 'Nunca'}\n\n"
        f"🆓 <b>FREE:</b>\n"
        f"  • Enviados: {stats['free']['total_sent']}\n"
        f"  • Disponíveis: {stats['free']['available']}\n"
        f"  • Último envio: {stats['free']['last_sent'].strftime('%d/%m/%Y %H:%M') if stats['free']['last_sent'] else 'Nunca'}"
    )

    await update.effective_message.reply_text(msg, parse_mode='HTML')

    # Também enviar para grupo de logs
    await log_to_group(f"📊 Admin {update.effective_user.id} consultou estatísticas do sistema")


async def reset_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reseta histórico de envios"""
    if not is_admin(update.effective_user.id):
        return

    tier = context.args[0] if context.args else None

    if tier and tier not in ['vip', 'free']:
        await update.effective_message.reply_text(
            "❌ Uso incorreto!\n\n"
            "<b>Uso:</b> /reset_history [vip|free]\n\n"
            "<b>Exemplos:</b>\n"
            "• /reset_history vip - Reseta apenas VIP\n"
            "• /reset_history free - Reseta apenas FREE\n"
            "• /reset_history - Reseta ambos",
            parse_mode='HTML'
        )
        return

    # Confirmação
    tier_name = tier.upper() if tier else "TODOS"
    await update.effective_message.reply_text(
        f"⚠️ <b>ATENÇÃO!</b>\n\n"
        f"Você está prestes a resetar o histórico de envios para: <b>{tier_name}</b>\n\n"
        f"Isso permitirá que arquivos já enviados sejam enviados novamente.\n\n"
        f"Digite /confirmar_reset para confirmar.",
        parse_mode='HTML'
    )

    # Armazenar tier no contexto para o próximo comando
    context.user_data['reset_tier'] = tier


async def confirmar_reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirma reset do histórico"""
    if not is_admin(update.effective_user.id):
        return

    tier = context.user_data.get('reset_tier')

    with SessionLocal() as session:
        count = await reset_sent_history(session, tier)

    tier_name = tier.upper() if tier else "TODOS"
    await update.effective_message.reply_text(
        f"✅ <b>Histórico resetado com sucesso!</b>\n\n"
        f"🎯 Tier: {tier_name}\n"
        f"🗑️ Registros removidos: {count}",
        parse_mode='HTML'
    )

    # Limpar contexto
    if 'reset_tier' in context.user_data:
        del context.user_data['reset_tier']

    # Log
    await log_to_group(
        f"🗑️ <b>Histórico Resetado</b>\n"
        f"👤 Admin: {update.effective_user.id}\n"
        f"🎯 Tier: {tier_name}\n"
        f"📊 Registros removidos: {count}"
    )


async def catalogo_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Força atualização do catálogo VIP (admin)"""
    if not is_admin(update.effective_user.id):
        return

    await update.effective_message.reply_text("🔄 Atualizando catálogo VIP...")

    with SessionLocal() as session:
        try:
            await send_or_update_vip_catalog(context.bot, session)
            await update.effective_message.reply_text("✅ Catálogo VIP atualizado com sucesso!")
        except Exception as e:
            await update.effective_message.reply_text(f"❌ Erro ao atualizar catálogo: {e}")


async def test_send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Testa envio manual (admin) com debug detalhado"""
    if not is_admin(update.effective_user.id):
        return

    tier = context.args[0] if context.args else 'vip'

    if tier not in ['vip', 'free']:
        await update.effective_message.reply_text(
            "❌ Uso incorreto!\n\n"
            "<b>Uso:</b> /test_send [vip|free]\n\n"
            "<b>Exemplos:</b>\n"
            "• /test_send vip\n"
            "• /test_send free",
            parse_mode='HTML'
        )
        return

    await update.effective_message.reply_text(f"🔄 Testando envio {tier.upper()}...\n⏳ Aguarde...")

    with SessionLocal() as session:
        try:
            # 1. Verificar stats antes
            from auto_sender import get_stats
            stats_before = await get_stats(session)

            indexed = stats_before.get('indexed_files', 0)
            available = stats_before.get(tier, {}).get('available', 0)

            status_msg = (
                f"📊 <b>Status Antes do Envio:</b>\n"
                f"📁 Arquivos indexados: {indexed}\n"
                f"✅ Disponíveis para {tier.upper()}: {available}\n"
                f"🆔 Canal destino: {VIP_CHANNEL_ID if tier == 'vip' else FREE_CHANNEL_ID}\n\n"
                f"🔄 Enviando..."
            )
            await update.effective_message.reply_text(status_msg, parse_mode='HTML')

            # 2. Tentar enviar
            if tier == 'vip':
                await send_daily_vip_file(context.bot, session)
            else:
                await send_weekly_free_file(context.bot, session)

            # 3. Verificar stats depois
            stats_after = await get_stats(session)
            sent_after = stats_after.get(tier, {}).get('total_sent', 0)

            await update.effective_message.reply_text(
                f"✅ <b>Teste de envio {tier.upper()} concluído!</b>\n\n"
                f"📤 Total enviados: {sent_after}\n"
                f"📍 Verifique o canal: {VIP_CHANNEL_ID if tier == 'vip' else FREE_CHANNEL_ID}",
                parse_mode='HTML'
            )

            # Log
            await log_to_group(
                f"🧪 <b>Teste de Envio</b>\n"
                f"👤 Admin: {update.effective_user.id}\n"
                f"🎯 Tier: {tier.upper()}\n"
                f"✅ Status: Concluído\n"
                f"📊 Total enviados: {sent_after}"
            )

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()

            await update.effective_message.reply_text(
                f"❌ <b>Erro no teste de envio!</b>\n\n"
                f"<code>{str(e)}</code>\n\n"
                f"💡 Verifique:\n"
                f"• Arquivos indexados: use /stats_auto\n"
                f"• Canal configurado: {VIP_CHANNEL_ID if tier == 'vip' else FREE_CHANNEL_ID}\n"
                f"• Bot é admin no canal?",
                parse_mode='HTML'
            )

            # Log de erro detalhado
            await log_to_group(
                f"❌ <b>Erro no Teste de Envio</b>\n"
                f"👤 Admin: {update.effective_user.id}\n"
                f"🎯 Tier: {tier.upper()}\n"
                f"⚠️ Erro: {str(e)}\n\n"
                f"📋 Detalhes:\n<code>{error_details[:500]}</code>"
            )


async def debug_version_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra versão do código em execução (admin)"""
    if not is_admin(update.effective_user.id):
        return

    import subprocess
    import os
    from auto_sender import __version__ as auto_sender_version, __updated__ as auto_sender_updated

    try:
        # Tentar pegar commit hash atual
        cwd = os.path.dirname(os.path.abspath(__file__))
        commit = subprocess.check_output(['git', 'rev-parse', '--short', 'HEAD'], cwd=cwd).decode().strip()
        branch = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], cwd=cwd).decode().strip()
    except:
        commit = "unknown"
        branch = "unknown"

    # Verificar se o auto_sender.py tem o código correto
    try:
        with open('auto_sender.py', 'r', encoding='utf-8') as f:
            content = f.read()
            has_old_bug = 'if sent_file_ids else True' in content
            has_new_fix = 'if sent_file_ids:' in content and 'query = query.filter' in content
    except:
        has_old_bug = "?"
        has_new_fix = "?"

    msg = (
        f"🔍 <b>Debug - Versão do Código</b>\n\n"
        f"📍 <b>Git Info:</b>\n"
        f"  • Branch: <code>{branch}</code>\n"
        f"  • Commit: <code>{commit}</code>\n\n"
        f"📦 <b>Módulo Importado:</b>\n"
        f"  • Versão: <code>{auto_sender_version}</code>\n"
        f"  • Atualizado: <code>{auto_sender_updated}</code>\n\n"
        f"🐛 <b>Status do Bug (arquivo):</b>\n"
        f"  • Código antigo (bug): {'❌ SIM' if has_old_bug else '✅ NÃO'}\n"
        f"  • Código novo (fix): {'✅ SIM' if has_new_fix else '❌ NÃO'}\n\n"
        f"💡 <b>Esperado:</b>\n"
        f"  • Versão: <code>2.0.1</code> ou superior\n"
        f"  • Código antigo: ❌ NÃO\n"
        f"  • Código novo: ✅ SIM"
    )

    await update.effective_message.reply_text(msg, parse_mode='HTML')


async def check_files_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica arquivos indexados usando SQL direto (não depende de get_stats)"""
    if not is_admin(update.effective_user.id):
        return

    await update.effective_message.reply_text("🔍 Verificando arquivos...")

    try:
        with SessionLocal() as session:
            # Verificar se tabelas existem
            from sqlalchemy import inspect, text
            inspector = inspect(session.bind)
            tables = inspector.get_table_names()

            if 'source_files' not in tables:
                await update.effective_message.reply_text(
                    "⚠️ Tabela source_files não existe!\n\n"
                    "O banco foi criado mas as tabelas ainda não foram inicializadas.",
                    parse_mode='HTML'
                )
                return

            # Query SQL direta
            result = session.execute(text("""
                SELECT
                    COUNT(*) as total,
                    COUNT(CASE WHEN active = true THEN 1 END) as ativos,
                    MAX(indexed_at) as ultimo
                FROM source_files
                WHERE source_chat_id = -1003080645605
            """)).fetchone()

            total = result[0] if result else 0
            ativos = result[1] if result else 0
            ultimo = result[2] if result else None

            # Query para arquivos enviados
            sent_result = session.execute(text("""
                SELECT
                    sent_to_tier,
                    COUNT(*) as quantidade
                FROM sent_files
                WHERE source_chat_id = -1003080645605
                GROUP BY sent_to_tier
            """)).fetchall()

            sent_vip = 0
            sent_free = 0
            for row in sent_result:
                if row[0] == 'vip':
                    sent_vip = row[1]
                elif row[0] == 'free':
                    sent_free = row[1]

            msg = (
                f"📊 <b>Status dos Arquivos</b>\n\n"
                f"📁 <b>Grupo Fonte:</b> -1003080645605\n\n"
                f"📦 <b>Indexados:</b>\n"
                f"  • Total: {total}\n"
                f"  • Ativos: {ativos}\n"
                f"  • Último: {ultimo.strftime('%d/%m/%Y %H:%M') if ultimo else 'Nunca'}\n\n"
                f"📤 <b>Enviados:</b>\n"
                f"  • VIP: {sent_vip}\n"
                f"  • FREE: {sent_free}\n\n"
                f"✅ <b>Disponíveis:</b>\n"
                f"  • VIP: {ativos - sent_vip}\n"
                f"  • FREE: {ativos - sent_free}\n\n"
                f"🆔 <b>Canais:</b>\n"
                f"  • VIP: {VIP_CHANNEL_ID}\n"
                f"  • FREE: {FREE_CHANNEL_ID}"
            )

            await update.effective_message.reply_text(msg, parse_mode='HTML')

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()

        await update.effective_message.reply_text(
            f"❌ Erro ao verificar arquivos:\n\n"
            f"<code>{str(e)}</code>\n\n"
            f"Detalhes: <code>{error_details[:300]}</code>",
            parse_mode='HTML'
        )


async def scan_history_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faz scan do histórico do grupo fonte e indexa arquivos antigos"""
    if not is_admin(update.effective_user.id):
        return

    # Obter limite (padrão 100)
    try:
        limit = int(context.args[0]) if context.args else 100
    except:
        limit = 100

    await update.effective_message.reply_text(
        f"🔄 Iniciando scan do histórico...\n"
        f"📊 Limite: {limit} mensagens\n\n"
        f"⏳ Isso pode demorar alguns minutos...",
        parse_mode='HTML'
    )

    from auto_sender import index_message_file

    total_processadas = 0
    total_indexadas = 0
    total_duplicadas = 0
    total_erros = 0

    with SessionLocal() as session:
        try:
            # Buscar updates recentes
            updates = await context.bot.get_updates(limit=100, timeout=30)

            for upd in updates:
                if not upd.message or upd.message.chat_id != SOURCE_CHAT_ID:
                    continue

                total_processadas += 1

                # Tentar indexar
                try:
                    indexed = await index_message_file(upd, session)
                    if indexed:
                        total_indexadas += 1
                    else:
                        # Já existia
                        total_duplicadas += 1

                except Exception as e:
                    total_erros += 1
                    logging.error(f"Erro ao indexar message_id {upd.message.message_id}: {e}")

                if total_processadas >= limit:
                    break

            # Relatório
            msg = (
                f"✅ <b>Scan Concluído!</b>\n\n"
                f"📨 Mensagens processadas: {total_processadas}\n"
                f"✅ Novas indexadas: {total_indexadas}\n"
                f"⏭️ Já existentes: {total_duplicadas}\n"
                f"❌ Erros: {total_erros}\n\n"
            )

            # Verificar total no banco
            total_banco = session.query(SourceFile).filter(
                SourceFile.source_chat_id == SOURCE_CHAT_ID,
                SourceFile.active == True
            ).count()

            msg += (
                f"💾 <b>Total no banco:</b> {total_banco} arquivos\n\n"
                f"💡 <b>Dica:</b> O Bot API tem limite de ~100 mensagens recentes.\n"
                f"Para histórico completo, envie arquivos antigos novamente\n"
                f"ou encaminhe para o grupo fonte."
            )

            await update.effective_message.reply_text(msg, parse_mode='HTML')

            # Log
            await log_to_group(
                f"📊 <b>Scan de Histórico</b>\n"
                f"👤 Admin: {update.effective_user.id}\n"
                f"📨 Processadas: {total_processadas}\n"
                f"✅ Indexadas: {total_indexadas}\n"
                f"💾 Total no banco: {total_banco}"
            )

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()

            await update.effective_message.reply_text(
                f"❌ <b>Erro no scan:</b>\n\n"
                f"<code>{str(e)}</code>",
                parse_mode='HTML'
            )

            await log_to_group(
                f"❌ <b>Erro no Scan de Histórico</b>\n"
                f"👤 Admin: {update.effective_user.id}\n"
                f"⚠️ Erro: {str(e)}\n\n"
                f"<code>{error_details[:500]}</code>"
            )


async def scan_full_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Faz scan COMPLETO do histórico usando Pyrogram (User API)"""
    if not is_admin(update.effective_user.id):
        return

    await update.effective_message.reply_text(
        "🔄 <b>Scan Completo do Histórico</b>\n\n"
        "⏳ Verificando Pyrogram...",
        parse_mode='HTML'
    )

    # Verificar se Pyrogram está instalado
    try:
        from pyrogram import Client
        from pyrogram.enums import ChatType
    except ImportError:
        await update.effective_message.reply_text(
            "❌ <b>Pyrogram não instalado!</b>\n\n"
            "Para fazer scan completo, instale:\n"
            "<code>pip install pyrogram tgcrypto</code>\n\n"
            "Ou use: /scan_history (limitado a ~100 mensagens)",
            parse_mode='HTML'
        )
        return

    # Obter credenciais
    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        await update.effective_message.reply_text(
            "❌ <b>Credenciais não configuradas!</b>\n\n"
            "Configure no .env:\n"
            "<code>TELEGRAM_API_ID=seu_id\n"
            "TELEGRAM_API_HASH=seu_hash</code>\n\n"
            "Obtenha em: https://my.telegram.org",
            parse_mode='HTML'
        )
        return

    # Obter limite (padrão 0 = ilimitado)
    try:
        limit = int(context.args[0]) if context.args else 0
    except:
        limit = 0

    await update.effective_message.reply_text(
        f"✅ Pyrogram OK!\n\n"
        f"🔄 Iniciando scan completo...\n"
        f"📊 Limite: {'Ilimitado' if limit == 0 else f'{limit} mensagens'}\n\n"
        f"⏳ Isso pode demorar vários minutos...\n\n"
        f"💡 <b>Primeira vez?</b> Você receberá código SMS.",
        parse_mode='HTML'
    )

    total_processadas = 0
    total_indexadas = 0
    total_duplicadas = 0
    total_erros = 0
    tipos_encontrados = {}

    with SessionLocal() as session:
        try:
            # Criar cliente Pyrogram (User API)
            app = Client(
                "bot_scanner",
                api_id=int(api_id),
                api_hash=api_hash,
                workdir="."
            )

            async with app:
                # Verificar se está autenticado
                me = await app.get_me()
                await update.effective_message.reply_text(
                    f"👤 <b>Autenticado como:</b> {me.first_name}\n\n"
                    f"🔍 Escaneando grupo {SOURCE_CHAT_ID}...",
                    parse_mode='HTML'
                )

                # Iterar por TODAS as mensagens do grupo
                async for message in app.get_chat_history(SOURCE_CHAT_ID, limit=limit if limit > 0 else None):
                    total_processadas += 1

                    # Progress a cada 100 mensagens
                    if total_processadas % 100 == 0:
                        await update.effective_message.reply_text(
                            f"📊 Progresso: {total_processadas} mensagens processadas...",
                            parse_mode='HTML'
                        )

                    # Verificar se tem arquivo
                    file_data = None

                    if message.photo:
                        file_data = {
                            'file_id': message.photo.file_id,
                            'file_unique_id': message.photo.file_unique_id,
                            'file_type': 'photo',
                            'file_size': message.photo.file_size,
                            'file_name': None
                        }
                    elif message.video:
                        file_data = {
                            'file_id': message.video.file_id,
                            'file_unique_id': message.video.file_unique_id,
                            'file_type': 'video',
                            'file_size': message.video.file_size,
                            'file_name': message.video.file_name
                        }
                    elif message.document:
                        file_data = {
                            'file_id': message.document.file_id,
                            'file_unique_id': message.document.file_unique_id,
                            'file_type': 'document',
                            'file_size': message.document.file_size,
                            'file_name': message.document.file_name
                        }
                    elif message.animation:
                        file_data = {
                            'file_id': message.animation.file_id,
                            'file_unique_id': message.animation.file_unique_id,
                            'file_type': 'animation',
                            'file_size': message.animation.file_size,
                            'file_name': message.animation.file_name
                        }
                    elif message.audio:
                        file_data = {
                            'file_id': message.audio.file_id,
                            'file_unique_id': message.audio.file_unique_id,
                            'file_type': 'audio',
                            'file_size': message.audio.file_size,
                            'file_name': message.audio.file_name
                        }

                    if not file_data:
                        continue

                    # Contar tipos
                    tipo = file_data['file_type']
                    tipos_encontrados[tipo] = tipos_encontrados.get(tipo, 0) + 1

                    # Verificar se já existe
                    existing = session.query(SourceFile).filter(
                        SourceFile.file_unique_id == file_data['file_unique_id']
                    ).first()

                    if existing:
                        total_duplicadas += 1
                        continue

                    # Criar novo registro
                    try:
                        source_file = SourceFile(
                            file_id=file_data['file_id'],
                            file_unique_id=file_data['file_unique_id'],
                            file_type=file_data['file_type'],
                            message_id=message.id,
                            source_chat_id=SOURCE_CHAT_ID,
                            caption=message.caption,
                            file_name=file_data.get('file_name'),
                            file_size=file_data.get('file_size'),
                            indexed_at=datetime.now(timezone.utc),
                            active=True
                        )
                        session.add(source_file)
                        session.commit()

                        total_indexadas += 1

                    except Exception as e:
                        session.rollback()
                        total_erros += 1
                        logging.error(f"Erro ao indexar {message.id}: {e}")

            # Relatório final
            total_banco = session.query(SourceFile).filter(
                SourceFile.source_chat_id == SOURCE_CHAT_ID,
                SourceFile.active == True
            ).count()

            tipos_str = "\n".join([f"  • {tipo}: {count}" for tipo, count in tipos_encontrados.items()])

            msg = (
                f"✅ <b>Scan Completo Finalizado!</b>\n\n"
                f"📨 Mensagens processadas: {total_processadas}\n"
                f"✅ Novas indexadas: {total_indexadas}\n"
                f"⏭️ Já existentes: {total_duplicadas}\n"
                f"❌ Erros: {total_erros}\n\n"
                f"📁 <b>Tipos encontrados:</b>\n{tipos_str}\n\n"
                f"💾 <b>Total no banco:</b> {total_banco} arquivos\n\n"
                f"🎉 Histórico completo indexado!"
            )

            await update.effective_message.reply_text(msg, parse_mode='HTML')

            # Log
            await log_to_group(
                f"📊 <b>Scan Completo (Pyrogram)</b>\n"
                f"👤 Admin: {update.effective_user.id}\n"
                f"📨 Processadas: {total_processadas}\n"
                f"✅ Indexadas: {total_indexadas}\n"
                f"💾 Total no banco: {total_banco}"
            )

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()

            await update.effective_message.reply_text(
                f"❌ <b>Erro no scan completo:</b>\n\n"
                f"<code>{str(e)}</code>\n\n"
                f"💡 Verifique:\n"
                f"• API_ID e API_HASH corretos\n"
                f"• Primeira vez? Digite código SMS\n"
                f"• Bot tem acesso ao grupo?",
                parse_mode='HTML'
            )

            await log_to_group(
                f"❌ <b>Erro no Scan Completo</b>\n"
                f"👤 Admin: {update.effective_user.id}\n"
                f"⚠️ Erro: {str(e)}\n\n"
                f"<code>{error_details[:500]}</code>"
            )


async def listar_canais_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista todos os canais/grupos que o bot está (admin)"""
    if not is_admin(update.effective_user.id):
        return

    await update.effective_message.reply_text("🔍 Buscando canais e grupos...")

    try:
        # Tentar listar os grupos conhecidos primeiro
        grupos_conhecidos = {
            "Grupo VIP": GROUP_VIP_ID,
            "Grupo FREE": GROUP_FREE_ID,
            "Grupo Fonte (Admin)": SOURCE_CHAT_ID,
            "Storage VIP": STORAGE_GROUP_ID,
            "Storage FREE": STORAGE_GROUP_FREE_ID,
            "Grupo de Logs": LOGS_GROUP_ID,
        }

        msg = "📋 <b>Grupos/Canais Configurados:</b>\n\n"

        for nome, chat_id in grupos_conhecidos.items():
            try:
                chat = await context.bot.get_chat(chat_id)
                tipo = "Canal" if chat.type == "channel" else "Grupo"
                msg += f"• <b>{nome}</b>\n"
                msg += f"  └ {tipo}: {chat.title}\n"
                msg += f"  └ ID: <code>{chat_id}</code>\n\n"
            except Exception as e:
                msg += f"• <b>{nome}</b>\n"
                msg += f"  └ ID: <code>{chat_id}</code>\n"
                msg += f"  └ ⚠️ Erro: {str(e)[:50]}\n\n"

        msg += "\n💡 <b>Dica:</b> Copie o ID para usar nas configurações!"

        await update.effective_message.reply_text(msg, parse_mode='HTML')

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Erro ao listar canais:\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )


async def get_chat_id_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mostra o ID do chat/canal de onde a mensagem veio"""
    if not is_admin(update.effective_user.id):
        return

    msg = update.effective_message

    # Se for uma mensagem encaminhada (verificar forward_origin)
    if msg.forward_origin:
        # forward_origin pode ser de vários tipos
        origin = msg.forward_origin

        # Tentar extrair informações do canal/chat encaminhado
        if hasattr(origin, 'chat'):
            chat = origin.chat
            info = (
                f"📋 <b>Informações do Canal/Grupo Encaminhado:</b>\n\n"
                f"📌 <b>Título:</b> {chat.title}\n"
                f"🆔 <b>ID:</b> <code>{chat.id}</code>\n"
                f"📊 <b>Tipo:</b> {chat.type}\n"
            )
            if hasattr(chat, 'username') and chat.username:
                info += f"🔗 <b>Username:</b> @{chat.username}\n"

            info += f"\n💡 <b>Copie o ID acima para usar nas configurações!</b>"
        else:
            info = (
                f"⚠️ <b>Mensagem encaminhada detectada</b>\n\n"
                f"Mas não consegui extrair o ID do canal.\n"
                f"Tipo de origem: {type(origin).__name__}\n\n"
                f"💡 <b>Dica:</b> Envie o comando diretamente no canal "
                f"ou use o bot como admin no canal."
            )
    else:
        # Informações do chat atual
        chat = msg.chat
        info = (
            f"📋 <b>Informações deste Chat:</b>\n\n"
            f"📌 <b>Título:</b> {chat.title if chat.title else 'Chat Privado'}\n"
            f"🆔 <b>ID:</b> <code>{chat.id}</code>\n"
            f"📊 <b>Tipo:</b> {chat.type}\n"
        )
        if chat.username:
            info += f"🔗 <b>Username:</b> @{chat.username}\n"

        info += (
            f"\n💡 <b>Dicas para descobrir ID de canais:</b>\n"
            f"1. Adicione o bot como admin no canal\n"
            f"2. Envie /get_chat_id no canal\n"
            f"3. Ou encaminhe uma mensagem do canal pra cá"
        )

    await msg.reply_text(info, parse_mode='HTML')


async def check_permissions_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Verifica permissões do bot nos canais VIP e FREE"""
    if not is_admin(update.effective_user.id):
        return

    await update.effective_message.reply_text("🔍 Verificando permissões do bot...")

    canais_para_testar = [
        ("Canal VIP", VIP_CHANNEL_ID),
        ("Canal FREE", FREE_CHANNEL_ID),
    ]

    msg = "🔐 <b>Verificação de Permissões</b>\n\n"

    for nome, channel_id in canais_para_testar:
        try:
            # Obter informações do chat
            chat = await context.bot.get_chat(channel_id)

            msg += f"📢 <b>{nome}</b>\n"
            msg += f"  └ Título: {chat.title}\n"
            msg += f"  └ ID: <code>{channel_id}</code>\n"
            msg += f"  └ Tipo: {chat.type}\n"

            # Tentar obter as permissões do bot
            try:
                me = await context.bot.get_me()
                member = await context.bot.get_chat_member(channel_id, me.id)

                msg += f"  └ Status do bot: {member.status}\n"

                if member.status == "administrator":
                    # Verificar permissões específicas
                    perms = []
                    if member.can_post_messages:
                        perms.append("✅ Postar mensagens")
                    else:
                        perms.append("❌ Postar mensagens")

                    if member.can_edit_messages:
                        perms.append("✅ Editar mensagens")
                    else:
                        perms.append("❌ Editar mensagens")

                    if member.can_delete_messages:
                        perms.append("✅ Deletar mensagens")
                    else:
                        perms.append("❌ Deletar mensagens")

                    msg += "  └ Permissões:\n"
                    for perm in perms:
                        msg += f"     • {perm}\n"

                    # Verificar se pode postar
                    if not member.can_post_messages:
                        msg += "  └ ⚠️ <b>PROBLEMA: Bot não pode postar!</b>\n"
                    else:
                        msg += "  └ ✅ <b>OK: Bot pode postar!</b>\n"

                elif member.status == "member":
                    msg += "  └ ⚠️ <b>PROBLEMA: Bot é apenas membro!</b>\n"
                    msg += "  └ 💡 Promova o bot para administrador\n"
                else:
                    msg += f"  └ ⚠️ Status inesperado: {member.status}\n"

            except Exception as e:
                msg += f"  └ ❌ Erro ao verificar permissões: {str(e)[:100]}\n"

            msg += "\n"

        except Exception as e:
            msg += f"📢 <b>{nome}</b>\n"
            msg += f"  └ ID: <code>{channel_id}</code>\n"
            msg += f"  └ ❌ Erro: {str(e)[:100]}\n\n"

    msg += "\n💡 <b>Como corrigir problemas:</b>\n"
    msg += "1. Adicione o bot como administrador do canal\n"
    msg += "2. Ative a permissão 'Postar mensagens'\n"
    msg += "3. Rode /test_send novamente\n"

    await update.effective_message.reply_text(msg, parse_mode='HTML')


async def send_promo_message_to_free(bot: Bot):
    """
    Envia mensagem promocional para o canal FREE incentivando assinatura VIP.
    """
    # Obter username do bot para criar deep link
    bot_info = await bot.get_me()
    bot_username = bot_info.username


    promo_msg = (
        "💎 <b>QUER TER ACESSO AO CONTEÚDO COMPLETO?</b>\n\n"
         "🔥 Assine o canal VIP e receba:\n"
         "  ✅ Conteúdos diários exclusivos\n"
         "  ✅ Arquivos completos (sem limites)\n"
         "  ✅ Sem anúncios\n"
         "  ✅ Suporte prioritário\n\n"
         "💰 <b>Planos Disponíveis:</b>\n"
         "  • 30 dias: $30.00 USD (Mensal)\n"
         "  • 90 dias: $70.00 USD (Trimestral) 💰\n"
         "  • 180 dias: $110.00 USD (Semestral)\n"
         "  • 365 dias: $179.00 USD (Anual) 🔥\n\n"
         "🔒 <b>Pagamento 100% Seguro</b>\n"
         "  • Aceita qualquer criptomoeda\n"
         "  • Ativação automática e instantânea\n"
         "  • Comprovante e convite enviados no privado\n\n"
         "👇 Clique no botão abaixo para assinar!"

    )

    # ====== VALORES ORIGINAIS (PRODUÇÃO) ======
    # Descomente abaixo e comente o bloco acima quando voltar para produção
    # promo_msg = (
    #     "💎 <b>QUER TER ACESSO AO CONTEÚDO COMPLETO?</b>\n\n"
    #     "🔥 Assine o canal VIP e receba:\n"
    #     "  ✅ Conteúdos diários exclusivos\n"
    #     "  ✅ Arquivos completos (sem limites)\n"
    #     "  ✅ Sem anúncios\n"
    #     "  ✅ Suporte prioritário\n\n"
    #     "💰 <b>Planos Disponíveis:</b>\n"
    #     "  • 30 dias: $30.00 USD (Mensal)\n"
    #     "  • 90 dias: $70.00 USD (Trimestral) 💰\n"
    #     "  • 180 dias: $110.00 USD (Semestral)\n"
    #     "  • 365 dias: $179.00 USD (Anual) 🔥\n\n"
    #     "🔒 <b>Pagamento 100% Seguro</b>\n"
    #     "  • Aceita qualquer criptomoeda\n"
    #     "  • Ativação automática e instantânea\n"
    #     "  • Comprovante e convite enviados no privado\n\n"
    #     "👇 Clique no botão abaixo para assinar!"
    # )

    # Criar botão inline com deep link para conversa privada
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup

    # Gerar deep link para conversa privada (captura ID automaticamente)
    deep_link = f"https://t.me/{bot_username}?start=vip"

    keyboard = [[
        InlineKeyboardButton("💳 ASSINAR VIP AGORA", url=deep_link)
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await bot.send_message(
        chat_id=FREE_CHANNEL_ID,
        text=promo_msg,
        parse_mode='HTML',
        reply_markup=reply_markup
    )


async def promo_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia mensagem promocional manualmente para o canal FREE (admin)"""
    if not is_admin(update.effective_user.id):
        return

    try:
        await update.effective_message.reply_text("📤 Enviando mensagem promocional para o canal FREE...")

        await send_promo_message_to_free(context.bot)

        await update.effective_message.reply_text(
            "✅ <b>Mensagem promocional enviada!</b>\n\n"
            f"📢 Canal: FREE ({FREE_CHANNEL_ID})\n"
            f"💬 Mensagem com botão de assinatura VIP",
            parse_mode='HTML'
        )

        # Log
        await log_to_group(
            f"📢 <b>Promoção FREE enviada manualmente</b>\n"
            f"👤 Admin: {update.effective_user.id}"
        )

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ <b>Erro ao enviar promoção:</b>\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )


async def gerar_url_pagamento_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gera URL estática de pagamento para descrição do canal (admin)"""
    if not is_admin(update.effective_user.id):
        return

    try:
        # URL estática para descrição dos canais
        url_free = f"{WEBAPP_URL}?ref=channel_free_desc"
        url_vip = f"{WEBAPP_URL}?ref=channel_vip_desc"

        msg = (
            "🔗 <b>URLs de Pagamento para Descrição dos Canais</b>\n\n"
            "📋 <b>Para Canal FREE:</b>\n"
            f"<code>{url_free}</code>\n\n"
            "💎 <b>Para Canal VIP:</b>\n"
            f"<code>{url_vip}</code>\n\n"
            "💡 <b>Como usar:</b>\n"
            "1. Copie a URL acima\n"
            "2. Cole na descrição do canal\n"
            "3. Usuários podem clicar e pagar diretamente\n\n"
            "✅ As URLs são permanentes e funcionam sempre!"
        )

        await update.effective_message.reply_text(msg, parse_mode='HTML')

        # Log
        await log_to_group(f"🔗 Admin {update.effective_user.id} gerou URLs de pagamento")

    except Exception as e:
        await update.effective_message.reply_text(
            f"❌ Erro ao gerar URLs:\n<code>{str(e)}</code>",
            parse_mode='HTML'
        )


# =========================
# Error handler global
# =========================
# =========================
# Middleware para debug de mensagens
# =========================
async def log_all_updates(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Log todas as mensagens recebidas para debug"""
    if update.message:
        msg = update.message
        user = msg.from_user
        chat = msg.chat
        text = msg.text or "[sem texto]"

        logging.info(f"📨 [MESSAGE] User: {user.id} (@{user.username}) | Chat: {chat.id} ({chat.type}) | Text: {text[:100]}")
    elif update.callback_query:
        logging.info(f"📱 [CALLBACK] User: {update.callback_query.from_user.id} | Data: {update.callback_query.data}")
    else:
        logging.info(f"📬 [UPDATE] Type: {update.update_id}")

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
            # Obter username apenas se for ID real
            if not is_temp_uid:
                try:
                    u = await application.bot.get_chat(int(uid))
                    username = u.username or u.first_name or f"user_{uid}"
                    logging.info(f"Username obtido para UID {uid}: {username}")
                except Exception as e:
                    logging.warning(f"Erro ao obter dados do usuário {uid}: {e}")
                    username = f"user_{uid}"
            # Para temp UID, manter username do payload

            # Verificar se é ID temporário ou real (formato antigo "temp_*" ou novo timestamp)
            is_temp_uid = False
            if isinstance(uid, str) and uid.startswith("temp_"):
                is_temp_uid = True
            elif isinstance(uid, (int, str)):
                # Verificar se é um timestamp (UID temporário numérico)
                uid_num = int(uid) if isinstance(uid, str) else uid
                if 1600000000 <= uid_num <= 2000000000:
                    is_temp_uid = True
            
            if is_temp_uid:
                # Pagamento com ID temporário - será associado quando usuário entrar no grupo
                logging.info(f"[WEBHOOK] Pagamento aprovado com UID temporário: {uid}")
                # Criar VIP com user_id = 0 (será atualizado quando entrar no grupo)
                vip_upsert_start_or_extend(0, username, tx_hash, plan)
                user_id_final = 0
            else:
                # ID real fornecido
                user_id_final = int(uid)
                vip_upsert_start_or_extend(user_id_final, username, tx_hash, plan)
                logging.info(f"[WEBHOOK] Pagamento aprovado para usuário {user_id_final}")
            
            # Gerar convite apenas se for ID real
            invite_link = None
            if not is_temp_uid:
                try:
                    invite_link = await create_and_store_personal_invite(user_id_final)
                    logging.info(f"[WEBHOOK] Convite gerado com sucesso para {user_id_final}")
                except Exception as e:
                    logging.error(f"[WEBHOOK] Falha ao gerar convite pessoal: {e}")
                    # Fallback: usar função alternativa
                    try:
                        from utils import create_one_time_invite
                        invite_link = await create_one_time_invite(
                            application.bot, GROUP_VIP_ID, 
                            expire_seconds=7200, member_limit=1
                        )
                        logging.info(f"[WEBHOOK] Convite fallback gerado para {user_id_final}")
                    except Exception as e2:
                        logging.error(f"[WEBHOOK] Falha no fallback de convite: {e2}")
            else:
                # Para ID temporário, gerar link genérico do grupo
                try:
                    invite_link = await application.bot.export_chat_invite_link(GROUP_VIP_ID)
                    logging.info(f"[WEBHOOK] Link genérico do grupo gerado para temp UID")
                except Exception as e:
                    logging.error(f"[WEBHOOK] Falha ao gerar link genérico: {e}")
            
            # Notificar apenas se for ID real
            if not is_temp_uid and user_id_final > 0:
                if invite_link:
                    message_text = (
                        f"✅ Pagamento confirmado para {username}!\n"
                        f"Seu VIP foi ativado por {PLAN_DAYS[plan]} dias.\n"
                        f"Entre no VIP: {invite_link}"
                    )
                else:
                    message_text = (
                        f"✅ Pagamento confirmado para {username}!\n"
                        f"Seu VIP foi ativado por {PLAN_DAYS[plan]} dias.\n"
                        f"⚠️ Entre em contato com o suporte para receber seu convite VIP."
                    )

                # Tentar enviar mensagem privada
                try:
                    await application.bot.send_message(
                        chat_id=user_id_final,
                        text=message_text
                    )
                    logging.info(f"[WEBHOOK] Mensagem privada enviada com sucesso para {user_id_final}")
                except Exception as e:
                    # Se falhar (usuário não iniciou conversa), criar deep link
                    logging.warning(f"[WEBHOOK] Falha ao enviar privado para {user_id_final}: {e}")

                    if invite_link:
                        # Salvar o link VIP temporariamente com código único
                        import hashlib
                        vip_code = hashlib.md5(f"{user_id_final}{tx_hash}".encode()).hexdigest()[:8]
                        cfg_set(f"vip_link_{vip_code}", invite_link)
                        cfg_set(f"vip_code_{user_id_final}", vip_code)

                        # Obter username do bot
                        bot_info = await application.bot.get_me()
                        bot_username = bot_info.username

                        # Criar deep link
                        deep_link = f"https://t.me/{bot_username}?start=vip_{vip_code}"

                        # Enviar mensagem no grupo de logs
                        await log_to_group(
                            f"💳 <b>Novo Pagamento VIP Aprovado!</b>\n\n"
                            f"👤 Usuário: @{username} (ID: {user_id_final})\n"
                            f"📦 Plano: {PLAN_DAYS[plan]} dias\n"
                            f"💰 Valor: {amount}\n"
                            f"🔗 Hash: <code>{tx_hash[:16]}...</code>\n\n"
                            f"⚠️ <b>Usuário não iniciou conversa com o bot!</b>\n\n"
                            f"📲 Envie este link para o usuário:\n"
                            f"<code>{deep_link}</code>\n\n"
                            f"Ou peça para ele enviar /start para @{bot_username}"
                        )

                        logging.info(f"[WEBHOOK] Deep link criado: {deep_link}")
                    else:
                        # Sem link VIP, avisar no grupo de logs
                        await log_to_group(
                            f"💳 <b>Novo Pagamento VIP Aprovado!</b>\n\n"
                            f"👤 Usuário: @{username} (ID: {user_id_final})\n"
                            f"📦 Plano: {PLAN_DAYS[plan]} dias\n\n"
                            f"⚠️ Erro ao gerar link VIP!\n"
                            f"⚠️ Usuário não iniciou conversa com o bot!\n\n"
                            f"💡 Gere um convite manualmente com /debug_convite {user_id_final}"
                        )
            else:
                # Para ID temporário, apenas logar
                logging.info(f"[WEBHOOK] Pagamento processado com temp UID {uid}. Aguardando entrada no grupo para associação.")
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
            "$30.00": "30 dias (Mensal)",
            "$70.00": "90 dias (Trimestral)",
            "$110.00": "180 dias (Semestral)",
            "$179.00": "365 dias (Anual)"
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
                # Permitir UIDs temporários no formato antigo
                if isinstance(uid, str) and uid.startswith("temp_"):
                    uid_int = uid  # Manter como string para UIDs temporários
                    logging.info(f"[API-CONFIG] UID temporário aceito: {uid}")
                else:
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
        
        # ====== MODO TESTE - VALORES REDUZIDOS ======
        # Use estes valores para testar com quantias pequenas
        value_tiers = {
            "30": 30.00,   # Preço para 1 mês
            "90": 70.00,   # Preço para 3 meses
            "180": 110.00, # Preço para 6 meses
            "365": 179.00  # Preço para 1 ano
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
        
        # Garantir que UID seja numérico ou temporário válido
        try:
            # Permitir UIDs temporários no formato antigo
            if isinstance(uid, str) and uid.startswith("temp_"):
                uid_int = uid  # Manter como string para UIDs temporários
                logging.info(f"[API-VALIDATE] UID temporário aceito: {uid}")
            else:
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
            ok, msg, payload = await approve_by_usd_and_invite(uid_int, username, hash, notify_user=True)
            
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
    """Sistema completo de avisos de expiração com botões de renovação"""
    now = now_utc()
    
    with SessionLocal() as s:
        # Buscar VIPs ativos que ainda não expiraram
        membros = s.query(VipMembership).filter(
            VipMembership.active == True, 
            VipMembership.expires_at > now
        ).all()
        
        for m in membros:
            # Corrigir timezone se necessário
            expires_at = m.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=dt.timezone.utc)
                m.expires_at = expires_at
                s.commit()
            
            days_left = (expires_at - now).days
            hours_left = (expires_at - now).total_seconds() / 3600
            
            # Aviso 7 dias
            if days_left <= 7 and not m.notified_7_days:
                await send_expiration_warning(m, 7, days_left)
                m.notified_7_days = True
                s.commit()
                
            # Aviso 3 dias
            elif days_left <= 3 and not m.notified_3_days:
                await send_expiration_warning(m, 3, days_left)
                m.notified_3_days = True
                s.commit()
                
            # Aviso 1 dia (24 horas)
            elif hours_left <= 24 and not m.notified_1_day:
                await send_expiration_warning(m, 1, days_left, hours_left)
                m.notified_1_day = True
                s.commit()
        
        # Processar VIPs expirados
        expired_vips = s.query(VipMembership).filter(
            VipMembership.active == True,
            VipMembership.expires_at <= now,
            VipMembership.removal_scheduled == False
        ).all()
        
        for expired_vip in expired_vips:
            await process_expired_vip(expired_vip, s)

async def send_expiration_warning(vip_member: 'VipMembership', warning_days: int, days_left: int, hours_left: float = None):
    """Envia aviso de expiração com botão de renovação"""
    user_id = vip_member.user_id
    username = vip_member.username or f"user_{user_id}"
    
    # Criar botão de renovação
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🔄 RENOVAR VIP AGORA", 
            callback_data="renew_vip_callback"
        )],
        [InlineKeyboardButton(
            "ℹ️ Ver Planos", 
            callback_data="checkout_callback"
        )]
    ])
    
    # Determinar urgência da mensagem
    if warning_days == 7:
        emoji = "⚠️"
        urgency = "⏰ <b>LEMBRETE</b>"
        time_msg = f"em {days_left} dias"
    elif warning_days == 3:
        emoji = "🚨"
        urgency = "⚠️ <b>ATENÇÃO!</b>"
        time_msg = f"em apenas {days_left} dias"
    else:  # 1 dia
        emoji = "🚨"
        urgency = "🚨 <b>URGENTE!</b>"
        if hours_left <= 24:
            hours = int(hours_left)
            time_msg = f"em {hours} horas" if hours > 1 else "em menos de 1 hora"
        else:
            time_msg = "amanhã"
    
    expires_str = vip_member.expires_at.strftime("%d/%m/%Y às %H:%M")
    
    message = (
        f"{emoji} {urgency}\n\n"
        f"👤 Olá, {username}!\n\n"
        f"🔔 <b>Seu VIP expira {time_msg}!</b>\n"
        f"📅 <b>Data de expiração:</b> {expires_str}\n\n"
        f"💡 <b>Para continuar aproveitando:</b>\n"
        f"• Conteúdo exclusivo\n"
        f"• Acesso prioritário\n"
        f"• Suporte VIP\n\n"
        f"🔥 <b>Renove agora e não perca acesso!</b>\n"
        f"Clique no botão abaixo para renovar:"
    )
    
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        # Registrar notificação enviada
        with SessionLocal() as s:
            notification = VipNotification(
                user_id=user_id,
                notification_type=f"{warning_days}_days",
                vip_expires_at=vip_member.expires_at
            )
            s.add(notification)
            s.commit()
            
        logging.info(f"[VIP-WARNING] Aviso {warning_days} dias enviado para {user_id}")
        
    except Exception as e:
        logging.error(f"[VIP-WARNING] Erro ao enviar aviso para {user_id}: {e}")

async def renew_vip_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para botão de renovação VIP"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username
    
    logging.info(f"[VIP-RENEW] Usuário {user_id} ({username}) clicou em renovar VIP")
    
    try:
        with SessionLocal() as s:
            # Verificar se usuário tem VIP ativo/expirado
            vip_member = s.query(VipMembership).filter(
                VipMembership.user_id == user_id
            ).first()
            
            if not vip_member:
                await query.edit_message_text(
                    "❌ Você não possui um VIP cadastrado.\n"
                    "Use /checkout para adquirir seu primeiro VIP.",
                    parse_mode="HTML"
                )
                return
            
            # Criar mensagem explicativa sobre renovação
            expires_at = vip_member.expires_at
            if expires_at:
                expires_str = expires_at.strftime("%d/%m/%Y às %H:%M")
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                
                now = now_utc()
                is_expired = expires_at <= now
                
                if is_expired:
                    status_msg = f"❌ <b>VIP EXPIRADO</b> em {expires_str}"
                    renewal_msg = "✨ <b>RENOVAÇÃO:</b> Você receberá um novo período VIP completo!"
                else:
                    days_left = (expires_at - now).days
                    status_msg = f"✅ <b>VIP ATIVO</b> até {expires_str} ({days_left} dias)"
                    renewal_msg = "✨ <b>RENOVAÇÃO:</b> Seu VIP atual será substituído por um novo período completo!"
            else:
                status_msg = "⚠️ <b>Status indefinido</b>"
                renewal_msg = "✨ <b>RENOVAÇÃO:</b> Você receberá um novo período VIP!"
            
            # Criar botões para planos
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("💎 1 MÊS - $30.00", callback_data="renew_plan_30"),
                    InlineKeyboardButton("💎 3 MESES - $70.00", callback_data="renew_plan_90")
                ],
                [
                    InlineKeyboardButton("💎 6 MESES - $110.00", callback_data="renew_plan_180"),
                    InlineKeyboardButton("💎 1 ANO - $179.00", callback_data="renew_plan_365")
                ],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancel_renewal")]
            ])
            
            renewal_text = (
                f"🔄 <b>RENOVAÇÃO DE VIP</b>\n\n"
                f"👤 <b>Usuário:</b> {username or 'N/A'}\n"
                f"🆔 <b>ID:</b> <code>{user_id}</code>\n\n"
                f"{status_msg}\n\n"
                f"{renewal_msg}\n"
                f"❗ <b>IMPORTANTE:</b> A renovação substitui completamente seu VIP atual.\n\n"
                f"💰 <b>Escolha seu novo plano:</b>"
            )
            
            await query.edit_message_text(
                text=renewal_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            
    except Exception as e:
        logging.error(f"[VIP-RENEW] Erro ao processar renovação para {user_id}: {e}")
        await query.edit_message_text(
            "❌ Erro interno. Tente novamente ou contate o suporte.",
            parse_mode="HTML"
        )

async def renew_plan_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para seleção de plano de renovação"""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    username = query.from_user.username
    
    # Extrair dias do callback_data (renew_plan_30, renew_plan_60, etc.)
    callback_data = query.data
    if not callback_data.startswith("renew_plan_"):
        await query.edit_message_text("❌ Dados inválidos.")
        return
    
    try:
        days = int(callback_data.split("renew_plan_")[1])
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Plano inválido.")
        return
    
    # Mapeamento de dias para preços e descrições
    plan_info = {
        30: {"price": 30.00, "name": "1 Mês"},
        90: {"price": 70.00, "name": "3 Meses"},
        180: {"price": 110.00, "name": "6 Meses"},
        365: {"price": 179.00, "name": "1 Ano"}
    }
    
    if days not in plan_info:
        await query.edit_message_text("❌ Plano não encontrado.")
        return
    
    plan = plan_info[days]
    logging.info(f"[VIP-RENEW] Usuário {user_id} selecionou renovação: {plan['name']} - ${plan['price']}")
    
    try:
        with SessionLocal() as s:
            # Marcar VIP atual como inativo (será substituído)
            current_vip = s.query(VipMembership).filter(
                VipMembership.user_id == user_id
            ).first()
            
            if current_vip:
                # Manter dados do VIP atual para referência
                old_expires = current_vip.expires_at.strftime("%d/%m/%Y") if current_vip.expires_at else "N/A"
                current_vip.active = False
                current_vip.notes = f"Substituído por renovação em {now_utc().strftime('%d/%m/%Y %H:%M')}"
            
            # Criar novo pagamento temporário para renovação
            temp_payment_id = f"RENEW_{user_id}_{int(time.time())}"
            
            new_payment = Payment(
                user_id=None,  # Será associado quando o pagamento for confirmado
                temp_user_id=temp_payment_id,
                username=username,
                amount_usd=plan['price'],
                amount_crypto=0.0,  # Será preenchido no pagamento
                crypto_symbol="BNB",  # Default
                network="bsc",  # Default 
                wallet_address="",  # Será preenchido
                status="pending_payment",
                created_at=now_utc(),
                days_vip=days,
                plan=plan['name'].lower().replace(" ", "_")
            )
            
            s.add(new_payment)
            s.commit()
            
            # Criar mensagem de confirmação com instruções
            confirmation_text = (
                f"✅ <b>RENOVAÇÃO CONFIRMADA</b>\n\n"
                f"📦 <b>Plano Selecionado:</b> {plan['name']}\n"
                f"💰 <b>Valor:</b> ${plan['price']:.2f} USD\n"
                f"🔄 <b>Tipo:</b> Renovação (substitui VIP atual)\n\n"
                f"⚡ <b>PRÓXIMOS PASSOS:</b>\n"
                f"1️⃣ Clique em 'Pagar Agora' abaixo\n"
                f"2️⃣ Escolha a criptomoeda (BNB, ETH, USDT, etc.)\n"
                f"3️⃣ Faça o pagamento no valor exato mostrado\n"
                f"4️⃣ Seu VIP será ativado automaticamente!\n\n"
                f"❗ <b>IMPORTANTE:</b> Este pagamento substituirá completamente seu VIP atual."
            )
            
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("💳 PAGAR AGORA", callback_data=f"checkout_temp_{temp_payment_id}")],
                [InlineKeyboardButton("❌ Cancelar", callback_data="cancel_renewal")]
            ])
            
            await query.edit_message_text(
                text=confirmation_text,
                parse_mode="HTML",
                reply_markup=keyboard
            )
            
    except Exception as e:
        logging.error(f"[VIP-RENEW] Erro ao processar plano para {user_id}: {e}")
        await query.edit_message_text(
            "❌ Erro interno. Tente novamente ou contate o suporte.",
            parse_mode="HTML"
        )

async def cancel_renewal_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handler para cancelamento de renovação"""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "❌ <b>Renovação cancelada.</b>\n\n"
        "Para renovar depois, use o comando /status e clique em 'Renovar VIP'.",
        parse_mode="HTML"
    )

async def process_expired_vip(expired_vip: 'VipMembership', session):
    """Processa VIP expirado - desativa e remove do grupo"""
    user_id = expired_vip.user_id
    username = expired_vip.username or f"user_{user_id}"
    
    try:
        # 1. Desativar VIP
        expired_vip.active = False
        expired_vip.removal_scheduled = True
        
        # 2. Enviar notificação de expiração
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton(
                "🔄 REATIVAR VIP", 
                callback_data="renew_vip_callback"
            )]
        ])
        
        expire_msg = (
            f"❌ <b>SEU VIP EXPIROU!</b>\n\n"
            f"👤 {username}\n"
            f"📅 Expirou em: {expired_vip.expires_at.strftime('%d/%m/%Y às %H:%M')}\n\n"
            f"🚨 <b>Você será removido do grupo VIP em alguns minutos.</b>\n\n"
            f"💡 <b>Para reativar:</b>\n"
            f"• Clique no botão abaixo\n"
            f"• Escolha seu plano\n"
            f"• Faça o pagamento\n"
            f"• Retorne automaticamente ao grupo!\n\n"
            f"🔥 <b>Reative agora com desconto especial!</b>"
        )
        
        await application.bot.send_message(
            chat_id=user_id,
            text=expire_msg,
            parse_mode="HTML",
            reply_markup=keyboard
        )
        
        # 3. Remover do grupo VIP
        try:
            await application.bot.ban_chat_member(
                chat_id=GROUP_VIP_ID,
                user_id=user_id
            )
            # Desbanir imediatamente (só para remover, não bloquear permanentemente)
            await application.bot.unban_chat_member(
                chat_id=GROUP_VIP_ID,
                user_id=user_id
            )
            logging.info(f"[VIP-EXPIRED] Usuário {user_id} removido do grupo VIP")
        except Exception as remove_error:
            logging.error(f"[VIP-EXPIRED] Erro ao remover {user_id} do grupo: {remove_error}")
        
        # 4. Registrar notificação
        notification = VipNotification(
            user_id=user_id,
            notification_type="expired",
            vip_expires_at=expired_vip.expires_at
        )
        session.add(notification)
        session.commit()
        
        logging.info(f"[VIP-EXPIRED] VIP {user_id} processado: desativado e removido do grupo")
        
    except Exception as e:
        logging.error(f"[VIP-EXPIRED] Erro ao processar VIP expirado {user_id}: {e}")


async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    if not SELF_URL: return
    url = SELF_URL.rstrip("/") + "/keepalive"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url); logging.info(f"[keepalive] GET {url} -> {r.status_code}")
    except Exception as e: logging.warning(f"[keepalive] erro: {e}")

# ===== Guard global: só permite /tx para não-admin (em qualquer chat)
ALLOWED_NON_ADMIN = {"tx", "status", "novopack", "novopackvip", "novopackfree", "getid", "comandos", "listar_comandos"}

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
    if not BOT_TOKEN:
        logging.error("❌ BOT_TOKEN não está configurado!")
        logging.error("   Configure BOT_TOKEN ou TELEGRAM_BOT_TOKEN nas variáveis de ambiente")
        logging.error("   No Render: Settings → Environment → Add BOT_TOKEN")
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

        # ==== Middleware de debug (PRIMEIRO DE TODOS)
        application.add_handler(MessageHandler(filters.ALL, log_all_updates), group=-100)
        logging.info("✅ Middleware de debug ativado - todas as mensagens serão logadas")

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
        application.add_handler(conv_main, group=-50)

        conv_vip = ConversationHandler(
            entry_points=[CommandHandler("novopackvip", novopackvip_start, filters=filters.ChatType.PRIVATE)],
            states=states_map, fallbacks=[CommandHandler("cancelar", novopack_cancel)], allow_reentry=True,
        )
        application.add_handler(conv_vip, group=-50)

        conv_free = ConversationHandler(
            entry_points=[CommandHandler("novopackfree", novopackfree_start, filters=filters.ChatType.PRIVATE)],
            states=states_map, fallbacks=[CommandHandler("cancelar", novopack_cancel)], allow_reentry=True,
        )
        application.add_handler(conv_free, group=-50)

        # ===== Conversa /excluir_pack
        excluir_conv = ConversationHandler(
            entry_points=[CommandHandler("excluir_pack", excluir_pack_cmd)],
            states={DELETE_PACK_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, excluir_pack_confirm)]},
            fallbacks=[], allow_reentry=True,
        )
        application.add_handler(excluir_conv, group=-50)

        # ===== Conversa /excluir_todos_packs
        excluir_todos_conv = ConversationHandler(
            entry_points=[CommandHandler("excluir_todos_packs", excluir_todos_packs_cmd)],
            states={EXCLUIR_TODOS_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, excluir_todos_packs_confirm)]},
            fallbacks=[CommandHandler("cancelar", lambda u, c: ConversationHandler.END)],
            allow_reentry=True,
        )
        application.add_handler(excluir_todos_conv, group=-50)

        # ===== Handlers de storage
        application.add_handler(
            MessageHandler(
                (filters.Chat(STORAGE_GROUP_ID) | filters.Chat(STORAGE_GROUP_FREE_ID) | filters.Chat(PACK_ADMIN_CHAT_ID)) & filters.TEXT & ~filters.COMMAND,
                storage_text_handler
            ),
            group=1,
        )
        media_filter = (
            (filters.Chat(STORAGE_GROUP_ID) | filters.Chat(STORAGE_GROUP_FREE_ID) | filters.Chat(PACK_ADMIN_CHAT_ID)) &
            (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.AUDIO | filters.Document.ALL | filters.VOICE)
        )
        application.add_handler(MessageHandler(media_filter, storage_media_handler), group=1)

        # ===== Comandos gerais (group=1)
        application.add_handler(CommandHandler("start", start_cmd), group=1)
        application.add_handler(CommandHandler("index_files", index_files_cmd), group=1)  # Indexação automática
        application.add_handler(CommandHandler("comandos", comandos_cmd), group=5)
        application.add_handler(CommandHandler("listar_comandos", comandos_cmd), group=5)

        # Comandos de monitoramento para admin
        if MONITORING_COMMANDS_AVAILABLE:
            register_monitoring_commands(application)
            logging.info("✅ Sistema de monitoramento ativo")

        # Sistema de validação de pagamentos para admin
        if PAYMENT_VALIDATION_AVAILABLE:
            application.add_handler(CommandHandler("payment_test", vip_payment_test_cmd), group=1)
            application.add_handler(CommandHandler("payment_quick", vip_payment_quick_cmd), group=1)
            logging.info("✅ Sistema de validação de pagamentos ativo")
        application.add_handler(CommandHandler("getid", getid_cmd), group=1)
        application.add_handler(CommandHandler("debug_grupos", debug_grupos_cmd), group=1)
        application.add_handler(CommandHandler("debug_packs", debug_packs_cmd), group=1)
        application.add_handler(CommandHandler("limpar_packs_problematicos", limpar_packs_problematicos_cmd), group=1)

        application.add_handler(CommandHandler("say_vip", say_vip_cmd), group=1)
        application.add_handler(CommandHandler("say_free", say_free_cmd), group=1)
        application.add_handler(CommandHandler("test_mensagem_free", test_mensagem_free_cmd), group=1)

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
        application.add_handler(CommandHandler("debug_convite", debug_convite_cmd), group=1)
        application.add_handler(CommandHandler("fix_vip_dates", fix_vip_dates_cmd), group=1)
        application.add_handler(CommandHandler("migrate_vip_columns", migrate_vip_columns_cmd), group=1)
        application.add_handler(CommandHandler("comprovante", comprovante_cmd), group=1)
        application.add_handler(CommandHandler("recibo", comprovante_cmd), group=1)  # Alias
        application.add_handler(CommandHandler("status", status_cmd), group=1)
        application.add_handler(CommandHandler("pagar_vip", pagar_vip_cmd), group=1)

        # ===== Comandos do Sistema de Envio Automático
        application.add_handler(CommandHandler("stats_auto", stats_auto_cmd), group=1)
        application.add_handler(CommandHandler("reset_history", reset_history_cmd), group=1)
        application.add_handler(CommandHandler("confirmar_reset", confirmar_reset_cmd), group=1)
        application.add_handler(CommandHandler("test_send", test_send_cmd), group=1)
        application.add_handler(CommandHandler("catalogo", catalogo_cmd), group=1)
        application.add_handler(CommandHandler("debug_version", debug_version_cmd), group=1)
        application.add_handler(CommandHandler("check_files", check_files_cmd), group=1)
        application.add_handler(CommandHandler("get_chat_id", get_chat_id_cmd), group=1)
        application.add_handler(CommandHandler("check_permissions", check_permissions_cmd), group=1)
        application.add_handler(CommandHandler("scan_history", scan_history_cmd), group=1)
        application.add_handler(CommandHandler("scan_full", scan_full_cmd), group=1)
        application.add_handler(CommandHandler("listar_canais", listar_canais_cmd), group=1)
        application.add_handler(CommandHandler("gerar_url", gerar_url_pagamento_cmd), group=1)
        application.add_handler(CommandHandler("promo_free", promo_free_cmd), group=1)

        # Handler de indexação automática de arquivos do grupo fonte
        auto_index_filter = (
            filters.Chat(chat_id=SOURCE_CHAT_ID) &
            (filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.ANIMATION | filters.AUDIO)
        )
        application.add_handler(MessageHandler(auto_index_filter, auto_index_handler), group=2)
        logging.info(f"✅ Sistema de indexação automática configurado para grupo {SOURCE_CHAT_ID}")

        # ===== Member Join Handlers - para capturar ID quando usuário ENTRA no grupo

        # Handler para novos membros (new_chat_members)
        application.add_handler(
            MessageHandler(
                filters.StatusUpdate.NEW_CHAT_MEMBERS & filters.Chat(GROUP_VIP_ID),
                vip_member_joined_handler
            ),
            group=0  # Prioridade alta
        )

        # Handler para mudanças de status de membro (chat_member) - LOG DE MEMBROS
        from vip_manager import log_member_change
        application.add_handler(
            ChatMemberHandler(
                log_member_change,
                ChatMemberHandler.CHAT_MEMBER
            ),
            group=0
        )
        logging.info("✅ Sistema de log de membros ativado")

        # ===== Comandos de Gerenciamento VIP
        from vip_manager import view_member_logs_cmd, check_vip_status_cmd
        application.add_handler(CommandHandler("logs", view_member_logs_cmd), group=1)
        application.add_handler(CommandHandler("meu_vip", check_vip_status_cmd), group=1)
        logging.info("✅ Comandos /logs e /meu_vip registrados")
        
        # ===== Callback Query Handler - Checkout e Renovação
        application.add_handler(CallbackQueryHandler(checkout_callback_handler, pattern="checkout_callback"), group=1)
        application.add_handler(CallbackQueryHandler(renew_vip_callback_handler, pattern="renew_vip_callback"), group=1)
        application.add_handler(CallbackQueryHandler(renew_plan_callback_handler, pattern="renew_plan_"), group=1)
        application.add_handler(CallbackQueryHandler(cancel_renewal_callback_handler, pattern="cancel_renewal"), group=1)

        # ===== Sistema de Suporte =====
        from support import get_support_conversation_handler, tickets_cmd, reply_cmd, close_ticket_cmd, msg_cmd
        application.add_handler(get_support_conversation_handler(), group=-40)
        application.add_handler(CommandHandler("tickets", tickets_cmd), group=1)
        application.add_handler(CommandHandler("reply", reply_cmd), group=1)
        application.add_handler(CommandHandler("close_ticket", close_ticket_cmd), group=1)
        application.add_handler(CommandHandler("msg", msg_cmd), group=1)
        logging.info("✅ Sistema de suporte registrado (/tickets, /reply, /close_ticket, /msg)")

        # Jobs
        await _reschedule_daily_packs()
        _register_all_scheduled_messages(application.job_queue)

        application.job_queue.run_daily(vip_expiration_warn_job, time=dt.time(hour=9, minute=0, tzinfo=pytz.timezone("America/Sao_Paulo")), name="vip_warn")
        application.job_queue.run_repeating(keepalive_job, interval=dt.timedelta(minutes=4), first=dt.timedelta(seconds=20), name="keepalive")

        # ===== Job de Verificação de Expirações VIP =====
        from vip_manager import check_expirations
        application.job_queue.run_repeating(
            check_expirations,
            interval=dt.timedelta(hours=6),  # Verifica a cada 6 horas
            first=dt.timedelta(seconds=60),  # Primeira verificação após 1 minuto
            name="vip_expiration_check"
        )
        logging.info("✅ Sistema de verificação de expirações VIP ativado (a cada 6 horas)")

        # ===== Jobs do Sistema de Envio Automático =====

        # Job diário VIP (15h)
        async def daily_vip_job(context: ContextTypes.DEFAULT_TYPE):
            """Job diário para envio VIP (APENAS arquivo, sem mensagem de renovação no canal)"""
            with SessionLocal() as session:
                try:
                    # Enviar arquivo diário
                    await send_daily_vip_file(context.bot, session)

                    await log_to_group("✅ <b>Envio VIP diário concluído</b>")
                except Exception as e:
                    await log_to_group(f"❌ <b>Erro no envio VIP diário</b>\n⚠️ {str(e)}")
                    logging.error(f"Erro no job VIP diário: {e}")

        # Job semanal FREE (15h quartas) - APENAS arquivo
        async def weekly_free_file_job(context: ContextTypes.DEFAULT_TYPE):
            """Job semanal para envio de arquivo FREE (quartas 15h)"""
            with SessionLocal() as session:
                try:
                    # Envio de arquivo (apenas quartas)
                    await send_weekly_free_file(context.bot, session)
                    await log_to_group("✅ <b>Envio FREE semanal concluído</b>")
                except Exception as e:
                    await log_to_group(f"❌ <b>Erro no envio FREE semanal</b>\n⚠️ {str(e)}")
                    logging.error(f"Erro no job FREE semanal: {e}")

        # Job semanal de promoção FREE (15:30 quartas)
        async def weekly_free_promo_job(context: ContextTypes.DEFAULT_TYPE):
            """Job semanal para mensagem promocional FREE (quartas 15:30)"""
            # Verificar se é quarta-feira
            if datetime.now().weekday() != 2:  # 0=segunda, 2=quarta
                logging.info(f"[PROMO] Hoje não é quarta-feira, pulando mensagem promocional")
                return

            try:
                await send_promo_message_to_free(context.bot)
                await log_to_group("✅ <b>Mensagem promocional FREE enviada</b>")
            except Exception as e:
                await log_to_group(f"❌ <b>Erro ao enviar promoção FREE</b>\n⚠️ {str(e)}")
                logging.error(f"Erro no job de promoção FREE: {e}")

        # Registrar jobs
        BR_TZ = pytz.timezone('America/Sao_Paulo')

        # VIP: Diariamente às 15h
        application.job_queue.run_daily(
            daily_vip_job,
            time=dt.time(hour=15, minute=0, second=0, tzinfo=BR_TZ),
            name='daily_vip_send'
        )
        logging.info("✅ Job VIP diário configurado (15h)")

        # CATÁLOGO VIP: Atualiza lista de arquivos às 15:05 (após envio do arquivo)
        async def daily_vip_catalog_job(context: ContextTypes.DEFAULT_TYPE):
            """Job diário para atualizar catálogo de arquivos VIP"""
            with SessionLocal() as session:
                try:
                    await send_or_update_vip_catalog(context.bot, session)
                    await log_to_group("✅ <b>Catálogo VIP atualizado</b>")
                except Exception as e:
                    await log_to_group(f"❌ <b>Erro ao atualizar catálogo VIP</b>\n⚠️ {str(e)}")
                    logging.error(f"Erro no job catálogo VIP: {e}")

        application.job_queue.run_daily(
            daily_vip_catalog_job,
            time=dt.time(hour=15, minute=5, second=0, tzinfo=BR_TZ),
            name='daily_vip_catalog'
        )
        logging.info("✅ Job catálogo VIP configurado (15:05)")

        # FREE: Arquivo semanal às 15h (quartas)
        application.job_queue.run_daily(
            weekly_free_file_job,
            time=dt.time(hour=15, minute=0, second=0, tzinfo=BR_TZ),
            name='weekly_free_file'
        )
        logging.info("✅ Job FREE arquivo configurado (15h quartas)")

        # FREE: Promoção às 15:30 (quartas)
        application.job_queue.run_daily(
            weekly_free_promo_job,
            time=dt.time(hour=15, minute=30, second=0, tzinfo=BR_TZ),
            name='weekly_free_promo'
        )
        logging.info("✅ Job FREE promoção configurado (15:30 quartas)")

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

    if LOCAL_MODE:
        # ============ MODO LOCAL (POLLING) ============
        logging.info("🚀 Iniciando bot em MODO LOCAL (Polling)...")
        logging.info("📡 Bot buscará mensagens ativamente do Telegram")

        async def run_bot():
            # Executar startup event (inicializar handlers)
            logging.info("🔧 Executando inicialização do bot...")
            await on_startup()

            # Remover webhook se existir
            await application.bot.delete_webhook()
            logging.info("✅ Webhook removido")

            # Iniciar polling
            await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)

            logging.info("✅ Bot iniciado e escutando mensagens!")
            logging.info("📱 Pressione Ctrl+C para parar o bot")

            # Manter rodando
            stop_event = asyncio.Event()
            try:
                await stop_event.wait()
            except (KeyboardInterrupt, SystemExit):
                logging.info("🛑 Bot interrompido pelo usuário")
            finally:
                await application.updater.stop()
                await application.stop()
                await application.shutdown()
                logging.info("👋 Bot finalizado")

        # Executar bot
        asyncio.run(run_bot())
    else:
        # ============ MODO PRODUÇÃO (WEBHOOK) ============
        logging.info("🚀 Iniciando bot em MODO PRODUÇÃO (Webhook)...")
        logging.info("📡 Bot receberá mensagens via webhook HTTP")

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
