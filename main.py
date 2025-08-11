# main.py
import os
import logging
import asyncio
import datetime as dt
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Tuple

import html
import pytz
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

import uvicorn

from telegram import (
    Update, InputMediaPhoto, ChatInviteLink
)
from telegram.constants import ParseMode
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

def chunk_text(lines: List[str], max_len: int = 3800) -> List[str]:
    """Divide long texts safely for Telegram HTML."""
    out, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > max_len:
            out.append(cur)
            cur = ""
        cur += ("" if not cur else "\n") + ln
    if cur:
        out.append(cur)
    return out

# =========================
# ENV / CONFIG
# =========================
load_dotenv()

# Obrigatórios
BOT_TOKEN = os.getenv("BOT_TOKEN")  # já está no Render
BASE_URL = os.getenv("BASE_URL")  # ex.: https://seu-servico.onrender.com

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido.")

if not BASE_URL:
    # pode funcionar sem, mas sem webhook auto-set
    logging.warning("BASE_URL não definido; defina para setar webhook automaticamente.")

# Grupos
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))              # VIP de entrega
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID", "-1002509364079"))            # FREE de marketing
STORAGE_VIP_GROUP_ID = int(os.getenv("STORAGE_VIP_GROUP_ID", "-4806334341")) # grupo de cadastro VIP
STORAGE_FREE_GROUP_ID = int(os.getenv("STORAGE_FREE_GROUP_ID", "-4806334342"))# grupo de cadastro FREE (ajuste se desejar)

# Web server
PORT = int(os.getenv("PORT", 10000))

# Pagamento cripto
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "0x40dDBD27F878d07808339F9965f013F1CBc2F812").strip()
DEFAULT_CHAIN = os.getenv("DEFAULT_CHAIN", "polygon").strip().lower()  # polygon / bsc / ethereum / arbitrum / base / etc.

# Preço padrão
DEFAULT_PRICE_AMOUNT = os.getenv("DEFAULT_PRICE_AMOUNT", "50")  # string
DEFAULT_PRICE_CURRENCY = os.getenv("DEFAULT_PRICE_CURRENCY", "USDT")

# Mensagem teaser FREE
DEFAULT_FREE_TEASER = os.getenv("DEFAULT_FREE_TEASER", "Hoje liberamos no VIP: {title}\nPara entrar no VIP digite /grupovip")

# Keepalive
KEEPALIVE_INTERVAL_SEC = int(os.getenv("KEEPALIVE_INTERVAL_SEC", "240"))

# =========================
# FASTAPI + PTB
# =========================
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None

# =========================
# DB setup
# =========================
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./bot_data.db")  # Use Postgres no Render para persistir!
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

@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()

# ---- Config chave/valor persistente ----
class ConfigKV(Base):
    __tablename__ = "config_kv"
    key = Column(String, primary_key=True)
    value = Column(String, nullable=True)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)

def cfg_get(key: str, default: Optional[str] = None) -> Optional[str]:
    with session_scope() as s:
        row = s.query(ConfigKV).filter(ConfigKV.key == key).first()
        return row.value if row else default

def cfg_set(key: str, value: Optional[str]):
    with session_scope() as s:
        row = s.query(ConfigKV).filter(ConfigKV.key == key).first()
        if not row:
            row = ConfigKV(key=key, value=value)
            s.add(row)
        else:
            row.value = value

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
    audience = Column(String, default="vip")  # vip | free
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
    audience = Column(String, default="vip")  # vip | free
    hhmm = Column(String, nullable=False)      # "HH:MM"
    tz = Column(String, default="America/Sao_Paulo")
    text = Column(Text, nullable=False)
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

def init_db():
    Base.metadata.create_all(bind=engine)
    # Admin inicial
    initial_admin_id = os.getenv("INITIAL_ADMIN_ID")
    if initial_admin_id:
        with session_scope() as s:
            uid = int(initial_admin_id)
            if not s.query(Admin).filter(Admin.user_id == uid).first():
                s.add(Admin(user_id=uid))
    # Defaults
    if not cfg_get("daily_pack_hhmm"):
        cfg_set("daily_pack_hhmm", "18:49")
    if not cfg_get("price_amount"):
        cfg_set("price_amount", DEFAULT_PRICE_AMOUNT)
    if not cfg_get("price_currency"):
        cfg_set("price_currency", DEFAULT_PRICE_CURRENCY)
    if not cfg_get("free_teaser"):
        cfg_set("free_teaser", DEFAULT_FREE_TEASER)

ensure_bigint_columns()
init_db()

# =========================
# DB helpers
# =========================
def is_admin(user_id: int) -> bool:
    with session_scope() as s:
        return s.query(Admin).filter(Admin.user_id == user_id).first() is not None

def list_admin_ids() -> List[int]:
    with session_scope() as s:
        return [a.user_id for a in s.query(Admin).order_by(Admin.added_at.asc()).all()]

def add_admin_db(user_id: int) -> bool:
    with session_scope() as s:
        if s.query(Admin).filter(Admin.user_id == user_id).first():
            return False
        s.add(Admin(user_id=user_id))
        return True

def remove_admin_db(user_id: int) -> bool:
    with session_scope() as s:
        a = s.query(Admin).filter(Admin.user_id == user_id).first()
        if not a:
            return False
        s.delete(a)
        return True

def create_pack(title: str, audience: str, header_message_id: Optional[int] = None) -> Pack:
    with session_scope() as s:
        p = Pack(title=title.strip(), audience=audience, header_message_id=header_message_id)
        s.add(p)
        s.flush()
        s.refresh(p)
        return p

def get_pack_by_header(message_id: int) -> Optional[Pack]:
    with session_scope() as s:
        return s.query(Pack).filter(Pack.header_message_id == message_id).first()

def add_file_to_pack(pack_id: int, file_id: str, file_unique_id: Optional[str], file_type: str, role: str, file_name: Optional[str] = None):
    with session_scope() as s:
        pf = PackFile(
            pack_id=pack_id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_type=file_type,
            role=role,
            file_name=file_name,
        )
        s.add(pf)
        s.flush()
        s.refresh(pf)
        return pf

def get_next_unsent_pack() -> Optional[Pack]:
    with session_scope() as s:
        return s.query(Pack).filter(Pack.sent == False, Pack.audience == "vip").order_by(Pack.created_at.asc()).first()

def mark_pack_sent(pack_id: int):
    with session_scope() as s:
        p = s.query(Pack).filter(Pack.id == pack_id).first()
        if p:
            p.sent = True

def list_packs() -> List[Pack]:
    with session_scope() as s:
        return s.query(Pack).order_by(Pack.created_at.desc()).all()

def packs_detail_for_list() -> List[str]:
    with session_scope() as s:
        items = s.query(Pack).order_by(Pack.created_at.desc()).all()
        lines = []
        for p in items:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(f"[{p.id}] {esc(p.title)} — {p.audience.upper()} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        return lines

def pending_payments_lines() -> List[str]:
    with session_scope() as s:
        pend = s.query(Payment).filter(Payment.status == "pending").order_by(Payment.created_at.asc()).all()
        lines = []
        for p in pend:
            lines.append(f"- user_id:{p.user_id} @{p.username or '-'} | {p.tx_hash} | {p.chain} | {p.created_at.strftime('%d/%m %H:%M')}")
        return lines

# =========================
# Conversa /novopack
# =========================
TITLE, CHOOSE_AUDIENCE, PREVIEWS, FILES, CONFIRM_SAVE = range(5)

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—")
    audience = user_data.get("audience", "vip")
    previews = user_data.get("previews", [])
    files = user_data.get("files", [])
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
        f"📦 <b>Resumo do Pack</b>",
        f"• Nome: <b>{esc(title)}</b>",
        f"• Público: <b>{audience.upper()}</b>",
        f"• Previews ({len(previews)}): " + (", ".join(preview_names) if preview_names else "—"),
        f"• Arquivos ({len(files)}): " + (", ".join(file_names) if file_names else "—"),
        "",
        "Deseja salvar? (<b>sim</b>/<b>não</b>)"
    ]
    return "\n".join(text)

async def novopack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # permitido no privado e também nos grupos de storage
    context.user_data.clear()
    await update.effective_message.reply_text(
        "🧩 Vamos criar um novo pack!\n\n"
        "1) Envie o <b>título do pack</b> (apenas texto).",
        parse_mode=ParseMode.HTML
    )
    return TITLE

async def novopack_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.effective_message.text or "").strip()
    if not title:
        await update.effective_message.reply_text("Título vazio. Envie um texto com o título do pack.")
        return TITLE
    context.user_data["title"] = title
    await update.effective_message.reply_text(
        "2) Este pack é para <b>VIP</b> ou <b>FREE</b>? Responda com <b>vip</b> ou <b>free</b>.",
        parse_mode=ParseMode.HTML
    )
    return CHOOSE_AUDIENCE

async def novopack_choose_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = (update.effective_message.text or "").strip().lower()
    if ans not in ("vip", "free"):
        await update.effective_message.reply_text("Responda apenas com: vip ou free.")
        return CHOOSE_AUDIENCE
    context.user_data["audience"] = ans
    context.user_data["previews"] = []
    context.user_data["files"] = []
    await update.effective_message.reply_text(
        "3) Envie as <b>PREVIEWS</b> (📷 fotos / 🎞 vídeos / 🎞 animações).\n"
        "Envie quantas quiser. Quando terminar, mande /proximo.",
        parse_mode=ParseMode.HTML
    )
    return PREVIEWS

async def novopack_collect_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    previews: List[Dict[str, Any]] = context.user_data.get("previews", [])
    if msg.photo:
        biggest = msg.photo[-1]
        previews.append({"file_id": biggest.file_id, "file_type": "photo", "file_name": (msg.caption or "").strip() or None})
        await msg.reply_text("✅ Preview (foto) adicionada. Envie mais ou /proximo.")
    elif msg.video:
        previews.append({"file_id": msg.video.file_id, "file_type": "video", "file_name": (msg.caption or "").strip() or None})
        await msg.reply_text("✅ Preview (vídeo) adicionada. Envie mais ou /proximo.")
    elif msg.animation:
        previews.append({"file_id": msg.animation.file_id, "file_type": "animation", "file_name": (msg.caption or "").strip() or None})
        await msg.reply_text("✅ Preview (animação) adicionada. Envie mais ou /proximo.")
    else:
        await msg.reply_text("Envie foto/vídeo/animação ou /proximo.")
        return PREVIEWS
    context.user_data["previews"] = previews
    return PREVIEWS

async def novopack_next_to_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "4) Agora envie os <b>ARQUIVOS</b> (📄 documentos / 🎵 áudio / 🎙 voice).\n"
        "Envie quantos quiser. Quando terminar, mande /finalizar.",
        parse_mode=ParseMode.HTML
    )
    return FILES

async def novopack_collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    files: List[Dict[str, Any]] = context.user_data.get("files", [])
    if msg.document:
        files.append({"file_id": msg.document.file_id, "file_type": "document", "file_name": getattr(msg.document, "file_name", None) or (msg.caption or "").strip() or None})
        await msg.reply_text("✅ Documento adicionado. Envie mais ou /finalizar.")
    elif msg.audio:
        files.append({"file_id": msg.audio.file_id, "file_type": "audio", "file_name": getattr(msg.audio, "file_name", None) or (msg.caption or "").strip() or None})
        await msg.reply_text("✅ Áudio adicionado. Envie mais ou /finalizar.")
    elif msg.voice:
        files.append({"file_id": msg.voice.file_id, "file_type": "voice", "file_name": (msg.caption or "").strip() or None})
        await msg.reply_text("✅ Voice adicionado. Envie mais ou /finalizar.")
    else:
        await msg.reply_text("Envie documento/áudio/voice ou /finalizar.")
        return FILES
    context.user_data["files"] = files
    return FILES

async def novopack_finish_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    summary = _summary_from_session(context.user_data)
    await update.effective_message.reply_text(summary, parse_mode=ParseMode.HTML)
    return CONFIRM_SAVE

async def novopack_confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Responda <b>sim</b> para salvar ou <b>não</b> para cancelar.", parse_mode=ParseMode.HTML)
        return CONFIRM_SAVE
    if answer in ("não", "nao"):
        context.user_data.clear()
        await update.effective_message.reply_text("Operação cancelada. Nada foi salvo.")
        return ConversationHandler.END

    title = context.user_data.get("title")
    audience = context.user_data.get("audience", "vip")
    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])

    p = create_pack(title=title, audience=audience, header_message_id=None)
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
    await update.effective_message.reply_text(f"🎉 {title} cadastrado com sucesso ({audience.upper()}).")
    return ConversationHandler.END

async def novopack_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Operação cancelada.")
    return ConversationHandler.END

# =========================
# STORAGE GROUP handlers (VIP & FREE)
# =========================
async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id
    if chat_id not in (STORAGE_VIP_GROUP_ID, STORAGE_FREE_GROUP_ID):
        return
    title = (msg.text or "").strip()
    if not title or msg.reply_to_message:
        return
    lower = title.lower()
    banned = {"sim", "não", "nao", "/proximo", "/finalizar", "/cancelar"}
    if lower in banned or title.startswith("/") or len(title) < 4:
        return
    words = title.split()
    looks_like_title = (len(words) >= 2)
    if not looks_like_title:
        return

    # Qualquer membro pode cadastrar no grupo de storage
    if get_pack_by_header(msg.message_id):
        await msg.reply_text("Pack já registrado para este cabeçalho.")
        return

    audience = "vip" if chat_id == STORAGE_VIP_GROUP_ID else "free"
    p = create_pack(title=title, audience=audience, header_message_id=msg.message_id)
    await msg.reply_text(f"Pack registrado: <b>{esc(p.title)}</b> (id {p.id}, {audience.upper()})", parse_mode=ParseMode.HTML)

async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    chat_id = msg.chat.id
    if chat_id not in (STORAGE_VIP_GROUP_ID, STORAGE_FREE_GROUP_ID):
        return
    reply = msg.reply_to_message
    if not reply or not reply.message_id:
        await msg.reply_text("Envie este arquivo como <b>resposta</b> ao título do pack.", parse_mode=ParseMode.HTML)
        return
    pack = get_pack_by_header(reply.message_id)
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
        await msg.reply_text("Tipo de mídia não suportado.", parse_mode=ParseMode.HTML)
        return

    add_file_to_pack(pack_id=pack.id, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role, file_name=visible_name)
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b>.", parse_mode=ParseMode.HTML)

# =========================
# ENVIO DO PACK (JobQueue)
# =========================
async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        pack = get_next_unsent_pack()
        if not pack:
            logging.info("Nenhum pack VIP pendente para envio.")
            return "Nenhum pack VIP pendente para envio."

        # Carrega arquivos
        with session_scope() as s:
            p = s.query(Pack).filter(Pack.id == pack.id).first()
            files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()

        if not files:
            logging.warning(f"Pack '{p.title}' sem arquivos; marcando como enviado.")
            mark_pack_sent(p.id)
            return f"Pack '{p.title}' não possui arquivos. Marcado como enviado."

        previews = [f for f in files if f.role == "preview"]
        docs     = [f for f in files if f.role == "file"]

        # 1) Enviar previews (fotos) em media group no VIP (caption na 1ª)
        photo_ids = [f.file_id for f in previews if f.file_type == "photo"]
        sent_first = False
        if photo_ids:
            media = []
            for i, fid in enumerate(photo_ids):
                if i == 0:
                    media.append(InputMediaPhoto(media=fid, caption=pack.title))
                else:
                    media.append(InputMediaPhoto(media=fid))
            try:
                await context.application.bot.send_media_group(chat_id=GROUP_VIP_ID, media=media)
                sent_first = True
            except Exception as e:
                logging.warning(f"Falha send_media_group: {e}. Enviando individual.")
                for i, fid in enumerate(photo_ids):
                    cap = pack.title if i == 0 else None
                    await context.application.bot.send_photo(chat_id=GROUP_VIP_ID, photo=fid, caption=cap)
                    sent_first = True

        # 2) Enviar vídeos/animações de preview (caption no primeiro envio se ainda não usado)
        for f in [f for f in previews if f.file_type in ("video", "animation")]:
            cap = pack.title if not sent_first else None
            try:
                if f.file_type == "video":
                    await context.application.bot.send_video(chat_id=GROUP_VIP_ID, video=f.file_id, caption=cap)
                else:
                    await context.application.bot.send_animation(chat_id=GROUP_VIP_ID, animation=f.file_id, caption=cap)
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando preview {f.id}: {e}")

        # 3) Enviar arquivos
        for f in docs:
            try:
                cap = pack.title if not sent_first else None
                if f.file_type == "document":
                    await context.application.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                elif f.file_type == "audio":
                    await context.application.bot.send_audio(chat_id=GROUP_VIP_ID, audio=f.file_id, caption=cap)
                elif f.file_type == "voice":
                    await context.application.bot.send_voice(chat_id=GROUP_VIP_ID, voice=f.file_id, caption=cap)
                else:
                    await context.application.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando arquivo {f.file_name or f.id}: {e}")

        # 4) Enviar TODAS as PREVIEWS no FREE com teaser
        teaser_tpl = cfg_get("free_teaser", DEFAULT_FREE_TEASER)
        teaser = (teaser_tpl or "").format(title=pack.title)
        if teaser.strip():
            try:
                await context.application.bot.send_message(chat_id=GROUP_FREE_ID, text=teaser)
            except Exception as e:
                logging.warning(f"Erro enviando teaser FREE: {e}")

        # fotos preview para FREE
        if photo_ids:
            media_free = []
            for i, fid in enumerate(photo_ids):
                if i == 0:
                    media_free.append(InputMediaPhoto(media=fid, caption=f"{pack.title}"))
                else:
                    media_free.append(InputMediaPhoto(media=fid))
            try:
                await context.application.bot.send_media_group(chat_id=GROUP_FREE_ID, media=media_free)
            except Exception as e:
                logging.warning(f"Falha media_group FREE: {e}. Enviando individual.")
                for i, fid in enumerate(photo_ids):
                    cap = pack.title if i == 0 else None
                    try:
                        await context.application.bot.send_photo(chat_id=GROUP_FREE_ID, photo=fid, caption=cap)
                    except Exception as e2:
                        logging.warning(f"Erro foto FREE: {e2}")

        mark_pack_sent(p.id)
        logging.info(f"Pack enviado: {p.title}")
        return f"✅ Enviado pack VIP '{p.title}' (e previews no FREE)."

    except Exception as e:
        logging.exception("Erro no enviar_pack_vip_job")
        return f"❌ Erro no envio: {e!r}"

# =========================
# KEEPALIVE
# =========================
async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        # 1) GET no /ping local
        import httpx
        url = f"{BASE_URL}/ping" if BASE_URL else "http://127.0.0.1"
        async with httpx.AsyncClient(timeout=10) as cli:
            await cli.get(url)
        logging.info("[keepalive] tick")
    except Exception as e:
        logging.warning(f"[keepalive] erro: {e}")

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
    price = f"{cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}"
    lines = [
        "📋 <b>Comandos</b>",
        "• /start — mensagem inicial",
        "• /comandos — lista de comandos",
        "• /listar_comandos — (alias)",
        "• /getid — mostra seus IDs",
        "",
        "💸 Pagamento (MetaMask, multi-rede):",
        "• /pagar — instruções e redes aceitas",
        "• /tx HASH — auto-detecta rede (ex.: /tx 0xabc...)",
        "• /tx REDE HASH — força rede (ex.: /tx polygon 0xabc...)",
        f"Preço VIP atual: <b>{esc(price)}</b>",
        "",
        "🧩 Packs:",
        "• /novopack — pergunta VIP/FREE (privado ou grupos de cadastro)",
        "• /novopackvip — atalho VIP (privado)",
        "• /novopackfree — atalho FREE (privado)",
        "• /cancelar — cancela o fluxo do novo pack",
        "• /listar_packs — lista todos os packs (admins no PV, liberado nos storages)",
        "",
        "🕒 Mensagens agendadas:",
        "• /add_msg_vip HH:MM <texto> | /add_msg_free HH:MM <texto>",
        "• /list_msgs_vip | /list_msgs_free",
        "• /edit_msg_vip <id> [HH:MM] [novo texto]",
        "• /edit_msg_free <id> [HH:MM] [novo texto]",
        "• /toggle_msg_vip <id> | /toggle_msg_free <id>",
        "• /del_msg_vip <id> | /del_msg_free <id>",
        "",
        "🛠 Admin & Config:",
        "• /simularvip — envia o próximo pack pendente agora (e previews no FREE)",
        "• /set_pack_horario HH:MM — define o horário diário do envio de pack VIP",
        "• /set_preco VALOR MOEDA — define o preço (ex.: /set_preco 50 USDT)",
        "• /ver_preco — mostra o preço atual",
        "• /set_free_teaser <texto> — teaser do FREE (usa {title})",
        "• /add_admin <user_id> | /rem_admin <user_id> | /listar_admins",
        "• /listar_pendentes — pagamentos a aprovar",
        "• /aprovar_tx <user_id> | /rejeitar_tx <user_id> [motivo]",
        "• /grupovip — instruções VIP no privado",
    ]
    chunks = chunk_text(lines)
    for ch in chunks:
        await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

listar_comandos_cmd = comandos_cmd

async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            f"Seu nome: {esc(user.full_name)}\nSeu ID: {user.id}\nID deste chat: {chat.id}",
            parse_mode=ParseMode.HTML
        )

# ===== Admin utils =====
def _is_admin_or_storage(update: Update) -> bool:
    """Admin no privado/qualquer lugar, ou qualquer usuário nos grupos de storage."""
    user_is_admin = update.effective_user and is_admin(update.effective_user.id)
    in_storage = update.effective_chat and update.effective_chat.id in (STORAGE_VIP_GROUP_ID, STORAGE_FREE_GROUP_ID)
    return user_is_admin or in_storage

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

# ===== Packs admin/list =====
async def listar_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin_or_storage(update):
        await update.effective_message.reply_text("Apenas admins (ou use este comando nos grupos de storage).")
        return
    lines = packs_detail_for_list()
    if not lines:
        await update.effective_message.reply_text("Nenhum pack registrado.")
        return
    # envia em partes se necessário
    chunks = chunk_text(lines)
    for ch in chunks:
        await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    status = await enviar_pack_vip_job(context)
    await update.effective_message.reply_text(status)

# =========================
# Pagamento por MetaMask - Fluxo
# =========================
NETWORK_ALIASES = {
    "polygon": ["polygon", "matic", "pol"],
    "bsc": ["bsc", "bnb", "binance"],
    "ethereum": ["eth", "ethereum", "mainnet"],
    "arbitrum": ["arbitrum", "arb"],
    "base": ["base"],
    "avalanche": ["avax", "avalanche"],
}

def _detect_network_from_hash(txh: str) -> Optional[str]:
    # Sem calls externas: mantenha padrão
    return cfg_get("default_chain", DEFAULT_CHAIN)

async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    price = f"{cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}"
    chains = ", ".join(sorted(NETWORK_ALIASES.keys()))
    msg = (
        f"💸 <b>Pagamento via MetaMask</b>\n"
        f"Carteira ({esc(cfg_get('default_chain', DEFAULT_CHAIN)).upper()}):\n"
        f"<code>{esc(WALLET_ADDRESS)}</code>\n"
        f"Valor do VIP: <b>{esc(price)}</b>\n\n"
        f"Após pagar, envie:\n"
        f"/tx HASH  (tento detectar a rede)\n"
        f"/tx REDE HASH  (ex.: /tx polygon 0xabc...)\n\n"
        f"Redes aceitas: {esc(chains)}"
    )
    # Se no grupo FREE: apaga depois de 5s e manda PV
    if chat and chat.id == GROUP_FREE_ID:
        try:
            sent = await update.effective_message.reply_text("✅ Te enviei as instruções no privado. (vou apagar aqui em 5s)")
            await asyncio.sleep(5)
            try:
                await update.effective_message.delete()
            except:
                pass
            try:
                await sent.delete()
            except:
                pass
        except:
            pass
        try:
            await application.bot.send_message(chat_id=user.id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e:
            logging.warning(f"Falha ao DM /pagar para {user.id}: {e}")
        return

    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

async def grupovip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    price = f"{cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}"
    hello = f"Olá, {esc(user.first_name)}! 👋"
    msg = (
        f"{hello}\n\n"
        f"Para entrar no VIP, o valor é <b>{esc(price)}</b> em cripto na carteira abaixo "
        f"({esc(cfg_get('default_chain', DEFAULT_CHAIN)).upper()}):\n"
        f"<code>{esc(WALLET_ADDRESS)}</code>\n\n"
        f"Depois envie o comando /pagar para ver as instruções ou mande diretamente /tx ..."
    )
    try:
        await application.bot.send_message(chat_id=user.id, text=msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.warning(f"Falha ao DM /grupovip: {e}")
        await update.effective_message.reply_text("Te enviei no privado; se não chegou, me chame aqui no PV.")

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user
    args = context.args
    if not args:
        await msg.reply_text("Uso:\n/tx HASH\nou\n/tx REDE HASH (ex.: /tx polygon 0xabc...)")
        return

    if len(args) == 1:
        tx_hash = args[0].strip()
        chain = _detect_network_from_hash(tx_hash)
    else:
        chain_candidate = args[0].lower()
        tx_hash = args[1].strip()
        # normaliza rede
        chain = None
        for key, aliases in NETWORK_ALIASES.items():
            if chain_candidate in aliases or chain_candidate == key:
                chain = key
                break
        if not chain:
            await msg.reply_text("Rede inválida. Ex.: polygon, bsc, ethereum, arbitrum, base, avalanche")
            return

    if not tx_hash.startswith("0x") or len(tx_hash) < 10:
        await msg.reply_text("Hash inválido.")
        return

    with session_scope() as s:
        if s.query(Payment).filter(Payment.tx_hash == tx_hash).first():
            await msg.reply_text("Esse hash já foi registrado. Aguarde aprovação.")
            return
        p = Payment(
            user_id=user.id,
            username=user.username,
            tx_hash=tx_hash,
            chain=chain,
            status="pending",
        )
        s.add(p)

    await msg.reply_text("✅ Recebi seu hash! Assim que for aprovado, te envio o convite do VIP.")

async def listar_pendentes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Admin no PV ou em storage groups
    if not _is_admin_or_storage(update):
        await update.effective_message.reply_text("Apenas admins (ou use nos grupos de storage).")
        return
    lines = pending_payments_lines()
    if not lines:
        await update.effective_message.reply_text("Sem pagamentos pendentes.")
        return
    lines = ["⏳ <b>Pendentes</b>"] + lines
    for ch in chunk_text(lines):
        await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

async def _notify_admins(text: str):
    for uid in list_admin_ids():
        try:
            await application.bot.send_message(chat_id=uid, text=text)
        except:
            pass

async def _send_invite_one_click() -> Optional[ChatInviteLink]:
    """Cria link de uma utilização e expira em 1h."""
    try:
        expire_date = int((dt.datetime.utcnow() + dt.timedelta(hours=1)).timestamp())
        link = await application.bot.create_chat_invite_link(
            chat_id=GROUP_VIP_ID,
            expire_date=expire_date,
            member_limit=1,
            creates_join_request=False,
            name="1-click VIP"
        )
        return link
    except Exception as e:
        logging.exception("Erro criando invite 1-click")
        return None

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

    with session_scope() as s:
        p = s.query(Payment).filter(Payment.user_id == uid, Payment.status == "pending").order_by(Payment.created_at.asc()).first()
        if not p:
            await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
            return
        p.status = "approved"
        p.decided_at = now_utc()

    link = await _send_invite_one_click()
    if link:
        try:
            await application.bot.send_message(chat_id=uid, text=f"✅ Pagamento aprovado! Entre no VIP: {link.invite_link}")
        except Exception:
            logging.exception("Erro enviando invite ao usuário")

    # Notificar admins
    try:
        count = await application.bot.get_chat_member_count(chat_id=GROUP_VIP_ID)
    except Exception:
        count = None
    await _notify_admins(f"Novo VIP aprovado: {uid}.\nMembros no VIP agora: {count if count is not None else 'n/d'}.")

    await update.effective_message.reply_text(f"Aprovado. Convite enviado {'(1 clique)' if link else ''}.")

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

    with session_scope() as s:
        p = s.query(Payment).filter(Payment.user_id == uid, Payment.status == "pending").order_by(Payment.created_at.asc()).first()
        if not p:
            await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
            return
        p.status = "rejected"
        p.notes = motivo
        p.decided_at = now_utc()

    try:
        await application.bot.send_message(chat_id=uid, text=f"❌ Pagamento rejeitado. Motivo: {motivo}")
    except:
        pass
    await update.effective_message.reply_text("Pagamento rejeitado.")

# ===== Preço & teaser =====
async def set_preco_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /set_preco VALOR MOEDA\nEx.: /set_preco 50 USDT")
        return
    amount = context.args[0]
    currency = context.args[1].upper()
    cfg_set("price_amount", amount)
    cfg_set("price_currency", currency)
    await update.effective_message.reply_text(f"✅ Preço atualizado para {amount} {currency}.")

async def ver_preco_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = f"{cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}"
    await update.effective_message.reply_text(f"Preço VIP atual: {price}")

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

# ===== Horário diário =====
async def set_pack_horario_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /set_pack_horario HH:MM")
        return
    try:
        hhmm = context.args[0]
        parse_hhmm(hhmm)
        cfg_set("daily_pack_hhmm", hhmm)
        await _reschedule_daily_pack()
        await update.effective_message.reply_text(f"✅ Horário diário dos packs definido para {hhmm}.")
    except Exception as e:
        await update.effective_message.reply_text(f"Hora inválida: {e}")

# =========================
# Mensagens agendadas (VIP/FREE)
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
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
    if not m or not m.enabled:
        return
    chat_id = GROUP_VIP_ID if m.audience == "vip" else GROUP_FREE_ID
    try:
        await context.application.bot.send_message(chat_id=chat_id, text=m.text)
    except Exception as e:
        logging.warning(f"Falha ao enviar scheduled_message id={sid}: {e}")

def _register_all_scheduled_messages(job_queue: JobQueue):
    for j in list(job_queue.jobs()):
        if j.name and (j.name.startswith(JOB_PREFIX_SM) or j.name == "keepalive" or j.name == "daily_pack"):
            continue
    with session_scope() as s:
        msgs = s.query(ScheduledMessage).order_by(ScheduledMessage.hhmm.asc(), ScheduledMessage.id.asc()).all()
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

async def add_msg_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text(f"Uso: /add_msg_{audience} HH:MM <texto>")
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
    with session_scope() as s:
        m = ScheduledMessage(audience=audience, hhmm=hhmm, text=texto, tz="America/Sao_Paulo", enabled=True)
        s.add(m)
        s.flush()
        sid = m.id
    tz = _tz("America/Sao_Paulo")
    h, k = parse_hhmm(hhmm)
    context.job_queue.run_daily(
        _scheduled_message_job,
        time=dt.time(hour=h, minute=k, tzinfo=tz),
        name=f"{JOB_PREFIX_SM}{sid}",
    )
    await update.effective_message.reply_text(f"✅ Mensagem #{sid} criada para {hhmm} ({audience.upper()}).")

async def list_msgs_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    with session_scope() as s:
        msgs = s.query(ScheduledMessage).filter(ScheduledMessage.audience == audience).order_by(ScheduledMessage.hhmm.asc(), ScheduledMessage.id.asc()).all()
    if not msgs:
        await update.effective_message.reply_text("Não há mensagens agendadas.")
        return
    lines = ["🕒 <b>Mensagens agendadas</b>"]
    for m in msgs:
        status = "ON" if m.enabled else "OFF"
        preview = (m.text[:80] + "…") if len(m.text) > 80 else m.text
        lines.append(f"#{m.id} — {m.hhmm} ({m.tz}) [{status}] — {esc(preview)}")
    for ch in chunk_text(lines):
        await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

async def edit_msg_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text(f"Uso: /edit_msg_{audience} <id> [HH:MM] [novo texto]")
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
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid, ScheduledMessage.audience == audience).first()
        if not m:
            await update.effective_message.reply_text("Mensagem não encontrada.")
            return
        if hhmm:
            m.hhmm = hhmm
        if new_text is not None:
            m.text = new_text
        # re-agendar
    # remove e recria job
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}":
            j.schedule_removal()
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid).first()
    if m:
        tz = _tz(m.tz)
        h, k = parse_hhmm(m.hhmm)
        context.job_queue.run_daily(
            _scheduled_message_job,
            time=dt.time(hour=h, minute=k, tzinfo=tz),
            name=f"{JOB_PREFIX_SM}{m.id}",
        )
    await update.effective_message.reply_text("✅ Mensagem atualizada.")

async def toggle_msg_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text(f"Uso: /toggle_msg_{audience} <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid, ScheduledMessage.audience == audience).first()
        if not m:
            await update.effective_message.reply_text("Mensagem não encontrada.")
            return
        m.enabled = not m.enabled
        new_state = m.enabled
    await update.effective_message.reply_text(f"✅ Mensagem #{sid} agora está {'ON' if new_state else 'OFF'}.")

async def del_msg_generic(update: Update, context: ContextTypes.DEFAULT_TYPE, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text(f"Uso: /del_msg_{audience} <id>")
        return
    try:
        sid = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido.")
        return
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter(ScheduledMessage.id == sid, ScheduledMessage.audience == audience).first()
        if not m:
            await update.effective_message.reply_text("Mensagem não encontrada.")
            return
        s.delete(m)
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}":
            j.schedule_removal()
    await update.effective_message.reply_text("✅ Mensagem removida.")

# Wraps
async def add_msg_vip_cmd(u, c):  await add_msg_generic(u, c, "vip")
async def add_msg_free_cmd(u, c): await add_msg_generic(u, c, "free")
async def list_msgs_vip_cmd(u, c):  await list_msgs_generic(u, c, "vip")
async def list_msgs_free_cmd(u, c): await list_msgs_generic(u, c, "free")
async def edit_msg_vip_cmd(u, c):  await edit_msg_generic(u, c, "vip")
async def edit_msg_free_cmd(u, c): await edit_msg_generic(u, c, "free")
async def toggle_msg_vip_cmd(u, c):  await toggle_msg_generic(u, c, "vip")
async def toggle_msg_free_cmd(u, c): await toggle_msg_generic(u, c, "free")
async def del_msg_vip_cmd(u, c):  await del_msg_generic(u, c, "vip")
async def del_msg_free_cmd(u, c): await del_msg_generic(u, c, "free")

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
    chain = (data.get("chain") or cfg_get("default_chain", DEFAULT_CHAIN)).lower()
    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)
    with session_scope() as s:
        pay = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
        if not pay:
            pay = Payment(user_id=int(uid), tx_hash=tx_hash, amount=amount, chain=chain, status="approved", decided_at=now_utc())
            s.add(pay)
        else:
            pay.status = "approved"
            pay.decided_at = now_utc()
    link = await _send_invite_one_click()
    if link:
        try:
            await application.bot.send_message(chat_id=int(uid), text=f"✅ Pagamento confirmado! Entre no VIP: {link.invite_link}")
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
    return {"status": "online", "message": "Bot ready (crypto + schedules + packs)"}

# =========================
# Startup: register handlers & jobs
# =========================
tz_sp = pytz.timezone("America/Sao_Paulo")

async def _reschedule_daily_pack():
    for j in list(application.job_queue.jobs()):
        if j.name == "daily_pack":
            j.schedule_removal()
    hhmm = cfg_get("daily_pack_hhmm") or "18:49"
    h, m = parse_hhmm(hhmm)
    application.job_queue.run_daily(enviar_pack_vip_job, time=dt.time(hour=h, minute=m, tzinfo=tz_sp), name="daily_pack")
    logging.info(f"Job diário de pack agendado para {hhmm} America/Sao_Paulo")

@app.on_event("startup")
async def on_startup():
    global bot
    # Logging
    logging.basicConfig(level=logging.INFO)
    # PTB
    await application.initialize()
    await application.start()
    bot = application.bot

    # Webhook
    if BASE_URL:
        try:
            await bot.set_webhook(url=f"{BASE_URL}/webhook")
        except Exception as e:
            logging.warning(f"Falha set_webhook: {e}")
    logging.info("Bot iniciado (cripto + schedules + packs).")

    # ===== Error handler =====
    application.add_error_handler(error_handler)

    # ===== Conversa /novopack — privado e storage groups =====
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("novopack", novopack_start),
            CommandHandler("novopackvip", novopack_start),
            CommandHandler("novopackfree", novopack_start),
        ],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_title)],
            CHOOSE_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_choose_audience)],
            PREVIEWS: [
                CommandHandler("proximo", novopack_next_to_files),
                MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION, novopack_collect_previews),
                MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_collect_previews),
            ],
            FILES: [
                CommandHandler("finalizar", novopack_finish_review),
                MessageHandler(filters.Document.ALL | filters.AUDIO | filters.VOICE, novopack_collect_files),
                MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_collect_files),
            ],
            CONFIRM_SAVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_confirm_save)],
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_handler, group=0)

    # ===== Handlers do grupo de armazenamento =====
    application.add_handler(
        MessageHandler(
            filters.Chat(STORAGE_VIP_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler
        ),
        group=1,
    )
    application.add_handler(
        MessageHandler(
            filters.Chat(STORAGE_FREE_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler
        ),
        group=1,
    )
    media_filter_vip = (
        filters.Chat(STORAGE_VIP_GROUP_ID)
        & (
            filters.PHOTO
            | filters.VIDEO
            | filters.ANIMATION
            | filters.AUDIO
            | filters.Document.ALL
            | filters.VOICE
        )
    )
    media_filter_free = (
        filters.Chat(STORAGE_FREE_GROUP_ID)
        & (
            filters.PHOTO
            | filters.VIDEO
            | filters.ANIMATION
            | filters.AUDIO
            | filters.Document.ALL
            | filters.VOICE
        )
    )
    application.add_handler(MessageHandler(media_filter_vip, storage_media_handler), group=1)
    application.add_handler(MessageHandler(media_filter_free, storage_media_handler), group=1)

    # ===== Comandos gerais =====
    application.add_handler(CommandHandler("start", start_cmd), group=1)
    application.add_handler(CommandHandler("comandos", comandos_cmd), group=1)
    application.add_handler(CommandHandler("listar_comandos", listar_comandos_cmd), group=1)
    application.add_handler(CommandHandler("getid", getid_cmd), group=1)

    # Packs list/simular
    application.add_handler(CommandHandler("listar_packs", listar_packs_cmd), group=1)
    application.add_handler(CommandHandler("simularvip", simularvip_cmd), group=1)

    # Admin mgmt
    application.add_handler(CommandHandler("listar_admins", listar_admins_cmd), group=1)
    application.add_handler(CommandHandler("add_admin", add_admin_cmd), group=1)
    application.add_handler(CommandHandler("rem_admin", rem_admin_cmd), group=1)

    # Preço & teaser
    application.add_handler(CommandHandler("set_preco", set_preco_cmd), group=1)
    application.add_handler(CommandHandler("ver_preco", ver_preco_cmd), group=1)
    application.add_handler(CommandHandler("set_free_teaser", set_free_teaser_cmd), group=1)
    application.add_handler(CommandHandler("set_pack_horario", set_pack_horario_cmd), group=1)
    application.add_handler(CommandHandler("grupovip", grupovip_cmd), group=1)

    # Pagamentos cripto
    application.add_handler(CommandHandler("pagar", pagar_cmd), group=1)
    application.add_handler(CommandHandler("tx", tx_cmd), group=1)
    application.add_handler(CommandHandler("listar_pendentes", listar_pendentes_cmd), group=1)
    application.add_handler(CommandHandler("aprovar_tx", aprovar_tx_cmd), group=1)
    application.add_handler(CommandHandler("rejeitar_tx", rejeitar_tx_cmd), group=1)

    # ===== Jobs =====
    await _reschedule_daily_pack()

    # Keepalive
    application.job_queue.run_repeating(keepalive_job, interval=KEEPALIVE_INTERVAL_SEC, first=10, name="keepalive")

    # Recarregar mensagens agendadas
    _register_all_scheduled_messages(application.job_queue)

    logging.info("Handlers e jobs registrados.")

# =========================
# Run (local)
# =========================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
