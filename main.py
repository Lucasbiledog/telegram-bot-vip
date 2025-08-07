import os
import logging
import asyncio
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
import telegram
import pytz

# Configurações iniciais
load_dotenv()
logging.basicConfig(level=logging.INFO)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROUP_ID = int(os.getenv("FREE_GROUP_ID", "0"))
ASSETS = [
    {"file_id": "<file_id_1>", "caption": "Asset gratuito 1"},
    {"file_id": "<file_id_2>", "caption": "Asset gratuito 2"},
    # Adicione mais assets conforme necessário
]

if not WEBHOOK_URL or not TELEGRAM_TOKEN or GROUP_ID == 0:
    raise ValueError("Configure WEBHOOK_URL, TELEGRAM_TOKEN e FREE_GROUP_ID corretamente no .env")

# FastAPI app
app = FastAPI()

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá! Bot funcionando com sucesso na Render!")

# Envio automático de asset gratuito
sent_assets = set()

async def send_preparation_message(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enviando mensagem de preparo para o grupo...")
    await context.bot.send_message(chat_id=GROUP_ID, text="🎁 Um novo asset gratuito será enviado em instantes! Fique ligado!")

async def send_daily_asset(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enviando asset gratuito para o grupo...")
    available_assets = [a for a in ASSETS if a["file_id"] not in sent_assets]

    if not available_assets:
        sent_assets.clear()
        available_assets = ASSETS

    asset = random.choice(available_assets)
    sent_assets.add(asset["file_id"])
    await context.bot.send_document(chat_id=GROUP_ID, document=asset["file_id"], caption=asset["caption"])

# Inicialização do bot
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
logging.info(f"Bot Telegram online na Render. Versão PTB: {telegram.__version__}")

# Webhook do Telegram
@app.post("/webhook")
async def telegram_webhook(req: Request):
    try:
        json_data = await req.json()
        update = Update.de_json(json_data, application.bot)
        await application.update_queue.put(update)
        return PlainTextResponse("ok")
    except Exception as e:
        logging.exception("Erro ao processar update do Telegram:")
        raise HTTPException(status_code=400, detail=str(e))

# Startup e agendamentos
@app.on_event("startup")
async def on_startup():
    await application.bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"Webhook configurado: {WEBHOOK_URL}")

    # Agendar envio de asset diário às 12:26 horário de Brasília
    timezone = pytz.timezone("America/Sao_Paulo")
    now = datetime.now(timezone)
    run_time_asset = now.replace(hour=12, minute=30, second=0, microsecond=0)
    run_time_preparation = now.replace(hour=12, minute=29, second=0, microsecond=0)

    if run_time_asset < now:
        run_time_asset += timedelta(days=1)
        run_time_preparation += timedelta(days=1)

    job_queue: JobQueue = application.job_queue
    job_queue.run_daily(send_daily_asset, time=run_time_asset.timetz(), name="daily_asset")
    job_queue.run_daily(send_preparation_message, time=run_time_preparation.timetz(), name="prep_msg")

    asyncio.create_task(application.initialize())
    asyncio.create_task(application.start())
