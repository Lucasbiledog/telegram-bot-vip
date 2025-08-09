# main.py
import os
import logging
import asyncio
import datetime as dt
from typing import Optional, List, Dict, Any

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
    ConversationHandler,
    filters,
)

# SQLAlchemy (SQLite local)
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

# =========================
# ENV / CONFIG
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")

# seu(s) admin(s)
ADMIN_USER_IDS = {7123614866}

# grupos
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))   # grupo onde você posta os packs
GROUP_VIP_ID     = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))    # grupo VIP
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no .env")

if STRIPE_API_KEY:
    stripe.api_key = STRIPE_API_KEY

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
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Pack(Base):
    __tablename__ = "packs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    header_message_id = Column(Integer, nullable=True, unique=True)
    created_at = Column(DateTime, default=dt.datetime.utcnow)
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
    added_at = Column(DateTime, default=dt.datetime.utcnow)

    pack = relationship("Pack", back_populates="files")

def init_db():
    Base.metadata.create_all(bind=engine)

init_db()

# =========================
# DB helpers
# =========================
def create_pack(title: str, header_message_id: Optional[int] = None) -> Pack:
    s = SessionLocal()
    try:
        p = Pack(title=title.strip(), header_message_id=header_message_id)
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

def add_file_to_pack(pack: Pack, file_id: str, file_unique_id: Optional[str], file_type: str, role: str, file_name: Optional[str] = None):
    s = SessionLocal()
    try:
        p = s.query(Pack).filter(Pack.id == pack.id).first()
        pf = PackFile(
            pack_id=p.id,
            file_id=file_id,
            file_unique_id=file_unique_id,
            file_type=file_type,
            role=role,
            file_name=file_name,
        )
        s.add(pf)
        s.commit()
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

# =========================
# STORAGE GROUP handlers (opcional, continua funcionando)
# =========================
async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.chat.id != STORAGE_GROUP_ID:
        return
    title = (msg.text or "").strip()
    if not title:
        return
    if get_pack_by_header(msg.message_id):
        await msg.reply_text("Pack já registrado.")
        return
    p = create_pack(title=title, header_message_id=msg.message_id)
    await msg.reply_text(f"Pack registrado: *{p.title}* (id {p.id})", parse_mode="Markdown")

async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or msg.chat.id != STORAGE_GROUP_ID:
        return

    reply = msg.reply_to_message
    if not reply or not reply.message_id:
        await msg.reply_text("Envie este arquivo como *resposta* ao título do pack.", parse_mode="Markdown")
        return

    pack = get_pack_by_header(reply.message_id)
    if not pack:
        await msg.reply_text("Cabeçalho do pack não encontrado. Responda à mensagem de título.")
        return

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
    elif msg.video:
        file_id = msg.video.file_id
        file_unique_id = getattr(msg.video, "file_unique_id", None)
        file_type = "video"
        role = "preview"
    elif msg.animation:
        file_id = msg.animation.file_id
        file_unique_id = getattr(msg.animation, "file_unique_id", None)
        file_type = "animation"
        role = "preview"
    elif msg.document:
        file_id = msg.document.file_id
        file_unique_id = getattr(msg.document, "file_unique_id", None)
        file_type = "document"
        file_name = getattr(msg.document, "file_name", None)
        role = "file"
    elif msg.audio:
        file_id = msg.audio.file_id
        file_unique_id = getattr(msg.audio, "file_unique_id", None)
        file_type = "audio"
        role = "file"
    elif msg.voice:
        file_id = msg.voice.file_id
        file_unique_id = getattr(msg.voice, "file_unique_id", None)
        file_type = "voice"
        role = "file"
    else:
        await msg.reply_text("Tipo de mídia não suportado (use foto/vídeo/animação como preview, documento/áudio/voice como arquivo).")
        return

    add_file_to_pack(pack, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role, file_name=file_name)
    await msg.reply_text(f"Arquivo adicionado ao pack *{pack.title}*.", parse_mode="Markdown")

# =========================
# ENVIO DO PACK (JobQueue) — retorna status p/ debug
# =========================
async def enviar_pack_vip_job(context: ContextTypes.DEFAULT_TYPE) -> str:
    try:
        pack = get_next_unsent_pack()
        if not pack:
            logging.info("Nenhum pack pendente para envio.")
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

        # Fotos como álbum quando >=2
        photo_ids = [f.file_id for f in previews if f.file_type == "photo"]
        if photo_ids:
            media = []
            for i, fid in enumerate(photo_ids):
                if i == 0:
                    media.append(InputMediaPhoto(media=fid, caption=p.title))
                else:
                    media.append(InputMediaPhoto(media=fid))
            try:
                await context.bot.send_media_group(chat_id=GROUP_VIP_ID, media=media)
                sent_first = True
                sent_counts["photos"] += len(photo_ids)
            except Exception as e:
                logging.warning(f"Falha send_media_group: {e}. Enviando individual.")
                for i, fid in enumerate(photo_ids):
                    cap = p.title if i == 0 else None
                    await context.bot.send_photo(chat_id=GROUP_VIP_ID, photo=fid, caption=cap)
                    sent_first = True
                    sent_counts["photos"] += 1

        # Outros previews (vídeo/animação)
        for f in [f for f in previews if f.file_type in ("video", "animation")]:
            cap = p.title if not sent_first else None
            try:
                if f.file_type == "video":
                    await context.bot.send_video(chat_id=GROUP_VIP_ID, video=f.file_id, caption=cap)
                    sent_counts["videos"] += 1
                elif f.file_type == "animation":
                    await context.bot.send_animation(chat_id=GROUP_VIP_ID, animation=f.file_id, caption=cap)
                    sent_counts["animations"] += 1
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando preview {f.id}: {e}")

        # Arquivos (documento/áudio/voice)
        for f in docs:
            try:
                cap = p.title if not sent_first else None
                if f.file_type == "document":
                    await context.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                    sent_counts["docs"] += 1
                elif f.file_type == "audio":
                    await context.bot.send_audio(chat_id=GROUP_VIP_ID, audio=f.file_id, caption=cap)
                    sent_counts["audios"] += 1
                elif f.file_type == "voice":
                    await context.bot.send_voice(chat_id=GROUP_VIP_ID, voice=f.file_id, caption=cap)
                    sent_counts["voices"] += 1
                else:
                    await context.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                    sent_counts["docs"] += 1
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando arquivo {f.file_name or f.id}: {e}")

        mark_pack_sent(p.id)
        logging.info(f"Pack enviado: {p.title}")

        return (
            f"✅ Enviado pack '{p.title}'. "
            f"Previews: {sent_counts['photos']} fotos, {sent_counts['videos']} vídeos, {sent_counts['animations']} animações. "
            f"Arquivos: {sent_counts['docs']} docs, {sent_counts['audios']} áudios, {sent_counts['voices']} voices."
        )

    except Exception as e:
        logging.exception("Erro no enviar_pack_vip_job")
        return f"❌ Erro no envio: {e!r}"

# =========================
# COMMANDS BÁSICOS
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala! Eu gerencio packs VIP. Use /novopack para cadastrar via conversa ou publique no grupo de assets com título + replies.")

async def getid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"Seu nome: {user.full_name}\nSeu ID: {user.id}\nID deste chat: {chat_id}")

async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return
    status = await enviar_pack_vip_job(context)
    await update.message.reply_text(status)

async def listar_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return
    s = SessionLocal()
    try:
        packs = s.query(Pack).order_by(Pack.created_at.desc()).all()
        if not packs:
            await update.message.reply_text("Nenhum pack registrado.")
            return
        lines = []
        for p in packs:
            previews = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "preview").count()
            docs    = s.query(PackFile).filter(PackFile.pack_id == p.id, PackFile.role == "file").count()
            status = "ENVIADO" if p.sent else "PENDENTE"
            lines.append(f"[{p.id}] {p.title} — {status} — previews:{previews} arquivos:{docs} — {p.created_at.strftime('%d/%m %H:%M')}")
        await update.message.reply_text("\n".join(lines))
    finally:
        s.close()

async def pack_info_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /pack_info <id>")
        return
    try:
        pid = int(context.args[0])
    except:
        await update.message.reply_text("ID inválido.")
        return
    s = SessionLocal()
    try:
        p = s.query(Pack).filter(Pack.id == pid).first()
        if not p:
            await update.message.reply_text("Pack não encontrado.")
            return
        files = s.query(PackFile).filter(PackFile.pack_id == p.id).order_by(PackFile.id.asc()).all()
        if not files:
            await update.message.reply_text(f"Pack '{p.title}' não possui arquivos.")
            return
        lines = [f"Pack [{p.id}] {p.title} — {'ENVIADO' if p.sent else 'PENDENTE'}"]
        for f in files:
            lines.append(f" - item #{f.id} | {f.file_type} ({f.role}) {f.file_name or ''}")
        await update.message.reply_text("\n".join(lines))
    finally:
        s.close()

async def remover_item_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_USER_IDS:
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /remover_item <id>")
        return
    try:
        item_id = int(context.args[0])
    except:
        await update.message.reply_text("ID inválido. Use: /remover_item <id>")
        return

    s = SessionLocal()
    try:
        item = s.query(PackFile).filter(PackFile.id == item_id).first()
        if not item:
            await update.message.reply_text("Item não encontrado.")
            return
        pack = s.query(Pack).filter(Pack.id == item.pack_id).first()
        s.delete(item)
        s.commit()
        await update.message.reply_text(f"✅ Item #{item_id} removido do pack '{pack.title if pack else '?'}'.")
    except Exception as e:
        s.rollback()
        logging.exception("Erro ao remover item")
        await update.message.reply_text(f"❌ Erro ao remover item: {e}")
    finally:
        s.close()

# =========================
# NOVO: Conversa /novopack com confirmação passo a passo
# =========================
# Estados
TITLE, CONFIRM_TITLE, PREVIEWS, FILES, CONFIRM_SAVE = range(5)

def _require_admin(update: Update) -> bool:
    return update.effective_user and update.effective_user.id in ADMIN_USER_IDS

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—")
    previews = user_data.get("previews", [])
    files = user_data.get("files", [])
    # gerar nomes “amigáveis”
    preview_names = []
    p_index = 1
    for it in previews:
        label = "Foto" if it["file_type"] == "photo" else ("Vídeo" if it["file_type"] == "video" else "Animação")
        preview_names.append(f"{label} {p_index}")
        p_index += 1
    file_names = []
    f_index = 1
    for it in files:
        if it["file_type"] == "document" and it.get("file_name"):
            file_names.append(it["file_name"])
        else:
            # áudio/voice não tem nome de arquivo
            file_names.append(f"{it['file_type'].capitalize()} {f_index}")
            f_index += 1
    text = [
        f"📦 *Resumo do Pack*",
        f"• Nome: *{title}*",
        f"• Previews ({len(previews)}): " + (", ".join(preview_names) if preview_names else "—"),
        f"• Arquivos ({len(files)}): " + (", ".join(file_names) if file_names else "—"),
        "",
        "Deseja salvar? *(sim/não)*"
    ]
    return "\n".join(text)

async def novopack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update):
        await update.message.reply_text("Apenas admins podem usar este comando.")
        return ConversationHandler.END
    context.user_data.clear()
    await update.message.reply_text(
        "🧩 Vamos criar um novo pack!\n\n"
        "1) Me diga o **título do pack** (apenas texto)."
    )
    return TITLE

async def novopack_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = (update.message.text or "").strip()
    if not title:
        await update.message.reply_text("Título vazio. Envie um texto com o título do pack.")
        return TITLE
    context.user_data["title_candidate"] = title
    await update.message.reply_text(f"Confirma o nome: *{title}*? (sim/não)", parse_mode="Markdown")
    return CONFIRM_TITLE

async def novopack_confirm_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.message.reply_text("Por favor, responda *sim* ou *não*.", parse_mode="Markdown")
        return CONFIRM_TITLE
    if answer in ("não", "nao"):
        await update.message.reply_text("Ok! Envie o *novo título* do pack.", parse_mode="Markdown")
        return TITLE
    # sim -> fixa título e inicia coleções temporárias
    context.user_data["title"] = context.user_data.get("title_candidate")
    context.user_data["previews"] = []
    context.user_data["files"] = []
    await update.message.reply_text(
        "2) Envie as **PREVIEWS** (📷 fotos / 🎞 vídeos / 🎞 animações).\n"
        "Envie quantas quiser. Quando terminar, mande /proximo.",
        parse_mode="Markdown"
    )
    return PREVIEWS

async def novopack_collect_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    previews: List[Dict[str, Any]] = context.user_data.get("previews", [])
    if msg.photo:
        biggest = msg.photo[-1]
        previews.append({"file_id": biggest.file_id, "file_type": "photo"})
        await update.message.reply_text("✅ *Foto cadastrada*. Envie mais ou /proximo.", parse_mode="Markdown")
    elif msg.video:
        previews.append({"file_id": msg.video.file_id, "file_type": "video"})
        await update.message.reply_text("✅ *Preview (vídeo) cadastrado*. Envie mais ou /proximo.", parse_mode="Markdown")
    elif msg.animation:
        previews.append({"file_id": msg.animation.file_id, "file_type": "animation"})
        await update.message.reply_text("✅ *Preview (animação) cadastrado*. Envie mais ou /proximo.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Envie *foto/vídeo/animação* como preview, ou /proximo para ir aos arquivos.", parse_mode="Markdown")
    context.user_data["previews"] = previews
    return PREVIEWS

async def novopack_next_to_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("title"):
        await update.message.reply_text("Título não encontrado. Use /cancelar e recomece com /novopack.")
        return ConversationHandler.END
    await update.message.reply_text(
        "3) Agora envie os **ARQUIVOS** (📄 documentos / 🎵 áudio / 🎙 voice).\n"
        "Envie quantos quiser. Quando terminar, mande /finalizar.",
        parse_mode="Markdown"
    )
    return FILES

async def novopack_collect_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    files: List[Dict[str, Any]] = context.user_data.get("files", [])
    if msg.document:
        files.append({
            "file_id": msg.document.file_id,
            "file_type": "document",
            "file_name": getattr(msg.document, "file_name", None),
        })
        await update.message.reply_text("✅ *Arquivo cadastrado*. Envie mais ou /finalizar.", parse_mode="Markdown")
    elif msg.audio:
        files.append({"file_id": msg.audio.file_id, "file_type": "audio"})
        await update.message.reply_text("✅ *Áudio cadastrado*. Envie mais ou /finalizar.", parse_mode="Markdown")
    elif msg.voice:
        files.append({"file_id": msg.voice.file_id, "file_type": "voice"})
        await update.message.reply_text("✅ *Voice cadastrado*. Envie mais ou /finalizar.", parse_mode="Markdown")
    else:
        await update.message.reply_text("Envie *documento/áudio/voice*, ou /finalizar para revisar e salvar.", parse_mode="Markdown")
    context.user_data["files"] = files
    return FILES

async def novopack_finish_review(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # mostra o resumo e pede confirmação final
    summary = _summary_from_session(context.user_data)
    await update.message.reply_text(summary, parse_mode="Markdown")
    return CONFIRM_SAVE

async def novopack_confirm_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    answer = (update.message.text or "").strip().lower()
    if answer not in ("sim", "não", "nao"):
        await update.message.reply_text("Responda *sim* para salvar ou *não* para cancelar.", parse_mode="Markdown")
        return CONFIRM_SAVE
    if answer in ("não", "nao"):
        context.user_data.clear()
        await update.message.reply_text("Operação cancelada. Nada foi salvo.")
        return ConversationHandler.END

    # salvar no DB agora
    title = context.user_data.get("title")
    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])

    p = create_pack(title=title, header_message_id=None)
    for it in previews:
        add_file_to_pack(
            p,
            file_id=it["file_id"],
            file_unique_id=None,
            file_type=it["file_type"],
            role="preview",
            file_name=None,
        )
    for it in files:
        add_file_to_pack(
            p,
            file_id=it["file_id"],
            file_unique_id=None,
            file_type=it["file_type"],
            role="file",
            file_name=it.get("file_name"),
        )

    context.user_data.clear()
    await update.message.reply_text(f"🎉 *{title}* cadastrado com sucesso!", parse_mode="Markdown")
    return ConversationHandler.END

async def novopack_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("Operação cancelada.")
    return ConversationHandler.END

# =========================
# Stripe webhook (mínimo)
# =========================
@app.post("/stripe_webhook")
async def stripe_webhook(request: Request):
    if not STRIPE_WEBHOOK_SECRET:
        raise HTTPException(status_code=400, detail="Stripe webhook secret não configurado")
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
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
            try:
                invite = await bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                await bot.send_message(chat_id=int(telegram_user_id), text=f"✅ Pagamento confirmado! Entre no VIP: {invite}")
            except Exception:
                logging.exception("Erro enviando invite")
    return PlainTextResponse("", status_code=200)

# =========================
# Telegram webhook receiver
# =========================
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

# =========================
# Startup: register handlers & jobs
# =========================
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

    # ===== Conversa /novopack (registrar ANTES dos handlers de storage) =====
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("novopack", novopack_start)],
        states={
            TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_title),
            ],
            CONFIRM_TITLE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_confirm_title),
            ],
            PREVIEWS: [
                CommandHandler("proximo", novopack_next_to_files),
                MessageHandler(filters.PHOTO | filters.VIDEO | filters.ANIMATION, novopack_collect_previews),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Agora envie PREVIEWS (foto/vídeo/animação) ou /proximo.")),
            ],
            FILES: [
                CommandHandler("finalizar", novopack_finish_review),
                MessageHandler(filters.Document.ALL | filters.AUDIO | filters.VOICE, novopack_collect_files),
                MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: u.message.reply_text("Agora envie ARQUIVOS (documento/áudio/voice) ou /finalizar.")),
            ],
            CONFIRM_SAVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_confirm_save),
            ],
        },
        fallbacks=[CommandHandler("cancelar", novopack_cancel)],
        allow_reentry=True,
    )
    application.add_handler(conv_handler)

    # ===== Handlers do grupo de armazenamento (opcionais) =====
    application.add_handler(
        MessageHandler(
            filters.Chat(STORAGE_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler
        )
    )
    media_filter = (
        filters.Chat(STORAGE_GROUP_ID)
        & (
            filters.PHOTO
            | filters.VIDEO
            | filters.ANIMATION
            | filters.AUDIO
            | filters.Document.ALL
            | filters.VOICE
        )
    )
    application.add_handler(MessageHandler(media_filter, storage_media_handler))

    # ===== Comandos gerais =====
    application.add_handler(CommandHandler("start", start_cmd))
    application.add_handler(CommandHandler("getid", getid_cmd))
    application.add_handler(CommandHandler("simularvip", simularvip_cmd))
    application.add_handler(CommandHandler("listar_packs", listar_packs_cmd))
    application.add_handler(CommandHandler("pack_info", pack_info_cmd))
    application.add_handler(CommandHandler("remover_item", remover_item_cmd))

    # ===== Job diário às 09:00 America/Sao_Paulo =====
    tz = pytz.timezone("America/Sao_Paulo")
    job_queue: JobQueue = application.job_queue
    job_queue.run_daily(enviar_pack_vip_job, time=dt.time(hour=9, minute=0, tzinfo=tz), name="daily_pack_vip")

    logging.info("Handlers e jobs registrados.")

# =========================
# Run
# =========================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
