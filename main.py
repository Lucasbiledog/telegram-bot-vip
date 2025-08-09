# main.py
import os
import logging
import asyncio
import random
import datetime as dt
from typing import Optional, List

import pytz
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

import stripe
import uvicorn

from telegram import Update, InputMediaPhoto
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    JobQueue,
    filters,
)

# SQLAlchemy (local SQLite)
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# -------------------------
# Load env
# -------------------------
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
ADMIN_USER_IDS = {7123614866}  # seu id (já configurado)
# Defaults (use .env to override)
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))
GROUP_FREE_ID = os.getenv("GROUP_FREE_ID")  # opcional
if GROUP_FREE_ID:
    GROUP_FREE_ID = int(GROUP_FREE_ID)
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no .env")

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

# -------------------------
# App, bot, DB
# -------------------------
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None

# SQLite (local file bot_data.db)
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./bot_data.db")
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


# -------------------------
# Database models
# -------------------------
class Pack(Base):
    __tablename__ = "packs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    header_message_id = Column(Integer, nullable=True, unique=True)  # message id of header (if available)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
    sent = Column(Boolean, default=False)

    files = relationship("PackFile", back_populates="pack", cascade="all, delete-orphan")


class PackFile(Base):
    __tablename__ = "pack_files"
    id = Column(Integer, primary_key=True, index=True)
    pack_id = Column(Integer, ForeignKey("packs.id", ondelete="CASCADE"))
    file_id = Column(String, nullable=False)           # Telegram file_id
    file_unique_id = Column(String, nullable=True)     # optional unique id
    file_type = Column(String, nullable=True)         # photo, document, video, audio
    role = Column(String, nullable=True)              # 'preview' or 'file'
    file_name = Column(String, nullable=True)         # original filename (if document)
    added_at = Column(DateTime, default=dt.datetime.utcnow)

    pack = relationship("Pack", back_populates="files")


def init_db():
    Base.metadata.create_all(bind=engine)


init_db()

# -------------------------
# Helpers DB
# -------------------------
def create_pack(title: str, header_message_id: Optional[int] = None) -> Pack:
    session = SessionLocal()
    try:
        p = Pack(title=title.strip(), header_message_id=header_message_id)
        session.add(p)
        session.commit()
        session.refresh(p)
        return p
    finally:
        session.close()


def get_pack_by_header(message_id: int) -> Optional[Pack]:
    session = SessionLocal()
    try:
        return session.query(Pack).filter(Pack.header_message_id == message_id).first()
    finally:
        session.close()


def add_file_to_pack(pack: Pack, file_id: str, file_unique_id: Optional[str], file_type: str, role: str, file_name: Optional[str] = None):
    session = SessionLocal()
    try:
        p = session.query(Pack).filter(Pack.id == pack.id).first()
        pf = PackFile(
            pack_id=p.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_type=file_type,
            role=role,
            file_name=file_name,
        )
        session.add(pf)
        session.commit()
        return pf
    finally:
        session.close()


def get_next_unsent_pack() -> Optional[Pack]:
    session = SessionLocal()
    try:
        p = session.query(Pack).filter(Pack.sent == False).order_by(Pack.created_at.asc()).first()
        return p
    finally:
        session.close()


def mark_pack_sent(pack_id: int):
    session = SessionLocal()
    try:
        p = session.query(Pack).filter(Pack.id == pack_id).first()
        if p:
            p.sent = True
            session.commit()
    finally:
        session.close()


def list_packs() -> List[Pack]:
    session = SessionLocal()
    try:
        return session.query(Pack).order_by(Pack.created_at.desc()).all()
    finally:
        session.close()


# -------------------------
# Message handlers: build packs in real time
# -------------------------
# Behavior required: user must post a TITLE message (text) in STORAGE_GROUP_ID.
# Then, other messages with media should be sent as *replies* to that title message.
# Handler will:
#  - on text messages in STORAGE_GROUP_ID: create a new Pack (title = text, header_message_id = message_id)
#  - on media messages in STORAGE_GROUP_ID that are replies to a header: add file to that pack
#
# NOTE: Bot must be added to STORAGE_GROUP_ID BEFORE assets are posted, or reposts must be made.

async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages in storage group -> create Pack"""
    msg = update.message
    if not msg or msg.chat.id != STORAGE_GROUP_ID:
        return
    title = (msg.text or "").strip()
    if not title:
        return
    # create pack (if header_message_id already exists, ignore)
    existing = get_pack_by_header(msg.message_id)
    if existing:
        await msg.reply_text("Pack já registrado.")
        return
    p = create_pack(title=title, header_message_id=msg.message_id)
    await msg.reply_text(f"Pack registrado: *{p.title}* (id {p.id})", parse_mode="Markdown")


async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photos/documents/videos posted as replies to a pack header -> add file to pack"""
    msg = update.message
    if not msg or msg.chat.id != STORAGE_GROUP_ID:
        return

    # must be a reply to header
    reply = msg.reply_to_message
    if not reply or not reply.message_id:
        # Optionally: find most recent pack header earlier in DB (within X minutes)
        # For simplicity, instruct user to reply to header
        await msg.reply_text("Envie este arquivo como *resposta* ao título do pack (mensagem de texto).", parse_mode="Markdown")
        return

    pack = get_pack_by_header(reply.message_id)
    if not pack:
        await msg.reply_text("Cabeçalho do pack não encontrado. Certifique-se de que você está respondendo ao título do pack.")
        return

    # detect file type and file_id
    file_id = None
    file_unique_id = None
    file_type = None
    file_name = None
    role = "file"

    if msg.photo:
        biggest = msg.photo[-1]
        file_id = biggest.file_id
        file_unique_id = getattr(biggest, "file_unique_id", None)
        file_type = "photo"
        role = "preview"
    elif msg.document:
        file_id = msg.document.file_id
        file_unique_id = getattr(msg.document, "file_unique_id", None)
        file_type = "document"
        file_name = getattr(msg.document, "file_name", None)
        role = "file"
    elif msg.video:
        file_id = msg.video.file_id
        file_unique_id = getattr(msg.video, "file_unique_id", None)
        file_type = "video"
        role = "preview"
    elif msg.audio:
        file_id = msg.audio.file_id
        file_unique_id = getattr(msg.audio, "file_unique_id", None)
        file_type = "audio"
        role = "file"
    else:
        await msg.reply_text("Tipo de mídia não suportado. Use foto, documento ou vídeo.")
        return

    add_file_to_pack(pack, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role, file_name=file_name)
    await msg.reply_text(f"Arquivo adicionado ao pack *{pack.title}*.", parse_mode="Markdown")


# -------------------------
# Sending function (JobQueue friendly)
# -------------------------
async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue calls this (passes context). Select next unsent pack and send it to VIP."""
    try:
        pack = get_next_unsent_pack()
        if not pack:
            logging.info("Nenhum pack pendente para envio.")
            return

        session = SessionLocal()
        try:
            p = session.query(Pack).filter(Pack.id == pack.id).first()
            files = session.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()
        finally:
            session.close()

        if not files:
            logging.warning(f"Pack {p.title} sem arquivos.")
            mark_pack_sent(p.id)
            return

        # Separate previews and files preserving order
        previews = [f for f in files if f.role == "preview"]
        docs = [f for f in files if f.role == "file"]

        # Send title as first caption on first media (if possible)
        sent_first = False

        # Send previews as media_group if there are >=2 photos
        photo_file_ids = [f.file_id for f in previews if f.file_type == "photo"]
        other_previews = [f for f in previews if f.file_type != "photo"]

        if photo_file_ids:
            media = []
            # build InputMediaPhoto list; caption on the first
            from telegram import InputMediaPhoto
            for i, fid in enumerate(photo_file_ids):
                if i == 0:
                    media.append(InputMediaPhoto(media=fid, caption=p.title))
                else:
                    media.append(InputMediaPhoto(media=fid))
            try:
                await context.bot.send_media_group(chat_id=GROUP_VIP_ID, media=media)
                sent_first = True
            except Exception as e:
                logging.warning(f"Falha ao enviar media_group: {e}")
                # fallback: send individually
                for i, fid in enumerate(photo_file_ids):
                    cap = p.title if (i == 0) else None
                    await context.bot.send_photo(chat_id=GROUP_VIP_ID, photo=fid, caption=cap)
                    sent_first = True

        # Send other previews (video etc.)
        for i, f in enumerate(other_previews):
            cap = p.title if (not sent_first) else None
            try:
                if f.file_type == "video":
                    await context.bot.send_video(chat_id=GROUP_VIP_ID, video=f.file_id, caption=cap)
                else:
                    await context.bot.send_photo(chat_id=GROUP_VIP_ID, photo=f.file_id, caption=cap)
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando preview {f.id}: {e}")

        # Now send docs/files
        for f in docs:
            try:
                # put title as caption on first doc if not already put
                cap = p.title if (not sent_first) else None
                await context.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando doc {f.file_name or f.id}: {e}")

        mark_pack_sent(p.id)
        logging.info(f"Pack enviado e marcado como sent: {p.title}")

    except Exception as e:
        logging.exception("Erro em enviar_pack_vip_job")


# -------------------------
# Commands
# -------------------------
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala! Eu gerencio packs VIP. Publique títulos no grupo de assets e responda com fotos/arquivos.")


async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Seu nome: {user.full_name}\nSeu ID: {user.id}\nID deste chat: {chat_id}")


async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if caller not in ADMIN_USER_IDS:
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return
    await enviar_pack_vip_job(context)
    await update.message.reply_text("Envio do pack simulado (manual).")


async def testepagamento_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if caller not in ADMIN_USER_IDS:
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return
    db = SessionLocal()
    try:
        validade = dt.datetime.utcnow() + dt.timedelta(days=7)
        key = f"vip_validade:{caller}"
        # store in Config table if exists in your original DB, otherwise skip
        try:
            cfg = db.query(Config).filter(Config.key == key).first()
            if cfg:
                cfg.value = validade.isoformat()
            else:
                cfg = Config(key=key, value=validade.isoformat())
                db.add(cfg)
            db.commit()
        except Exception:
            db.rollback()
            logging.exception("Erro ao gravar validade no Config (se existir)")
    finally:
        db.close()
    try:
        invite = await context.bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
    except Exception:
        invite = "Não foi possível gerar invite (verifique permissões)."
    await update.message.reply_text("Teste de pagamento: VIP concedido por 7 dias (simulado).")
    try:
        await context.bot.send_message(chat_id=caller, text=f"Você recebeu VIP (teste). Link: {invite}")
    except Exception:
        pass


async def listar_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if caller not in ADMIN_USER_IDS:
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return
    packs = list_packs()
    if not packs:
        await update.message.reply_text("Nenhum pack registrado.")
        return
    lines = []
    for p in packs:
        status = "ENVIADO" if p.sent else "PENDENTE"
        lines.append(f"[{p.id}] {p.title} — {status} — criado {p.created_at.strftime('%Y-%m-%d %H:%M')}")
    text = "\n".join(lines)
    await update.message.reply_text(text)


# -------------------------
# Stripe webhook (kept minimal)
# -------------------------
@app.post("/stripe_webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="Stripe webhook secret not set")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        logging.error("Invalid Stripe payload")
        raise HTTPException(status_code=400, detail=str(e))
    except stripe.error.SignatureVerificationError as e:
        logging.error("Invalid Stripe signature")
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session_obj = event["data"]["object"]
        telegram_user_id = session_obj.get("metadata", {}).get("telegram_user_id")
        if telegram_user_id:
            # example: add vip to Config table or similar
            session_db = SessionLocal()
            try:
                validade = dt.datetime.utcnow() + dt.timedelta(days=30)
                key = f"vip_validade:{telegram_user_id}"
                try:
                    cfg = session_db.query(Config).filter(Config.key == key).first()
                    if cfg:
                        cfg.value = validade.isoformat()
                    else:
                        cfg = Config(key=key, value=validade.isoformat())
                        session_db.add(cfg)
                    session_db.commit()
                except Exception:
                    session_db.rollback()
                try:
                    invite = await bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                    await bot.send_message(chat_id=int(telegram_user_id), text=f"✅ Pagamento confirmado! Entre no VIP: {invite}")
                except Exception:
                    logging.exception("Erro enviando invite")
            finally:
                session_db.close()
    return PlainTextResponse("", status_code=200)


# -------------------------
# Webhook receiver
# -------------------------
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
async def root():
    return {"status": "online", "message": "Bot ready"}


# -------------------------
# Startup: register handlers & jobs
# -------------------------
@app.on_event("startup")
async def on_startup():
    global bot
    await application.initialize()
    await application.start()
    bot = application.bot

    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL não definido no .env")
    await bot.set_webhook(url=WEBHOOK_URL)

    logging.info("Bot iniciado.")

    # handlers to capture assets posted live in storage group
    # text messages = new pack header
    application.add_handler(MessageHandler(filters.Chat(STORAGE_GROUP_ID) & filters.TEXT & ~filters.COMMAND, storage_text_handler))
    # media as replies to header = add to pack
    media_filter = filters.Chat(STORAGE_GROUP_ID) & (filters.PHOTO | filters.DOCUMENT | filters.VIDEO | filters.AUDIO)
    application.add_handler(MessageHandler(media_filter, storage_media_handler))

    # commands
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("getid", getid_cmd))
    application.add_handler(CommandHandler("simularvip", simularvip_cmd))
    application.add_handler(CommandHandler("testepagamento", testepagamento_cmd))
    application.add_handler(CommandHandler("listar_packs", listar_packs_cmd))

    # JobQueue: schedule daily send at 09:00 America/Sao_Paulo
    tz = pytz.timezone("America/Sao_Paulo")
    job_queue: JobQueue = application.job_queue
    job_queue.run_daily(enviar_pack_vip_job, time=dt.time(hour=9, minute=0, tzinfo=tz), name="daily_pack_vip")

    # start VIP expiry checker background
    asyncio.create_task(verificar_vips())

    logging.info("Handlers e jobs registrados.")


# -------------------------
# Run
# -------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
