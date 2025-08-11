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

from telegram import (
    Update as TgUpdate,
    InputMediaPhoto,
)
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

# =============== Helpers ===============
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

# =============== ENV / CONFIG ===============
load_dotenv()

# --- CONFIG editável inline (pode sobrescrever via env) ---
CONFIG: Dict[str, Any] = {
    "BOT_TOKEN": os.getenv("BOT_TOKEN"),  # deixe no Render
    "BASE_URL": os.getenv("BASE_URL"),    # ex.: https://seu-servico.onrender.com

    # Grupos principais
    "GROUP_VIP_ID": int(os.getenv("GROUP_VIP_ID", "-1002791988432")),  # grupo VIP destino dos packs
    # Se não tiver um grupo FREE separado, usamos o storage FREE como fallback:
    "GROUP_FREE_ID": int(os.getenv("GROUP_FREE_ID", os.getenv("STORAGE_FREE_GROUP_ID", "-1002509364079"))),

    # Grupos de armazenamento (onde pode cadastrar packs via /novopack também)
    "STORAGE_VIP_GROUP_ID": int(os.getenv("STORAGE_VIP_GROUP_ID", "-4806334341")),
    "STORAGE_FREE_GROUP_ID": int(os.getenv("STORAGE_FREE_GROUP_ID", "-1002509364079")),

    # Pagamento (MetaMask, multi-rede somente EVM por enquanto)
    "WALLET_ADDRESS": os.getenv("WALLET_ADDRESS", "0x40dDBD27F878d07808339F9965f013F1CBc2F812").strip(),
    "DEFAULT_CHAIN": os.getenv("DEFAULT_CHAIN", "Polygon").strip(),  # usada quando /tx <hash> sem rede

    # Preço/calendário
    "VIP_PRICE_VALUE": float(os.getenv("VIP_PRICE_VALUE", "25")),
    "VIP_PRICE_SYMBOL": os.getenv("VIP_PRICE_SYMBOL", "USDT"),
    "DAILY_PACK_HHMM": os.getenv("DAILY_PACK_HHMM", "09:00"),

    # Teaser pro FREE ao enviar previews do VIP
    "FREE_TEASER_TEMPLATE": os.getenv(
        "FREE_TEASER_TEMPLATE",
        "🔥 Hoje liberamos no VIP: {title}\nPara participar, digite /grupovip"
    ),

    # Convite VIP com limite de 1 clique e validade (horas)
    "INVITE_TTL_HOURS": int(os.getenv("INVITE_TTL_HOURS", "3")),
}

BOT_TOKEN = CONFIG["BOT_TOKEN"]
BASE_URL = CONFIG["BASE_URL"]

if not BOT_TOKEN:
    raise RuntimeError("Defina BOT_TOKEN em CONFIG['BOT_TOKEN'] (ou em env BOT_TOKEN).")

# =============== FastAPI + PTB ===============
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None

# =============== DB setup ===============
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
    user_id = Column(BigInteger, unique=True, index=True)
    added_at = Column(DateTime, default=now_utc)

# ---- Packs ----
class Pack(Base):
    __tablename__ = "packs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    header_message_id = Column(Integer, nullable=True, unique=True)
    target = Column(String, default="vip")  # "vip" ou "free"
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
    user_id = Column(BigInteger, index=True)
    username = Column(String, nullable=True)
    tx_hash = Column(String, unique=True, index=True)
    chain = Column(String, default=CONFIG["DEFAULT_CHAIN"])
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
    enabled = Column(Boolean, default=True)
    target = Column(String, default="vip")    # "vip" ou "free"
    created_at = Column(DateTime, default=now_utc)
    __table_args__ = (UniqueConstraint('id', name='uq_scheduled_messages_id'),)

# ---- MIGRA ----
from sqlalchemy import inspect

def ensure_bigint_columns():
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
            # add missing columns if needed
            insp = inspect(engine)
            cols = [c['name'] for c in insp.get_columns('packs')]
            if 'target' not in cols:
                conn.execute(text("ALTER TABLE packs ADD COLUMN IF NOT EXISTS target VARCHAR"))
            cols2 = [c['name'] for c in insp.get_columns('scheduled_messages')]
            if 'target' not in cols2:
                conn.execute(text("ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS target VARCHAR"))
    except Exception as e:
        logging.warning("Falha em ensure_bigint_columns: %s", e)


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
    if not cfg_get("daily_pack_hhmm"):
        cfg_set("daily_pack_hhmm", CONFIG["DAILY_PACK_HHMM"])
    if not cfg_get("vip_price_value"):
        cfg_set("vip_price_value", str(CONFIG["VIP_PRICE_VALUE"]))
    if not cfg_get("vip_price_symbol"):
        cfg_set("vip_price_symbol", CONFIG["VIP_PRICE_SYMBOL"]) 
    if not cfg_get("free_teaser_template"):
        cfg_set("free_teaser_template", CONFIG["FREE_TEASER_TEMPLATE"]) 

ensure_bigint_columns()
init_db()

# =============== DB helpers ===============

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

# ---- Packs CRUD ----

def create_pack(title: str, target: str = "vip", header_message_id: Optional[int] = None) -> Pack:
    s = SessionLocal()
    try:
        p = Pack(title=title.strip(), header_message_id=header_message_id, target=target)
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

def get_next_unsent_pack(target: str = "vip") -> Optional[Pack]:
    s = SessionLocal()
    try:
        return s.query(Pack).filter(Pack.sent == False, Pack.target == target).order_by(Pack.created_at.asc()).first()
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
        m = ScheduledMessage(hhmm=hhmm, text=text, tz=tz_name, enabled=True, target=target)
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

# =============== STORAGE GROUP handlers (VIP e FREE) ===============
async def _storage_text_handler_common(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE, storage_group_id: int, target: str):
    msg = update.effective_message
    if not msg or msg.chat.id != storage_group_id:
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

    # NESTE PONTO, qualquer pessoa no grupo de storage pode criar pack
    if get_pack_by_header(msg.message_id):
        await msg.reply_text("Pack já registrado.")
        return

    p = create_pack(title=title, header_message_id=msg.message_id, target=target)
    await msg.reply_text(f"Pack registrado: <b>{esc(p.title)}</b> (id {p.id}) → destino: <b>{esc(target.upper())}</b>", parse_mode="HTML")

async def storage_text_handler_vip(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    await _storage_text_handler_common(update, context, CONFIG["STORAGE_VIP_GROUP_ID"], "vip")

async def storage_text_handler_free(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    await _storage_text_handler_common(update, context, CONFIG["STORAGE_FREE_GROUP_ID"], "free")

async def _storage_media_handler_common(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE, storage_group_id: int):
    msg = update.effective_message
    if not msg or msg.chat.id != storage_group_id:
        return

    reply = msg.reply_to_message
    if not reply or not reply.message_id:
        await msg.reply_text("Envie este arquivo como <b>resposta</b> ao título do pack.", parse_mode="HTML")
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
        await msg.reply_text("Tipo de mídia não suportado.")
        return

    add_file_to_pack(pack_id=pack.id, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role, file_name=visible_name)
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b>.", parse_mode="HTML")

async def storage_media_handler_vip(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    await _storage_media_handler_common(update, context, CONFIG["STORAGE_VIP_GROUP_ID"])

async def storage_media_handler_free(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    await _storage_media_handler_common(update, context, CONFIG["STORAGE_FREE_GROUP_ID"])

# =============== Envio do pack (JobQueue) ===============
async def _send_previews_to_free(context: ContextTypes.DEFAULT_TYPE, pack: Pack):
    # Busca todos previews e manda para o grupo FREE com teaser
    s = SessionLocal()
    try:
        files = s.query(PackFile).filter(PackFile.pack_id == pack.id).order_by(PackFile.id.asc()).all()
    finally:
        s.close()

    previews = [f for f in files if f.role == "preview" and f.file_type == "photo"]
    teaser_tpl = cfg_get("free_teaser_template", CONFIG["FREE_TEASER_TEMPLATE"]) or ""
    teaser_text = (teaser_tpl or "").format(title=pack.title)

    if teaser_text:
        try:
            await context.application.bot.send_message(chat_id=CONFIG["GROUP_FREE_ID"], text=teaser_text)
        except Exception as e:
            logging.warning(f"Falha ao enviar teaser pro FREE: {e}")

    if previews:
        media = []
        # Enviar em lote de até 10 fotos por media_group (limite Telegram)
        batch = []
        for i, pf in enumerate(previews, start=1):
            batch.append(InputMediaPhoto(media=pf.file_id))
            if len(batch) == 10:
                try:
                    await context.application.bot.send_media_group(chat_id=CONFIG["GROUP_FREE_ID"], media=batch)
                except Exception as e:
                    logging.warning(f"Falha media_group FREE: {e}")
                batch = []
        if batch:
            try:
                await context.application.bot.send_media_group(chat_id=CONFIG["GROUP_FREE_ID"], media=batch)
            except Exception as e:
                logging.warning(f"Falha media_group FREE final: {e}")

async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        pack = get_next_unsent_pack(target="vip")
        if not pack:
            logging.info("Nenhum pack VIP pendente para envio.")
            return "Nenhum pack pendente para envio."

        s = SessionLocal()
        try:
            p = s.query(Pack).filter(Pack.id == pack.id).first()
            files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()
        finally:
            s.close()

        if not files:
            logging.warning(f"Pack '{p.title}' sem arquivos; marcando como enviado.")
            mark_pack_sent(p.id)
            return f"Pack '{p.title}' não possui arquivos. Marcado como enviado."

        previews = [f for f in files if f.role == "preview"]
        docs     = [f for f in files if f.role == "file"]

        sent_first = False
        sent_counts = {"photos": 0, "videos": 0, "animations": 0, "docs": 0, "audios": 0, "voices": 0}

        # Previews - fotos em grupo
        photo_ids = [f.file_id for f in previews if f.file_type == "photo"]
        if photo_ids:
            media = []
            for i, fid in enumerate(photo_ids):
                if i == 0:
                    media.append(InputMediaPhoto(media=fid, caption=p.title))
                else:
                    media.append(InputMediaPhoto(media=fid))
            try:
                await context.application.bot.send_media_group(chat_id=CONFIG["GROUP_VIP_ID"], media=media)
                sent_first = True
                sent_counts["photos"] += len(photo_ids)
            except Exception as e:
                logging.warning(f"Falha send_media_group VIP: {e}. Enviando individual.")
                for i, fid in enumerate(photo_ids):
                    cap = p.title if i == 0 else None
                    await context.application.bot.send_photo(chat_id=CONFIG["GROUP_VIP_ID"], photo=fid, caption=cap)
                    sent_first = True
                    sent_counts["photos"] += 1

        # Previews - vídeos/animações
        for f in [f for f in previews if f.file_type in ("video", "animation")]:
            cap = p.title if not sent_first else None
            try:
                if f.file_type == "video":
                    await context.application.bot.send_video(chat_id=CONFIG["GROUP_VIP_ID"], video=f.file_id, caption=cap)
                    sent_counts["videos"] += 1
                elif f.file_type == "animation":
                    await context.application.bot.send_animation(chat_id=CONFIG["GROUP_VIP_ID"], animation=f.file_id, caption=cap)
                    sent_counts["animations"] += 1
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando preview {f.id} VIP: {e}")

        # Arquivos
        for f in docs:
            try:
                cap = p.title if not sent_first else None
                if f.file_type == "document":
                    await context.application.bot.send_document(chat_id=CONFIG["GROUP_VIP_ID"], document=f.file_id, caption=cap)
                    sent_counts["docs"] += 1
                elif f.file_type == "audio":
                    await context.application.bot.send_audio(chat_id=CONFIG["GROUP_VIP_ID"], audio=f.file_id, caption=cap)
                    sent_counts["audios"] += 1
                elif f.file_type == "voice":
                    await context.application.bot.send_voice(chat_id=CONFIG["GROUP_VIP_ID"], voice=f.file_id, caption=cap)
                    sent_counts["voices"] += 1
                else:
                    await context.application.bot.send_document(chat_id=CONFIG["GROUP_VIP_ID"], document=f.file_id, caption=cap)
                    sent_counts["docs"] += 1
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando arquivo {f.file_name or f.id} VIP: {e}")

        # Marca enviado
        mark_pack_sent(p.id)
        logging.info(f"Pack VIP enviado: {p.title}")

        # Envia todos previews (fotos) pro FREE com teaser
        try:
            await _send_previews_to_free(context, p)
        except Exception as e:
            logging.warning(f"Falha ao propagar previews pro FREE: {e}")

        return (
            f"✅ Enviado pack VIP '{p.title}'. "
            f"Previews: {sent_counts['photos']} fotos, {sent_counts['videos']} vídeos, {sent_counts['animations']} animações. "
            f"Arquivos: {sent_counts['docs']} docs, {sent_counts['audios']} áudios, {sent_counts['voices']} voices."
        )

    except Exception as e:
        logging.exception("Erro no enviar_pack_vip_job")
        return f"❌ Erro no envio: {e!r}"

# =============== Conversa /novopack (privado OU storage groups) ===============
TITLE, CONFIRM_TITLE, PREVIEWS, FILES, CONFIRM_SAVE, CHOOSE_TARGET = range(6)


def _context_is_storage(chat_id: int) -> bool:
    return chat_id in {CONFIG["STORAGE_VIP_GROUP_ID"], CONFIG["STORAGE_FREE_GROUP_ID"]}


def _require_can_create_pack(update: TgUpdate) -> bool:
    """Pode criar se:
    - for admin em qualquer chat, ou
    - estiver nos grupos de storage (qualquer usuário pode), ou
    - estiver no privado e for admin.
    """
    user_ok = is_admin(update.effective_user.id) if update.effective_user else False
    chat_ok = _context_is_storage(update.effective_chat.id)
    if update.effective_chat.type == "private":
        return user_ok or True  # permitir no privado para qualquer um? vamos permitir.
    return user_ok or chat_ok


def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—")
    previews = user_data.get("previews", [])
    files = user_data.get("files", [])
    target = user_data.get("target", "vip").upper()

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
        f"• Destino: <b>{esc(target)}</b>",
        f"• Previews ({len(previews)}): " + (", ".join(preview_names) if preview_names else "—"),
        f"• Arquivos ({len(files)}): " + (", ".join(file_names) if file_names else "—"),
        "",
        "Deseja salvar? (<b>sim</b>/<b>não</b>)"
    ]
    return "\n".join(text)

async def novopack_entry(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not _require_can_create_pack(update):
        await update.effective_message.reply_text("Você não tem permissão aqui para criar pack. Tente no privado ou no grupo de storage.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.effective_message.reply_text(
        "🧩 Vamos criar um novo pack!\n\nEscolha o destino: <b>VIP</b> ou <b>FREE</b> (digite).",
        parse_mode="HTML"
    )
    return CHOOSE_TARGET

async def novopack_choose_target(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    choice = (update.effective_message.text or "").strip().lower()
    if choice not in ("vip", "free"):
        await update.effective_message.reply_text("Responda com <b>VIP</b> ou <b>FREE</b>.", parse_mode="HTML")
        return CHOOSE_TARGET
    context.user_data["target"] = choice
    await update.effective_message.reply_text("1) Envie o <b>título do pack</b> (apenas texto).", parse_mode="HTML")
    return TITLE

async def novopack_start_vip(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not _require_can_create_pack(update):
        await update.effective_message.reply_text("Você não tem permissão para criar pack aqui.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["target"] = "vip"
    await update.effective_message.reply_text("🧩 Criando pack para <b>VIP</b>\n\n1) Envie o <b>título</b>.", parse_mode="HTML")
    return TITLE

async def novopack_start_free(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not _require_can_create_pack(update):
        await update.effective_message.reply_text("Você não tem permissão para criar pack aqui.")
        return ConversationHandler.END
    context.user_data.clear()
    context.user_data["target"] = "free"
    await update.effective_message.reply_text("🧩 Criando pack para <b>FREE</b>\n\n1) Envie o <b>título</b>.", parse_mode="HTML")
    return TITLE

async def novopack_title(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    title = (update.effective_message.text or "").strip()
    if not title:
        await update.effective_message.reply_text("Título vazio. Envie um texto com o título do pack.")
        return TITLE
    context.user_data["title_candidate"] = title
    await update.effective_message.reply_text(f"Confirma o nome: <b>{esc(title)}</b>? (sim/não)", parse_mode="HTML")
    return CONFIRM_TITLE

async def novopack_confirm_title(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
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
        "2) Envie as <b>PREVIEWS</b> (📷 fotos / 🎞 vídeos / 🎞 animações).\nEnvie quantas quiser. Quando terminar, mande /proximo.",
        parse_mode="HTML"
    )
    return PREVIEWS

async def hint_previews(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Envie PREVIEWS (📷 foto / 🎞 vídeo / 🎞 animação) ou use /proximo para ir aos ARQUIVOS."
    )

async def hint_files(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Envie ARQUIVOS (📄 documento / 🎵 áudio / 🎙 voice) ou use /finalizar para revisar e salvar."
    )

async def novopack_collect_previews(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
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

async def novopack_next_to_files(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("title"):
        await update.effective_message.reply_text("Título não encontrado. Use /cancelar e recomece com /novopack.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "3) Agora envie os <b>ARQUIVOS</b> (📄 documentos / 🎵 áudio / 🎙 voice).\nEnvie quantos quiser. Quando terminar, mande /finalizar.",
        parse_mode="HTML"
    )
    return FILES

async def novopack_collect_files(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
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

async def novopack_finish_review(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    summary = _summary_from_session(context.user_data)
    await update.effective_message.reply_text(summary, parse_mode="HTML")
    return CONFIRM_SAVE

async def novopack_confirm_save(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.effective_message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.effective_message.reply_text("Responda <b>sim</b> para salvar ou <b>não</b> para cancelar.", parse_mode="HTML")
        return CONFIRM_SAVE
    if answer in ("não", "nao"):
        context.user_data.clear()
        await update.effective_message.reply_text("Operação cancelada. Nada foi salvo.")
        return ConversationHandler.END

    title = context.user_data.get("title")
    target = context.user_data.get("target", "vip")
    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])

    p = create_pack(title=title, target=target, header_message_id=None)
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
    await update.effective_message.reply_text(f"🎉 <b>{esc(title)}</b> cadastrado com sucesso para <b>{esc(target.upper())}</b>!", parse_mode="HTML")
    return ConversationHandler.END

async def novopack_cancel(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Operação cancelada.")
    return ConversationHandler.END

# =============== Comandos básicos & Admin ===============
async def start_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (
        "Fala! Eu gerencio packs VIP/FREE, pagamentos via MetaMask e mensagens agendadas.\n"
        "Use /comandos para ver tudo."
    )
    if msg:
        await msg.reply_text(text)

NETWORK_ALIASES = {
    "eth": "Ethereum", "ethereum": "Ethereum",
    "bsc": "BSC", "binance": "BSC",
    "polygon": "Polygon", "matic": "Polygon",
    "arbitrum": "Arbitrum", "arb": "Arbitrum",
    "optimism": "Optimism", "op": "Optimism",
    "base": "Base",
    "avalanche": "Avalanche", "avax": "Avalanche",
    "fantom": "Fantom", "ftm": "Fantom",
}

async def comandos_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    # Lista completa (marca os admin-only)
    vip_price_val = cfg_get("vip_price_value", str(CONFIG["VIP_PRICE_VALUE"]))
    vip_price_sym = cfg_get("vip_price_symbol", CONFIG["VIP_PRICE_SYMBOL"]) or CONFIG["VIP_PRICE_SYMBOL"]

    lines = [
        "📋 <b>Comandos</b>",
        "• /start — mensagem inicial",
        "• /comandos — lista de comandos",
        "• /listar_comandos — (alias)",
        "• /getid — mostra seus IDs",
        "",
        "💸 <b>Pagamento (MetaMask, multi-rede)</b>:",
        f"• /pagar — instruções e redes aceitas (preço atual: <b>{esc(vip_price_val)} {esc(vip_price_sym)}</b>)",
        "• /tx &lt;hash&gt; — auto-detecta rede",
        "• /tx &lt;rede&gt; &lt;hash&gt; — força rede (ex.: /tx polygon 0xabc...)",
        "• /grupovip — como participar (no privado)",
        "",
        "🧩 <b>Packs</b>:",
        "• /novopack — pergunta VIP/FREE (privado ou nos grupos de cadastro)",
        "• /novopackvip — atalho VIP (privado/storage)",
        "• /novopackfree — atalho FREE (privado/storage)",
        "• /cancelar — cancela a criação do pack",
        "",
        "🕒 <b>Mensagens agendadas</b>:",
        "• /add_msg_vip HH:MM &lt;texto&gt; | /add_msg_free HH:MM &lt;texto&gt;",
        "• /list_msgs_vip | /list_msgs_free",
        "• /edit_msg_vip &lt;id&gt; [HH:MM] [novo texto]",
        "• /edit_msg_free &lt;id&gt; [HH:MM] [novo texto]",
        "• /toggle_msg_vip &lt;id&gt; | /toggle_msg_free &lt;id&gt;",
        "• /del_msg_vip &lt;id&gt; | /del_msg_free &lt;id&gt;",
        "",
        "🛠 <b>Admin</b>:",
        "• /simularvip — envia o próximo pack VIP pendente agora (e o teaser no FREE)",
        "• /listar_packs — lista packs",
        "• /pack_info &lt;id&gt; — detalhes do pack",
        "• /excluir_item &lt;id_item&gt; — remove item do pack",
        "• /excluir_pack &lt;id&gt; — remove pack (com confirmação)",
        "• /set_pendente &lt;id&gt; | /set_enviado &lt;id&gt;",
        "• /mudar_nome &lt;novo nome&gt;",
        "• /add_admin &lt;user_id&gt; | /rem_admin &lt;user_id&gt; | /listar_admins",
        "• /listar_pendentes — pagamentos pendentes",
        "• /aprovar_tx &lt;user_id&gt; | /rejeitar_tx &lt;user_id&gt; [motivo]",
        "• /set_pack_horario HH:MM — define o horário diário dos packs (America/Sao_Paulo)",
        "• /set_preco &lt;valor&gt; &lt;moeda&gt; — define preço VIP (ex.: 25 USDT)",
        "• /ver_preco — mostra preço atual",
        "• /set_free_teaser &lt;texto&gt; — teaser pro FREE (usa {title})",
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

listar_comandos_cmd = comandos_cmd

async def getid_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if msg:
        await msg.reply_text(
            f"Seu nome: {esc(user.full_name)}\nSeu ID: {user.id}\nID deste chat: {chat.id}",
            parse_mode="HTML"
        )

# ====== Admin utils ======
async def mudar_nome_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /mudar_nome &lt;novo nome exibido do bot&gt;", parse_mode="HTML")
        return
    novo_nome = " ".join(context.args).strip()
    try:
        await application.bot.set_my_name(name=novo_nome)
        await update.effective_message.reply_text(f"✅ Nome exibido alterado para: {esc(novo_nome)}", parse_mode="HTML")
    except Exception as e:
        await update.effective_message.reply_text(f"Erro: {e}")

async def listar_admins_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    ids = list_admin_ids()
    if not ids:
        await update.effective_message.reply_text("Sem admins cadastrados.")
        return
    await update.effective_message.reply_text("👑 Admins:\n" + "\n".join(f"- {i}" for i in ids))

async def add_admin_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /add_admin &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.effective_message.reply_text("user_id inválido.")
        return
    ok = add_admin_db(uid)
    await update.effective_message.reply_text("✅ Admin adicionado." if ok else "Já era admin.")

async def rem_admin_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /rem_admin &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(context.args[0])
    except:
        await update.effective_message.reply_text("user_id inválido.")
        return
    ok = remove_admin_db(uid)
    await update.effective_message.reply_text("✅ Admin removido." if ok else "Este user não é admin.")

# ===== Packs admin =====
async def simularvip_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    status = await enviar_pack_vip_job(context)
    await update.effective_message.reply_text(status)

async def listar_packs_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
            lines.append(f"[{p.id}] {esc(p.title)} → {p.target.upper()} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

async def pack_info_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /pack_info &lt;id&gt;", parse_mode="HTML")
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
        lines = [f"Pack [{p.id}] {esc(p.title)} → {p.target.upper()} — {'ENVIADO' if p.sent else 'PENDENTE'}"]
        for f in files:
            name = f.file_name or ""
            lines.append(f" - item #{f.id} | {f.file_type} ({f.role}) {name}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

async def excluir_item_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /excluir_item &lt;id_item&gt;", parse_mode="HTML")
        return
    try:
        item_id = int(context.args[0])
    except:
        await update.effective_message.reply_text("ID inválido. Use: /excluir_item &lt;id_item&gt;", parse_mode="HTML")
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

async def excluir_pack_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
                lines.append(f"[{p.id}] {esc(p.title)} → {p.target.upper()}")
            await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")
            return ConversationHandler.END
        finally:
            s.close()

    try:
        pid = int(context.args[0])
    except:
        await update.effective_message.reply_text("Uso: /excluir_pack &lt;id&gt;", parse_mode="HTML")
        return ConversationHandler.END

    context.user_data["delete_pid"] = pid
    await update.effective_message.reply_text(
        f"Confirma excluir o pack <b>#{pid}</b>? (sim/não)",
        parse_mode="HTML"
    )
    return DELETE_PACK_CONFIRM

async def excluir_pack_confirm(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
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
async def set_pendente_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /set_pendente &lt;id_do_pack&gt;", parse_mode="HTML")
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

async def set_enviado_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return

    if not context.args:
        await update.effective_message.reply_text("Uso: /set_enviado &lt;id_do_pack&gt;", parse_mode="HTML")
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

# =============== Pagamento por MetaMask - Fluxo ===============
async def pagar_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat = update.effective_chat

    vip_price_val = cfg_get("vip_price_value", str(CONFIG["VIP_PRICE_VALUE"]))
    vip_price_sym = cfg_get("vip_price_symbol", CONFIG["VIP_PRICE_SYMBOL"]) or CONFIG["VIP_PRICE_SYMBOL"]

    redes = ", ".join(sorted({v for v in NETWORK_ALIASES.values()}))

    texto = (
        f"💸 <b>Pagamento via MetaMask</b>\n"
        f"Preço do VIP: <b>{esc(vip_price_val)} {esc(vip_price_sym)}</b>\n\n"
        f"1) Envie <b>exatamente</b> esse valor para a carteira ({esc(CONFIG['DEFAULT_CHAIN'])} por padrão):\n"
        f"<code>{esc(CONFIG['WALLET_ADDRESS'])}</code>\n\n"
        f"2) Depois me mande o comprovante com o comando:\n"
        f"• <code>/tx &lt;hash&gt;</code> — tento detectar a rede\n"
        f"• <code>/tx &lt;rede&gt; &lt;hash&gt;</code> — você força a rede (ex.: <code>/tx polygon 0xabc...</code>)\n\n"
        f"Redes aceitas: {esc(redes)}\n"
        f"Assim que aprovado, te envio um convite <b>de 1 clique</b> para o VIP."
    )

    # Se for no grupo FREE, apaga a mensagem depois de 5s e manda no privado
    sent_group_hint = None
    if chat.id == CONFIG["GROUP_FREE_ID"]:
        try:
            sent_group_hint = await update.effective_message.reply_text("Te enviei as instruções no privado. 👇")
        except Exception:
            pass
        try:
            await context.application.bot.send_message(chat_id=user.id, text=texto, parse_mode="HTML")
        except Exception:
            # Não conseguiu abrir privado
            if sent_group_hint:
                await sent_group_hint.edit_text("Não consegui te chamar no privado. Me envia /start no PV e repete /pagar aqui.")
        # apagar após 5s (a mensagem do usuário e a nossa)
        await asyncio.sleep(5)
        try:
            await context.application.bot.delete_message(chat_id=chat.id, message_id=update.effective_message.message_id)
        except Exception:
            pass
        if sent_group_hint:
            try:
                await context.application.bot.delete_message(chat_id=chat.id, message_id=sent_group_hint.message_id)
            except Exception:
                pass
        return

    # Outras conversas (privado / VIP etc.)
    await update.effective_message.reply_text(texto, parse_mode="HTML")


def _guess_chain(name_or_hash: str) -> str:
    cand = name_or_hash.lower()
    if cand in NETWORK_ALIASES:
        return NETWORK_ALIASES[cand]
    # Heurística: se for 0x + 66 chars -> transação EVM, usamos default
    if cand.startswith("0x") and len(cand) >= 10:
        return CONFIG["DEFAULT_CHAIN"]
    return CONFIG["DEFAULT_CHAIN"]

async def tx_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    user = update.effective_user

    if not context.args:
        await msg.reply_text("Uso: /tx &lt;hash&gt; ou /tx &lt;rede&gt; &lt;hash&gt;", parse_mode="HTML")
        return

    if len(context.args) == 1:
        tx_hash = context.args[0].strip()
        chain = _guess_chain(tx_hash)
    else:
        chain = _guess_chain(context.args[0])
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
            chain=chain,
            status="pending",
        )
        s.add(p)
        s.commit()
        await msg.reply_text("✅ Recebi seu hash! Assim que for aprovado, te envio o convite do VIP.")
    finally:
        s.close()

async def listar_pendentes_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
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

async def _notify_admins_new_vip(context: ContextTypes.DEFAULT_TYPE, new_user_id: int, new_username: Optional[str]):
    s = SessionLocal()
    try:
        total_vips = s.query(Payment.user_id).filter(Payment.status == "approved").distinct().count()
    finally:
        s.close()
    admins = list_admin_ids()
    for aid in admins:
        try:
            await context.application.bot.send_message(
                chat_id=aid,
                text=f"👤 Novo VIP aprovado: user_id {new_user_id} (@{new_username or '-'})\nTotal de VIPs ativos: {total_vips}"
            )
        except Exception:
            pass

async def _invite_link_one_click(context: ContextTypes.DEFAULT_TYPE) -> str:
    from datetime import timedelta
    expire_date = dt.datetime.utcnow() + timedelta(hours=CONFIG["INVITE_TTL_HOURS"])
    try:
        inv = await context.application.bot.create_chat_invite_link(
            chat_id=CONFIG["GROUP_VIP_ID"],
            member_limit=1,
            expire_date=expire_date,
            creates_join_request=False,
        )
        return inv.invite_link
    except Exception as e:
        logging.warning(f"create_chat_invite_link falhou: {e}, tentando export_chat_invite_link")
        try:
            return await context.application.bot.export_chat_invite_link(chat_id=CONFIG["GROUP_VIP_ID"])
        except Exception as e2:
            logging.exception("Falha ao obter invite")
            raise e2

async def aprovar_tx_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /aprovar_tx &lt;user_id&gt;", parse_mode="HTML")
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
            invite = await _invite_link_one_click(context)
            await application.bot.send_message(chat_id=uid, text=f"✅ Pagamento aprovado! Entre no VIP: {invite}")
            await update.effective_message.reply_text(f"Aprovado e convite enviado para {uid}.")
        except Exception as e:
            logging.exception("Erro enviando invite")
            await update.effective_message.reply_text(f"Aprovado, mas falhou ao enviar convite: {e}")
    finally:
        s.close()

    await _notify_admins_new_vip(context, new_user_id=uid, new_username=update.effective_user.username if update.effective_user else None)

async def rejeitar_tx_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /rejeitar_tx &lt;user_id&gt; [motivo]", parse_mode="HTML")
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
        except Exception:
            pass
        await update.effective_message.reply_text("Pagamento rejeitado.")
    finally:
        s.close()

# =============== Mensagens agendadas (VIP/FREE) ===============
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
    target_chat = CONFIG["GROUP_VIP_ID"] if m.target == "vip" else CONFIG["GROUP_FREE_ID"]
    try:
        await context.application.bot.send_message(chat_id=target_chat, text=m.text)
    except Exception as e:
        logging.warning(f"Falha ao enviar scheduled_message id={sid}: {e}")

def _register_all_scheduled_messages(job_queue: JobQueue):
    # limpa
    for j in list(job_queue.jobs()):
        if j.name and j.name.startswith(JOB_PREFIX_SM):
            j.schedule_removal()
    # recria
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

async def add_msg_vip_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /add_msg_vip HH:MM &lt;texto&gt;", parse_mode="HTML")
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
    context.job_queue.run_daily(
        _scheduled_message_job,
        time=dt.time(hour=h, minute=k, tzinfo=tz),
        name=f"{JOB_PREFIX_SM}{m.id}",
    )
    await update.effective_message.reply_text(f"✅ Mensagem VIP #{m.id} criada para {m.hhmm} (diária).")

async def add_msg_free_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args or len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /add_msg_free HH:MM &lt;texto&gt;", parse_mode="HTML")
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
    context.job_queue.run_daily(
        _scheduled_message_job,
        time=dt.time(hour=h, minute=k, tzinfo=tz),
        name=f"{JOB_PREFIX_SM}{m.id}",
    )
    await update.effective_message.reply_text(f"✅ Mensagem FREE #{m.id} criada para {m.hhmm} (diária).")

async def _list_msgs_common(update: TgUpdate, target: str):
    msgs = scheduled_all(target)
    if not msgs:
        await update.effective_message.reply_text("Não há mensagens agendadas.")
        return
    lines = [f"🕒 <b>Mensagens agendadas — {target.upper()}</b>"]
    for m in msgs:
        status = "ON" if m.enabled else "OFF"
        preview = (m.text[:80] + "…") if len(m.text) > 80 else m.text
        lines.append(f"#{m.id} — {m.hhmm} ({m.tz}) [{status}] — {esc(preview)}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")

async def list_msgs_vip_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    await _list_msgs_common(update, "vip")

async def list_msgs_free_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    await _list_msgs_common(update, "free")

async def _edit_msg_common(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.effective_message.reply_text("Uso: /edit_msg_* &lt;id&gt; [HH:MM] [novo texto]", parse_mode="HTML")
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
    # reschedule
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

async def edit_msg_vip_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    await _edit_msg_common(update, context)

async def edit_msg_free_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    await _edit_msg_common(update, context)

async def toggle_msg_vip_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /toggle_msg_vip &lt;id&gt;", parse_mode="HTML")
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

async def toggle_msg_free_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /toggle_msg_free &lt;id&gt;", parse_mode="HTML")
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

async def del_msg_vip_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /del_msg_vip &lt;id&gt;", parse_mode="HTML")
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

async def del_msg_free_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /del_msg_free &lt;id&gt;", parse_mode="HTML")
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

# =============== Preço & Teaser config ===============
async def set_preco_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if len(context.args) < 2:
        await update.effective_message.reply_text("Uso: /set_preco &lt;valor&gt; &lt;moeda&gt;  (ex.: 25 USDT)", parse_mode="HTML")
        return
    valor = context.args[0]
    moeda = context.args[1].upper()
    try:
        float(valor)
    except:
        await update.effective_message.reply_text("Valor inválido.")
        return
    cfg_set("vip_price_value", str(valor))
    cfg_set("vip_price_symbol", moeda)
    await update.effective_message.reply_text(f"✅ Preço atualizado: {valor} {moeda}")

async def ver_preco_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    val = cfg_get("vip_price_value", str(CONFIG["VIP_PRICE_VALUE"]))
    sym = cfg_get("vip_price_symbol", CONFIG["VIP_PRICE_SYMBOL"]) or CONFIG["VIP_PRICE_SYMBOL"]
    await update.effective_message.reply_text(f"Preço atual do VIP: {val} {sym}")

async def set_free_teaser_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /set_free_teaser &lt;texto&gt; (usa {title})", parse_mode="HTML")
        return
    texto = " ".join(context.args)
    cfg_set("free_teaser_template", texto)
    await update.effective_message.reply_text("✅ Teaser do FREE atualizado.")

# =============== Grupo VIP info /grupovip ===============
async def grupovip_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    vip_price_val = cfg_get("vip_price_value", str(CONFIG["VIP_PRICE_VALUE"]))
    vip_price_sym = cfg_get("vip_price_symbol", CONFIG["VIP_PRICE_SYMBOL"]) or CONFIG["VIP_PRICE_SYMBOL"]
    texto = (
        f"Olá, {esc(user.first_name)}! 👋\n\n"
        f"Para entrar no nosso <b>Grupo VIP</b>, o valor é <b>{esc(vip_price_val)} {esc(vip_price_sym)}</b>.\n"
        f"Pague via MetaMask para a carteira:\n<code>{esc(CONFIG['WALLET_ADDRESS'])}</code> (rede padrão: {esc(CONFIG['DEFAULT_CHAIN'])})\n\n"
        f"Depois me envie o hash com <code>/tx &lt;hash&gt;</code> ou <code>/tx &lt;rede&gt; &lt;hash&gt;</code>.\n"
        f"Assim que aprovado, você recebe um <b>convite de 1 clique</b> com validade limitada."
    )
    try:
        await context.application.bot.send_message(chat_id=user.id, text=texto, parse_mode="HTML")
        if update.effective_chat.id != user.id:
            await update.effective_message.reply_text("Te enviei as instruções no privado. 👇")
    except Exception:
        await update.effective_message.reply_text("Não consegui te chamar no privado. Me envia /start no PV e repete /grupovip aqui.")

# =============== Error handler global ===============
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Erro não tratado", exc_info=context.error)

# =============== Webhooks & HTTP routes ===============
@app.post("/crypto_webhook")
async def crypto_webhook(request: Request):
    data = await request.json()
    uid = data.get("telegram_user_id")
    tx_hash = data.get("tx_hash")
    amount = data.get("amount")
    chain = data.get("chain") or CONFIG["DEFAULT_CHAIN"]

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
        invite = await _invite_link_one_click(context=ContextTypes.DEFAULT_TYPE(application=application))  # not used directly
    except Exception:
        invite = None

    try:
        real_invite = await _invite_link_one_click(context=await application.bot.get_context())
    except Exception:
        # fallback: simple export
        try:
            real_invite = await application.bot.export_chat_invite_link(chat_id=CONFIG["GROUP_VIP_ID"])
        except Exception:
            real_invite = None

    if real_invite:
        try:
            await application.bot.send_message(chat_id=int(uid), text=f"✅ Pagamento confirmado! Entre no VIP: {real_invite}")
        except Exception:
            logging.exception("Erro enviando invite")

    return JSONResponse({"ok": True})

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except Exception:
        raw = await request.body()
        try:
            import json as _json
            data = _json.loads(raw.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid payload")

    update = TgUpdate.de_json(data, application.bot)
    await application.process_update(update)
    return PlainTextResponse("ok", status_code=200)

@app.get("/")
async def root():
    return {"status": "online", "message": "Bot ready (crypto + schedules + packs)"}

@app.get("/ping")
async def ping():
    logging.info("[keepalive] tick")
    return PlainTextResponse("pong", status_code=200)

# =============== Startup: register handlers & jobs ===============
@app.on_event("startup")
async def on_startup():
    global bot
    await application.initialize()
    await application.start()
    bot = application.bot

    # Webhook
    global BASE_URL
    if not BASE_URL:
        # tenta WEBHOOK_URL legado
        legacy = os.getenv("WEBHOOK_URL")
        if legacy:
            BASE_URL = legacy.rstrip("/webhook")
    if not BASE_URL:
        raise RuntimeError("Defina BASE_URL (ex.: https://seu-servico.onrender.com)")
    webhook_url = BASE_URL.rstrip("/") + "/webhook"
    await bot.set_webhook(url=webhook_url)

    logging.basicConfig(level=logging.INFO)
    logging.info("Bot iniciado (cripto + schedules + packs).")

    # ===== Error handler =====
    application.add_error_handler(error_handler)

    # ===== Conversas /novopack =====
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("novopack", novopack_entry),
            CommandHandler("novopackvip", novopack_start_vip),
            CommandHandler("novopackfree", novopack_start_free),
        ],
        states={
            CHOOSE_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_choose_target)],
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
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_handler, group=0)

    # ===== Conversa /excluir_pack (com confirmação) =====
    excluir_conv = ConversationHandler(
        entry_points=[CommandHandler("excluir_pack", excluir_pack_cmd)],
        states={DELETE_PACK_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, excluir_pack_confirm)]},
        fallbacks=[],
        allow_reentry=True,
    )
    application.add_handler(excluir_conv, group=0)

    # ===== Handlers dos grupos de armazenamento =====
    # VIP storage
    application.add_handler(
        MessageHandler(
            filters.Chat(CONFIG["STORAGE_VIP_GROUP_ID"]) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler_vip
        ),
        group=1,
    )
    media_filter_vip = (
        filters.Chat(CONFIG["STORAGE_VIP_GROUP_ID"]) & (
            filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.AUDIO | filters.Document.ALL | filters.VOICE
        )
    )
    application.add_handler(MessageHandler(media_filter_vip, storage_media_handler_vip), group=1)

    # FREE storage
    application.add_handler(
        MessageHandler(
            filters.Chat(CONFIG["STORAGE_FREE_GROUP_ID"]) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler_free
        ),
        group=1,
    )
    media_filter_free = (
        filters.Chat(CONFIG["STORAGE_FREE_GROUP_ID"]) & (
            filters.PHOTO | filters.VIDEO | filters.ANIMATION | filters.AUDIO | filters.Document.ALL | filters.VOICE
        )
    )
    application.add_handler(MessageHandler(media_filter_free, storage_media_handler_free), group=1)

    # ===== Comandos gerais (disponíveis em qualquer chat) =====
    application.add_handler(CommandHandler("start", start_cmd), group=1)
    application.add_handler(CommandHandler("comandos", comandos_cmd), group=1)
    application.add_handler(CommandHandler("listar_comandos", listar_comandos_cmd), group=1)
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

    # Preço & teaser
    application.add_handler(CommandHandler("set_preco", set_preco_cmd), group=1)
    application.add_handler(CommandHandler("ver_preco", ver_preco_cmd), group=1)
    application.add_handler(CommandHandler("set_free_teaser", set_free_teaser_cmd), group=1)

    # ===== Job diário de envio de pack (persistente) =====
    tz = pytz.timezone("America/Sao_Paulo")

    async def _reschedule_daily_pack():
        for j in list(application.job_queue.jobs()):
            if j.name == "daily_pack":
                j.schedule_removal()
        hhmm = cfg_get("daily_pack_hhmm") or CONFIG["DAILY_PACK_HHMM"]
        h, m = parse_hhmm(hhmm)
        application.job_queue.run_daily(enviar_pack_vip_job, time=dt.time(hour=h, minute=m, tzinfo=tz), name="daily_pack")
        logging.info(f"Job diário de pack agendado para {hhmm} America/Sao_Paulo")

    async def set_pack_horario_cmd(update: TgUpdate, context: ContextTypes.DEFAULT_TYPE):
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

    application.add_handler(CommandHandler("set_pack_horario", set_pack_horario_cmd), group=1)

    await _reschedule_daily_pack()
    _register_all_scheduled_messages(application.job_queue)

    # ===== Keepalive a cada 4 minutos (evita Render dormir) =====
    async def keepalive_job(context: ContextTypes.DEFAULT_TYPE):
        import httpx
        try:
            url = BASE_URL.rstrip("/") + "/ping"
            async with httpx.AsyncClient(timeout=5.0) as client:
                r = await client.get(url)
                logging.info(f"[keepalive] {r.status_code} {r.text[:60]}")
        except Exception as e:
            logging.warning(f"[keepalive] erro: {e}")

    application.job_queue.run_repeating(keepalive_job, interval=dt.timedelta(minutes=4), first=dt.timedelta(seconds=10), name="keepalive")

    logging.info("Handlers e jobs registrados.")

# =============== Run ===============
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
