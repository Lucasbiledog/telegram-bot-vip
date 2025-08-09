# main.py
import os
import logging
import asyncio
import datetime as dt
from typing import Optional, List, Dict, Any
import html

import pytz
from dotenv import load_dotenv

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse, JSONResponse

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

# SQLAlchemy
from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey, Text
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

# =========================
# ENV / CONFIG
# =========================
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# grupos
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))   # grupo com os assets
GROUP_VIP_ID     = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))    # grupo VIP
PORT = int(os.getenv("PORT", 8000))

# Pagamento cripto
WALLET_ADDRESS = os.getenv("WALLET_ADDRESS", "").strip()
CHAIN_NAME = os.getenv("CHAIN_NAME", "Polygon").strip()

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido no .env")

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

# ---- Admins dinâmicos ----
class Admin(Base):
    __tablename__ = "admins"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, unique=True, index=True)
    added_at = Column(DateTime, default=now_utc)

# ---- Packs ----
class Pack(Base):
    __tablename__ = "packs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
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
    user_id = Column(Integer, index=True)
    username = Column(String, nullable=True)
    tx_hash = Column(String, unique=True, index=True)
    chain = Column(String, default=CHAIN_NAME)
    amount = Column(String, nullable=True)  # opcional (texto)
    status = Column(String, default="pending")  # pending | approved | rejected
    notes = Column(Text, nullable=True)  # livre
    created_at = Column(DateTime, default=now_utc)
    decided_at = Column(DateTime, nullable=True)

def init_db():
    Base.metadata.create_all(bind=engine)
    # Garante que existe pelo menos 1 admin inicial (coloque o seu ID aqui na primeira execução, se quiser)
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

# =========================
# STORAGE GROUP handlers
# =========================
async def storage_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.id != STORAGE_GROUP_ID:
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

    if update.effective_user and not is_admin(update.effective_user.id):
        return

    if get_pack_by_header(msg.message_id):
        await msg.reply_text("Pack já registrado.")
        return

    p = create_pack(title=title, header_message_id=msg.message_id)
    await msg.reply_text(f"Pack registrado: <b>{esc(p.title)}</b> (id {p.id})", parse_mode="HTML")

async def storage_media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    if not msg or msg.chat.id != STORAGE_GROUP_ID:
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
        await msg.reply_text("Tipo de mídia não suportado.", parse_mode="HTML")
        return

    add_file_to_pack(pack_id=pack.id, file_id=file_id, file_unique_id=file_unique_id, file_type=file_type, role=role, file_name=visible_name)
    await msg.reply_text(f"Item adicionado ao pack <b>{esc(pack.title)}</b>.", parse_mode="HTML")

# =========================
# ENVIO DO PACK (JobQueue)
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

        photo_ids = [f.file_id for f in previews if f.file_type == "photo"]
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
                sent_counts["photos"] += len(photo_ids)
            except Exception as e:
                logging.warning(f"Falha send_media_group: {e}. Enviando individual.")
                for i, fid in enumerate(photo_ids):
                    cap = pack.title if i == 0 else None
                    await context.application.bot.send_photo(chat_id=GROUP_VIP_ID, photo=fid, caption=cap)
                    sent_first = True
                    sent_counts["photos"] += 1

        for f in [f for f in previews if f.file_type in ("video", "animation")]:
            cap = pack.title if not sent_first else None
            try:
                if f.file_type == "video":
                    await context.application.bot.send_video(chat_id=GROUP_VIP_ID, video=f.file_id, caption=cap)
                    sent_counts["videos"] += 1
                elif f.file_type == "animation":
                    await context.application.bot.send_animation(chat_id=GROUP_VIP_ID, animation=f.file_id, caption=cap)
                    sent_counts["animations"] += 1
                sent_first = True
            except Exception as e:
                logging.warning(f"Erro enviando preview {f.id}: {e}")

        for f in docs:
            try:
                cap = pack.title if not sent_first else None
                if f.file_type == "document":
                    await context.application.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
                    sent_counts["docs"] += 1
                elif f.file_type == "audio":
                    await context.application.bot.send_audio(chat_id=GROUP_VIP_ID, audio=f.file_id, caption=cap)
                    sent_counts["audios"] += 1
                elif f.file_type == "voice":
                    await context.application.bot.send_voice(chat_id=GROUP_VIP_ID, voice=f.file_id, caption=cap)
                    sent_counts["voices"] += 1
                else:
                    await context.application.bot.send_document(chat_id=GROUP_VIP_ID, document=f.file_id, caption=cap)
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
# COMMANDS BÁSICOS & ADMIN
# =========================
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.effective_message
    text = (
        "Fala! Eu gerencio packs VIP e pagamentos via MetaMask.\n"
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
        "• /pagar — instruções de pagamento (MetaMask)",
        "• /tx <hash> — registrar o hash da transação",
        "",
        "🧩 Packs (privado):",
        "• /novopack — cadastrar pack (título, previews e arquivos)",
        "",
    ]
    adm = [
        "🛠 <b>Admin</b>",
        "• /simularvip — força o envio do próximo pack pendente",
        "• /listar_packs — lista packs",
        "• /pack_info <id> — detalhes do pack",
        "• /excluir_item <id_item> — remove item do pack",
        "• /excluir_pack [<id>] — remove pack (com confirmação)",
        "• /set_pendente <id> — marca pack como pendente",
        "• /set_enviado <id> — marca pack como enviado",
        "• /limpar_chat <N> — apaga últimas N mensagens (melhor esforço)",
        "• /mudar_nome <novo nome> — muda o nome do bot",
        "• /add_admin <user_id> — adiciona admin",
        "• /rem_admin <user_id> — remove admin",
        "• /listar_admins — lista admins",
        "• /listar_pendentes — pagamentos pendentes",
        "• /aprovar_tx <user_id> — aprova pagamento e envia convite VIP",
        "• /rejeitar_tx <user_id> [motivo] — rejeita pagamento",
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

async def simularvip_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    status = await enviar_pack_vip_job(context)
    await update.effective_message.reply_text(status)

async def listar_packs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        lines = [f"Pack [{p.id}] {esc(p.title)} — {'ENVIADO' if p.sent else 'PENDENTE'}"]
        for f in files:
            name = f.file_name or ""
            lines.append(f" - item #{f.id} | {f.file_type} ({f.role}) {name}")
        await update.effective_message.reply_text("\n".join(lines))
    finally:
        s.close()

# ===== EXCLUIR ITEM =====
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
            packs = s.query(Pack).order_by(Pack.created_at.desc()).all()
            if not packs:
                await update.effective_message.reply_text("Nenhum pack registrado.")
                return ConversationHandler.END
            lines = ["🗑 <b>Excluir Pack</b>\n", "Envie: <code>/excluir_pack &lt;id&gt;</code> para escolher um."]
            for p in packs:
                lines.append(f"[{p.id}] {esc(p.title)}")
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
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
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
# NOVOPACK (privado)
# =========================
TITLE, CONFIRM_TITLE, PREVIEWS, FILES, CONFIRM_SAVE = range(5)

def _require_admin(update: Update) -> bool:
    return update.effective_user and is_admin(update.effective_user.id)

def _summary_from_session(user_data: Dict[str, Any]) -> str:
    title = user_data.get("title", "—")
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

async def novopack_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _require_admin(update):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return ConversationHandler.END
    if update.effective_chat.type != "private":
        await update.effective_message.reply_text("Use este comando no privado comigo, por favor.")
        return ConversationHandler.END

    context.user_data.clear()
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

    p = create_pack(title=title, header_message_id=None)
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
    await update.effective_message.reply_text(f"🎉 <b>{esc(title)}</b> cadastrado com sucesso!", parse_mode="HTML")
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
        # evita duplicado
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

        # Envia convite
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
# Admin management commands
# =========================
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

async def mudar_nome_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not (update.effective_user and is_admin(update.effective_user.id)):
        await update.effective_message.reply_text("Apenas admins podem usar este comando.")
        return
    if not context.args:
        await update.effective_message.reply_text("Uso: /mudar_nome <novo nome>")
        return
    novo_nome = " ".join(context.args).strip()
    try:
        await application.bot.set_my_name(name=novo_nome)
        await update.effective_message.reply_text(f"✅ Nome alterado para: {novo_nome}")
    except Exception as e:
        await update.effective_message.reply_text(f"Erro: {e}")

async def limpar_chat_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apaga as últimas N mensagens deste chat (melhor esforço).
       Observação: o bot precisa ser admin com permissão de apagar mensagens."""
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
            await asyncio.sleep(0.03)  # evita flood
        except Exception:
            pass
    await application.bot.send_message(chat_id=chat_id, text=f"🧹 Apaguei ~{deleted} mensagens (melhor esforço).")

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
    """
    Webhook opcional para automação externa.
    Espera JSON: { "telegram_user_id": 123, "tx_hash": "0x...", "amount": "xx", "chain": "Polygon" }
    """
    data = await request.json()
    uid = data.get("telegram_user_id")
    tx_hash = data.get("tx_hash")
    amount = data.get("amount")
    chain = data.get("chain") or CHAIN_NAME

    if not uid or not tx_hash:
        return JSONResponse({"ok": False, "error": "telegram_user_id e tx_hash são obrigatórios"}, status_code=400)

    s = SessionLocal()
    try:
        # registra caso não exista
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

@app.get("/")
async def root():
    return {"status": "online", "message": "Bot ready (crypto payments)"}

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

    logging.basicConfig(level=logging.INFO)
    logging.info("Bot iniciado (cripto).")

    # ===== Error handler =====
    application.add_error_handler(error_handler)

    # ===== Conversa /novopack – SOMENTE NO PRIVADO (group=0) =====
    TITLE, CONFIRM_TITLE, PREVIEWS, FILES, CONFIRM_SAVE = range(5)
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("novopack", novopack_start, filters=filters.ChatType.PRIVATE)],
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
                MessageHandler(filters.TEXT & ~filters.COMMAND, hint_previews),
            ],
            FILES: [
                CommandHandler("finalizar", novopack_finish_review),
                MessageHandler(filters.Document.ALL | filters.AUDIO | filters.VOICE, novopack_collect_files),
                MessageHandler(filters.TEXT & ~filters.COMMAND, hint_files),
            ],
            CONFIRM_SAVE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, novopack_confirm_save),
            ],
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

    # ===== Handlers do grupo de armazenamento (group=1) =====
    application.add_handler(
        MessageHandler(
            filters.Chat(STORAGE_GROUP_ID) & filters.TEXT & ~filters.COMMAND,
            storage_text_handler
        ),
        group=1,
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
    application.add_handler(MessageHandler(media_filter, storage_media_handler), group=1)

    # ===== Comandos gerais (group=1) =====
    application.add_handler(CommandHandler("start", start_cmd), group=1)
    application.add_handler(CommandHandler("comandos", comandos_cmd), group=1)
    application.add_handler(CommandHandler("getid", getid_cmd), group=1)

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
    application.add_handler(CommandHandler("limpar_chat", limpar_chat_cmd), group=1)

    # Pagamentos cripto
    application.add_handler(CommandHandler("pagar", pagar_cmd), group=1)
    application.add_handler(CommandHandler("tx", tx_cmd), group=1)
    application.add_handler(CommandHandler("listar_pendentes", listar_pendentes_cmd), group=1)
    application.add_handler(CommandHandler("aprovar_tx", aprovar_tx_cmd), group=1)
    application.add_handler(CommandHandler("rejeitar_tx", rejeitar_tx_cmd), group=1)

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
