# main.py
import os
import logging
import asyncio
import datetime as dt
from typing import Optional, List, Dict, Any, Tuple
import html
import json
from urllib.parse import urlsplit

import pytz
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

import uvicorn
import httpx

from telegram import Update, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    JobQueue,
    ConversationHandler,
    filters,
)

# SQLAlchemy
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text, BigInteger, UniqueConstraint, text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.engine import make_url

# =========================
# Helpers
# =========================
def esc(s):
    return html.escape(str(s) if s is not None else "")

def now_utc():
    return dt.datetime.utcnow()

def parse_hhmm(s: str) -> Tuple[int, int]:
    s = (s or "").strip()
    if ":" not in s:
        raise ValueError("Formato inválido; use HH:MM")
    hh, mm = s.split(":", 1)
    h = int(hh); m = int(mm)
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError("Hora fora do intervalo 00:00–23:59")
    return h, m

def json_bool(val: str, default=False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in {"1","true","yes","on","y","sim"}

# =========================
# ENV / CONFIG
# =========================
load_dotenv()

BOT_TOKEN   = os.getenv("BOT_TOKEN")  # mantemos no Render
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # exemplo: https://seu-dominio/webhook

# Grupos
GROUP_VIP_ID  = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))     # para ENVIAR packs VIP
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID", "-1002509364079"))    # para ENVIAR teasers FREE

# Grupos onde é possível CADASTRAR packs (storages)
STORAGE_VIP_ID  = int(os.getenv("STORAGE_VIP_ID", str(GROUP_VIP_ID)))    # storage VIP (pode ser o mesmo do VIP)
STORAGE_FREE_ID = int(os.getenv("STORAGE_FREE_ID", str(GROUP_FREE_ID)))   # storage FREE
STORAGE_IDS = {STORAGE_VIP_ID, STORAGE_FREE_ID}

PORT = int(os.getenv("PORT", 8000))

# Pagamento cripto (somente MetaMask / carteiras EVM)
DEFAULT_WALLET_ADDRESS = "0x40dDBD27F878d07808339F9965f013F1CBc2F812"
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", DEFAULT_WALLET_ADDRESS).strip()
DEFAULT_CHAIN = os.getenv("CHAIN_NAME", "Polygon").strip()
SUPPORTED_CHAINS = [
    "Polygon", "Ethereum", "BSC", "Arbitrum", "Optimism", "Base", "Avalanche"
]

# Admin PIN para /iam_admin
ADMIN_PIN = os.getenv("ADMIN_PIN", "4242")

# Mantém serviço “acordado” no Render
KEEPALIVE_ENABLED = json_bool(os.getenv("KEEPALIVE_ENABLED", "true"), True)
HEALTH_PING_URL = os.getenv("HEALTH_PING_URL", None)  # se não setar, tentamos usar a origem do WEBHOOK_URL
KEEPALIVE_SECONDS = int(os.getenv("KEEPALIVE_SECONDS", "240"))

if not BOT_TOKEN:
    raise RuntimeError("Defina BOT_TOKEN em variáveis de ambiente.")

# =========================
# FASTAPI + PTB
# =========================
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None

# =========================
# DB setup
# =========================
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./bot_data.db")
url = make_url(DB_URL)

if url.get_backend_name().startswith("sqlite"):
    engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DB_URL,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

# ---- Config chave/valor persistente ----
class ConfigKV(Base):
    __tablename__ = "config_kv"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)

def cfg_get(key: str, default: Optional[str] = None) -> Optional[str]:
    s = SessionLocal()
    try:
        row = s.query(ConfigKV).filter(ConfigKV.key == key).first()
        return row.value if row else default
    finally:
        s.close()

def cfg_set(key: str, value: Optional[str]):
    s = SessionLocal()
    try:
        row = s.query(ConfigKV).filter(ConfigKV.key == key).first()
        if not row:
            row = ConfigKV(key=key, value=value)
            s.add(row)
        else:
            row.value = value
        s.commit()
    finally:
        s.close()

# ---- Admins ----
class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)  # BIGINT
    added_at = Column(DateTime, default=now_utc)

# ---- Packs ----
class Pack(Base):
    __tablename__ = "packs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    audience = Column(String, default="vip")  # 'vip' | 'free'
    storage_chat_id = Column(BigInteger, nullable=True)
    header_message_id = Column(Integer, nullable=True, unique=True)
    created_at = Column(DateTime, default=now_utc)
    sent = Column(Boolean, default=False)
    files = relationship("PackFile", back_populates="pack", cascade="all, delete-orphan")

class PackFile(Base):
    __tablename__ = "pack_files"
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(Integer, ForeignKey("packs.id", ondelete="CASCADE"))
    file_id = Column(String, nullable=False)
    file_unique_id = Column(String, nullable=True)
    file_type = Column(String, nullable=True)   # photo, video, animation, document, audio, voice
    role = Column(String, nullable=True)        # preview | file
    file_name = Column(String, nullable=True)
    added_at = Column(DateTime, default=now_utc)
    pack = relationship("Pack", back_populates="files")

# ---- Pagamentos ----
class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)  # BIGINT
    username = Column(String, nullable=True)
    tx_hash = Column(String, unique=True, index=True)
    chain = Column(String, default=DEFAULT_CHAIN)
    amount = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | approved | rejected
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc)
    decided_at = Column(DateTime, nullable=True)

# ---- Mensagens agendadas (VIP/FREE) ----
class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    id = Column(Integer, primary_key=True)
    hhmm = Column(String, nullable=False)      # "HH:MM"
    tz = Column(String, default="America/Sao_Paulo")
    text = Column(Text, nullable=False)
    target = Column(String, default="vip")     # 'vip' | 'free'
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_utc)
    __table_args__ = (UniqueConstraint('id', name='uq_scheduled_messages_id'),)

def ensure_bigint_columns():
    """Migra colunas user_id para BIGINT no Postgres (safe idempotente)."""
    if not url.get_backend_name().startswith("postgresql"):
        return
    try:
        with engine.begin() as conn:
            try:
                conn.execute(text("ALTER TABLE admins   ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint"))
            except Exception:
                pass
            try:
                conn.execute(text("ALTER TABLE payments ALTER COLUMN user_id TYPE BIGINT USING user_id::bigint"))
            except Exception:
                pass
    except Exception as e:
        logging.warning("Falha em ensure_bigint_columns: %s", e)

def ensure_schema_migrations():
    """Tenta adicionar colunas novas sem quebrar (SQLite/Postgres)."""
    with engine.begin() as conn:
        # Packs.audience / storage_chat_id
        try:
            conn.execute(text("ALTER TABLE packs ADD COLUMN audience VARCHAR"))
        except Exception:
            pass
        try:
            conn.execute(text("ALTER TABLE packs ADD COLUMN storage_chat_id BIGINT"))
        except Exception:
            pass
        # ScheduledMessage.target
        try:
            if url.get_backend_name().startswith("postgresql"):
                conn.execute(text("ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS target VARCHAR"))
            else:
                conn.execute(text("ALTER TABLE scheduled_messages ADD COLUMN target VARCHAR"))
        except Exception:
            pass

def init_db():
    Base.metadata.create_all(bind=engine)
    ensure_bigint_columns()
    ensure_schema_migrations()

    # Admin inicial
    initial_admin_id = os.getenv("INITIAL_ADMIN_ID")
    if initial_admin_id:
        s = SessionLocal()
        try:
            uid = int(initial_admin_id)
            if not s.query(Admin).filter(Admin.user_id == uid).first():
                s.add(Admin(user_id=uid))
                s.commit()
        finally:
            s.close()

    # Defaults de config
    if not cfg_get("daily_pack_hhmm"):
        cfg_set("daily_pack_hhmm", "09:00")
    if not cfg_get("vip_price_amount"):
        cfg_set("vip_price_amount", "25")
    if not cfg_get("vip_price_currency"):
        cfg_set("vip_price_currency", "USDT")
    if not cfg_get("free_teaser_tpl"):
        cfg_set("free_teaser_tpl", "Hoje foi liberado no grupo VIP este asset: <b>{title}</b>\n"
                                   "Para participar do VIP, digite /grupovip")

init_db()

# =========================
# DB helpers
# =========================
def is_admin(user_id: int) -> bool:
    s = SessionLocal()
    try:
        return s.query(Admin).filter(Admin.user_id == user_id).first() is not None
    finally:
        s.close()

def list_admin_ids() -> List[int]:
    s = SessionLocal()
    try:
        return [a.user_id for a in s.query(Admin).order_by(Admin.added_at.asc()).all()]
    finally:
        s.close()

def add_admin_db(user_id: int) -> bool:
    s = SessionLocal()
    try:
        if s.query(Admin).filter(Admin.user_id == user_id).first():
            return False
        s.add(Admin(user_id=user_id))
        s.commit()
        return True
    finally:
        s.close()

def remove_admin_db(user_id: int) -> bool:
    s = SessionLocal()
    try:
        a = s.query(Admin).filter(Admin.user_id == user_id).first()
        if not a:
            return False
        s.delete(a)
        s.commit()
        return True
    finally:
        s.close()

def create_pack(title: str, audience: str = "vip", storage_chat_id: Optional[int] = None,
                header_message_id: Optional[int] = None) -> Pack:
    s = SessionLocal()
    try:
        p = Pack(title=title.strip(), audience=audience, storage_chat_id=storage_chat_id,
                 header_message_id=header_message_id)
        s.add(p)
        s.commit()
        s.refresh(p)
        return p
    finally:
        s.close()

def get_pack_by_header(message_id: int) -> Optional[Pack]:
    s = SessionLocal()
    try:
        return s.query(Pack).filter(Pack.header_message_id == message_id).first()
    finally:
        s.close()

def add_file_to_pack(pack_id: int, file_id: str, file_unique_id: Optional[str], file_type: str, role: str, file_name: Optional[str] = None):
    s = SessionLocal()
    try:
        pf = PackFile(
            pack_id=pack_id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_type=file_type,
            role=role,
            file_name=file_name,
        )
        s.add(pf)
        s.commit()
        s.refresh(pf)
        return pf
    finally:
        s.close()

def get_next_unsent_pack() -> Optional[Pack]:
    s = SessionLocal()
    try:
        return s.query(Pack).filter(Pack.sent == False).order_by(Pack.created_at.asc()).first()
    finally:
        s.close()

def mark_pack_sent(pack_id: int):
    s = SessionLocal()
    try:
        p = s.query(Pack).filter(Pack.id == pack_id).first()
        if p:
            p.sent = True
            s.commit()
    finally:
        s.close()

def list_packs_db():
    s = SessionLocal()
    try:
        return s.query(Pack).order_by(Pack.created_at.desc()).all()
    finally:
        s.close()

# ---- Scheduled messages helpers ----
def scheduled_all(target: Optional[str] = None) -> List['ScheduledMessage']:
    s = SessionLocal()
    try:
        q = s.query(ScheduledMessage)
        if target:
            q = q.filter(ScheduledMessage.target == target)
        return q.order_by(ScheduledMessage.hhmm.asc(), ScheduledMessage.id.asc()).all()
    finally:
        s.close()

def scheduled_get(sid: int) -> Optional['ScheduledMessage']:
    s = SessionLocal()
    try:
        return s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
    finally:
        s.close()

def scheduled_create(hhmm: str, text: str, target: str = "vip", tz_name: str = "America/Sao_Paulo") -> 'ScheduledMessage':
    s = SessionLocal()
    try:
        m = ScheduledMessage(hhmm=hhmm, text=text, target=target, tz=tz_name, enabled=True)
        s.add(m)
        s.commit()
        s.refresh(m)
        return m
    finally:
        s.close()

def scheduled_update(sid: int, hhmm: Optional[str], text: Optional[str]) -> bool:
    s = SessionLocal()
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
    finally:
        s.close()

def scheduled_toggle(sid: int) -> Optional[bool]:
    s = SessionLocal()
    try:
        m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
        if not m:
            return None
        m.enabled = not m.enabled
        s.commit()
        return m.enabled
    finally:
        s.close()

def scheduled_delete(sid: int) -> bool:
    s = SessionLocal()
    try:
        m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
        if not m:
            return False
        s.delete(m)
        s.commit()
        return True
    finally:
        s.close()

# =========================
# Permissões / contexto
# =========================
def _is_storage_or_private(update: Update) -> bool:
    chat = update.effective_chat
    if not chat:
        return False
    if chat.type == "private":
        return True
    return chat.id in STORAGE_IDS

def _require_admin_here(update: Update) -> bool:
    return bool(update.effective_user and is_admin(update.effective_user.id) and _is_storage_or_private(update))

def _audience_from_chat(chat_id: int) -> str:
    if chat_id == STORAGE_FREE_ID:
        return "free"
    if chat_id == STORAGE_VIP_ID:
        return "vip"
    return "vip"

# =========================
# STORAGE GROUP handlers
# =========================
async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or chat.id not in STORAGE_IDS:
        return
    if msg.reply_to_message:
        return

    title = (msg.text or "").strip()
    if not title:
        return

    # Heurística simples pra reconhecer título
    lower = title.lower()
    if lower in {"sim", "não", "nao", "/proximo", "/finalizar", "/cancelar"} or title.startswith("/") or len(title) < 4:
        return

    if update.effective_user and not is_admin(update.effective_user.id):
        return

    if get_pack_by_header(msg.message_id):
        await msg.reply_text("Pack já registrado.")
        return

    audience = _audience_from_chat(chat.id)
    p = create_pack(title=title, audience=audience, storage_chat_id=chat.id, header_message_id=msg.message_id)
    await msg.reply_text(f"Pack registrado: <b>{esc(p.title)}</b> (id {p.id}) [{audience.upper()}]", parse_mode="HTML")

async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat = update.effective_chat
    if not msg or chat.id not in STORAGE_IDS:
        return

    reply = msg.reply_to_message
    if not reply or not reply.message_id:
        await msg.reply_text("Envie este arquivo como <b>resposta</b> ao título do pack.", parse_mode="HTML")
        return

    pack = get_pack_by_header(reply.message_id)
    if not pack:
        await msg.reply_text("Cabeçalho do pack não encontrado. Responda à mensagem de título.")
        return

    if update.effective_user and not is_admin(update.effective_user.id):
        return

    file_id = None
    file_unique_id = None
    file_type = None
    role = "file"
    visible_name = None

    if msg.photo:
        biggest = msg.photo[-1]
        file_id = biggest.file_id
        file_unique_id = getattr(biggest, "file_unique_id", None)
        file_type = "photo"
        role = "preview"
        visible_name = (msg.caption or "").strip() or None
    elif msg.video:
        file_id = msg.video.file_id
        file_unique_id = getattr(msg.video, "file_unique_id", None)
        file_type = "video"
        role = "preview"
        visible_name = (msg.caption or "").strip() or None
    elif msg.animation:
        file_id = msg.animation.file_id
        file_unique_id = getattr(msg.animation, "file_unique_id", None)
        file_type = "animation"
        role = "preview"
        visible_name = (msg.caption or "").strip() or None
    elif msg.document:
        file_id = msg.document.file_id
        file_unique_id = getattr(msg.document, "file_unique_id", None)
        file_type = "document"
        role = "file"
        visible_name = getattr(msg.document, "file_name", None)
    elif msg.audio:
        file_id = msg.audio.file_id
        file_unique_id = getattr(msg.audio, "file_unique_id", None)
        file_type = "audio"
        role = "file"
        visible_name = getattr(msg.audio, "file_name", None) or (msg.caption or "").strip() or None
    elif msg.voice:
        file_id = msg.voice.file_id
        file_unique_id = getattr(msg.voice, "file_unique_id", None)
        file_type = "voice"
        role = "file"
        visible_name = (msg.caption or "").strip() or None
    else:
        await msg.reply_text("Tipo de mídia não suportado.", parse_mode="HTML")
        return

    add_file_to_pack(pack_id=pack.id, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role, file_name=visible_name)
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b>.", parse_mode="HTML")

# =========================
# ENVIO DO PACK (JobQueue)
# =========================
async def _send_pack(p: Pack, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, int]:
    """Envia o pack para o destino conforme audiência. Retorna contadores por tipo."""
    s = SessionLocal()
    try:
        files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()
    finally:
        s.close()

    counts = {"photos": 0, "videos": 0, "animations": 0, "docs": 0, "audios": 0, "voices": 0}
    if not files:
        return counts

    previews = [f for f in files if f.role == "preview"]
    docs     = [f for f in files if f.role == "file"]

    # Destino principal
    if p.audience == "vip":
        dest_chat = GROUP_VIP_ID
    else:
        dest_chat = GROUP_FREE_ID

    # 1) previews (se houver fotos, tenta enviar em álbum)
    photo_ids = [f.file_id for f in previews if f.file_type == "photo"]
    sent_first_caption = False
    if photo_ids:
        media = []
        for i, fid in enumerate(photo_ids):
            if i == 0:
                media.append(InputMediaPhoto(media=fid, caption=p.title))
            else:
                media.append(InputMediaPhoto(media=fid))
        try:
            await context.application.bot.send_media_group(chat_id=dest_chat, media=media)
            counts["photos"] += len(photo_ids)
            sent_first_caption = True
        except Exception as e:
            logging.warning(f"Falha send_media_group: {e}. Enviando fotos individual.")
            for i, fid in enumerate(photo_ids):
                cap = p.title if i == 0 else None
                await context.application.bot.send_photo(chat_id=dest_chat, photo=fid, caption=cap)
                counts["photos"] += 1
                sent_first_caption = True

    # 2) outros previews (vídeo/animação)
    for f in [f for f in previews if f.file_type in ("video", "animation")]:
        cap = p.title if not sent_first_caption else None
        try:
            if f.file_type == "video":
                await context.application.bot.send_video(chat_id=dest_chat, video=f.file_id, caption=cap)
                counts["videos"] += 1
            elif f.file_type == "animation":
                await context.application.bot.send_animation(chat_id=dest_chat, animation=f.file_id, caption=cap)
                counts["animations"] += 1
            sent_first_caption = True
        except Exception as e:
            logging.warning(f"Erro enviando preview {f.id}: {e}")

    # 3) arquivos
    for f in docs:
        try:
            cap = p.title if not sent_first_caption else None
            if f.file_type == "document":
                await context.application.bot.send_document(chat_id=dest_chat, document=f.file_id, caption=cap)
                counts["docs"] += 1
            elif f.file_type == "audio":
                await context.application.bot.send_audio(chat_id=dest_chat, audio=f.file_id, caption=cap)
                counts["audios"] += 1
            elif f.file_type == "voice":
                await context.application.bot.send_voice(chat_id=dest_chat, voice=f.file_id, caption=cap)
                counts["voices"] += 1
            else:
                await context.application.bot.send_document(chat_id=dest_chat, document=f.file_id, caption=cap)
                counts["docs"] += 1
            sent_first_caption = True
        except Exception as e:
            logging.warning(f"Erro enviando arquivo {f.file_name or f.id}: {e}")

    # 4) Teaser no FREE se o pack é VIP: mandar TODAS as fotos de preview + mensagem configurável
    if p.audience == "vip":
        try:
            tpl = cfg_get("free_teaser_tpl") or "Hoje foi liberado no grupo VIP este asset: <b>{title}</b>\nPara participar do VIP, digite /grupovip"
            teaser = tpl.replace("{title}", esc(p.title))
            await context.application.bot.send_message(chat_id=GROUP_FREE_ID, text=teaser, parse_mode="HTML")
        except Exception as e:
            logging.warning(f"Falha ao enviar teaser FREE: {e}")
        if photo_ids:
            try:
                media = [InputMediaPhoto(media=fid) for fid in photo_ids[:10]]  # Telegram limita álbum em 10
                await context.application.bot.send_media_group(chat_id=GROUP_FREE_ID, media=media)
            except Exception as e:
                logging.warning(f"Falha ao enviar fotos FREE: {e}")
                for fid in photo_ids:
                    try:
                        await context.application.bot.send_photo(chat_id=GROUP_FREE_ID, photo=fid)
                    except Exception:
                        pass

    return counts

async def enviar_proximo_pack_job(context: ContextTypes.DEFAULT_TYPE, *, simulate: bool=False) -> str:
    try:
        pack = get_next_unsent_pack()
        if not pack:
            logging.info("Nenhum pack pendente para envio.")
            return "Nenhum pack pendente para envio."

        counts = await _send_pack(pack, context)
        if not simulate:
            mark_pack_sent(pack.id)
        status = (
            f"{'🧪 (SIMULAÇÃO) ' if simulate else '✅ '}Enviado pack "
            f"[#{pack.id}] '{pack.title}' para {pack.audience.upper()}. "
            f"Previews: {counts['photos']} fotos, {counts['videos']} vídeos, {counts['animations']} animações. "
            f"Arquivos: {counts['docs']} docs, {counts['audios']} áudios, {counts['voices']} voices."
        )
        logging.info(status)
        return status
    except Exception as e:
        logging.exception("Erro no enviar_proximo_pack_job")
        return f"❌ Erro no envio: {e!r}"

# =========================
# COMMANDS BÁSICOS & ADMIN
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (
        "Fala! Eu gerencio packs VIP/FREE, pagamentos via MetaMask e mensagens agendadas.\n"
        "Use /comandos para ver tudo."
    )
    if msg:
        await msg.reply_text(text)

def _all_commands_text(isadm: bool) -> str:
    price = f"{cfg_get('vip_price_amount') or '—'} {cfg_get('vip_price_currency') or ''}".strip()
    redes = ", ".join(SUPPORTED_CHAINS)
    lines = [
        "📋 <b>Comandos</b>",
        "• /start — mensagem inicial",
        "• /comandos — lista de comandos",
        "• /listar_comandos — (alias)",
        "• /getid — mostra seus IDs",
        "",
        "💸 <b>Pagamento (MetaMask, multi-rede)</b>:",
        "• /pagar — instruções e redes aceitas",
        "• /tx <code>&lt;hash&gt;</code> — auto-detecta rede (EVM)",
        "• /tx <code>&lt;rede&gt; &lt;hash&gt;</code> — força rede (ex.: <code>/tx polygon 0xabc...</code>)",
        f"• Preço VIP atual: <b>{esc(price)}</b>",
        "",
        "🧩 <b>Packs</b>:",
        "• /novopack — pergunta VIP/FREE (pode usar no privado e nos grupos de storage)",
        "• /novopackvip — atalho VIP (privado/storage)",
        "• /novopackfree — atalho FREE (privado/storage)",
        "• /cancelar — cancela o cadastro do pack em andamento",
        "",
        "🕒 <b>Mensagens agendadas</b>:",
        "• /add_msg_vip HH:MM <texto> | /add_msg_free HH:MM <texto>",
        "• /list_msgs_vip | /list_msgs_free",
        "• /edit_msg_vip <id> [HH:MM] [novo texto]",
        "• /edit_msg_free <id> [HH:MM] [novo texto]",
        "• /toggle_msg_vip <id> | /toggle_msg_free <id>",
        "• /del_msg_vip <id> | /del_msg_free <id>",
    ]
    if isadm:
        lines += [
            "",
            "🛠 <b>Admin</b> (privado ou grupos de storage):",
            "• /simularvip — envia o próximo pack pendente (SEM marcar como enviado)",
            "• /listar_packs — lista packs",
            "• /pack_info <id> — detalhes do pack",
            "• /excluir_item <id_item> — remove item do pack",
            "• /excluir_pack [<id>] — remove pack (com confirmação)",
            "• /set_pendente <id> — marca pack como pendente",
            "• /set_enviado <id> — marca pack como enviado",
            "• /mudar_nome <novo nome> — muda o nome exibido do bot",
            "• /listar_admins — lista admins",
            "• /add_admin <user_id> — adiciona admin",
            "• /rem_admin <user_id> — remove admin",
            "• /iam_admin <pin> — vira admin com PIN",
            "• /set_horario HH:MM — define horário diário para envio do próximo pack",
            "• /set_pack_horario HH:MM — (alias de /set_horario)",
            "• /set_vip_preco <valor> <moeda> — define preço do VIP (ex.: 25 USDT ou 0.001 ETH)",
            "• /set_free_teaser <texto> — define msg teaser FREE (usa {title} no lugar do nome)",
            "• /listar_pendentes — pagamentos pendentes",
            "• /aprovar_tx <user_id> — aprova e envia convite VIP (link 1 clique / 1 uso)",
            "• /rejeitar_tx <user_id> [motivo] — rejeita pagamento",
        ]
    lines += [
        "",
        f"<i>Redes aceitas:</i> {esc(redes)}",
    ]
    return "\n".join(lines)

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    isadm = is_admin(update.effective_user.id) if update.effective_user else False
    await update.effective_message.reply_text(_all_commands_text(isadm), parse_mode="HTML", disable_web_page_preview=True)

async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            f"Seu nome: {esc(user.full_name)}\nSeu ID: {user.id}\nID deste chat: {chat.id}",
            parse_mode="HTML"
        )

# ====== Admin utils ======
async def iam_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Uso: /iam_admin <pin>")
        return
    pin = context.args[0].strip()
    if pin != ADMIN_PIN:
        await update.effective_message.reply_text("PIN incorreto.")
        return
    ok = add_admin_db(update.effective_user.id)
    await update.effective_message.reply_text("✅ Agora você é admin!" if ok else "Você já era admin.")

async def mudar_nome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins (no privado ou nos grupos de storage).")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /mudar_nome <novo nome exibido do bot>")
        return
    novo_nome = " ".join(context.args).strip()
    try:
        await application.bot.set_my_name(name=novo_nome)
        await update.effective_message.reply_text(f"✅ Nome exibido alterado para: {novo_nome}")
    except Exception as e:
        await update.effective_message.reply_text(f"Erro: {e}")

async def listar_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins (no privado ou nos grupos de storage).")
        return
    ids = list_admin_ids()
    if not ids:
        await update.effective_message.reply_text("Sem admins cadastrados.")
        return
    await update.effective_message.reply_text("👑 Admins:\n" + "\n".join(f"- {i}" for i in ids))

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /add_admin <user_id>")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.effective_message.reply_text("user_id inválido.")
        return
    ok = add_admin_db(uid)
    await update.effective_message.reply_text("✅ Admin adicionado." if ok else "Já era admin.")

async def rem_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /rem_admin <user_id>")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.effective_message.reply_text("user_id inválido.")
        return
    ok = remove_admin_db(uid)
    await update.effective_message.reply_text("✅ Admin removido." if ok else "Este user não é admin.")

# ====== Packs admin ======
async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    status = await enviar_proximo_pack_job(context, simulate=True)
    await update.effective_message.reply_text(status, parse_mode="HTML")

async def listar_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    s = SessionLocal()
    try:
        packs = s.query(Pack).order_by(Pack.created_at.desc()).all()
        if not packs:
            await update.effective_message.reply_text("Nenhum pack registrado.")
            return
        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(f"[{p.id}] {esc(p.title)} — {status} — {p.audience.upper()} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

async def pack_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /pack_info <id>")
        return
    try:
        pid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    s = SessionLocal()
    try:
        p = s.query(Pack).filter(Pack.id == pid).first()
        if not p:
            await update.effective_message.reply_text("Pack não encontrado.")
            return
        files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()
        if not files:
            await update.effective_message.reply_text(f"Pack '{p.title}' não possui arquivos.")
            return
        lines = [f"Pack [{p.id}] {esc(p.title)} — {'ENVIADO' if p.sent else 'PENDENTE'} — {p.audience.upper()}"]
        for f in files:
            name = f.file_name or ""
            lines.append(f" - item #{f.id} | {f.file_type} ({f.role}) {name}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

async def excluir_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /excluir_item <id_item>")
        return
    try:
        item_id = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido. Use: /excluir_item <id_item>")
        return

    s = SessionLocal()
    try:
        item = s.query(PackFile).filter(PackFile.id == item_id).first()
        if not item:
            await update.effective_message.reply_text("Item não encontrado.")
            return
        pack = s.query(Pack).filter(Pack.id == item.pack_id).first()
        s.delete(item)
        s.commit()
        await update.effective_message.reply_text(f"✅ Item #{item_id} removido do pack '{pack.title if pack else '?'}'.")
    except Exception as e:
        s.rollback()
        logging.exception("Erro ao remover item")
        await update.effective_message.reply_text(f"❌ Erro ao remover item: {e}")
    finally:
        s.close()

# ===== EXCLUIR PACK (lista + confirmação) =====
DELETE_PACK_CONFIRM = range(1)

async def excluir_pack_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return ConversationHandler.END

    if not context.args:
        s = SessionLocal()
        try:
            packs = s.query(Pack).order_by(Pack.created_at.desc()).all()
            if not packs:
                await update.effective_message.reply_text("Nenhum pack registrado.")
                return ConversationHandler.END
            lines = ["🗑 <b>Excluir Pack</b>\n", "Envie: <code>/excluir_pack &lt;id&gt;</code> para escolher um."]
            for p in packs:
                lines.append(f"[{p.id}] {esc(p.title)} ({p.audience.upper()})")
            await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
            return ConversationHandler.END
        finally:
            s.close()

    try:
        pid = int(context.args[0])
    except:
        await update.effective_message.reply_text("Uso: /excluir_pack <id>")
        return ConversationHandler.END

    context.user_data["delete_pid"] = pid
    await update.effective_message.reply_text(
        f"Confirma excluir o pack <b>#{pid}</b>? (sim/não)",
        parse_mode="HTML"
    )
    return DELETE_PACK_CONFIRM

async def excluir_pack_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = (update.effective_message.text or "").strip().lower()
    if ans not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Responda <b>sim</b> para confirmar ou <b>não</b> para cancelar.", parse_mode="HTML")
        return DELETE_PACK_CONFIRM

    pid = context.user_data.get("delete_pid")
    context.user_data.pop("delete_pid", None)

    if ans in ("não", "nao"):
        await update.effective_message.reply_text("Cancelado.")
        return ConversationHandler.END

    s = SessionLocal()
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
    finally:
        s.close()

    return ConversationHandler.END

# ===== SET PENDENTE / SET ENVIADO =====
async def set_pendente_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /set_pendente <id_do_pack>")
        return

    try:
        pid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido. Ex: /set_pendente 3")
        return

    s = SessionLocal()
    try:
        p = s.query(Pack).filter(Pack.id == pid).first()
        if not p:
            await update.effective_message.reply_text("Pack não encontrado.")
            return
        p.sent = False
        s.commit()
        await update.effective_message.reply_text(f"✅ Pack #{p.id} — “{esc(p.title)}” marcado como <b>PENDENTE</b>.", parse_mode="HTML")
    except Exception as e:
        s.rollback()
        await update.effective_message.reply_text(f"❌ Erro ao atualizar: {e}")
    finally:
        s.close()

async def set_enviado_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /set_enviado <id_do_pack>")
        return

    try:
        pid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido. Ex: /set_enviado 3")
        return

    s = SessionLocal()
    try:
        p = s.query(Pack).filter(Pack.id == pid).first()
        if not p:
            await update.effective_message.reply_text("Pack não encontrado.")
            return
        p.sent = True
        s.commit()
        await update.effective_message.reply_text(f"✅ Pack #{p.id} — “{esc(p.title)}” marcado como <b>ENVIADO</b>.", parse_mode="HTML")
    except Exception as e:
        s.rollback()
        await update.effective_message.reply_text(f"❌ Erro ao atualizar: {e}")
    finally:
        s.close()

# =========================
# NOVOPACK (privado e grupos de storage)
# =========================
TITLE, ASK_AUDIENCE, CONFIRM_TITLE, PREVIEWS, FILES, CONFIRM_SAVE = range(6)

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—")
    previews = user_data.get("previews", [])
    files = user_data.get("files", [])
    audience = user_data.get("audience", "vip")

    preview_names = []
    p_index = 1
    for it in previews:
        base = it.get("file_name")
        if base:
            preview_names.append(esc(base))
        else:
            label = "Foto" if it["file_type"] == "photo" else ("Vídeo" if it["file_type"] == "video" else "Animação")
            preview_names.append(f"{label} {p_index}")
            p_index += 1

    file_names = []
    f_index = 1
    for it in files:
        base = it.get("file_name")
        if base:
            file_names.append(esc(base))
        else:
            file_names.append(f"{it['file_type'].capitalize()} {f_index}")
            f_index += 1

    text = [
        f"📦 <b>Resumo do Pack</b> [{audience.upper()}]",
        f"• Nome: <b>{esc(title)}</b>",
        f"• Previews ({len(previews)}): " + (", ".join(preview_names) if preview_names else "—"),
        f"• Arquivos ({len(files)}): " + (", ".join(file_names) if file_names else "—"),
        "",
        "Deseja salvar? (<b>sim</b>/<b>não</b>)"
    ]
    return "\n".join(text)

async def hint_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Agora envie PREVIEWS (📷 foto / 🎞 vídeo / 🎞 animação) ou use /proximo para ir aos ARQUIVOS."
    )

async def hint_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Agora envie ARQUIVOS (📄 documento / 🎵 áudio / 🎙 voice) ou use /finalizar para revisar e salvar."
    )

novopack_entry_filter = (filters.ChatType.PRIVATE) | filters.Chat(list(STORAGE_IDS))

async def novopack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins (no privado ou nos grupos de storage).")
        return ConversationHandler.END

    context.user_data.clear()

    # Se estiver num storage, já definimos a audiência automaticamente
    chat = update.effective_chat
    if chat and chat.id in STORAGE_IDS:
        context.user_data["audience"] = _audience_from_chat(chat.id)
        context.user_data["previews"] = []
        context.user_data["files"] = []
        await update.effective_message.reply_text(
            "🧩 Vamos criar um novo pack!\n\n"
            "1) Me diga o <b>título do pack</b> (apenas texto).",
            parse_mode="HTML"
        )
        return TITLE

    # No privado: perguntar VIP/FREE
    await update.effective_message.reply_text(
        "Para onde o pack vai? Responda <b>vip</b> ou <b>free</b>.",
        parse_mode="HTML"
    )
    return ASK_AUDIENCE

async def novopack_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = (update.effective_message.text or "").strip().lower()
    if ans not in ("vip", "free"):
        await update.effective_message.reply_text("Responda apenas <b>vip</b> ou <b>free</b>.", parse_mode="HTML")
        return ASK_AUDIENCE
    context.user_data["audience"] = ans
    context.user_data["previews"] = []
    context.user_data["files"] = []
    await update.effective_message.reply_text(
        "🧩 Vamos criar um novo pack!\n\n"
        "1) Me diga o <b>título do pack</b> (apenas texto).",
        parse_mode="HTML"
    )
    return TITLE

async def novopack_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.effective_message.text or "").strip()
    if not title:
        await update.effective_message.reply_text("Título vazio. Envie um texto com o título do pack.")
        return TITLE
    context.user_data["title_candidate"] = title
    await update.effective_message.reply_text(f"Confirma o nome: <b>{esc(title)}</b>? (sim/não)", parse_mode="HTML")
    return CONFIRM_TITLE

async def novopack_confirm_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Por favor, responda <b>sim</b> ou <b>não</b>.", parse_mode="HTML")
        return CONFIRM_TITLE
    if answer in ("não", "nao"):
        await update.effective_message.reply_text("Ok! Envie o <b>novo título</b> do pack.", parse_mode="HTML")
        return TITLE
    context.user_data["title"] = context.user_data.get("title_candidate")
    await update.effective_message.reply_text(
        "2) Envie as <b>PREVIEWS</b> (📷 fotos / 🎞 vídeos / 🎞 animações).\n"
        "Envie quantas quiser. Quando terminar, mande /proximo.",
        parse_mode="HTML"
    )
    return PREVIEWS

async def novopack_collect_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    previews: List[Dict[str, Any]] = context.user_data.get("previews", [])

    if msg.photo:
        biggest = msg.photo[-1]
        previews.append({
            "file_id": biggest.file_id,
            "file_type": "photo",
            "file_name": (msg.caption or "").strip() or None,
        })
        await msg.reply_text("✅ <b>Foto cadastrada</b>. Envie mais ou /proximo.", parse_mode="HTML")

    elif msg.video:
        previews.append({
            "file_id": msg.video.file_id,
            "file_type": "video",
            "file_name": (msg.caption or "").strip() or None,
        })
        await msg.reply_text("✅ <b>Preview (vídeo) cadastrado</b>. Envie mais ou /proximo.", parse_mode="HTML")

    elif msg.animation:
        previews.append({
            "file_id": msg.animation.file_id,
            "file_type": "animation",
            "file_name": (msg.caption or "").strip() or None,
        })
        await msg.reply_text("✅ <b>Preview (animação) cadastrado</b>. Envie mais ou /proximo.", parse_mode="HTML")

    else:
        await hint_previews(update, context)
        return PREVIEWS

    context.user_data["previews"] = previews
    return PREVIEWS

async def novopack_next_to_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("title"):
        await update.effective_message.reply_text("Título não encontrado. Use /cancelar e recomece com /novopack.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "3) Agora envie os <b>ARQUIVOS</b> (📄 documentos / 🎵 áudio / 🎙 voice).\n"
        "Envie quantos quiser. Quando terminar, mande /finalizar.",
        parse_mode="HTML"
    )
    return FILES

async def novopack_collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    files: List[Dict[str, Any]] = context.user_data.get("files", [])

    if msg.document:
        files.append({
            "file_id": msg.document.file_id,
            "file_type": "document",
            "file_name": getattr(msg.document, "file_name", None) or (msg.caption or "").strip() or None,
        })
        await msg.reply_text("✅ <b>Arquivo cadastrado</b>. Envie mais ou /finalizar.", parse_mode="HTML")

    elif msg.audio:
        files.append({
            "file_id": msg.audio.file_id,
            "file_type": "audio",
            "file_name": getattr(msg.audio, "file_name", None) or (msg.caption or "").strip() or None,
        })
        await msg.reply_text("✅ <b>Áudio cadastrado</b>. Envie mais ou /finalizar.", parse_mode="HTML")

    elif msg.voice:
        files.append({
            "file_id": msg.voice.file_id,
            "file_type": "voice",
            "file_name": (msg.caption or "").strip() or None,
        })
        await msg.reply_text("✅ <b>Voice cadastrado</b>. Envie mais ou /finalizar.", parse_mode="HTML")

    else:
        await hint_files(update, context)
        return FILES

    context.user_data["files"] = files
    return FILES

async def novopack_finish_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = _summary_from_session(context.user_data)
    await update.effective_message.reply_text(summary, parse_mode="HTML")
    return CONFIRM_SAVE

async def novopack_confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Responda <b>sim</b> para salvar ou <b>não</b> para cancelar.", parse_mode="HTML")
        return CONFIRM_SAVE
    if answer in ("não", "nao"):
        context.user_data.clear()
        await update.effective_message.reply_text("Operação cancelada. Nada foi salvo.")
        return ConversationHandler.END

    title = context.user_data.get("title")
    audience = context.user_data.get("audience", "vip")
    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])

    p = create_pack(title=title, audience=audience, storage_chat_id=update.effective_chat.id if update.effective_chat else None, header_message_id=None)
    for it in previews:
        add_file_to_pack(
            pack_id=p.id,
            file_id=it["file_id"],
            file_unique_id=None,
            file_type=it["file_type"],
            role="preview",
            file_name=it.get("file_name"),
        )
    for it in files:
        add_file_to_pack(
            pack_id=p.id,
            file_id=it["file_id"],
            file_unique_id=None,
            file_type=it["file_type"],
            role="file",
            file_name=it.get("file_name"),
        )

    context.user_data.clear()
    await update.effective_message.reply_text(f"🎉 <b>{esc(title)}</b> cadastrado com sucesso para {audience.upper()}!", parse_mode="HTML")
    return ConversationHandler.END

async def novopack_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Operação cancelada.")
    return ConversationHandler.END

# Atalhos
async def novopackvip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["audience"] = "vip"
    context.user_data["previews"] = []
    context.user_data["files"] = []
    await update.effective_message.reply_text(
        "🧩 Novo pack VIP!\n\n1) Envie o <b>título</b> do pack.",
        parse_mode="HTML"
    )
    return TITLE

async def novopackfree_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["audience"] = "free"
    context.user_data["previews"] = []
    context.user_data["files"] = []
    await update.effective_message.reply_text(
        "🧩 Novo pack FREE!\n\n1) Envie o <b>título</b> do pack.",
        parse_mode="HTML"
    )
    return TITLE

# =========================
# Pagamento por MetaMask - Fluxo
# =========================
def _current_price() -> str:
    return f"{cfg_get('vip_price_amount') or '—'} {cfg_get('vip_price_currency') or ''}".strip()

async def _notify_admins(text: str):
    for admin_id in list_admin_ids():
        try:
            await application.bot.send_message(chat_id=admin_id, text=text, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass

async def _delete_message_later(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data or {}
    chat_id = data.get("chat_id")
    message_id = data.get("message_id")
    try:
        await application.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass

async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    private_instructions = (
        "💸 <b>Pagamento via MetaMask (EVM)</b>\n"
        f"Rede padrão: <b>{esc(DEFAULT_CHAIN)}</b>\n"
        f"Carteira: <code>{esc(WALLET_ADDRESS)}</code>\n"
        f"Preço do VIP: <b>{esc(_current_price())}</b>\n\n"
        "Redes aceitas: " + ", ".join(SUPPORTED_CHAINS) + "\n\n"
        "Após pagar, envie:\n"
        "• <code>/tx &lt;hash&gt;</code> (detecta rede)\n"
        "ou\n"
        "• <code>/tx &lt;rede&gt; &lt;hash&gt;</code> (ex.: <code>/tx polygon 0xABC...</code>)\n"
        "A equipe valida e libera o VIP."
    )

    # Se for em grupo FREE (ou qualquer grupo), apagar a msg do usuário e responder curto por 5s
    if chat.type in ("group", "supergroup"):
        # avisa e apaga depois de 5s
        try:
            ack = await update.effective_message.reply_text("✅ Te enviei as instruções no privado.")
            # agenda deleção do ACK
            context.job_queue.run_once(_delete_message_later, when=5, data={"chat_id": ack.chat.id, "message_id": ack.message_id})
            # agenda deleção da msg do usuário
            context.job_queue.run_once(_delete_message_later, when=5, data={"chat_id": chat.id, "message_id": update.effective_message.message_id})
        except Exception:
            pass
        # tenta DM
        try:
            await application.bot.send_message(chat_id=user.id, text=private_instructions, parse_mode="HTML")
        except Exception:
            # se não conseguiu, manda aviso rápido no grupo (e apaga)
            try:
                warn = await update.effective_message.reply_text("❗️Não consegui te chamar em privado. Inicia o chat comigo e manda /pagar de novo.")
                context.job_queue.run_once(_delete_message_later, when=8, data={"chat_id": warn.chat.id, "message_id": warn.message_id})
            except Exception:
                pass
        return

    # privado
    await update.effective_message.reply_text(private_instructions, parse_mode="HTML")

async def grupovip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    greet = f"Olá, {esc(user.first_name)}!\n\n"
    texto = greet + (
        "Para entrar no VIP, basta pagar via MetaMask:\n\n"
        f"• Preço: <b>{esc(_current_price())}</b>\n"
        f"• Rede padrão: <b>{esc(DEFAULT_CHAIN)}</b>\n"
        f"• Carteira: <code>{esc(WALLET_ADDRESS)}</code>\n\n"
        "Depois envie:\n"
        "• <code>/tx &lt;hash&gt;</code> ou <code>/tx &lt;rede&gt; &lt;hash&gt;</code>\n"
        "Ex.: <code>/tx polygon 0xABC123...</code>"
    )
    try:
        await application.bot.send_message(chat_id=user.id, text=texto, parse_mode="HTML")
        if update.effective_chat.type in ("group", "supergroup"):
            await update.effective_message.reply_text("✅ Te enviei as instruções no privado.")
    except Exception:
        await update.effective_message.reply_text("❗️Não consegui te chamar em privado. Inicia o chat comigo e manda /grupovip de novo.")

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not context.args:
        await msg.reply_text("Uso:\n• /tx <hash>\n• /tx <rede> <hash>\nEx.: /tx polygon 0xABC...", parse_mode="HTML")
        return

    if len(context.args) == 1:
        tx_hash = context.args[0].strip()
        chain = DEFAULT_CHAIN
    else:
        chain = context.args[0].strip().capitalize()
        tx_hash = context.args[1].strip()

    if not tx_hash or len(tx_hash) < 10:
        await msg.reply_text("Hash inválido.")
        return

    s = SessionLocal()
    try:
        if s.query(Payment).filter(Payment.tx_hash == tx_hash).first():
            await msg.reply_text("Esse hash já foi registrado. Aguarde aprovação.")
            return
        p = Payment(
            user_id=user.id,
            username=user.username,
            tx_hash=tx_hash,
            chain=chain if chain in SUPPORTED_CHAINS else DEFAULT_CHAIN,
            status="pending",
        )
        s.add(p)
        s.commit()
        await msg.reply_text("✅ Recebi seu hash! Assim que for aprovado, te envio o convite do VIP (link 1 uso).")
        # Avisar admins
        await _notify_admins(f"🆕 Pagamento pendente de <b>{esc(user.full_name)}</b> (id {user.id}) — {esc(p.chain)} — {esc(tx_hash)}")
    finally:
        s.close()

async def listar_pendentes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    s = SessionLocal()
    try:
        pend = s.query(Payment).filter(Payment.status == "pending").order_by(Payment.created_at.asc()).all()
        if not pend:
            await update.effective_message.reply_text("Sem pagamentos pendentes.")
            return
        lines = ["⏳ <b>Pendentes</b>"]
        for p in pend:
            lines.append(f"- user_id:{p.user_id} @{p.username or '-'} | {p.tx_hash} | {p.chain} | {p.created_at.strftime('%d/%m %H:%M')}")
        await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
    finally:
        s.close()

async def _create_one_time_invite() -> Optional[str]:
    try:
        # link com 1 uso e expiração de 1 hora
        expire = int(dt.datetime.now(dt.timezone.utc).timestamp()) + 3600
        link = await application.bot.create_chat_invite_link(
            chat_id=GROUP_VIP_ID,
            expire_date=expire,
            member_limit=1,
            creates_join_request=False
        )
        return link.invite_link
    except Exception as e:
        logging.exception("Erro criando invite 1 uso")
        return None

async def aprovar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /aprovar_tx <user_id>")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.effective_message.reply_text("user_id inválido.")
        return

    s = SessionLocal()
    try:
        p = s.query(Payment).filter(Payment.user_id == uid, Payment.status == "pending").order_by(Payment.created_at.asc()).first()
        if not p:
            await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
            return
        p.status = "approved"
        p.decided_at = now_utc()
        s.commit()

        invite = await _create_one_time_invite()
        ok = False
        try:
            if invite:
                await application.bot.send_message(chat_id=uid, text=f"✅ Pagamento aprovado! Entre no VIP: {invite}")
                ok = True
        except Exception as e:
            logging.exception("Erro enviando invite ao usuário")

        await update.effective_message.reply_text("Aprovado e convite enviado." if ok else "Aprovado, mas falhou ao enviar o convite (tente novamente).")

        # contar membros VIP e avisar admins
        total = None
        try:
            total = await application.bot.get_chat_member_count(chat_id=GROUP_VIP_ID)
        except Exception:
            pass
        total_str = f"{total}" if total else "?"
        await _notify_admins(f"👤 Novo VIP aprovado: <b>{esc(uid)}</b>. Total de membros no VIP: <b>{total_str}</b>.")
    finally:
        s.close()

async def rejeitar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /rejeitar_tx <user_id> [motivo]")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.effective_message.reply_text("user_id inválido.")
        return
    motivo = " ".join(context.args[1:]).strip() if len(context.args) > 1 else "Não especificado"

    s = SessionLocal()
    try:
        p = s.query(Payment).filter(Payment.user_id == uid, Payment.status == "pending").order_by(Payment.created_at.asc()).first()
        if not p:
            await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
            return
        p.status = "rejected"
        p.notes = motivo
        p.decided_at = now_utc()
        s.commit()
        try:
            await application.bot.send_message(chat_id=uid, text=f"❌ Pagamento rejeitado. Motivo: {motivo}")
        except:
            pass
        await update.effective_message.reply_text("Pagamento rejeitado.")
    finally:
        s.close()

# =========================
# Mensagens agendadas (VIP e FREE)
# =========================
JOB_PREFIX_SM = "schmsg_"

def _tz(tz_name: str):
    try:
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone("America/Sao_Paulo")

async def _scheduled_message_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    sid = int(job.name.replace(JOB_PREFIX_SM, "")) if job and job.name else None
    if sid is None:
        return
    m = scheduled_get(sid)
    if not m or not m.enabled:
        return
    try:
        chat_id = GROUP_VIP_ID if m.target == "vip" else GROUP_FREE_ID
        await context.application.bot.send_message(chat_id=chat_id, text=m.text)
    except Exception as e:
        logging.warning(f"Falha ao enviar scheduled_message id={sid}: {e}")

def _register_all_scheduled_messages(job_queue: JobQueue):
    for j in list(job_queue.jobs()):
        if j.name and j.name.startswith(JOB_PREFIX_SM):
            j.schedule_removal()
    msgs = scheduled_all()
    for m in msgs:
        try:
            h, k = parse_hhmm(m.hhmm)
        except Exception:
            continue
        tz = _tz(m.tz)
        job_queue.run_daily(
            _scheduled_message_job,
            time=dt.time(hour=h, minute=k, tzinfo=tz),
            name=f"{JOB_PREFIX_SM}{m.id}",
        )

async def add_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /add_msg_vip HH:MM <texto>")
        return
    hhmm = context.args[0]
    try:
        parse_hhmm(hhmm)
    except Exception as e:
        await update.effective_message.reply_text(f"Hora inválida: {e}")
        return
    texto = " ".join(context.args[1:]).strip()
    if not texto:
        await update.effective_message.reply_text("Texto vazio.")
        return
    m = scheduled_create(hhmm, texto, target="vip")
    tz = _tz(m.tz)
    h, k = parse_hhmm(m.hhmm)
    context.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")
    await update.effective_message.reply_text(f"✅ Mensagem VIP #{m.id} criada para {m.hhmm} (diária).")

async def add_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /add_msg_free HH:MM <texto>")
        return
    hhmm = context.args[0]
    try:
        parse_hhmm(hhmm)
    except Exception as e:
        await update.effective_message.reply_text(f"Hora inválida: {e}")
        return
    texto = " ".join(context.args[1:]).strip()
    if not texto:
        await update.effective_message.reply_text("Texto vazio.")
        return
    m = scheduled_create(hhmm, texto, target="free")
    tz = _tz(m.tz)
    h, k = parse_hhmm(m.hhmm)
    context.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")
    await update.effective_message.reply_text(f"✅ Mensagem FREE #{m.id} criada para {m.hhmm} (diária).")

async def list_msgs_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    msgs = scheduled_all("vip")
    if not msgs:
        await update.effective_message.reply_text("Não há mensagens VIP agendadas.")
        return
    lines = ["🕒 <b>Mensagens agendadas (VIP)</b>"]
    for m in msgs:
        status = "ON" if m.enabled else "OFF"
        preview = (m.text[:80] + "…") if len(m.text) > 80 else m.text
        lines.append(f"#{m.id} — {m.hhmm} ({m.tz}) [{status}] — {esc(preview)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def list_msgs_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    msgs = scheduled_all("free")
    if not msgs:
        await update.effective_message.reply_text("Não há mensagens FREE agendadas.")
        return
    lines = ["🕒 <b>Mensagens agendadas (FREE)</b>"]
    for m in msgs:
        status = "ON" if m.enabled else "OFF"
        preview = (m.text[:80] + "…") if len(m.text) > 80 else m.text
        lines.append(f"#{m.id} — {m.hhmm} ({m.tz}) [{status}] — {esc(preview)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def edit_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text("Uso: /edit_msg_vip <id> [HH:MM] [novo texto]")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    hhmm = None
    new_text = None
    if len(context.args) >= 2:
        candidate = context.args[1]
        if ":" in candidate and len(candidate) <= 5:
            try:
                parse_hhmm(candidate)
                hhmm = candidate
                new_text = " ".join(context.args[2:]).strip() if len(context.args) > 2 else None
            except Exception as e:
                await update.effective_message.reply_text(f"Hora inválida: {e}")
                return
        else:
            new_text = " ".join(context.args[1:]).strip()
    if hhmm is None and new_text is None:
        await update.effective_message.reply_text("Nada para alterar. Informe HH:MM e/ou novo texto.")
        return
    ok = scheduled_update(sid, hhmm, new_text)
    if not ok:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}":
            j.schedule_removal()
    m = scheduled_get(sid)
    if m:
        tz = _tz(m.tz)
        h, k = parse_hhmm(m.hhmm)
        context.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")
    await update.effective_message.reply_text("✅ Mensagem VIP atualizada.")

async def edit_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text("Uso: /edit_msg_free <id> [HH:MM] [novo texto]")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    hhmm = None
    new_text = None
    if len(context.args) >= 2:
        candidate = context.args[1]
        if ":" in candidate and len(candidate) <= 5:
            try:
                parse_hhmm(candidate)
                hhmm = candidate
                new_text = " ".join(context.args[2:]).strip() if len(context.args) > 2 else None
            except Exception as e:
                await update.effective_message.reply_text(f"Hora inválida: {e}")
                return
        else:
            new_text = " ".join(context.args[1:]).strip()
    if hhmm is None and new_text is None:
        await update.effective_message.reply_text("Nada para alterar. Informe HH:MM e/ou novo texto.")
        return
    ok = scheduled_update(sid, hhmm, new_text)
    if not ok:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}":
            j.schedule_removal()
    m = scheduled_get(sid)
    if m:
        tz = _tz(m.tz)
        h, k = parse_hhmm(m.hhmm)
        context.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=tz), name=f"{JOB_PREFIX_SM}{m.id}")
    await update.effective_message.reply_text("✅ Mensagem FREE atualizada.")

async def toggle_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text("Uso: /toggle_msg_vip <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    new_state = scheduled_toggle(sid)
    if new_state is None:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    await update.effective_message.reply_text(f"✅ Mensagem VIP #{sid} agora está {'ON' if new_state else 'OFF'}.")

async def toggle_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text("Uso: /toggle_msg_free <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    new_state = scheduled_toggle(sid)
    if new_state is None:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    await update.effective_message.reply_text(f"✅ Mensagem FREE #{sid} agora está {'ON' if new_state else 'OFF'}.")

async def del_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text("Uso: /del_msg_vip <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    ok = scheduled_delete(sid)
    if not ok:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}":
            j.schedule_removal()
    await update.effective_message.reply_text("✅ Mensagem VIP removida.")

async def del_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text("Uso: /del_msg_free <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    ok = scheduled_delete(sid)
    if not ok:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}":
            j.schedule_removal()
    await update.effective_message.reply_text("✅ Mensagem FREE removida.")

# =========================
# CONFIG comandos (preço VIP / teaser / horário)
# =========================
async def set_vip_preco_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /set_vip_preco <valor> <moeda>\nEx.: /set_vip_preco 25 USDT")
        return
    valor = context.args[0].strip()
    moeda = context.args[1].strip().upper()
    cfg_set("vip_price_amount", valor)
    cfg_set("vip_price_currency", moeda)
    await update.effective_message.reply_text(f"✅ Preço do VIP atualizado para {valor} {moeda}.")

async def set_free_teaser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text('Uso: /set_free_teaser <texto>\nUse {title} para o nome do pack.')
        return
    texto = " ".join(context.args).strip()
    cfg_set("free_teaser_tpl", texto)
    await update.effective_message.reply_text("✅ Teaser FREE atualizado.")

async def set_horario_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin_here(update): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args:
        await update.effective_message.reply_text("Uso: /set_horario HH:MM")
        return
    try:
        hhmm = context.args[0]
        parse_hhmm(hhmm)
        cfg_set("daily_pack_hhmm", hhmm)
        await _reschedule_daily_pack()
        await update.effective_message.reply_text(f"✅ Horário diário dos packs definido para {hhmm}.")
    except Exception as e:
        await update.effective_message.reply_text(f"Hora inválida: {e}")

# alias
async def set_pack_horario_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await set_horario_cmd(update, context)

# =========================
# Error handler global
# =========================
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Erro não tratado", exc_info=context.error)

# =========================
# Webhooks
# =========================
@app.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    data = await request.json()
    uid = data.get("telegram_user_id")
    tx_hash = data.get("tx_hash")
    amount = data.get("amount")
    chain = data.get("chain") or DEFAULT_CHAIN

    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)

    s = SessionLocal()
    try:
        pay = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if not pay:
            pay = Payment(user_id=int(uid), tx_hash=tx_hash, amount=amount, chain=chain, status="approved", decided_at=now_utc())
            s.add(pay)
        else:
            pay.status = "approved"
            pay.decided_at = now_utc()
        s.commit()
    finally:
        s.close()

    try:
        invite = await _create_one_time_invite()
        if invite:
            await application.bot.send_message(chat_id=int(uid), text=f"✅ Pagamento confirmado! Entre no VIP: {invite}")
    except Exception:
        logging.exception("Erro enviando invite")

    # notificar admins
    try:
        total = await application.bot.get_chat_member_count(chat_id=GROUP_VIP_ID)
    except Exception:
        total = None
    total_str = f"{total}" if total else "?"
    await _notify_admins(f"👤 Novo VIP confirmado via webhook: <b>{esc(uid)}</b>. Total no VIP: <b>{total_str}</b>.")

    return JSONResponse({"ok": True})

@app.get("/ping")
async def ping():
    return PlainTextResponse("pong", status_code=200)

@app.get("/")
async def root():
    return {"status": "online", "message": "Bot ready (crypto + schedules + packs)"}

# =========================
# Startup: register handlers & jobs
# =========================
async def _keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    # escreve log e bate um GET na raiz do app (se configurado)
    logging.info("[keepalive] tick")
    url = HEALTH_PING_URL
    if not url and WEBHOOK_URL:
        parts = urlsplit(WEBHOOK_URL)
        url = f"{parts.scheme}://{parts.netloc}/ping"
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(url)
    except Exception as e:
        logging.debug(f"[keepalive] ping falhou: {e}")

async def _reschedule_daily_pack():
    # remove agendamento anterior
    for j in list(application.job_queue.jobs()):
        if j.name == "daily_pack":
            j.schedule_removal()
    tz = pytz.timezone("America/Sao_Paulo")
    hhmm = cfg_get("daily_pack_hhmm") or "09:00"
    h, m = parse_hhmm(hhmm)
    application.job_queue.run_daily(lambda c: enviar_proximo_pack_job(c, simulate=False), time=dt.time(hour=h, minute=m, tzinfo=tz), name="daily_pack")
    logging.info(f"Job diário de pack agendado para {hhmm} America/Sao_Paulo")

@app.on_event("startup")
async def on_startup():
    global bot
    logging.basicConfig(level=logging.INFO)
    await application.initialize()
    await application.start()
    bot = application.bot

    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL não definido no .env")
    await bot.set_webhook(url=WEBHOOK_URL)

    logging.info("Bot iniciado (cripto + schedules + packs).")

    # ===== Error handler =====
    application.add_error_handler(error_handler)

    # ===== Conversa /novopack – PRIVADO e STORAGEs =====
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("novopack", novopack_start, filters=novopack_entry_filter)],
        states={
            ASK_AUDIENCE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_audience)],
            TITLE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_title)],
            CONFIRM_TITLE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_confirm_title)],
            PREVIEWS: [
                CommandHandler("proximo", novopack_next_to_files, filters=novopack_entry_filter),
                MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION) & novopack_entry_filter, novopack_collect_previews),
                MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, hint_previews),
            ],
            FILES: [
                CommandHandler("finalizar", novopack_finish_review, filters=novopack_entry_filter),
                MessageHandler((filters.Document.ALL | filters.AUDIO | filters.VOICE) & novopack_entry_filter, novopack_collect_files),
                MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, hint_files),
            ],
            CONFIRM_SAVE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_confirm_save)],
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel, filters=novopack_entry_filter)],
        allow_reentry=True,
    )
    application.add_handler(conv_handler, group=0)

    # Atalhos /novopackvip e /novopackfree
    conv_vip = ConversationHandler(
        entry_points=[CommandHandler("novopackvip", novopackvip_start, filters=novopack_entry_filter)],
        states={
            TITLE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_title)],
            CONFIRM_TITLE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_confirm_title)],
            PREVIEWS: [
                CommandHandler("proximo", novopack_next_to_files, filters=novopack_entry_filter),
                MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION) & novopack_entry_filter, novopack_collect_previews),
                MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, hint_previews),
            ],
            FILES: [
                CommandHandler("finalizar", novopack_finish_review, filters=novopack_entry_filter),
                MessageHandler((filters.Document.ALL | filters.AUDIO | filters.VOICE) & novopack_entry_filter, novopack_collect_files),
                MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, hint_files),
            ],
            CONFIRM_SAVE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_confirm_save)],
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel, filters=novopack_entry_filter)],
        allow_reentry=True,
    )
    application.add_handler(conv_vip, group=0)

    conv_free = ConversationHandler(
        entry_points=[CommandHandler("novopackfree", novopackfree_start, filters=novopack_entry_filter)],
        states={
            TITLE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_title)],
            CONFIRM_TITLE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_confirm_title)],
            PREVIEWS: [
                CommandHandler("proximo", novopack_next_to_files, filters=novopack_entry_filter),
                MessageHandler((filters.PHOTO | filters.VIDEO | filters.ANIMATION) & novopack_entry_filter, novopack_collect_previews),
                MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, hint_previews),
            ],
            FILES: [
                CommandHandler("finalizar", novopack_finish_review, filters=novopack_entry_filter),
                MessageHandler((filters.Document.ALL | filters.AUDIO | filters.VOICE) & novopack_entry_filter, novopack_collect_files),
                MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, hint_files),
            ],
            CONFIRM_SAVE: [MessageHandler((filters.TEXT & ~filters.COMMAND) & novopack_entry_filter, novopack_confirm_save)],
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel, filters=novopack_entry_filter)],
        allow_reentry=True,
    )
    application.add_handler(conv_free, group=0)

    # ===== Conversa /excluir_pack (com confirmação) =====
    excluir_conv = ConversationHandler(
        entry_points=[CommandHandler("excluir_pack", excluir_pack_cmd)],
        states={DELETE_PACK_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, excluir_pack_confirm)]},
        fallbacks=[],
        allow_reentry=True,
    )
    application.add_handler(excluir_conv, group=0)

    # ===== Handlers do storage (título + mídias) =====
    application.add_handler(
        MessageHandler(
            (filters.Chat(list(STORAGE_IDS)) & filters.TEXT & ~filters.COMMAND),
            storage_text_handler
        ),
        group=1,
    )
    media_filter = (
        filters.Chat(list(STORAGE_IDS))
        & (
            filters.PHOTO
            | filters.VIDEO
            | filters.ANIMATION
            | filters.AUDIO
            | filters.Document.ALL
            | filters.VOICE
        )
    )
    application.add_handler(MessageHandler(media_filter, storage_media_handler), group=1)

    # ===== Comandos gerais (group=1) =====
    application.add_handler(CommandHandler("start", start_cmd), group=1)
    application.add_handler(CommandHandler("comandos", comandos_cmd), group=1)
    application.add_handler(CommandHandler("listar_comandos", comandos_cmd), group=1)
    application.add_handler(CommandHandler("getid", getid_cmd), group=1)
    application.add_handler(CommandHandler("grupovip", grupovip_cmd), group=1)

    # Packs & admin
    application.add_handler(CommandHandler("simularvip", simularvip_cmd), group=1)
    application.add_handler(CommandHandler("listar_packs", listar_packs_cmd), group=1)
    application.add_handler(CommandHandler("pack_info", pack_info_cmd), group=1)
    application.add_handler(CommandHandler("excluir_item", excluir_item_cmd), group=1)
    application.add_handler(CommandHandler("set_pendente", set_pendente_cmd), group=1)
    application.add_handler(CommandHandler("set_enviado", set_enviado_cmd), group=1)

    # Admin mgmt & util
    application.add_handler(CommandHandler("listar_admins", listar_admins_cmd), group=1)
    application.add_handler(CommandHandler("add_admin", add_admin_cmd), group=1)
    application.add_handler(CommandHandler("rem_admin", rem_admin_cmd), group=1)
    application.add_handler(CommandHandler("mudar_nome", mudar_nome_cmd), group=1)
    application.add_handler(CommandHandler("iam_admin", iam_admin_cmd), group=1)

    # Pagamentos cripto
    application.add_handler(CommandHandler("pagar", pagar_cmd), group=1)
    application.add_handler(CommandHandler("tx", tx_cmd), group=1)
    application.add_handler(CommandHandler("listar_pendentes", listar_pendentes_cmd), group=1)
    application.add_handler(CommandHandler("aprovar_tx", aprovar_tx_cmd), group=1)
    application.add_handler(CommandHandler("rejeitar_tx", rejeitar_tx_cmd), group=1)

    # Mensagens agendadas
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

    # Config
    application.add_handler(CommandHandler("set_horario", set_horario_cmd), group=1)
    application.add_handler(CommandHandler("set_pack_horario", set_pack_horario_cmd), group=1)
    application.add_handler(CommandHandler("set_vip_preco", set_vip_preco_cmd), group=1)
    application.add_handler(CommandHandler("set_free_teaser", set_free_teaser_cmd), group=1)

    # ===== Job diário de envio de pack =====
    await _reschedule_daily_pack()

    # ===== Recarrega mensagens agendadas VIP/FREE =====
    _register_all_scheduled_messages(application.job_queue)

    # ===== KeepAlive =====
    if KEEPALIVE_ENABLED:
        application.job_queue.run_repeating(_keepalive_job, interval=KEEPALIVE_SECONDS, first=10, name="keepalive")

    logging.info("Handlers e jobs registrados.")

# =========================
# Run
# =========================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
