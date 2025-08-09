# main.py
import os
import json
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

from telegram import Update, Message, File
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

# SQLAlchemy (SQLite local)
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Text, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker

# --- Load env ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID"))  # grupo onde você vai 'tacar' os assets
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID"))
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID"))
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "0"))
PORT = int(os.getenv("PORT", 8000))

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN não definido em .env")

# Stripe
stripe.api_key = STRIPE_API_KEY

# FastAPI + Telegram application
app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Database (SQLite local) ---
Base = declarative_base()
DB_URL = os.getenv("DATABASE_URL", "sqlite:///./bot_data.db")
engine = create_engine(DB_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(String, unique=True, nullable=False)
    file_type = Column(String, nullable=True)  # 'document', 'photo', 'video', ...
    file_name = Column(String, nullable=True)
    last_sent = Column(DateTime, nullable=True)
    active = Column(Boolean, default=True)


class VIP(Base):
    __tablename__ = "vips"
    id = Column(Integer, primary_key=True, index=True)
    telegram_user_id = Column(Integer, unique=True, nullable=False)
    valid_until = Column(DateTime, nullable=False)


def init_db():
    Base.metadata.create_all(bind=engine)


init_db()

# --- Helpers DB ---
def db_add_asset(session, file_id: str, file_type: str = None, file_name: str = None):
    existing = session.query(Asset).filter(Asset.file_id == file_id).first()
    if existing:
        # update metadata se necessário
        updated = False
        if file_type and existing.file_type != file_type:
            existing.file_type = file_type
            updated = True
        if file_name and existing.file_name != file_name:
            existing.file_name = file_name
            updated = True
        if updated:
            session.commit()
        return existing
    a = Asset(file_id=file_id, file_type=file_type, file_name=file_name)
    session.add(a)
    session.commit()
    return a


def db_get_next_asset(session) -> Optional[Asset]:
    """
    Escolhe um asset aleatório que **não foi enviado** ainda (last_sent is NULL)
    ou, se todos já foram enviados, escolha o que tem o maior tempo desde last_sent.
    """
    # 1) tenta assets sem last_sent
    a = session.query(Asset).filter(Asset.active == True, Asset.last_sent.is_(None)).order_by(Asset.id).all()
    if a:
        return random.choice(a)
    # 2) caso todos já tenham sido enviados, escolher o que tem last_sent mais antigo
    a2 = session.query(Asset).filter(Asset.active == True).order_by(Asset.last_sent.asc()).all()
    if a2:
        return a2[0]
    return None


def db_mark_sent(session, asset: Asset):
    asset.last_sent = dt.datetime.utcnow()
    session.commit()


def db_add_vip(session, telegram_user_id: int, days: int = 30):
    now = dt.datetime.utcnow()
    valid_until = now + dt.timedelta(days=days)
    existing = session.query(VIP).filter(VIP.telegram_user_id == telegram_user_id).first()
    if existing:
        # estender validade se já existe
        existing.valid_until = max(existing.valid_until, valid_until)
        session.commit()
        return existing
    v = VIP(telegram_user_id=telegram_user_id, valid_until=valid_until)
    session.add(v)
    session.commit()
    return v


def db_get_all_vips(session) -> List[VIP]:
    return session.query(VIP).all()


def db_remove_vip(session, telegram_user_id: int):
    existing = session.query(VIP).filter(VIP.telegram_user_id == telegram_user_id).first()
    if existing:
        session.delete(existing)
        session.commit()


# --- Telegram Handlers ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala! Eu gerencio assets VIPs. Use /pagar para assinar ou fale com o admin.")


async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "Assinatura VIP Packs Unreal"},
                    "unit_amount": 1000,  # $10.00 exemplo
                },
                "quantity": 1,
            }],
            mode="payment",  # pagamento único; para assinatura use 'subscription' com price_id
            success_url="https://seu-site.com/sucesso?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://seu-site.com/cancelado",
            metadata={"telegram_user_id": str(user_id)},
        )
        await update.message.reply_text(f"Acesse para pagar: {session.url}")
    except Exception as e:
        logger.exception("Erro criando sessão Stripe")
        await update.message.reply_text("Erro ao gerar link de pagamento. Tente novamente mais tarde.")


async def atualizar_assets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando que varre o STORAGE_GROUP_ID e salva file_id no DB.
       Somente admin (ADMIN_USER_ID) pode executar."""
    caller = update.effective_user.id
    if caller != ADMIN_USER_ID:
        await update.message.reply_text("Apenas o admin pode executar este comando.")
        return

    await update.message.reply_text("Iniciando varredura do grupo de armazenamento. Isso pode levar alguns minutos...")

    session = SessionLocal()
    try:
        count = 0
        # Leitura do histórico do grupo de armazenamento
        # Observação: alguns métodos de iteração de histórico variam de versão. O PTB v20+ fornece método get_chat_history
        # aqui usamos bot.get_chat_history para iterar mensagens.
        async for msg in bot.get_chat_history(STORAGE_GROUP_ID, limit=2000):
            # checar tipos que contêm arquivos: document, photo, video, audio, voice, animation
            file_id = None
            file_type = None
            file_name = None

            if msg.document:
                file_id = msg.document.file_id
                file_type = "document"
                file_name = getattr(msg.document, "file_name", None)
            elif msg.video:
                file_id = msg.video.file_id
                file_type = "video"
                file_name = getattr(msg.video, "file_name", None)
            elif msg.audio:
                file_id = msg.audio.file_id
                file_type = "audio"
                file_name = getattr(msg.audio, "file_name", None)
            elif msg.photo:
                # photo é lista; pega maior
                biggest = msg.photo[-1]
                file_id = biggest.file_id
                file_type = "photo"
                file_name = None
            elif msg.animation:
                file_id = msg.animation.file_id
                file_type = "animation"
            elif msg.voice:
                file_id = msg.voice.file_id
                file_type = "voice"

            if file_id:
                db_add_asset(session, file_id=file_id, file_type=file_type, file_name=file_name)
                count += 1

        await update.message.reply_text(f"Importação concluída. {count} assets adicionados/atualizados.")
    except Exception as e:
        logger.exception("Erro ao atualizar assets do grupo")
        await update.message.reply_text("Erro ao atualizar assets. Veja logs.")
    finally:
        session.close()


async def list_vips_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    try:
        vips = db_get_all_vips(session)
        if not vips:
            await update.message.reply_text("Nenhum VIP ativo no momento.")
            return
        now = dt.datetime.utcnow()
        lines = []
        for v in vips:
            remaining = v.valid_until - now
            days = remaining.days
            lines.append(f"👤 `{v.telegram_user_id}` — expira {v.valid_until.strftime('%d/%m/%Y %H:%M')} ({days} dias)")
        text = "📋 *VIPs ativos:*\n\n" + "\n".join(lines)
        await update.message.reply_text(text, parse_mode="Markdown")
    finally:
        session.close()


async def bomdia_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target = GROUP_FREE_ID  # envia bom dia no grupo Free (ajuste se quiser VIP)
    await bot.send_message(chat_id=target, text="🌞 Bom dia, pessoal! Que o dia de hoje seja produtivo! 💪")


# Envia asset pro grupo VIP: pega asset do DB e reenvia usando file_id (fast)
async def enviar_asset_daily(context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    try:
        asset = db_get_next_asset(session)
        if not asset:
            logger.warning("Nenhum asset disponível para envio.")
            return

        # Reenvia arquivo ao grupo VIP de acordo com o tipo
        try:
            caption = f"🎁 Asset do dia: {asset.file_name or 'Arquivo'}"
            if asset.file_type == "photo":
                await bot.send_photo(chat_id=GROUP_VIP_ID, photo=asset.file_id, caption=caption)
            elif asset.file_type == "video":
                await bot.send_video(chat_id=GROUP_VIP_ID, video=asset.file_id, caption=caption)
            elif asset.file_type == "audio":
                await bot.send_audio(chat_id=GROUP_VIP_ID, audio=asset.file_id, caption=caption)
            elif asset.file_type == "animation":
                await bot.send_animation(chat_id=GROUP_VIP_ID, animation=asset.file_id, caption=caption)
            elif asset.file_type == "voice":
                await bot.send_voice(chat_id=GROUP_VIP_ID, voice=asset.file_id, caption=caption)
            else:
                # document or fallback
                await bot.send_document(chat_id=GROUP_VIP_ID, document=asset.file_id, caption=caption)
            db_mark_sent(session, asset)
            logger.info(f"Enviado asset {asset.id} -> {asset.file_name or asset.file_id}")
        except Exception as e:
            logger.exception("Erro ao reenviar asset no VIP")
    finally:
        session.close()


# Mensagem de preparação (aviso) antes do envio diário
async def send_preparation_message(context: ContextTypes.DEFAULT_TYPE):
    await bot.send_message(chat_id=GROUP_VIP_ID, text="⏳ Em 15 minutos será enviado o asset do dia para o grupo VIP. Fique ligado!")


# Remover VIPs expirados (rodar periodicamente)
async def verificar_vips_task():
    while True:
        try:
            session = SessionLocal()
            now = dt.datetime.utcnow()
            expired = session.query(VIP).filter(VIP.valid_until < now).all()
            for v in expired:
                try:
                    # remove do grupo VIP (ban + unban para remover)
                    await bot.ban_chat_member(chat_id=GROUP_VIP_ID, user_id=v.telegram_user_id)
                    await bot.unban_chat_member(chat_id=GROUP_VIP_ID, user_id=v.telegram_user_id)
                except Exception as e:
                    logger.warning(f"Erro ao remover VIP {v.telegram_user_id}: {e}")
                session.delete(v)
                session.commit()
                logger.info(f"VIP {v.telegram_user_id} removido por expiração.")
        except Exception:
            logger.exception("Erro no processo de verificação de VIPs")
        finally:
            session.close()
        await asyncio.sleep(3600)  # roda a cada hora


# Webhook endpoints

@app.post("/stripe_webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        logger.error(f"Payload inválido Stripe: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")
    except stripe.error.SignatureVerificationError as e:
        logger.error(f"Assinatura invalida Stripe: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    # Trata evento de checkout finalizado (pagamento)
    if event['type'] == 'checkout.session.completed':
        session_obj = event['data']['object']
        telegram_user_id = session_obj.get('metadata', {}).get('telegram_user_id')
        if telegram_user_id:
            try:
                session_db = SessionLocal()
                # exemplo: 30 dias de VIP
                db_add_vip(session_db, int(telegram_user_id), days=30)
                session_db.close()
                # envia convite para entrar no VIP
                invite_link = await bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                await bot.send_message(chat_id=int(telegram_user_id), text=f"✅ Pagamento confirmado! Entre no grupo VIP: {invite_link}")
                logger.info(f"VIP concedido ao {telegram_user_id}")
            except Exception:
                logger.exception("Erro tratando checkout.completed")
    # você pode tratar outros eventos (subscription, invoice.paid, etc.) aqui

    return PlainTextResponse("", status_code=200)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
    except Exception as e:
        logger.exception("Erro processando update Telegram")
        raise HTTPException(status_code=400, detail="Invalid update")
    return PlainTextResponse("", status_code=200)


@app.get("/")
async def root():
    return {"status": "online", "message": "Bot Telegram + Stripe rodando"}


# Register command handlers
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("pagar", pagar))
application.add_handler(CommandHandler("atualizar_assets", atualizar_assets))
application.add_handler(CommandHandler("vips", list_vips_cmd))
application.add_handler(CommandHandler("bomdia", bomdia_cmd))
# você pode adicionar /testepagamento se quiser (abaixo)

# Opcional: comando de teste para adicionar VIP (somente admin)
async def testepagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    caller = update.effective_user.id
    if caller != ADMIN_USER_ID:
        await update.message.reply_text("Apenas admin pode usar este comando.")
        return
    session = SessionLocal()
    try:
        target = update.effective_user.id
        db_add_vip(session, target, days=7)
        invite_link = await bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
        await bot.send_message(chat_id=target, text=f"Teste: VIP concedido por 7 dias. Convite: {invite_link}")
        await update.message.reply_text("Teste de pagamento realizado (VIP 7 dias).")
    finally:
        session.close()

application.add_handler(CommandHandler("testepagamento", testepagamento))

# STARTUP / JOBS
@app.on_event("startup")
async def on_startup():
    global bot
    await application.initialize()
    await application.start()
    bot = application.bot

    if not WEBHOOK_URL:
        raise RuntimeError("WEBHOOK_URL não definido no .env")

    await bot.set_webhook(url=WEBHOOK_URL)
    logger.info("Bot iniciado e webhook definido")

    # Start background task to check VIP expirations
    asyncio.create_task(verificar_vips_task())

    # Agendamento: timezone São Paulo
    tz = pytz.timezone("America/Sao_Paulo")
    job_queue: JobQueue = application.job_queue

    # Bom dia (grupo Free) 09:00
    job_queue.run_daily(lambda ctx: asyncio.create_task(bot.send_message(chat_id=GROUP_FREE_ID, text="🌞 Bom dia! Tenham um ótimo dia!")), time=dt.time(hour=9, minute=0, tzinfo=tz), name="bom_dia")

    # Mensagem de preparação para VIP 08:45
    job_queue.run_daily(send_preparation_message, time=dt.time(hour=8, minute=45, tzinfo=tz), name="prep_vip")

    # Envio do asset para VIP 09:00
    job_queue.run_daily(lambda ctx: asyncio.create_task(enviar_asset_daily(context=ctx)), time=dt.time(hour=9, minute=0, tzinfo=tz), name="send_asset")

    logger.info("Jobs agendados: bom dia, preparação e envio diário de assets.")


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=PORT)
