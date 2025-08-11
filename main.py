# main.py
import os, logging, asyncio, datetime as dt, html
from contextlib import contextmanager
from typing import Optional, List, Dict, Any, Tuple

import pytz
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse
import uvicorn

from telegram import Update, InputMediaPhoto, ChatInviteLink
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, ContextTypes,
    JobQueue, ConversationHandler, filters
)

# === DB / ORM ===
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey,
    Text, BigInteger, UniqueConstraint, text, inspect
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from sqlalchemy.engine import make_url

# ---------------------------
# Helpers
# ---------------------------
def esc(s): return html.escape(str(s) if s is not None else "")
def now_utc(): return dt.datetime.utcnow()

def parse_hhmm(s: str) -> Tuple[int, int]:
    s = (s or "").strip()
    if ":" not in s: raise ValueError("Formato inválido; use HH:MM")
    h, m = map(int, s.split(":", 1))
    if not (0 <= h <= 23 and 0 <= m <= 59): raise ValueError("Hora fora 00:00–23:59")
    return h, m

def chunk_text(lines: List[str], max_len: int = 3800) -> List[str]:
    out, cur = [], ""
    for ln in lines:
        if len(cur) + len(ln) + 1 > max_len: out.append(cur); cur = ""
        cur += ("" if not cur else "\n") + ln
    if cur: out.append(cur)
    return out

# ---------------------------
# ENV
# ---------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN") or ""
BASE_URL  = os.getenv("BASE_URL", "").strip()
if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN não definido.")

GROUP_VIP_ID           = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))
GROUP_FREE_ID          = int(os.getenv("GROUP_FREE_ID", "-1002509364079"))
STORAGE_VIP_GROUP_ID   = int(os.getenv("STORAGE_VIP_GROUP_ID", "-4806334341"))
STORAGE_FREE_GROUP_ID  = int(os.getenv("STORAGE_FREE_GROUP_ID", "-4806334342"))

PORT = int(os.getenv("PORT", "10000"))

WALLET_ADDRESS = (os.getenv("WALLET_ADDRESS", "0x40dDBD27F878d07808339F9965f013F1CBc2F812")).strip()
DEFAULT_CHAIN  = (os.getenv("DEFAULT_CHAIN", "polygon")).strip().lower()

DEFAULT_PRICE_AMOUNT   = os.getenv("DEFAULT_PRICE_AMOUNT", "50")
DEFAULT_PRICE_CURRENCY = os.getenv("DEFAULT_PRICE_CURRENCY", "USDT")
DEFAULT_FREE_TEASER    = os.getenv("DEFAULT_FREE_TEASER", "Hoje liberamos no VIP: {title}\nPara entrar no VIP digite /grupovip")

KEEPALIVE_INTERVAL_SEC = int(os.getenv("KEEPALIVE_INTERVAL_SEC", "240"))

# ---------------------------
# FastAPI + PTB
# ---------------------------
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None

# ---------------------------
# DB setup
# ---------------------------
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./bot_data.db")
url = make_url(DB_URL)
engine = create_engine(
    DB_URL,
    **({"connect_args": {"check_same_thread": False}} if url.get_backend_name().startswith("sqlite") else
       {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 5})
)
# expire_on_commit=False evita DetachedInstanceError em leituras após commit
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
Base = declarative_base()

@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s; s.commit()
    except Exception:
        s.rollback(); raise
    finally:
        s.close()

# ---- Tabelas
class ConfigKV(Base):
    __tablename__ = "config_kv"
    key = Column(String, primary_key=True)
    value = Column(String)
    updated_at = Column(DateTime, default=now_utc, onupdate=now_utc)

def cfg_get(key: str, default: Optional[str] = None) -> Optional[str]:
    with session_scope() as s:
        row = s.query(ConfigKV).filter_by(key=key).first()
        return row.value if row else default

def cfg_set(key: str, value: Optional[str]):
    with session_scope() as s:
        row = s.query(ConfigKV).filter_by(key=key).first()
        (setattr(row, "value", value) if row else s.add(ConfigKV(key=key, value=value)))

class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, unique=True, index=True)
    added_at = Column(DateTime, default=now_utc)

class Pack(Base):
    __tablename__ = "packs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    audience = Column(String, default="vip")  # vip | free
    header_message_id = Column(Integer, unique=True)
    created_at = Column(DateTime, default=now_utc)
    sent = Column(Boolean, default=False)
    files = relationship("PackFile", back_populates="pack", cascade="all, delete-orphan")

class PackFile(Base):
    __tablename__ = "pack_files"
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(Integer, ForeignKey("packs.id", ondelete="CASCADE"))
    file_id = Column(String, nullable=False)
    file_unique_id = Column(String)
    file_type = Column(String)    # photo, video, animation, document, audio, voice
    role = Column(String)         # preview | file
    file_name = Column(String)
    added_at = Column(DateTime, default=now_utc)
    pack = relationship("Pack", back_populates="files")

class Payment(Base):
    __tablename__ = "payments"
    id = Column(Integer, primary_key=True)
    user_id = Column(BigInteger, index=True)
    username = Column(String)
    tx_hash = Column(String, unique=True, index=True)
    chain = Column(String, default=DEFAULT_CHAIN)
    amount = Column(String)
    status = Column(String, default="pending")  # pending | approved | rejected
    notes = Column(Text)
    created_at = Column(DateTime, default=now_utc)
    decided_at = Column(DateTime)

class ScheduledMessage(Base):
    __tablename__ = "scheduled_messages"
    id = Column(Integer, primary_key=True)
    audience = Column(String, default="vip")  # vip | free
    hhmm = Column(String, nullable=False)     # "HH:MM"
    tz = Column(String, default="America/Sao_Paulo")
    text = Column(Text, nullable=False)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=now_utc)
    __table_args__ = (UniqueConstraint('id', name='uq_scheduled_messages_id'),)

def ensure_bigint_columns():
    if not url.get_backend_name().startswith("postgresql"): return
    try:
        with engine.begin() as conn:
            for t, col in (("admins","user_id"), ("payments","user_id")):
                try: conn.execute(text(f'ALTER TABLE {t} ALTER COLUMN {col} TYPE BIGINT USING {col}::bigint'))
                except Exception: pass
    except Exception as e:
        logging.warning("Falha ensure_bigint_columns: %s", e)

def ensure_schema_migrations():
    """Migrações mínimas idempotentes (sem Alembic)."""
    insp = inspect(engine)
    # scheduled_messages.audience
    try:
        cols = {c["name"] for c in insp.get_columns("scheduled_messages")}
        if "audience" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE scheduled_messages ADD COLUMN audience VARCHAR DEFAULT 'vip'"))
        with engine.begin() as conn:
            conn.execute(text("UPDATE scheduled_messages SET audience = 'vip' WHERE audience IS NULL"))
    except Exception as e:
        logging.warning("ensure_schema_migrations scheduled_messages: %s", e)
    # packs.audience
    try:
        cols = {c["name"] for c in insp.get_columns("packs")}
        if "audience" not in cols:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE packs ADD COLUMN audience VARCHAR DEFAULT 'vip'"))
        with engine.begin() as conn:
            conn.execute(text("UPDATE packs SET audience = 'vip' WHERE audience IS NULL"))
    except Exception as e:
        logging.warning("ensure_schema_migrations packs: %s", e)

def init_db():
    Base.metadata.create_all(bind=engine)
    initial_admin_id = os.getenv("INITIAL_ADMIN_ID")
    if initial_admin_id:
        with session_scope() as s:
            uid = int(initial_admin_id)
            if not s.query(Admin).filter_by(user_id=uid).first(): s.add(Admin(user_id=uid))
    if not cfg_get("daily_pack_hhmm"): cfg_set("daily_pack_hhmm", "18:49")
    if not cfg_get("price_amount"):    cfg_set("price_amount", DEFAULT_PRICE_AMOUNT)
    if not cfg_get("price_currency"):  cfg_set("price_currency", DEFAULT_PRICE_CURRENCY)
    if not cfg_get("free_teaser"):     cfg_set("free_teaser", DEFAULT_FREE_TEASER)
    if not cfg_get("default_chain"):   cfg_set("default_chain", DEFAULT_CHAIN)

ensure_bigint_columns()
init_db()
ensure_schema_migrations()  # <- corrige sua exceção e dados legados

# ---------------------------
# DB helpers
# ---------------------------
def is_admin(user_id: int) -> bool:
    with session_scope() as s:
        return s.query(Admin).filter_by(user_id=user_id).first() is not None

def list_admin_ids() -> List[int]:
    with session_scope() as s:
        return [a.user_id for a in s.query(Admin).order_by(Admin.added_at.asc()).all()]

def add_admin_db(user_id: int) -> bool:
    with session_scope() as s:
        if s.query(Admin).filter_by(user_id=user_id).first(): return False
        s.add(Admin(user_id=user_id)); return True

def remove_admin_db(user_id: int) -> bool:
    with session_scope() as s:
        a = s.query(Admin).filter_by(user_id=user_id).first()
        if not a: return False
        s.delete(a); return True

def create_pack(title: str, audience: str, header_message_id: Optional[int] = None) -> Pack:
    with session_scope() as s:
        p = Pack(title=title.strip(), audience=audience, header_message_id=header_message_id)
        s.add(p); s.flush(); s.refresh(p); return p

def get_pack_by_header(message_id: int) -> Optional[Pack]:
    with session_scope() as s:
        return s.query(Pack).filter_by(header_message_id=message_id).first()

def add_file_to_pack(pack_id: int, file_id: str, file_unique_id: Optional[str], file_type: str, role: str, file_name: Optional[str] = None):
    with session_scope() as s:
        pf = PackFile(
            pack_id=pack_id, file_id=file_id, file_unique_id=file_unique_id,
            file_type=file_type, role=role, file_name=file_name
        )
        s.add(pf); s.flush(); s.refresh(pf); return pf

def get_next_unsent_pack() -> Optional[Pack]:
    with session_scope() as s:
        return s.query(Pack).filter(Pack.sent == False, (Pack.audience == "vip") | (Pack.audience.is_(None))).order_by(Pack.created_at.asc()).first()

def mark_pack_sent(pack_id: int):
    with session_scope() as s:
        p = s.query(Pack).filter_by(id=pack_id).first()
        if p: p.sent = True

def packs_detail_for_list() -> List[str]:
    with session_scope() as s:
        items = s.query(Pack).order_by(Pack.created_at.desc()).all()
        lines = []
        for p in items:
            previews = s.query(PackFile).filter_by(pack_id=p.id, role="preview").count()
            docs    = s.query(PackFile).filter_by(pack_id=p.id, role="file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            aud = ((p.audience or "vip").upper())
            lines.append(f"[{p.id}] {esc(p.title)} — {aud} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        return lines

def pending_payments_lines() -> List[str]:
    with session_scope() as s:
        pend = s.query(Payment).filter_by(status="pending").order_by(Payment.created_at.asc()).all()
        return [f"- user_id:{p.user_id} @{p.username or '-'} | {p.tx_hash} | {p.chain} | {p.created_at.strftime('%d/%m %H:%M')}" for p in pend]

# ---------------------------
# Conversa /novopack
# ---------------------------
TITLE, CHOOSE_AUDIENCE, PREVIEWS, FILES, CONFIRM_SAVE = range(5)

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    t = user_data.get("title", "—")
    audience = user_data.get("audience", "vip")
    previews = user_data.get("previews", [])
    files = user_data.get("files", [])
    def _names(items):
        out, idx = [], 1
        for it in items:
            nm = it.get("file_name")
            if nm: out.append(esc(nm))
            else:
                kind = {"photo":"Foto","video":"Vídeo","animation":"Animação","document":"Documento","audio":"Áudio","voice":"Voice"}.get(it["file_type"], "Arquivo")
                out.append(f"{kind} {idx}"); idx += 1
        return out or ["—"]
    lines = [
        "📦 <b>Resumo do Pack</b>",
        f"• Nome: <b>{esc(t)}</b>",
        f"• Público: <b>{audience.upper()}</b>",
        f"• Previews ({len(previews)}): " + ", ".join(_names(previews)),
        f"• Arquivos ({len(files)}): " + ", ".join(_names(files)),
        "", "Deseja salvar? (<b>sim</b>/<b>não</b>)"
    ]
    return "\n".join(lines)

async def novopack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("🧩 Vamos criar um novo pack!\n\n1) Envie o <b>título do pack</b>.", parse_mode=ParseMode.HTML)
    return TITLE

async def novopack_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.effective_message.text or "").strip()
    if not title: return await update.effective_message.reply_text("Título vazio. Envie um texto com o título do pack.") or TITLE
    context.user_data["title"] = title
    await update.effective_message.reply_text("2) Este pack é para <b>VIP</b> ou <b>FREE</b>? (responda: vip | free)", parse_mode=ParseMode.HTML)
    return CHOOSE_AUDIENCE

async def novopack_choose_audience(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = (update.effective_message.text or "").strip().lower()
    if ans not in ("vip", "free"): return await update.effective_message.reply_text("Responda apenas com: vip ou free.") or CHOOSE_AUDIENCE
    context.user_data.update({"audience": ans, "previews": [], "files": []})
    await update.effective_message.reply_text("3) Envie as <b>PREVIEWS</b> (foto/vídeo/animação). Quando terminar: /proximo", parse_mode=ParseMode.HTML)
    return PREVIEWS

async def novopack_collect_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, previews = update.effective_message, context.user_data.get("previews", [])
    if   msg.photo:     previews.append({"file_id": msg.photo[-1].file_id, "file_type": "photo",     "file_name": (msg.caption or "").strip() or None})
    elif msg.video:     previews.append({"file_id": msg.video.file_id,       "file_type": "video",     "file_name": (msg.caption or "").strip() or None})
    elif msg.animation: previews.append({"file_id": msg.animation.file_id,    "file_type": "animation", "file_name": (msg.caption or "").strip() or None})
    else:               return await msg.reply_text("Envie foto/vídeo/animação ou /proximo.") or PREVIEWS
    context.user_data["previews"] = previews
    return await msg.reply_text("✅ Preview adicionada. Envie mais ou /proximo.") or PREVIEWS

async def novopack_next_to_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("4) Agora envie os <b>ARQUIVOS</b> (documento/áudio/voice). Quando terminar: /finalizar", parse_mode=ParseMode.HTML)
    return FILES

async def novopack_collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, files = update.effective_message, context.user_data.get("files", [])
    if   msg.document: files.append({"file_id": msg.document.file_id, "file_type": "document", "file_name": getattr(msg.document,"file_name",None) or (msg.caption or "").strip() or None})
    elif msg.audio:    files.append({"file_id": msg.audio.file_id,    "file_type": "audio",    "file_name": getattr(msg.audio,"file_name",None) or (msg.caption or "").strip() or None})
    elif msg.voice:    files.append({"file_id": msg.voice.file_id,    "file_type": "voice",    "file_name": (msg.caption or "").strip() or None})
    else:              return await msg.reply_text("Envie documento/áudio/voice ou /finalizar.") or FILES
    context.user_data["files"] = files
    return await msg.reply_text("✅ Arquivo adicionado. Envie mais ou /finalizar.") or FILES

async def novopack_finish_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(_summary_from_session(context.user_data), parse_mode=ParseMode.HTML)
    return CONFIRM_SAVE

async def novopack_confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    a = (update.effective_message.text or "").strip().lower()
    if a not in ("sim","não","nao"):
        return await update.effective_message.reply_text("Responda <b>sim</b> para salvar ou <b>não</b> para cancelar.", parse_mode=ParseMode.HTML) or CONFIRM_SAVE
    if a in ("não","nao"):
        context.user_data.clear(); await update.effective_message.reply_text("Operação cancelada."); return ConversationHandler.END
    title, audience = context.user_data.get("title"), context.user_data.get("audience","vip")
    p = create_pack(title=title, audience=audience, header_message_id=None)
    for it in context.user_data.get("previews", []):
        add_file_to_pack(p.id, it["file_id"], None, it["file_type"], "preview", it.get("file_name"))
    for it in context.user_data.get("files", []):
        add_file_to_pack(p.id, it["file_id"], None, it["file_type"], "file", it.get("file_name"))
    context.user_data.clear()
    await update.effective_message.reply_text(f"🎉 {title} cadastrado com sucesso ({audience.upper()}).")
    return ConversationHandler.END

async def novopack_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear(); await update.effective_message.reply_text("Operação cancelada."); return ConversationHandler.END

# ---------------------------
# STORAGE (VIP / FREE)
# ---------------------------
async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat_id = update.effective_message, update.effective_message.chat.id
    if chat_id not in (STORAGE_VIP_GROUP_ID, STORAGE_FREE_GROUP_ID): return
    title = (msg.text or "").strip()
    if not title or msg.reply_to_message: return
    lw = title.lower()
    if lw in {"sim","não","nao","/proximo","/finalizar","/cancelar"} or title.startswith("/") or len(title)<4: return
    if len(title.split()) < 2: return
    if get_pack_by_header(msg.message_id): return await msg.reply_text("Pack já registrado para este cabeçalho.")
    audience = "vip" if chat_id == STORAGE_VIP_GROUP_ID else "free"
    p = create_pack(title=title, audience=audience, header_message_id=msg.message_id)
    await msg.reply_text(f"Pack registrado: <b>{esc(p.title)}</b> (id {p.id}, {audience.upper()})", parse_mode=ParseMode.HTML)

async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, chat_id, reply = update.effective_message, update.effective_message.chat.id, update.effective_message.reply_to_message
    if chat_id not in (STORAGE_VIP_GROUP_ID, STORAGE_FREE_GROUP_ID): return
    if not reply: return await msg.reply_text("Envie este arquivo como <b>resposta</b> ao título do pack.", parse_mode=ParseMode.HTML)
    pack = get_pack_by_header(reply.message_id)
    if not pack: return await msg.reply_text("Cabeçalho do pack não encontrado. Responda à mensagem de título.")
    file_id = file_unique_id = visible_name = None; role = "file"; file_type = None
    if   msg.photo:     file_id, file_unique_id, file_type, role, visible_name = msg.photo[-1].file_id, getattr(msg.photo[-1],"file_unique_id",None), "photo","preview",(msg.caption or "").strip() or None
    elif msg.video:     file_id, file_unique_id, file_type, role, visible_name = msg.video.file_id,    getattr(msg.video,"file_unique_id",None), "video","preview",(msg.caption or "").strip() or None
    elif msg.animation: file_id, file_unique_id, file_type, role, visible_name = msg.animation.file_id,getattr(msg.animation,"file_unique_id",None),"animation","preview",(msg.caption or "").strip() or None
    elif msg.document:  file_id, file_unique_id, file_type, visible_name      = msg.document.file_id,  getattr(msg.document,"file_unique_id",None),"document", getattr(msg.document,"file_name",None)
    elif msg.audio:     file_id, file_unique_id, file_type, visible_name      = msg.audio.file_id,     getattr(msg.audio,"file_unique_id",None),   "audio",    getattr(msg.audio,"file_name",None) or (msg.caption or "").strip() or None
    elif msg.voice:     file_id, file_unique_id, file_type, visible_name      = msg.voice.file_id,     getattr(msg.voice,"file_unique_id",None),   "voice",    (msg.caption or "").strip() or None
    else:               return await msg.reply_text("Tipo de mídia não suportado.", parse_mode=ParseMode.HTML)
    add_file_to_pack(pack.id, file_id, file_unique_id, file_type, role, visible_name)
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b>.", parse_mode=ParseMode.HTML)

# ---------------------------
# Envio de pack (Job)
# ---------------------------
async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        pack = get_next_unsent_pack()
        if not pack: return "Nenhum pack VIP pendente para envio."
        with session_scope() as s:
            p = s.query(Pack).filter_by(id=pack.id).first()
            files = s.query(PackFile).filter_by(pack_id=p.id).order_by(PackFile.id.asc()).all()
        if not files: mark_pack_sent(p.id); return f"Pack '{p.title}' não possui arquivos. Marcado como enviado."

        previews = [f for f in files if f.role == "preview"]
        docs     = [f for f in files if f.role == "file"]

        photo_ids = [f.file_id for f in previews if f.file_type == "photo"]
        sent_first = False
        if photo_ids:
            media = [InputMediaPhoto(media=photo_ids[0], caption=pack.title)] + [InputMediaPhoto(media=fid) for fid in photo_ids[1:]]
            try:
                await context.application.bot.send_media_group(chat_id=GROUP_VIP_ID, media=media)
                sent_first = True
            except Exception as e:
                logging.warning(f"Falha send_media_group: {e}. Enviando individual.")
                for i, fid in enumerate(photo_ids):
                    await context.application.bot.send_photo(chat_id=GROUP_VIP_ID, photo=fid, caption=(pack.title if i==0 else None))
                    sent_first = True

        for f in [f for f in previews if f.file_type in ("video","animation")]:
            try:
                if f.file_type == "video":
                    await context.application.bot.send_video(chat_id=GROUP_VIP_ID, video=f.file_id, caption=(pack.title if not sent_first else None))
                else:
                    await context.application.bot.send_animation(chat_id=GROUP_VIP_ID, animation=f.file_id, caption=(pack.title if not sent_first else None))
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando preview {f.id}: {e}")

        for f in docs:
            try:
                cap = pack.title if not sent_first else None
                if   f.file_type == "document": await context.application.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                elif f.file_type == "audio":    await context.application.bot.send_audio(   chat_id=GROUP_VIP_ID, audio=f.file_id, caption=cap)
                elif f.file_type == "voice":    await context.application.bot.send_voice(   chat_id=GROUP_VIP_ID, voice=f.file_id, caption=cap)
                else:                            await context.application.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando arquivo {f.file_name or f.id}: {e}")

        teaser_tpl = cfg_get("free_teaser", DEFAULT_FREE_TEASER)
        teaser = (teaser_tpl or "").format(title=pack.title).strip()
        if teaser:
            try: await context.application.bot.send_message(chat_id=GROUP_FREE_ID, text=teaser)
            except Exception as e: logging.warning(f"Erro teaser FREE: {e}")
        if photo_ids:
            media_free = [InputMediaPhoto(media=photo_ids[0], caption=pack.title)] + [InputMediaPhoto(media=fid) for fid in photo_ids[1:]]
            try:
                await context.application.bot.send_media_group(chat_id=GROUP_FREE_ID, media=media_free)
            except Exception as e:
                logging.warning(f"Falha media_group FREE: {e}. Enviando individual.")
                for i, fid in enumerate(photo_ids):
                    try: await context.application.bot.send_photo(chat_id=GROUP_FREE_ID, photo=fid, caption=(pack.title if i==0 else None))
                    except Exception as e2: logging.warning(f"Erro foto FREE: {e2}")

        mark_pack_sent(p.id)
        return f"✅ Enviado pack VIP '{p.title}' (e previews no FREE)."
    except Exception as e:
        logging.exception("Erro no enviar_pack_vip_job")
        return f"❌ Erro no envio: {e!r}"

# ---------------------------
# KEEPALIVE
# ---------------------------
async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
    try:
        import httpx
        url = f"{BASE_URL}/ping" if BASE_URL else "http://127.0.0.1"
        async with httpx.AsyncClient(timeout=10) as cli: await cli.get(url)
        logging.info("[keepalive] tick")
    except Exception as e:
        logging.warning(f"[keepalive] erro: {e}")

# ---------------------------
# Comandos básicos & Admin
# ---------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Fala! Eu gerencio packs VIP/FREE, pagamentos via MetaMask e mensagens agendadas.\nUse /comandos para ver tudo.")

async def comandos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = f"{cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}"
    chains = ", ".join(sorted(NETWORK_ALIASES.keys()))
    lines = [
        "📋 <b>Comandos</b>",
        "• /start | /comandos | /listar_comandos | /getid",
        "",
        "💸 Pagamento (MetaMask):",
        "• /pagar — instruções e redes",
        "• /tx HASH  ou  /tx REDE HASH",
        f"Preço VIP: <b>{esc(price)}</b>",
        "",
        "🧩 Packs:",
        "• /novopack (fluxo guiado) | /novopackvip | /novopackfree | /cancelar",
        "• /listar_packs | /simularvip",
        "",
        "🕒 Mensagens agendadas:",
        "• /add_msg_vip HH:MM &lt;texto&gt; | /add_msg_free HH:MM &lt;texto&gt;",
        "• /list_msgs_vip | /list_msgs_free",
        "• /edit_msg_vip &lt;id&gt; [HH:MM] [novo texto] | /edit_msg_free ...",
        "• /toggle_msg_vip &lt;id&gt; | /toggle_msg_free &lt;id&gt;",
        "• /del_msg_vip &lt;id&gt; | /del_msg_free &lt;id&gt;",
        "",
        "🛠 Admin & Config:",
        "• /set_pack_horario HH:MM | /set_preco VALOR MOEDA | /ver_preco | /set_free_teaser &lt;texto com {title}&gt;",
        "• /add_admin &lt;id&gt; | /rem_admin &lt;id&gt; | /listar_admins",
        "• /listar_pendentes | /aprovar_tx &lt;user_id&gt; | /rejeitar_tx &lt;user_id&gt; [motivo]",
        "• /grupovip",
        f"\nRedes aceitas: {esc(chains)}"
    ]
    for ch in chunk_text(lines): await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

listar_comandos_cmd = comandos_cmd

async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u, c = update.effective_user, update.effective_chat
    await update.effective_message.reply_text(f"Seu nome: {esc(u.full_name)}\nSeu ID: {u.id}\nID deste chat: {c.id}", parse_mode=ParseMode.HTML)

def _is_admin_or_storage(update: Update) -> bool:
    return (update.effective_user and is_admin(update.effective_user.id)) or (update.effective_chat and update.effective_chat.id in (STORAGE_VIP_GROUP_ID, STORAGE_FREE_GROUP_ID))

async def listar_admins_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    ids = list_admin_ids()
    await update.effective_message.reply_text("👑 Admins:\n" + ("\n".join(f"- {i}" for i in ids) if ids else "Sem admins."))

async def add_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /add_admin <user_id>")
    try: ok = add_admin_db(int(context.args[0]))
    except: return await update.effective_message.reply_text("user_id inválido.")
    await update.effective_message.reply_text("✅ Admin adicionado." if ok else "Já era admin.")

async def rem_admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /rem_admin <user_id>")
    try: ok = remove_admin_db(int(context.args[0]))
    except: return await update.effective_message.reply_text("user_id inválido.")
    await update.effective_message.reply_text("✅ Admin removido." if ok else "Este user não é admin.")

async def listar_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin_or_storage(update): return await update.effective_message.reply_text("Apenas admins (ou use nos grupos de storage).")
    lines = packs_detail_for_list()
    for ch in chunk_text(lines or ["Nenhum pack registrado."]): await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    await update.effective_message.reply_text(await enviar_pack_vip_job(context))

# ---------------------------
# Pagamento MetaMask
# ---------------------------
NETWORK_ALIASES = {
    "polygon": ["polygon","matic","pol"],
    "bsc": ["bsc","bnb","binance"],
    "ethereum": ["eth","ethereum","mainnet"],
    "arbitrum": ["arbitrum","arb"],
    "base": ["base"],
    "avalanche": ["avax","avalanche"],
}

def _detect_network_from_hash(_: str) -> Optional[str]:
    return cfg_get("default_chain", DEFAULT_CHAIN)

async def pagar_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, chat = update.effective_user, update.effective_chat
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
    if chat and chat.id == GROUP_FREE_ID:
        try:
            sent = await update.effective_message.reply_text("✅ Te enviei as instruções no privado. (vou apagar aqui em 5s)")
            await asyncio.sleep(5)
            try: await update.effective_message.delete()
            except: pass
            try: await sent.delete()
            except: pass
        except: pass
        try: await application.bot.send_message(chat_id=user.id, text=msg, parse_mode=ParseMode.HTML)
        except Exception as e: logging.warning(f"Falha ao DM /pagar para {user.id}: {e}")
        return
    await update.effective_message.reply_text(msg, parse_mode=ParseMode.HTML)

async def grupovip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    price = f"{cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}"
    msg = (
        f"Olá, {esc(user.first_name)}! 👋\n\n"
        f"Para entrar no VIP, o valor é <b>{esc(price)}</b> em cripto ({esc(cfg_get('default_chain', DEFAULT_CHAIN)).upper()}):\n"
        f"<code>{esc(WALLET_ADDRESS)}</code>\n\n"
        f"Depois envie /pagar para ver as instruções ou mande diretamente /tx ..."
    )
    try: await application.bot.send_message(chat_id=user.id, text=msg, parse_mode=ParseMode.HTML)
    except Exception as e:
        logging.warning(f"Falha ao DM /grupovip: {e}")
        await update.effective_message.reply_text("Te enviei no privado; se não chegou, me chame no PV.")

async def tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg, user, args = update.effective_message, update.effective_user, context.args
    if not args: return await msg.reply_text("Uso:\n/tx HASH\nou\n/tx REDE HASH (ex.: /tx polygon 0xabc...)")
    if len(args) == 1:
        tx_hash, chain = args[0].strip(), _detect_network_from_hash(args[0].strip())
    else:
        cand, tx_hash = args[0].lower(), args[1].strip()
        chain = next((key for key, aliases in NETWORK_ALIASES.items() if cand in aliases or cand == key), None)
        if not chain: return await msg.reply_text("Rede inválida. Ex.: polygon, bsc, ethereum, arbitrum, base, avalanche")
    if not tx_hash.startswith("0x") or len(tx_hash) < 10: return await msg.reply_text("Hash inválido.")
    with session_scope() as s:
        if s.query(Payment).filter_by(tx_hash=tx_hash).first(): return await msg.reply_text("Esse hash já foi registrado. Aguarde aprovação.")
        s.add(Payment(user_id=user.id, username=user.username, tx_hash=tx_hash, chain=chain, status="pending"))
    await msg.reply_text("✅ Recebi seu hash! Assim que for aprovado, te envio o convite do VIP.")

async def listar_pendentes_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin_or_storage(update): return await update.effective_message.reply_text("Apenas admins (ou use nos grupos de storage).")
    lines = pending_payments_lines()
    for ch in chunk_text((["⏳ <b>Pendentes</b>"] + lines) if lines else ["Sem pagamentos pendentes."]):
        await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

async def _notify_admins(text: str):
    for uid in list_admin_ids():
        try: await application.bot.send_message(chat_id=uid, text=text)
        except: pass

async def _send_invite_one_click() -> Optional[ChatInviteLink]:
    try:
        expire_date = int((dt.datetime.utcnow() + dt.timedelta(hours=1)).timestamp())
        return await application.bot.create_chat_invite_link(
            chat_id=GROUP_VIP_ID, expire_date=expire_date, member_limit=1, creates_join_request=False, name="1-click VIP"
        )
    except Exception:
        logging.exception("Erro criando invite 1-click"); return None

async def aprovar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /aprovar_tx <user_id>")
    try: uid = int(context.args[0])
    except: return await update.effective_message.reply_text("user_id inválido.")
    with session_scope() as s:
        p = s.query(Payment).filter(Payment.user_id==uid, Payment.status=="pending").order_by(Payment.created_at.asc()).first()
        if not p: return await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
        p.status, p.decided_at = "approved", now_utc()
    link = await _send_invite_one_click()
    if link:
        try: await application.bot.send_message(chat_id=uid, text=f"✅ Pagamento aprovado! Entre no VIP: {link.invite_link}")
        except: logging.exception("Erro enviando invite ao usuário")
    try: count = await application.bot.get_chat_member_count(chat_id=GROUP_VIP_ID)
    except: count = None
    await _notify_admins(f"Novo VIP aprovado: {uid}.\nMembros no VIP agora: {count if count is not None else 'n/d'}.")
    await update.effective_message.reply_text(f"Aprovado. Convite enviado {'(1 clique)' if link else ''}.")

async def rejeitar_tx_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /rejeitar_tx <user_id> [motivo]")
    try: uid = int(context.args[0])
    except: return await update.effective_message.reply_text("user_id inválido.")
    motivo = " ".join(context.args[1:]).strip() if len(context.args) > 1 else "Não especificado"
    with session_scope() as s:
        p = s.query(Payment).filter(Payment.user_id==uid, Payment.status=="pending").order_by(Payment.created_at.asc()).first()
        if not p: return await update.effective_message.reply_text("Nenhum pagamento pendente para este usuário.")
        p.status, p.notes, p.decided_at = "rejected", motivo, now_utc()
    try: await application.bot.send_message(chat_id=uid, text=f"❌ Pagamento rejeitado. Motivo: {motivo}")
    except: pass
    await update.effective_message.reply_text("Pagamento rejeitado.")

# ----- Preço & teaser & horário
async def set_preco_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if len(context.args) < 2: return await update.effective_message.reply_text("Uso: /set_preco VALOR MOEDA (ex.: /set_preco 50 USDT)")
    cfg_set("price_amount", context.args[0]); cfg_set("price_currency", context.args[1].upper())
    await update.effective_message.reply_text(f"✅ Preço atualizado para {context.args[0]} {context.args[1].upper()}.")

async def ver_preco_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(f"Preço VIP atual: {cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}")

async def set_free_teaser_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    txt = " ".join(context.args).strip()
    if not txt: return await update.effective_message.reply_text("Uso: /set_free_teaser <texto com {title}>")
    cfg_set("free_teaser", txt); await update.effective_message.reply_text("✅ Teaser do FREE atualizado.")

async def set_pack_horario_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text("Uso: /set_pack_horario HH:MM")
    try: hhmm = context.args[0]; parse_hhmm(hhmm); cfg_set("daily_pack_hhmm", hhmm); await _reschedule_daily_pack(); await update.effective_message.reply_text(f"✅ Horário diário dos packs definido para {hhmm}.")
    except Exception as e: await update.effective_message.reply_text(f"Hora inválida: {e}")

# ---------------------------
# Mensagens agendadas
# ---------------------------
JOB_PREFIX_SM = "schmsg_"
def _tz(tz_name: str):
    try: return pytz.timezone(tz_name)
    except: return pytz.timezone("America/Sao_Paulo")

async def _scheduled_message_job(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    sid = int(job.name.replace(JOB_PREFIX_SM, "")) if job and job.name else None
    if sid is None: return
    with session_scope() as s: m = s.query(ScheduledMessage).filter_by(id=sid).first()
    if not m or not m.enabled: return
    chat_id = GROUP_VIP_ID if (m.audience or "vip") == "vip" else GROUP_FREE_ID
    try: await context.application.bot.send_message(chat_id=chat_id, text=m.text)
    except Exception as e: logging.warning(f"Falha scheduled_message id={sid}: {e}")

def _register_all_scheduled_messages(job_queue: JobQueue):
    with session_scope() as s: msgs = s.query(ScheduledMessage).order_by(ScheduledMessage.hhmm.asc(), ScheduledMessage.id.asc()).all()
    for m in msgs:
        try: h, k = parse_hhmm(m.hhmm)
        except: continue
        job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=_tz(m.tz)), name=f"{JOB_PREFIX_SM}{m.id}")

async def add_msg_generic(update, context, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if len(context.args) < 2: return await update.effective_message.reply_text(f"Uso: /add_msg_{audience} HH:MM <texto>")
    hhmm, texto = context.args[0], " ".join(context.args[1:]).strip()
    try: parse_hhmm(hhmm)
    except Exception as e: return await update.effective_message.reply_text(f"Hora inválida: {e}")
    if not texto: return await update.effective_message.reply_text("Texto vazio.")
    with session_scope() as s:
        m = ScheduledMessage(audience=audience, hhmm=hhmm, text=texto, tz="America/Sao_Paulo", enabled=True)
        s.add(m); s.flush(); sid = m.id
    h, k = parse_hhmm(hhmm)
    context.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=_tz("America/Sao_Paulo")), name=f"{JOB_PREFIX_SM}{sid}")
    await update.effective_message.reply_text(f"✅ Mensagem #{sid} criada para {hhmm} ({audience.upper()}).")

async def list_msgs_generic(update, context, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    with session_scope() as s:
        msgs = s.query(ScheduledMessage).filter_by(audience=audience).order_by(ScheduledMessage.hhmm.asc(), ScheduledMessage.id.asc()).all()
        if not msgs: return await update.effective_message.reply_text("Não há mensagens agendadas.")
        lines = ["🕒 <b>Mensagens agendadas</b>"]
        for m in msgs:
            preview = (m.text[:80] + "…") if len(m.text) > 80 else m.text
            lines.append(f"#{m.id} — {m.hhmm} ({m.tz}) [{'ON' if m.enabled else 'OFF'}] — {esc(preview)}")
    for ch in chunk_text(lines): await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

async def edit_msg_generic(update, context, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)): return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text(f"Uso: /edit_msg_{audience} <id> [HH:MM] [novo texto]")
    try: sid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    hhmm = None; new_text = None
    if len(context.args) >= 2:
        cand = context.args[1]
        if ":" in cand and len(cand) <= 5:
            try: parse_hhmm(cand); hhmm = cand; new_text = " ".join(context.args[2:]).strip() if len(context.args)>2 else None
            except Exception as e: return await update.effective_message.reply_text(f"Hora inválida: {e}")
        else:
            new_text = " ".join(context.args[1:]).strip()
    if hhmm is None and new_text is None: return await update.effective_message.reply_text("Nada para alterar. Informe HH:MM e/ou novo texto.")
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter_by(id=sid, audience=audience).first()
        if not m: return await update.effective_message.reply_text("Mensagem não encontrada.")
        if hhmm: m.hhmm = hhmm
        if new_text is not None: m.text = new_text
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}": j.schedule_removal()
    with session_scope() as s: m = s.query(ScheduledMessage).filter_by(id=sid).first()
    if m:
        h,k = parse_hhmm(m.hhmm)
        context.job_queue.run_daily(_scheduled_message_job, time=dt.time(hour=h, minute=k, tzinfo=_tz(m.tz)), name=f"{JOB_PREFIX_SM}{m.id}")
    await update.effective_message.reply_text("✅ Mensagem atualizada.")

async def toggle_msg_generic(update, context, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)) : return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text(f"Uso: /toggle_msg_{audience} <id>")
    try: sid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter_by(id=sid, audience=audience).first()
        if not m: return await update.effective_message.reply_text("Mensagem não encontrada.")
        m.enabled = not m.enabled; new_state = m.enabled
    await update.effective_message.reply_text(f"✅ Mensagem #{sid} agora está {'ON' if new_state else 'OFF'}.")

async def del_msg_generic(update, context, audience: str):
    if not (update.effective_user and is_admin(update.effective_user.id)) : return await update.effective_message.reply_text("Apenas admins.")
    if not context.args: return await update.effective_message.reply_text(f"Uso: /del_msg_{audience} <id>")
    try: sid = int(context.args[0])
    except: return await update.effective_message.reply_text("ID inválido.")
    with session_scope() as s:
        m = s.query(ScheduledMessage).filter_by(id=sid, audience=audience).first()
        if not m: return await update.effective_message.reply_text("Mensagem não encontrada.")
        s.delete(m)
    for j in list(context.job_queue.jobs()):
        if j.name == f"{JOB_PREFIX_SM}{sid}": j.schedule_removal()
    await update.effective_message.reply_text("✅ Mensagem removida.")

# Wraps
async def add_msg_vip_cmd(u,c):   await add_msg_generic(u,c,"vip")
async def add_msg_free_cmd(u,c):  await add_msg_generic(u,c,"free")
async def list_msgs_vip_cmd(u,c): await list_msgs_generic(u,c,"vip")
async def list_msgs_free_cmd(u,c):await list_msgs_generic(u,c,"free")
async def edit_msg_vip_cmd(u,c):  await edit_msg_generic(u,c,"vip")
async def edit_msg_free_cmd(u,c): await edit_msg_generic(u,c,"free")
async def toggle_msg_vip_cmd(u,c):await toggle_msg_generic(u,c,"vip")
async def toggle_msg_free_cmd(u,c):await toggle_msg_generic(u,c,"free")
async def del_msg_vip_cmd(u,c):   await del_msg_generic(u,c,"vip")
async def del_msg_free_cmd(u,c):  await del_msg_generic(u,c,"free")

# ---------------------------
# Ferramentas de diagnóstico/admin extra
# ---------------------------
async def diag_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    try:
        info = await application.bot.get_webhook_info()
        webhook_ok = bool(info.url)
    except Exception:
        webhook_ok = False
    with session_scope() as s:
        packs_c = s.query(Pack).count()
        pf_c    = s.query(PackFile).count()
        pay_c   = s.query(Payment).count()
        sm_c    = s.query(ScheduledMessage).count()
        # sanity: colunas audience existem?
        insp = inspect(engine)
        packs_cols = {c["name"] for c in insp.get_columns("packs")}
        sched_cols = {c["name"] for c in insp.get_columns("scheduled_messages")}
        packs_null_aud = s.execute(text("SELECT COUNT(*) FROM packs WHERE audience IS NULL")).scalar() or 0
        sched_null_aud = s.execute(text("SELECT COUNT(*) FROM scheduled_messages WHERE audience IS NULL")).scalar() or 0
    lines = [
        "🔎 <b>Diag</b>",
        f"Webhook setado: {'SIM' if webhook_ok else 'NÃO'}",
        f"DB: packs={packs_c}, pack_files={pf_c}, payments={pay_c}, sched_msgs={sm_c}",
        f"Schema packs tem 'audience': {'SIM' if 'audience' in packs_cols else 'NÃO'} (nulos={packs_null_aud})",
        f"Schema scheduled_messages tem 'audience': {'SIM' if 'audience' in sched_cols else 'NÃO'} (nulos={sched_null_aud})",
        f"default_chain={cfg_get('default_chain', DEFAULT_CHAIN)}  price={cfg_get('price_amount', DEFAULT_PRICE_AMOUNT)} {cfg_get('price_currency', DEFAULT_PRICE_CURRENCY)}",
        f"daily_pack_hhmm={cfg_get('daily_pack_hhmm')}",
    ]
    for ch in chunk_text(lines): await update.effective_message.reply_text(ch, parse_mode=ParseMode.HTML)

async def fix_legacy_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        return await update.effective_message.reply_text("Apenas admins.")
    with engine.begin() as conn:
        conn.execute(text("UPDATE packs SET audience='vip' WHERE audience IS NULL"))
        conn.execute(text("UPDATE scheduled_messages SET audience='vip' WHERE audience IS NULL"))
    await update.effective_message.reply_text("✅ Legacy corrigido (audience NULL -> 'vip').")

# ---------------------------
# Error handler
# ---------------------------
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Erro não tratado", exc_info=context.error)

# ---------------------------
# Webhooks
# ---------------------------
@app.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    data = await request.json()
    uid, tx_hash = data.get("telegram_user_id"), data.get("tx_hash")
    amount = data.get("amount")
    chain = (data.get("chain") or cfg_get("default_chain", DEFAULT_CHAIN)).lower()
    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)
    with session_scope() as s:
        pay = s.query(Payment).filter_by(tx_hash=tx_hash).first()
        if not pay:
            s.add(Payment(user_id=int(uid), tx_hash=tx_hash, amount=amount, chain=chain, status="approved", decided_at=now_utc()))
        else:
            pay.status, pay.decided_at = "approved", now_utc()
    link = await _send_invite_one_click()
    if link:
        try: await application.bot.send_message(chat_id=int(uid), text=f"✅ Pagamento confirmado! Entre no VIP: {link.invite_link}")
        except: logging.exception("Erro enviando invite")
    return JSONResponse({"ok": True})

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception:
        logging.exception("Erro processando update Telegram"); raise HTTPException(status_code=400, detail="Invalid update")
    return PlainTextResponse("", status_code=200)

@app.get("/ping")
async def ping(): return {"ok": True, "time": now_utc().isoformat()}

@app.get("/")
async def root(): return {"status": "online", "message": "Bot ready (crypto + schedules + packs)"}

# ---------------------------
# Startup
# ---------------------------
tz_sp = pytz.timezone("America/Sao_Paulo")

async def _reschedule_daily_pack():
    for j in list(application.job_queue.jobs()):
        if j.name == "daily_pack": j.schedule_removal()
    hhmm = cfg_get("daily_pack_hhmm") or "18:49"
    h, m = parse_hhmm(hhmm)
    application.job_queue.run_daily(enviar_pack_vip_job, time=dt.time(hour=h, minute=m, tzinfo=tz_sp), name="daily_pack")
    logging.info(f"Job diário de pack agendado para {hhmm} America/Sao_Paulo")

@app.on_event("startup")
async def on_startup():
    global bot
    logging.basicConfig(level=logging.INFO)
    await application.initialize(); await application.start()
    bot = application.bot

    if BASE_URL:
        try: await bot.set_webhook(url=f"{BASE_URL}/webhook")
        except Exception as e: logging.warning(f"Falha set_webhook: {e}")
    logging.info("Bot iniciado (cripto + schedules + packs).")

    application.add_error_handler(error_handler)

    # Conversa /novopack
    conv = ConversationHandler(
        entry_points=[CommandHandler("novopack", novopack_start),
                      CommandHandler("novopackvip", novopack_start),
                      CommandHandler("novopackfree", novopack_start)],
        states={
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_title)],
            CHOOSE_AUDIENCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_choose_audience)],
            PREVIEWS: [CommandHandler("proximo", novopack_next_to_files),
                       MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION, novopack_collect_previews),
                       MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_collect_previews)],
            FILES: [CommandHandler("finalizar", novopack_finish_review),
                    MessageHandler(filters.Document.ALL | filters.AUDIO | filters.VOICE, novopack_collect_files),
                    MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_collect_files)],
            CONFIRM_SAVE: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_confirm_save)],
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv, group=0)

    # Storage handlers (VIP / FREE)
    for chat_id in (STORAGE_VIP_GROUP_ID, STORAGE_FREE_GROUP_ID):
        application.add_handler(MessageHandler(filters.Chat(chat_id) & filters.TEXT & ~filters.COMMAND, storage_text_handler), group=1)
        application.add_handler(MessageHandler(
            filters.Chat(chat_id) & (filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.AUDIO | filters.Document.ALL | filters.VOICE),
            storage_media_handler
        ), group=1)

    # Comandos gerais
    for cmd, fn in {
        "start": start_cmd, "comandos": comandos_cmd, "listar_comandos": listar_comandos_cmd, "getid": getid_cmd,
        "listar_packs": listar_packs_cmd, "simularvip": simularvip_cmd,
        "listar_admins": listar_admins_cmd, "add_admin": add_admin_cmd, "rem_admin": rem_admin_cmd,
        "set_preco": set_preco_cmd, "ver_preco": ver_preco_cmd, "set_free_teaser": set_free_teaser_cmd, "set_pack_horario": set_pack_horario_cmd,
        "grupovip": grupovip_cmd, "pagar": pagar_cmd, "tx": tx_cmd,
        "listar_pendentes": listar_pendentes_cmd, "aprovar_tx": aprovar_tx_cmd, "rejeitar_tx": rejeitar_tx_cmd,
        "add_msg_vip": add_msg_vip_cmd, "add_msg_free": add_msg_free_cmd,
        "list_msgs_vip": list_msgs_vip_cmd, "list_msgs_free": list_msgs_free_cmd,
        "edit_msg_vip": edit_msg_vip_cmd, "edit_msg_free": edit_msg_free_cmd,
        "toggle_msg_vip": toggle_msg_vip_cmd, "toggle_msg_free": toggle_msg_free_cmd,
        "del_msg_vip": del_msg_vip_cmd, "del_msg_free": del_msg_free_cmd,
        "diag": diag_cmd, "fix_legacy": fix_legacy_cmd,
    }.items():
        application.add_handler(CommandHandler(cmd, fn), group=1)

    # Jobs
    await _reschedule_daily_pack()
    application.job_queue.run_repeating(keepalive_job, interval=KEEPALIVE_INTERVAL_SEC, first=10, name="keepalive")
    _register_all_scheduled_messages(application.job_queue)
    logging.info("Handlers e jobs registrados.")

# ---------------------------
# Run local
# ---------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
