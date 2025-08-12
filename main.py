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

# =========================
# ENV / CONFIG
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
BASE_URL = os.getenv("BASE_URL")  # para keepalive (ex.: https://seu-servico.onrender.com)

# VIP storage (grupo privado de cadastro)
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))
# VIP envio (grupo público VIP)
GROUP_VIP_ID     = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))

# FREE storage (grupo privado de cadastro)
STORAGE_GROUP_FREE_ID = int(os.getenv("STORAGE_GROUP_FREE_ID", "-1002509364079"))
# FREE envio (grupo público FREE)
GROUP_FREE_ID         = int(os.getenv("GROUP_FREE_ID", "-1002509364079"))

PORT = int(os.getenv("PORT", 8000))
KEEPALIVE_INTERVAL_SEC = int(os.getenv("KEEPALIVE_INTERVAL_SEC", "240"))

# Pagamento cripto
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "").strip()
CHAIN_NAME = os.getenv("CHAIN_NAME", "Polygon").strip()

# Teaser FREE (para espelho de preview do VIP)
DEFAULT_FREE_TEASER = os.getenv("DEFAULT_FREE_TEASER", "Hoje saiu no VIP: {title}\nPara entrar no VIP, use /pagar")

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no .env")

# =========================
# FASTAPI + PTB
# =========================
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None
BOT_USERNAME = None  # preenchido no startup

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

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
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
    header_message_id = Column(Integer, nullable=True, unique=True)  # chave do cabeçalho (por grupo)
    created_at = Column(DateTime, default=now_utc)
    sent = Column(Boolean, default=False)
    tier = Column(String, default="vip")  # 'vip' ou 'free'
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
    chain = Column(String, default=CHAIN_NAME)
    amount = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending | approved | rejected
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now_utc)
    decided_at = Column(DateTime, nullable=True)

# ---- Mensagens agendadas (por tier) ----
class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    id = Column(Integer, primary_key=True)
    hhmm = Column(String, nullable=False)      # "HH:MM"
    tz = Column(String, default="America/Sao_Paulo")
    text = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    tier = Column(String, default="vip")       # 'vip' ou 'free'
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

def ensure_pack_tier_column():
    """Garante coluna tier em packs e scheduled_messages."""
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

def init_db():
    Base.metadata.create_all(bind=engine)
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
    if not cfg_get("daily_pack_vip_hhmm"):
        cfg_set("daily_pack_vip_hhmm", "09:00")
    if not cfg_get("daily_pack_free_hhmm"):
        cfg_set("daily_pack_free_hhmm", "09:30")
    if not cfg_get("free_teaser"):
        cfg_set("free_teaser", DEFAULT_FREE_TEASER)

# migração antes de criar metadata
ensure_bigint_columns()
ensure_pack_tier_column()
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

def create_pack(title: str, header_message_id: Optional[int] = None, tier: str = "vip") -> 'Pack':
    s = SessionLocal()
    try:
        p = Pack(title=title.strip(), header_message_id=header_message_id, tier=tier)
        s.add(p)
        s.commit()
        s.refresh(p)
        return p
    finally:
        s.close()

def get_pack_by_header(header_message_id: int) -> Optional['Pack']:
    s = SessionLocal()
    try:
        return s.query(Pack).filter(Pack.header_message_id == header_message_id).first()
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

def get_next_unsent_pack(tier: str = "vip") -> Optional['Pack']:
    s = SessionLocal()
    try:
        return s.query(Pack).filter(Pack.sent == False, Pack.tier == tier).order_by(Pack.created_at.asc()).first()
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

def list_packs_by_tier(tier: str):
    s = SessionLocal()
    try:
        return s.query(Pack).filter(Pack.tier == tier).order_by(Pack.created_at.desc()).all()
    finally:
        s.close()

# ---- Scheduled messages helpers ----
def scheduled_all(tier: Optional[str] = None) -> List['ScheduledMessage']:
    s = SessionLocal()
    try:
        q = s.query(ScheduledMessage)
        if tier:
            q = q.filter(ScheduledMessage.tier == tier)
        return q.order_by(ScheduledMessage.hhmm.asc(), ScheduledMessage.id.asc()).all()
    finally:
        s.close()

def scheduled_get(sid: int) -> Optional['ScheduledMessage']:
    s = SessionLocal()
    try:
        return s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
    finally:
        s.close()

def scheduled_create(hhmm: str, text: str, tz_name: str = "America/Sao_Paulo", tier: str = "vip") -> 'ScheduledMessage':
    s = SessionLocal()
    try:
        m = ScheduledMessage(hhmm=hhmm, text=text, tz=tz_name, enabled=True, tier=tier)
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
# STORAGE GROUP handlers
# =========================

def header_key(chat_id: int, message_id: int) -> int:
    """
    Evita colisão entre grupos diferentes:
    - VIP storage usa +message_id
    - FREE storage usa -message_id
    """
    if chat_id == STORAGE_GROUP_ID:
        return int(message_id)
    if chat_id == STORAGE_GROUP_FREE_ID:
        return int(-message_id)
    return int(message_id)

async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.id not in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID}:
        return

    if msg.reply_to_message:
        return

    title = (msg.text or "").strip()
    if not title:
        return

    lower = title.lower()
    banned = {"sim", "não", "nao", "/proximo", "/finalizar", "/cancelar"}
    if lower in banned or title.startswith("/") or len(title) < 4:
        return

    words = title.split()
    looks_like_title = (
        len(words) >= 2
        or lower.startswith("pack ")
        or lower.startswith("#pack ")
        or lower.startswith("pack:")
        or lower.startswith("[pack]")
    )
    if not looks_like_title:
        return

    # Apenas admins podem registrar no storage
    if update.effective_user and not is_admin(update.effective_user.id):
        return

    hkey = header_key(msg.chat.id, msg.message_id)
    if get_pack_by_header(hkey):
        await msg.reply_text("Pack já registrado.")
        return

    tier = "vip" if msg.chat.id == STORAGE_GROUP_ID else "free"
    p = create_pack(title=title, header_message_id=hkey, tier=tier)
    await msg.reply_text(
        f"Pack registrado: <b>{esc(p.title)}</b> (id {p.id}) — <i>{tier.upper()}</i>",
        parse_mode="HTML"
    )

async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.id not in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID}:
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
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b> — <i>{pack.tier.upper()}</i>.", parse_mode="HTML")

# =========================
# ENVIO DO PACK (JobQueue)
# =========================
async def _send_previews_to_chat(context: ContextTypes.DEFAULT_TYPE, p: Pack, previews: List[PackFile], target_chat_id: int):
    """Envia previews (fotos em grupo, depois vídeos/animações) para o chat alvo."""
    sent_first = False
    # Fotos em media_group
    photo_ids = [f.file_id for f in previews if f.file_type == "photo"]
    if photo_ids:
        media = []
        for i, fid in enumerate(photo_ids):
            media.append(InputMediaPhoto(media=fid, caption=p.title if i == 0 else None))
        try:
            await context.application.bot.send_media_group(chat_id=target_chat_id, media=media)
            sent_first = True
        except Exception as e:
            logging.warning(f"Falha send_media_group: {e}. Enviando individual.")
            for i, fid in enumerate(photo_ids):
                cap = p.title if i == 0 else None
                await context.application.bot.send_photo(chat_id=target_chat_id, photo=fid, caption=cap)
                sent_first = True

    # Vídeos / animações
    for f in [f for f in previews if f.file_type in ("video", "animation")]:
        cap = p.title if not sent_first else None
        try:
            if f.file_type == "video":
                await context.application.bot.send_video(chat_id=target_chat_id, video=f.file_id, caption=cap)
            else:
                await context.application.bot.send_animation(chat_id=target_chat_id, animation=f.file_id, caption=cap)
            sent_first = True
        except Exception as e:
            logging.warning(f"Erro enviando preview {f.id}: {e}")

async def enviar_pack_job(context: ContextTypes.DEFAULT_TYPE, tier: str, target_chat_id: int) -> str:
    try:
        pack = get_next_unsent_pack(tier=tier)
        if not pack:
            logging.info(f"Nenhum pack pendente para envio ({tier}).")
            return f"Nenhum pack pendente para envio ({tier})."

        s = SessionLocal()
        try:
            p = s.query(Pack).filter(Pack.id == pack.id).first()
            files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()
        finally:
            s.close()

        if not files:
            logging.warning(f"Pack '{p.title}' ({tier}) sem arquivos; marcando como enviado.")
            mark_pack_sent(p.id)
            return f"Pack '{p.title}' ({tier}) não possui arquivos. Marcado como enviado."

        previews = [f for f in files if f.role == "preview"]
        docs     = [f for f in files if f.role == "file"]

        # 1) Envia PREVIEW no tier alvo
        await _send_previews_to_chat(context, p, previews, target_chat_id)

        # 2) Envia ARQUIVOS (se for VIP/FREE pack real)
        sent_first = True  # já usamos caption no preview
        for f in docs:
            try:
                cap = None
                if f.file_type == "document":
                    await context.application.bot.send_document(chat_id=target_chat_id, document=f.file_id, caption=cap)
                elif f.file_type == "audio":
                    await context.application.bot.send_audio(chat_id=target_chat_id, audio=f.file_id, caption=cap)
                elif f.file_type == "voice":
                    await context.application.bot.send_voice(chat_id=target_chat_id, voice=f.file_id, caption=cap)
                else:
                    await context.application.bot.send_document(chat_id=target_chat_id, document=f.file_id, caption=cap)
            except Exception as e:
                logging.warning(f"Erro enviando arquivo {f.file_name or f.id}: {e}")

        # 3) Se tier == VIP, espelhar preview no FREE com teaser configurável
        if tier == "vip":
            teaser_tpl = cfg_get("free_teaser", DEFAULT_FREE_TEASER) or ""
            teaser = (teaser_tpl.format(title=p.title)).strip()
            if teaser:
                try:
                    await context.application.bot.send_message(chat_id=GROUP_FREE_ID, text=teaser)
                except Exception as e:
                    logging.warning(f"Erro enviando teaser FREE: {e}")
            # Previews no FREE
            if previews:
                await _send_previews_to_chat(context, p, previews, GROUP_FREE_ID)

        mark_pack_sent(p.id)
        logging.info(f"Pack enviado: {p.title} ({tier})")
        return f"✅ Enviado pack '{p.title}' ({tier})."
    except Exception as e:
        logging.exception("Erro no enviar_pack_job")
        return f"❌ Erro no envio ({tier}): {e!r}"

async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    return await enviar_pack_job(context, tier="vip",  target_chat_id=GROUP_VIP_ID)

async def enviar_pack_free_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    return await enviar_pack_job(context, tier="free", target_chat_id=GROUP_FREE_ID)

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

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    isadm = is_admin(update.effective_user.id) if update.effective_user else False
    base = [
        "📋 <b>Comandos</b>",
        "• /start — mensagem inicial",
        "• /comandos — lista de comandos",
        "• /listar_comandos — (alias)",
        "• /getid — mostra seus IDs",
        "",
        "💸 Pagamento (MetaMask):",
        "• /pagar — instruções",
        "• /tx &lt;hash&gt; — registrar a transação",
        "",
        "🧩 Packs (privado ou grupo de armazenamento):",
        "• /novopack — perguntar VIP/FREE e iniciar fluxo",
        "• /novopackvip — atalho direto para VIP (privado)",
        "• /novopackfree — atalho direto para FREE (privado)",
        "",
        "🕒 Mensagens agendadas:",
        "• /add_msg_vip HH:MM &lt;texto&gt;",
        "• /add_msg_free HH:MM &lt;texto&gt;",
        "• /list_msgs_vip | /list_msgs_free",
        "• /edit_msg_vip &lt;id&gt; [HH:MM] [novo texto]",
        "• /edit_msg_free &lt;id&gt; [HH:MM] [novo texto]",
        "• /toggle_msg_vip &lt;id&gt; | /toggle_msg_free &lt;id&gt;",
        "• /del_msg_vip &lt;id&gt; | /del_msg_free &lt;id&gt;",
    ]
    adm = [
        "",
        "🛠 <b>Admin</b>",
        "• /simularvip — envia o próximo pack VIP pendente agora",
        "• /simularfree — envia o próximo pack FREE pendente agora",
        "• /listar_packsvip — lista packs VIP",
        "• /listar_packsfree — lista packs FREE",
        "• /pack_info &lt;id&gt; — detalhes do pack",
        "• /excluir_item &lt;id_item&gt; — remove item do pack",
        "• /excluir_pack [&lt;id&gt;] — remove pack (com confirmação)",
        "• /set_pendentevip &lt;id&gt; — marca pack VIP como pendente",
        "• /set_pendentefree &lt;id&gt; — marca pack FREE como pendente",
        "• /set_enviadovip &lt;id&gt; — marca pack VIP como enviado",
        "• /set_enviadofree &lt;id&gt; — marca pack FREE como enviado",
        "• /set_pack_horario_vip HH:MM — define o horário diário dos packs VIP",
        "• /set_pack_horario_free HH:MM — define o horário diário dos packs FREE",
        "• /set_free_teaser &lt;texto com {title}&gt; — define teaser do FREE",
        "• /ver_free_teaser — exibe teaser atual",
        "• /limpar_chat &lt;N&gt; — apaga últimas N mensagens (melhor esforço)",
        "• /mudar_nome &lt;novo nome&gt; — muda o nome exibido do bot",
        "• /mudar_username — instruções para mudar o @username (BotFather)",
        "• /add_admin &lt;user_id&gt; — adiciona admin",
        "• /rem_admin &lt;user_id&gt; — remove admin",
        "• /listar_admins — lista admins",
        "• /listar_pendentes — pagamentos pendentes",
        "• /aprovar_tx &lt;user_id&gt; — aprova e envia convite VIP",
        "• /rejeitar_tx &lt;user_id&gt; [motivo] — rejeita pagamento",
    ]
    lines = base + (adm if isadm else [])
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

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
async def mudar_nome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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

async def mudar_username_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = (
        "⚠️ Alterar o <b>@username</b> do bot não é possível via API.\n"
        "Siga no <b>@BotFather</b>:\n"
        "1) /mybots → selecione seu bot\n"
        "2) Bot Settings → Edit Username → informe o novo @username disponível\n"
        "Obs.: o nome exibido (não o @) você pode mudar com /mudar_nome aqui."
    )
    await update.effective_message.reply_text(txt, parse_mode="HTML")

async def limpar_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /limpar_chat <N>")
        return
    try:
        n = int(context.args[0])
        if n <= 0 or n > 500:
            await update.effective_message.reply_text("Escolha um N entre 1 e 500.")
            return
    except:
        await update.effective_message.reply_text("Número inválido.")
        return

    chat_id = update.effective_chat.id
    current_id = update.effective_message.message_id
    deleted = 0
    for mid in range(current_id, current_id - n, -1):
        try:
            await application.bot.delete_message(chat_id=chat_id, message_id=mid)
            deleted += 1
            await asyncio.sleep(0.03)
        except Exception:
            pass
    await application.bot.send_message(chat_id=chat_id, text=f"🧹 Apaguei ~{deleted} mensagens (melhor esforço).")

async def listar_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    ids = list_admin_ids()
    if not ids:
        await update.effective_message.reply_text("Sem admins cadastrados.")
        return
    await update.effective_message.reply_text("👑 Admins:\n" + "\n".join(f"- {i}" for i in ids))

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    status = await enviar_pack_vip_job(context)
    await update.effective_message.reply_text(status)

async def simularfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    status = await enviar_pack_free_job(context)
    await update.effective_message.reply_text(status)

async def listar_packsvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    s = SessionLocal()
    try:
        packs = list_packs_by_tier("vip")
        if not packs:
            await update.effective_message.reply_text("Nenhum pack VIP registrado.")
            return
        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

async def listar_packsfree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    s = SessionLocal()
    try:
        packs = list_packs_by_tier("free")
        if not packs:
            await update.effective_message.reply_text("Nenhum pack FREE registrado.")
            return
        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(f"[{p.id}] {esc(p.title)} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

async def pack_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
        lines = [f"Pack [{p.id}] {esc(p.title)} — {'ENVIADO' if p.sent else 'PENDENTE'} — {p.tier.upper()}"]
        for f in files:
            name = f.file_name or ""
            lines.append(f" - item #{f.id} | {f.file_type} ({f.role}) {name}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

async def excluir_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return ConversationHandler.END

    if not context.args:
        s = SessionLocal()
        try:
            packs = list_packs_by_tier("vip") + list_packs_by_tier("free")
            if not packs:
                await update.effective_message.reply_text("Nenhum pack registrado.")
                return ConversationHandler.END
            lines = ["🗑 <b>Excluir Pack</b>\n", "Envie: <code>/excluir_pack &lt;id&gt;</code> para escolher um."]
            for p in packs:
                lines.append(f"[{p.id}] {esc(p.title)} ({p.tier.upper()})")
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

# ===== SET PENDENTE / SET ENVIADO por tier =====
async def _set_sent_by_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str, sent: bool):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text(f"Uso: /{'set_enviado' if sent else 'set_pendente'}{tier} <id_do_pack>")
        return
    try:
        pid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    s = SessionLocal()
    try:
        p = s.query(Pack).filter(Pack.id == pid, Pack.tier == tier).first()
        if not p:
            await update.effective_message.reply_text(f"Pack não encontrado para {tier.upper()}.")
            return
        p.sent = sent
        s.commit()
        await update.effective_message.reply_text(
            f"✅ Pack #{p.id} — “{esc(p.title)}” marcado como <b>{'ENVIADO' if sent else 'PENDENTE'}</b> ({tier}).",
            parse_mode="HTML"
        )
    except Exception as e:
        s.rollback()
        await update.effective_message.reply_text(f"❌ Erro ao atualizar: {e}")
    finally:
        s.close()

async def set_pendentefree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_sent_by_tier(update, context, tier="free", sent=False)

async def set_pendentevip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_sent_by_tier(update, context, tier="vip", sent=False)

async def set_enviadofree_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_sent_by_tier(update, context, tier="free", sent=True)

async def set_enviadovip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _set_sent_by_tier(update, context, tier="vip", sent=True)

# =========================
# NOVOPACK (privado + grupos cadastrados)
# =========================
CHOOSE_TIER, TITLE, CONFIRM_TITLE, PREVIEWS, FILES, CONFIRM_SAVE = range(6)

def _require_admin(update: Update) -> bool:
    return update.effective_user and is_admin(update.effective_user.id)

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—")
    previews = user_data.get("previews", [])
    files = user_data.get("files", [])
    tier = (user_data.get("tier") or "vip").upper()

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
        f"📦 <b>Resumo do Pack</b> ({tier})",
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

def _is_allowed_group(chat_id: int) -> bool:
    """Permite iniciar o /novopack também dentro dos grupos cadastrados de armazenamento (VIP/FREE)."""
    return chat_id in {STORAGE_GROUP_ID, STORAGE_GROUP_FREE_ID}

async def novopack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return ConversationHandler.END

    chat = update.effective_chat
    if chat.type != "private" and not _is_allowed_group(chat.id):
        # empurra para o privado com deep-link
        try:
            username = BOT_USERNAME or (await application.bot.get_me()).username
        except Exception:
            username = None
        if username:
            link = f"https://t.me/{username}?start=novopack"
            await update.effective_message.reply_text(
                "Use este comando no privado comigo, por favor.\n" + link
            )
        else:
            await update.effective_message.reply_text("Use este comando no privado comigo, por favor.")
        return ConversationHandler.END

    # privado OU grupo permitido: inicia fluxo
    context.user_data.clear()
    await update.effective_message.reply_text(
        "Quer cadastrar em qual tier? Responda <b>vip</b> ou <b>free</b>.",
        parse_mode="HTML"
    )
    return CHOOSE_TIER

async def novopack_choose_tier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer in ("vip", "v"):
        context.user_data["tier"] = "vip"
    elif answer in ("free", "f", "gratis", "grátis"):
        context.user_data["tier"] = "free"
    else:
        await update.effective_message.reply_text(
            "Não entendi. Responda <b>vip</b> ou <b>free</b> 🙂",
            parse_mode="HTML"
        )
        return CHOOSE_TIER

    await update.effective_message.reply_text(
        f"🧩 Novo pack <b>{context.user_data['tier'].upper()}</b> — envie o <b>título</b>.",
        parse_mode="HTML"
    )
    return TITLE

# atalhos (privado)
async def novopackvip_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Use este comando no privado comigo, por favor.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["tier"] = "vip"
    await update.effective_message.reply_text("🧩 Novo pack VIP — envie o <b>título</b>.", parse_mode="HTML")
    return TITLE

async def novopackfree_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Use este comando no privado comigo, por favor.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["tier"] = "free"
    await update.effective_message.reply_text("🧩 Novo pack FREE — envie o <b>título</b>.", parse_mode="HTML")
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
    context.user_data["previews"] = []
    context.user_data["files"] = []
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
    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])
    tier = context.user_data.get("tier", "vip")

    p = create_pack(title=title, header_message_id=None, tier=tier)
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
    await update.effective_message.reply_text(f"🎉 <b>{esc(title)}</b> cadastrado com sucesso em <b>{tier.upper()}</b>!", parse_mode="HTML")
    return ConversationHandler.END

async def novopack_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Operação cancelada.")
    return ConversationHandler.END

# =========================
# Pagamento por MetaMask - Fluxo
# =========================
async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not WALLET_ADDRESS:
        await update.effective_message.reply_text("Método de pagamento não configurado. (WALLET_ADDRESS ausente)")
        return
    texto = (
        f"💸 <b>Pagamento via MetaMask</b>\n"
        f"1) Envie o valor para a carteira (<b>{esc(CHAIN_NAME)}</b>):\n"
        f"<code>{esc(WALLET_ADDRESS)}</code>\n"
        f"2) Após pagar, me envie: <code>/tx &lt;hash_da_transacao&gt;</code>\n\n"
        f"A equipe valida e libera o VIP. Se tiver automação externa, também aceitamos POST em /crypto_webhook."
    )
    await update.effective_message.reply_text(texto, parse_mode="HTML")

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    if not context.args:
        await msg.reply_text("Uso: /tx <hash_da_transacao>")
        return
    tx_hash = context.args[0].strip()
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
            chain=CHAIN_NAME,
            status="pending",
        )
        s.add(p)
        s.commit()
        await msg.reply_text("✅ Recebi seu hash! Assim que for aprovado, te envio o convite do VIP.")
    finally:
        s.close()

async def listar_pendentes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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

async def aprovar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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

        try:
            invite = await application.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
            await application.bot.send_message(chat_id=uid, text=f"✅ Pagamento aprovado! Entre no VIP: {invite}")
            await update.effective_message.reply_text(f"Aprovado e convite enviado para {uid}.")
        except Exception as e:
            logging.exception("Erro enviando invite")
            await update.effective_message.reply_text(f"Aprovado, mas falhou ao enviar convite: {e}")
    finally:
        s.close()

async def rejeitar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
# Mensagens agendadas (VIP / FREE)
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
        target_chat = GROUP_VIP_ID if m.tier == "vip" else GROUP_FREE_ID
        await context.application.bot.send_message(chat_id=target_chat, text=m.text)
    except Exception as e:
        logging.warning(f"Falha ao enviar scheduled_message id={sid}: {e}")

def _register_all_scheduled_messages(job_queue: JobQueue):
    # **Atenção**: só mexe nos jobs de mensagens; NÃO remove os jobs diários de packs!
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

async def _reschedule_daily_packs():
    # remove existentes dos packs e recria
    for j in list(application.job_queue.jobs()):
        if j.name in {"daily_pack_vip", "daily_pack_free"}:
            j.schedule_removal()

    tz = pytz.timezone("America/Sao_Paulo")
    hhmm_vip  = cfg_get("daily_pack_vip_hhmm")  or "09:00"
    hhmm_free = cfg_get("daily_pack_free_hhmm") or "09:30"
    hv, mv = parse_hhmm(hhmm_vip)
    hf, mf = parse_hhmm(hhmm_free)

    application.job_queue.run_daily(enviar_pack_vip_job,  time=dt.time(hour=hv, minute=mv, tzinfo=tz), name="daily_pack_vip")
    application.job_queue.run_daily(enviar_pack_free_job, time=dt.time(hour=hf, minute=mf, tzinfo=tz), name="daily_pack_free")

    logging.info(f"Job VIP agendado para {hhmm_vip}; FREE para {hhmm_free} (America/Sao_Paulo)")

# ----- Comandos de mensagens (VIP/FREE) -----
async def _add_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(f"Uso: /add_msg_{tier} HH:MM <texto>")
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
    m = scheduled_create(hhmm, texto, tier=tier)
    tz = _tz(m.tz)
    h, k = parse_hhmm(m.hhmm)
    application.job_queue.run_daily(
        _scheduled_message_job,
        time=dt.time(hour=h, minute=k, tzinfo=tz),
        name=f"{JOB_PREFIX_SM}{m.id}",
    )
    await update.effective_message.reply_text(f"✅ Mensagem #{m.id} ({tier.upper()}) criada para {m.hhmm} (diária).")

async def add_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _add_msg_tier(update, context, "vip")

async def add_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _add_msg_tier(update, context, "free")

async def _list_msgs_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    msgs = scheduled_all(tier=tier)
    if not msgs:
        await update.effective_message.reply_text(f"Não há mensagens agendadas ({tier.upper()}).")
        return
    lines = [f"🕒 <b>Mensagens agendadas — {tier.upper()}</b>"]
    for m in msgs:
        status = "ON" if m.enabled else "OFF"
        preview = (m.text[:80] + "…") if len(m.text) > 80 else m.text
        lines.append(f"#{m.id} — {m.hhmm} ({m.tz}) [{status}] — {esc(preview)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def list_msgs_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _list_msgs_tier(update, context, "vip")

async def list_msgs_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _list_msgs_tier(update, context, "free")

async def _edit_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text(f"Uso: /edit_msg_{tier} <id> [HH:MM] [novo texto]")
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

    m_current = scheduled_get(sid)
    if not m_current or m_current.tier != tier:
        await update.effective_message.reply_text(f"Mensagem não encontrada no tier {tier.upper()}.")
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
        context.job_queue.run_daily(
            _scheduled_message_job,
            time=dt.time(hour=h, minute=k, tzinfo=tz),
            name=f"{JOB_PREFIX_SM}{m.id}",
        )
    await update.effective_message.reply_text("✅ Mensagem atualizada.")

async def edit_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _edit_msg_tier(update, context, "vip")

async def edit_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _edit_msg_tier(update, context, "free")

async def _toggle_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text(f"Uso: /toggle_msg_{tier} <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    m_current = scheduled_get(sid)
    if not m_current or m_current.tier != tier:
        await update.effective_message.reply_text(f"Mensagem não encontrada no tier {tier.upper()}.")
        return
    new_state = scheduled_toggle(sid)
    if new_state is None:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    await update.effective_message.reply_text(f"✅ Mensagem #{sid} ({tier.upper()}) agora está {'ON' if new_state else 'OFF'}.")

async def toggle_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle_msg_tier(update, context, "vip")

async def toggle_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _toggle_msg_tier(update, context, "free")

async def _del_msg_tier(update: Update, context: ContextTypes.DEFAULT_TYPE, tier: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text(f"Uso: /del_msg_{tier} <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    m_current = scheduled_get(sid)
    if not m_current or m_current.tier != tier:
        await update.effective_message.reply_text(f"Mensagem não encontrada no tier {tier.upper()}.")
        return
    ok = scheduled_delete(sid)
    if not ok:
        await update.effective_message.reply_text("Mensagem não encontrada.")
        return
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}":
            j.schedule_removal()
    await update.effective_message.reply_text("✅ Mensagem removida.")

async def del_msg_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _del_msg_tier(update, context, "vip")

async def del_msg_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _del_msg_tier(update, context, "free")

# ====== Set horário dos jobs diários (VIP/FREE) ======
async def set_pack_horario_vip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /set_pack_horario_vip HH:MM")
        return
    try:
        hhmm = context.args[0]
        parse_hhmm(hhmm)
        cfg_set("daily_pack_vip_hhmm", hhmm)
        await _reschedule_daily_packs()
        await update.effective_message.reply_text(f"✅ Horário diário dos packs VIP definido para {hhmm}.")
    except Exception as e:
        await update.effective_message.reply_text(f"Hora inválida: {e}")

async def set_pack_horario_free_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /set_pack_horario_free HH:MM")
        return
    try:
        hhmm = context.args[0]
        parse_hhmm(hhmm)
        cfg_set("daily_pack_free_hhmm", hhmm)
        await _reschedule_daily_packs()
        await update.effective_message.reply_text(f"✅ Horário diário dos packs FREE definido para {hhmm}.")
    except Exception as e:
        await update.effective_message.reply_text(f"Hora inválida: {e}")

# ====== Teaser do FREE (preview espelhado do VIP) ======
async def set_free_teaser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    txt = " ".join(context.args).strip()
    if not txt:
        await update.effective_message.reply_text("Uso: /set_free_teaser <texto com {title}>")
        return
    cfg_set("free_teaser", txt)
    await update.effective_message.reply_text("✅ Teaser do FREE atualizado.")

async def ver_free_teaser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cur = cfg_get("free_teaser", DEFAULT_FREE_TEASER)
    await update.effective_message.reply_text(f"Teaser atual:\n\n{cur}")

# =========================
# KEEPALIVE (Render)
# =========================
async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        url = f"{BASE_URL}/ping" if BASE_URL else None
        if not url:
            url = "http://127.0.0.1"  # fallback local (não deve ser usado no Render)
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.get(url)
        logging.info("[keepalive] OK")
    except Exception as e:
        logging.warning(f"[keepalive] erro: {e}")

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
    chain = data.get("chain") or CHAIN_NAME

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
        invite = await application.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
        await application.bot.send_message(chat_id=int(uid), text=f"✅ Pagamento confirmado! Entre no VIP: {invite}")
    except Exception:
        logging.exception("Erro enviando invite")

    return JSONResponse({"ok": True})

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

@app.get("/ping")
async def ping():
    return {"ok": True, "time": now_utc().isoformat()}

@app.get("/")
async def root():
    return {"status": "online", "message": "Bot ready (crypto + schedules + VIP/FREE + keepalive)"}

# =========================
# Startup: register handlers & jobs
# =========================
@app.on_event("startup")
async def on_startup():
    global bot, BOT_USERNAME
    logging.basicConfig(level=logging.INFO)

    await application.initialize()
    await application.start()
    bot = application.bot

    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL não definido no .env")
    await bot.set_webhook(url=WEBHOOK_URL)

    me = await bot.get_me()
    BOT_USERNAME = me.username

    logging.info("Bot iniciado (cripto + schedules + VIP/FREE).")

    # ===== Error handler =====
    application.add_error_handler(error_handler)

    # ===== Conversas do NOVOPACK (group=0) =====
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

    # Handler principal: /novopack (privado ou grupos cadastrados) + /start novopack (privado via deep-link)
    conv_main = ConversationHandler(
        entry_points=[
            CommandHandler("novopack", novopack_start),
            CommandHandler("start", novopack_start, filters=filters.ChatType.PRIVATE & filters.Regex(r"^/start\s+novopack(\s|$)")),
        ],
        states={
            CHOOSE_TIER: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_choose_tier)],
            **states_map,
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_main, group=0)

    # Atalhos (opcionais, só privado)
    conv_vip = ConversationHandler(
        entry_points=[CommandHandler("novopackvip", novopackvip_start, filters=filters.ChatType.PRIVATE)],
        states=states_map,
        fallbacks=[CommandHandler("cancelar", novopack_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_vip, group=0)

    conv_free = ConversationHandler(
        entry_points=[CommandHandler("novopackfree", novopackfree_start, filters=filters.ChatType.PRIVATE)],
        states=states_map,
        fallbacks=[CommandHandler("cancelar", novopack_cancel)],
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

    # ===== Handlers do grupo de armazenamento (group=1) =====
    application.add_handler(
        MessageHandler(
            (filters.Chat(STORAGE_GROUP_ID) | filters.Chat(STORAGE_GROUP_FREE_ID)) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler
        ),
        group=1,
    )
    media_filter = (
        (filters.Chat(STORAGE_GROUP_ID) | filters.Chat(STORAGE_GROUP_FREE_ID))
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

    # Packs & admin
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

    # Admin mgmt & util
    application.add_handler(CommandHandler("listar_admins", listar_admins_cmd), group=1)
    application.add_handler(CommandHandler("add_admin", add_admin_cmd), group=1)
    application.add_handler(CommandHandler("rem_admin", rem_admin_cmd), group=1)
    application.add_handler(CommandHandler("mudar_nome", mudar_nome_cmd), group=1)
    application.add_handler(CommandHandler("mudar_username", mudar_username_cmd), group=1)
    application.add_handler(CommandHandler("limpar_chat", limpar_chat_cmd), group=1)

    # Pagamentos cripto
    application.add_handler(CommandHandler("pagar", pagar_cmd), group=1)
    application.add_handler(CommandHandler("tx", tx_cmd), group=1)
    application.add_handler(CommandHandler("listar_pendentes", listar_pendentes_cmd), group=1)
    application.add_handler(CommandHandler("aprovar_tx", aprovar_tx_cmd), group=1)
    application.add_handler(CommandHandler("rejeitar_tx", rejeitar_tx_cmd), group=1)

    # Mensagens agendadas (por tier)
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

    # Set horário dos packs diários
    application.add_handler(CommandHandler("set_pack_horario_vip", set_pack_horario_vip_cmd), group=1)
    application.add_handler(CommandHandler("set_pack_horario_free", set_pack_horario_free_cmd), group=1)

    # Teaser FREE
    application.add_handler(CommandHandler("set_free_teaser", set_free_teaser_cmd), group=1)
    application.add_handler(CommandHandler("ver_free_teaser", ver_free_teaser_cmd), group=1)

    # Jobs diários de packs
    await _reschedule_daily_packs()

    # (Re)registrar todas as mensagens agendadas existentes (não mexe nos jobs de packs)
    _register_all_scheduled_messages(application.job_queue)

    # Keepalive para evitar hibernação no Render
    application.job_queue.run_repeating(keepalive_job, interval=KEEPALIVE_INTERVAL_SEC, first=10, name="keepalive")

    logging.info("Handlers e jobs registrados.")

# =========================
# Run
# =========================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
