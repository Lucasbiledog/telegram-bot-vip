# main.py
import os
import logging
import asyncio
import datetime as dt
from typing import Optional, List, Dict, Any, Tuple

import html
import pytz
from dotenv import load_dotenv

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
)

# =========================
# Helpers
# =========================
def esc(s): return html.escape(str(s) if s is not None else "")
def now_utc(): return dt.datetime.utcnow()
import re

def wrap_ph(s: str) -> str:
    # Converte qualquer <algo> em <code>&lt;algo&gt;</code> para não quebrar o HTML
    return re.sub(r'<([^>\n]{1,80})>', r'<code>&lt;\1&gt;</code>', s)


from datetime import timedelta

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

# ----- Assinaturas
def vip_get(user_id: int) -> Optional['VipMembership']:
    with SessionLocal() as s:
        return s.query(VipMembership).filter(VipMembership.user_id == user_id).first()

def vip_upsert_start_or_extend(user_id: int, username: Optional[str], tx_hash: Optional[str], extra_days: int = 30) -> 'VipMembership':
    now = now_utc()
    with SessionLocal() as s:
        m = s.query(VipMembership).filter(VipMembership.user_id == user_id).first()
        if not m:
            m = VipMembership(
                user_id=user_id,
                username=username,
                tx_hash=tx_hash,
                start_at=now,
                expires_at=now + timedelta(days=extra_days),
                active=True,
            )
            s.add(m)
        else:
            # Se ainda ativo, soma dias a partir do expires_at; senão reinicia a partir de agora
            base = m.expires_at if m.active and m.expires_at and m.expires_at > now else now
            m.expires_at = base + timedelta(days=extra_days)
            m.tx_hash = tx_hash or m.tx_hash
            m.active = True
            m.username = username or m.username
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

async def dm(user_id: int, text: str, parse_mode: Optional[str] = "HTML"):
    try:
        await application.bot.send_message(chat_id=user_id, text=text, parse_mode=parse_mode)
    except Exception as e:
        logging.warning(f"Falha ao enviar DM para {user_id}: {e}")

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
WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS", "").strip() or "").lower()
CHAIN_NAME     = os.getenv("CHAIN_NAME", "Polygon").strip()
RPC_URL        = os.getenv("RPC_URL", "").strip()
AUTO_APPROVE_CRYPTO = os.getenv("AUTO_APPROVE_CRYPTO", "1") == "1"
MIN_CONFIRMATIONS = int(os.getenv("MIN_CONFIRMATIONS", "5"))

# Nativo
MIN_NATIVE_AMOUNT = float(os.getenv("MIN_NATIVE_AMOUNT", "0"))  # em moeda nativa

# ERC-20 (opcional)
TOKEN_CONTRACT   = (os.getenv("TOKEN_CONTRACT", "").strip() or "").lower()
TOKEN_DECIMALS   = int(os.getenv("TOKEN_DECIMALS", "18"))
MIN_TOKEN_AMOUNT = float(os.getenv("MIN_TOKEN_AMOUNT", "0"))

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

# =========================
# DB setup
# =========================
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text, BigInteger, UniqueConstraint, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.engine import make_url

DB_URL = os.getenv("DATABASE_URL", "sqlite:///./bot_data.db")
url = make_url(DB_URL)

if url.get_backend_name().startswith("sqlite"):
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(DB_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

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
    tx_hash = Column(String, nullable=True)  # última transação que ativou/renovou
    start_at = Column(DateTime, nullable=False, default=now_utc)
    expires_at = Column(DateTime, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_utc)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)




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


def ensure_bigint_columns():
    if not url.get_backend_name().startswith("postgresql"): return
    try:
        with engine.begin() as conn:
            try: conn.execute(text("ALTER TABLE admins   ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint"))
            except Exception: pass
            try: conn.execute(text("ALTER TABLE payments ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint"))
            except Exception: pass
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
    except Exception: pass

def ensure_packfile_src_columns():
    try:
        with engine.begin() as conn:
            try: conn.execute(text("ALTER TABLE pack_files ADD COLUMN src_chat_id BIGINT"))
            except Exception: pass
            try: conn.execute(text("ALTER TABLE pack_files ADD COLUMN src_message_id INTEGER"))
            except Exception: pass
    except Exception as e:
        logging.warning("Falha em ensure_packfile_src_columns: %s", e)

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

ensure_bigint_columns(); ensure_pack_tier_column(); ensure_packfile_src_columns(); init_db()

# =========================
# DB helpers
# =========================
def is_admin(user_id: int) -> bool:
   with SessionLocal() as s:
        return s.query(Admin).filter(Admin.user_id == user_id).first() is not None

def list_admin_ids() -> List[int]:
    with SessionLocal() as s:
        return [a.user_id for a in s.query(Admin).order_by(Admin.added_at.asc()).all()]

def add_admin_db(user_id: int) -> bool:
   with SessionLocal() as s:
        try:
            if s.query(Admin).filter(Admin.user_id == user_id).first():
                return False
            s.add(Admin(user_id=user_id))
            s.commit()
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
            return True
        except Exception:
            s.rollback()
            raise

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

def add_file_to_pack(pack_id: int, file_id: str, file_unique_id: Optional[str], file_type: str, role: str,
     file_name: Optional[str] = None, src_chat_id: Optional[int] = None, src_message_id: Optional[int] = None):
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

def list_packs_by_tier(tier: str) -> List['Pack']:
    with SessionLocal() as s:
        return (
            s.query(Pack)
             .filter(Pack.tier == tier)
             .order_by(Pack.created_at.asc())
             .all()
        )

# =========================
# STORAGE GROUP handlers
# =========================
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

    add_file_to_pack(
        pack_id=pack.id, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role,
        file_name=visible_name, src_chat_id=msg.chat.id, src_message_id=msg.message_id
    )
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b> — <i>{pack.tier.upper()}</i>.", parse_mode="HTML")

# =========================
# ENVIO DO PACK (JobQueue) com fallback copy_message
# =========================
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
    except Exception as e:
        logging.warning(f"[send_{pf.file_type}] Falha {pf.id}: {e}. Tentando copy_message.")
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
        if not pack: return f"Nenhum pack pendente para envio ({tier})."

        with SessionLocal() as s:
            p = s.query(Pack).filter(Pack.id == pack.id).first()
            files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()

        if not files:
            mark_pack_sent(p.id); return f"Pack '{p.title}' ({tier}) não possui arquivos. Marcado como enviado."

        previews = [f for f in files if f.role == "preview"]
        docs     = [f for f in files if f.role == "file"]

        if previews:
            await _send_preview_media(context, target_chat_id, previews)
        await context.application.bot.send_message(chat_id=target_chat_id, text=p.title)
        for f in docs:
            await _try_send_document_like(context, target_chat_id, f, caption=None)

        if tier == "vip" and previews:
            try:
                await _send_preview_media(context, GROUP_FREE_ID, previews)
                await context.application.bot.send_message(chat_id=GROUP_FREE_ID, text=FREE_PREVIEW_TEXT)
            except Exception as e:
                logging.warning(f"Falha no crosspost VIP->FREE: {e}")

        mark_pack_sent(p.id)
        return f"✅ Enviado pack '{p.title}' ({tier})."
    except Exception as e:
        logging.exception("Erro no enviar_pack_job"); return f"❌ Erro no envio ({tier}): {e!r}"

async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    return await enviar_pack_job(context, tier="vip",  target_chat_id=GROUP_VIP_ID)

async def enviar_pack_free_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    return await enviar_pack_job(context, tier="free", target_chat_id=GROUP_FREE_ID)

# =========================
# COMMANDS & ADMIN
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = ("Fala! Eu gerencio packs VIP/FREE, pagamentos via MetaMask e mensagens agendadas.\nUse /comandos para ver tudo.")
    if msg: await msg.reply_text(text)

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    isadm = is_admin(update.effective_user.id) if update.effective_user else False
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
    ]
    adm = [
        "",
        "🛠 <b>Admin</b>",
        "• /simularvip — envia o próximo pack VIP pendente",
        "• /simularfree — envia o próximo pack FREE pendente",
        "• /listar_packsvip — lista packs VIP",
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
    ]
    lines = base + (adm if isadm else [])
    safe_lines = [wrap_ph(x) for x in lines]  # <<<<<< AQUI
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
    m = vip_upsert_start_or_extend(uid, None, None, extra_days=dias)
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
    ok = vip_deactivate(uid)
    if ok:
        try:
            await application.bot.ban_chat_member(chat_id=GROUP_VIP_ID, user_id=uid)
            await application.bot.unban_chat_member(chat_id=GROUP_VIP_ID, user_id=uid)
        except Exception:
            pass
        await update.effective_message.reply_text("✅ VIP removido/desativado.")
    else:
        await update.effective_message.reply_text("Usuário não era VIP.")


async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    status = await enviar_pack_vip_job(context); await update.effective_message.reply_text(status)

async def simularfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    status = await enviar_pack_free_job(context); await update.effective_message.reply_text(status)

async def listar_packsvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    with SessionLocal() as s:
        packs = list_packs_by_tier("vip")
        if not packs:
            return await update.effective_message.reply_text("Nenhum pack VIP registrado.")
        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        await update.effective_message.reply_text("\n".join(lines))
    

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
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"  # keccak("Transfer(address,address,uint256)")

def _hex_to_int(h: Optional[str]) -> int:
    if not h: return 0
    return int(h, 16) if h.startswith(HEX_0X) else int(h)

def _to_wei(amount_native: float, decimals: int = 18) -> int:
    return int(round(amount_native * (10 ** decimals)))

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

async def verify_tx_any(tx_hash: str) -> Dict[str, Any]:
    if TOKEN_CONTRACT:
        res = await verify_erc20_payment(tx_hash)
        if res.get("ok"): return res
        return res
    else:
        return await verify_native_payment(tx_hash)

# =========================
# Pagamento – comandos
# =========================
async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WALLET_ADDRESS:
        return await update.effective_message.reply_text("Método de pagamento não configurado. (WALLET_ADDRESS ausente)")
    user = update.effective_user
    texto = (
        f"💸 <b>Pagamento via MetaMask</b>\n"
        f"1) Abra a MetaMask e selecione a rede <b>{esc(CHAIN_NAME)}</b>.\n"
        f"2) Envie o valor para a carteira:\n<code>{esc(WALLET_ADDRESS)}</code>\n"
        f"3) Depois envie aqui: <code>/tx &lt;hash_da_transacao&gt;</code>\n\n"
        f"⚙️ O sistema valida on-chain (confirmações mín.: {MIN_CONFIRMATIONS}).\n"
        f"✅ Confirmando, você recebe o link do VIP no privado."
    )
    await dm(user.id, texto)
    if update.effective_chat.type != "private":
        try:
            await update.effective_message.delete()
        except Exception:
            pass
        await update.effective_chat.send_message("Te enviei o passo a passo no privado. 👌")

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not context.args:
        return await msg.reply_text("Uso: /tx <hash_da_transacao>")
    tx_hash = context.args[0].strip().lower()

    # === Verifica se já existe no banco
    with SessionLocal() as s:
        existing = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()

    if existing:
        if existing.status == "approved":
            try:
                invite = await application.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                await dm(user.id, f"✅ Seu pagamento já estava aprovado!\nEntre no VIP: {invite}", parse_mode=None)
                return await msg.reply_text("Esse hash já estava aprovado. Reenviei o convite no seu privado. ✅")
            except Exception as e:
                return await msg.reply_text(f"Hash já aprovado, mas falhou ao reenviar o convite: {e}")
        elif existing.status == "pending":
            return await msg.reply_text("Esse hash já foi registrado e está pendente. Aguarde a validação.")
        else:
            return await msg.reply_text("Esse hash já foi rejeitado. Fale com um administrador.")

    # === Verificação on-chain
    try:
        res = await verify_tx_any(tx_hash)
    except Exception as e:
        logging.exception("Erro verificando transação")
        return await msg.reply_text(f"❌ Erro ao verificar on-chain: {e}")
        # === Verificação de valor mínimo conforme configuração
    paid_ok = res.get("ok", False)
    if paid_ok:
        if TOKEN_CONTRACT:
            # preço em token (unidades "humanas")
            price_tok = get_vip_price_token()
            if price_tok is not None:
                amount_raw = res.get("amount_raw")
                min_units = int(round(price_tok * (10 ** TOKEN_DECIMALS)))
                if amount_raw is None or amount_raw < min_units:
                    paid_ok = False
        else:
            price_nat = get_vip_price_native()
            if price_nat is not None:
                amount_wei = res.get("amount_wei")
                if amount_wei is None or amount_wei < _to_wei(price_nat, 18):
                    paid_ok = False

    if not paid_ok:
        # registra como pendente com razão
        human_reason = res.get("reason") or "Valor insuficiente"
        return await msg.reply_text(f"⏳ Hash recebido. Status: {human_reason or 'Valor insuficiente'}")


    status = "approved" if (AUTO_APPROVE_CRYPTO and res.get("ok")) else "pending"
    with SessionLocal() as s:
        try:
            p = Payment(
                user_id=user.id,
                username=user.username,
                tx_hash=tx_hash,
                chain=CHAIN_NAME,
                status=status,
                amount=str(res.get("amount_wei") or res.get("amount_raw") or ""),
                decided_at=now_utc() if status == "approved" else None,
            )
            s.add(p)
            s.commit()
        except Exception:
            s.rollback()
            raise

    if status == "approved":
        try:
            invite = await application.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
            await dm(user.id, f"✅ Pagamento confirmado na rede {CHAIN_NAME}!\nEntre no VIP: {invite}", parse_mode=None)
            return await msg.reply_text("✅ Verifiquei sua transação e já liberei seu acesso. Confira seu privado.")
        except Exception as e:
            logging.exception("Erro enviando invite auto-approve")
            return await msg.reply_text(f"Pagamento OK, mas falhou ao enviar o convite: {e}")
    else:
        human = res.get("reason", "Aguardando confirmação dos nós.")
        await msg.reply_text(f"⏳ Hash recebido. Status: {human}")
        # Notifica admins
        try:
            for aid in list_admin_ids():
                txt = f"📥 Pagamento pendente:\nuser_id:{user.id} @{user.username or '-'}\nhash:{tx_hash}\ninfo:{human}"
                await dm(aid, txt, parse_mode=None)
        except Exception:
            pass


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
    

async def aprovar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /aprovar_tx <user_id>")
    try: uid = int(context.args[0])
    except: return await update.effective_message.reply_text("user_id inválido.")
    with SessionLocal() as s:
        try:
            p = s.query(Payment).filter(Payment.user_id == uid, Payment.status == "pending").order_by(Payment.created_at.asc()).first()
            if not p:
                return await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
            p.status = "approved"
            p.decided_at = now_utc()
            s.commit()
            try:
                invite = await application.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                await application.bot.send_message(chat_id=uid, text=f"✅ Pagamento aprovado! Entre no VIP: {invite}")
                await update.effective_message.reply_text(f"Aprovado e convite enviado para {uid}.")
            except Exception as e:
                logging.exception("Erro enviando invite")
                await update.effective_message.reply_text(f"Aprovado, mas falhou ao enviar convite: {e}")
        except Exception as e:
             s.rollback()
             await update.effective_message.reply_text(f"❌ Erro ao aprovar: {e}")
        
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
    data = await request.json()
    uid = data.get("telegram_user_id")
    tx_hash = data.get("tx_hash")
    amount = data.get("amount")
    chain = data.get("chain") or CHAIN_NAME
    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)

    try:
        res = await verify_tx_any(tx_hash)
    except Exception as e:
        logging.exception("Erro verificando no webhook"); 
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
    with SessionLocal() as s:
        try:
            pay = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
            if not pay:
                pay = Payment(
                    user_id=int(uid),
                    tx_hash=tx_hash,
                    amount=str(res.get('amount_wei') or res.get('amount_raw') or amount or ""),
                    chain=chain,
                    status="approved" if res.get("ok") else "pending",
                    decided_at=now_utc() if res.get("ok") else None,
                )
                s.add(pay)
            else:
                pay.status = "approved" if res.get("ok") else "pending"
                pay.decided_at = now_utc() if res.get("ok") else None
            s.commit()
        except Exception:
            s.rollback()
            raise

    if res.get("ok"):
        try:
            invite = await application.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
            await application.bot.send_message(chat_id=int(uid), text=f"✅ Pagamento confirmado! Entre no VIP: {invite}")
        except Exception: logging.exception("Erro enviando invite")
    return JSONResponse({"ok": True, "verified": res.get("ok"), "reason": res.get("reason")})

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

# =========================
# Guards: ignorar comandos de não-admin em grupos
# =========================
async def _ignore_non_admin_commands_in_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat; user = update.effective_user
    if not chat or not user:
        return
    if chat.type in ("group", "supergroup") and not is_admin(user.id):
        text = update.effective_message.text or ""
        cmd = text.split()[0].lower()
        if cmd == "/pagar":
            return
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

    # ==== Guard (tem que vir ANTES)
    application.add_handler(MessageHandler(filters.COMMAND & filters.ChatType.GROUPS, _ignore_non_admin_commands_in_groups), group=-1)

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
    application.add_handler(CommandHandler("comandos", comandos_cmd), group=1)
    application.add_handler(CommandHandler("listar_comandos", comandos_cmd), group=1)
    application.add_handler(CommandHandler("getid", getid_cmd), group=1)

    application.add_handler(CommandHandler("say_vip", say_vip_cmd), group=1)
    application.add_handler(CommandHandler("say_free", say_free_cmd), group=1)

    application.add_handler(CommandHandler("simularvip", simularvip_cmd), group=1)
    application.add_handler(CommandHandler("simularfree", simularfree_cmd), group=1)
    application.add_handler(CommandHandler("listar_packsvip", listar_packsvip_cmd), group=1)
    application.add_handler(CommandHandler("listar_packsfree", listar_packsfree_cmd), group=1)
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

    # Jobs
    await _reschedule_daily_packs()
    _register_all_scheduled_messages(application.job_queue)
    
    application.job_queue.run_daily(vip_expiration_warn_job, time=dt.time(hour=9, minute=0, tzinfo=pytz.timezone("America/Sao_Paulo")), name="vip_warn")
    application.job_queue.run_repeating(keepalive_job, interval=dt.timedelta(minutes=4), first=dt.timedelta(seconds=20), name="keepalive")
    logging.info("Handlers e jobs registrados.")

# =========================
# Run
# =========================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
