import os
import json
import logging
import asyncio
import random
import datetime as dt
import pytz

from dotenv import load_dotenv
from database import init_db, SessionLocal, Config
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from telegram import Update, MessageEntity
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

import stripe

import uvicorn

# --- Configurações iniciais ---
load_dotenv()
init_db()

# Stripe setup
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
stripe.api_key = STRIPE_API_KEY

app = FastAPI()

# Bot e grupos
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "-1002791988432"))        # seu grupo VIP
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))   # grupo onde os assets estão armazenados

# Admins permitidos para comandos especiais
ADMIN_USER_IDS = {7123614866}  # substitua pelos seus IDs admins


# Inicializa bot e db globalmente
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None
db = SessionLocal()


# ----- FUNÇÕES PARA ENVIO DE ASSETS VIP -----
async def enviar_asset_vip():
    try:
        # Pega as últimas mensagens do grupo STORAGE_GROUP_ID
        messages = await bot.get_chat_history(STORAGE_GROUP_ID, limit=100)
        # Filtra mensagens que tenham comentário (caption) com texto, pois o asset fica aí
        assets = []
        for msg in messages:
            if msg.caption and msg.caption.strip():
                # Cada mensagem é um asset com legenda, captura id do arquivo (photo/document/video)
                # Pega o file_id dependendo do tipo de mensagem
                file_id = None
                if msg.photo:
                    # Pega a maior resolução
                    file_id = msg.photo[-1].file_id
                elif msg.document:
                    file_id = msg.document.file_id
                elif msg.video:
                    file_id = msg.video.file_id
                if file_id:
                    assets.append((file_id, msg.caption))

        if not assets:
            logging.warning("Nenhum asset encontrado no grupo de armazenamento.")
            return

        chosen_file_id, caption = random.choice(assets)

        # Envia pro grupo VIP conforme tipo (foto, doc, video)
        # Para simplicidade, vamos usar send_photo se for foto, send_document para doc e send_video para video.
        # Mas só temos file_id e caption aqui, então tentaremos em ordem:

        # Como não temos tipo aqui, enviamos como document, que aceita qualquer file_id
        await bot.send_document(chat_id=GROUP_VIP_ID, document=chosen_file_id, caption=caption)
        logging.info(f"Asset VIP enviado: {caption}")
    except Exception as e:
        logging.error(f"Erro ao enviar asset VIP: {e}")


# ----- COMANDOS -----

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Fala! Esse bot te dá acesso a arquivos premium. Entre no grupo Free e veja como virar VIP. 🚀"
    )


async def getid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    chat_id = update.effective_chat.id
    user_name = user.full_name
    await update.message.reply_text(f"Seu nome: {user_name}\nSeu ID de usuário: {user.id}\nID deste chat: {chat_id}")


async def simular_9h(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return
    await enviar_asset_vip()
    await update.message.reply_text("Asset VIP do dia enviado manualmente.")


async def testepagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    db_session = SessionLocal()
    validade = dt.datetime.utcnow() + dt.timedelta(days=7)
    key = f"vip_validade:{user_id}"
    config = db_session.query(Config).filter(Config.key == key).first()
    if config:
        config.value = validade.isoformat()
    else:
        config = Config(key=key, value=validade.isoformat())
        db_session.add(config)
    db_session.commit()
    db_session.close()

    try:
        await bot.send_message(chat_id=user_id, text="✅ Pagamento fictício confirmado! Você será adicionado ao grupo VIP.")
        invite_link = await bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
        await bot.send_message(chat_id=user_id, text=f"Aqui está o seu link para entrar no grupo VIP:\n{invite_link}")
    except Exception as e:
        await update.message.reply_text(f"Erro ao enviar link convite para grupo VIP: {e}")
        return
    await update.message.reply_text("Teste de pagamento simulado com sucesso!")


async def listar_vips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_USER_IDS:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    db_session = SessionLocal()
    vip_configs = db_session.query(Config).filter(Config.key.like("vip_validade:%")).all()
    if not vip_configs:
        await update.message.reply_text("Nenhum VIP encontrado.")
        db_session.close()
        return

    texto = "Lista de VIPs e validade:\n\n"
    for cfg in vip_configs:
        try:
            validade = dt.datetime.fromisoformat(cfg.value)
            user_id_vip = int(cfg.key.split(":", 1)[1])
            texto += f"UserID: {user_id_vip} — Validade até: {validade.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        except:
            continue
    db_session.close()
    await update.message.reply_text(texto)


# ----- CHECK VIPS -----
async def verificar_vips():
    while True:
        db_session = SessionLocal()
        now = dt.datetime.utcnow()
        vip_configs = db_session.query(Config).filter(Config.key.like("vip_validade:%")).all()
        for cfg in vip_configs:
            try:
                validade = dt.datetime.fromisoformat(cfg.value)
                user_id = int(cfg.key.split(":", 1)[1])
                if validade < now:
                    try:
                        await bot.ban_chat_member(chat_id=GROUP_VIP_ID, user_id=user_id)
                        logging.info(f"Usuário {user_id} removido do grupo VIP por validade expirada.")
                    except Exception as e:
                        logging.warning(f"Erro ao remover usuário {user_id} do grupo VIP: {e}")
            except Exception as e:
                logging.error(f"Erro processando validade VIP {cfg.key}: {e}")
        db_session.close()
        await asyncio.sleep(3600)  # verifica a cada 1h


# ----- WEBHOOKS -----

@app.post("/stripe_webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        logging.error(f"Payload inválido Stripe: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid payload: {e}")
    except stripe.error.SignatureVerificationError as e:
        logging.error(f"Assinatura inválida Stripe: {e}")
        raise HTTPException(status_code=400, detail=f"Invalid signature: {e}")

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        telegram_user_id = session.get('metadata', {}).get('telegram_user_id')
        if telegram_user_id:
            try:
                await bot.send_message(chat_id=int(telegram_user_id), text="✅ Pagamento confirmado! Você será adicionado ao grupo VIP.")
                invite_link = await bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                await bot.send_message(chat_id=int(telegram_user_id), text=f"Aqui está o seu link para entrar no grupo VIP:\n{invite_link}")
                logging.info(f"Link enviado com sucesso para o usuário {telegram_user_id}")
            except Exception as e:
                logging.error(f"Erro ao enviar link convite para grupo VIP: {e}")
        else:
            logging.warning("Webhook Stripe recebido sem telegram_user_id no metadata.")
    return PlainTextResponse("", status_code=200)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.process_update(update)
    except Exception as e:
        logging.error(f"Erro processando update Telegram: {e}")
        raise HTTPException(status_code=400, detail="Invalid update")
    return PlainTextResponse("", status_code=200)


@app.get("/")
async def root():
    return {"status": "online", "message": "Bot Telegram + Stripe rodando 🎉"}


# ----- MENSAGEM BOM DIA -----
async def enviar_bom_dia():
    texto = "☀️ Bom dia, VIPs! Aproveitem o asset exclusivo de hoje! 🚀"
    await bot.send_message(chat_id=GROUP_VIP_ID, text=texto)


# ----- JOBS AGENDADOS -----
@app.on_event("startup")
async def on_startup():
    global bot

    await application.initialize()
    await application.start()

    bot = application.bot

    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("WEBHOOK_URL não definido no ambiente")

    await bot.set_webhook(url=webhook_url)

    logging.info("Bot iniciado com sucesso.")

    # Start VIP expiration checker
    asyncio.create_task(verificar_vips())

    # Timezone São Paulo
    timezone = pytz.timezone("America/Sao_Paulo")

    job_queue: JobQueue = application.job_queue

    # Enviar asset VIP todo dia 9h
    job_queue.run_daily(enviar_asset_vip, time=dt.time(hour=14, minute=15, tzinfo=timezone), name="daily_vip_asset")

    # Enviar bom dia todo dia 9h (antes do asset)
    job_queue.run_daily(enviar_bom_dia, time=dt.time(hour=14, minute=20, tzinfo=timezone), name="daily_bom_dia")

    # Registrar handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("getid", getid))
    application.add_handler(CommandHandler("simular9h", simular_9h))
    application.add_handler(CommandHandler("testepagamento", testepagamento))
    application.add_handler(CommandHandler("listarvips", listar_vips))


# Rodar servidor
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", 8000)), reload=False)
