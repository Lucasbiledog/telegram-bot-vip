import os
import logging
import asyncio
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)
from database import init_db
from bot.telegram_webhook import setup_telegram_webhook, bot
from bot.stripe_handler import setup_stripe_webhook
from bot.scheduler import start_scheduler
from bot.vip import testepagamento, listar_vips, verificar_vips
from bot.assets import enviar_manual_drive, enviar_asset_drive
from bot.basic_commands import start, pagar, get_chat_id, limpar_chat
from bot.message_handlers import add_message, list_messages, delete_message, set_config

load_dotenv()
logging.basicConfig(level=logging.INFO)

app = FastAPI()

init_db()

BOT_TOKEN = os.getenv("BOT_TOKEN")
application = ApplicationBuilder().token(BOT_TOKEN).build()

# Configura webhooks e rotas
setup_telegram_webhook(application)
setup_stripe_webhook(app)
start_scheduler(application)

# Adiciona handlers Telegram
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("pagar", pagar))
application.add_handler(CommandHandler("get_chat_id", get_chat_id))
application.add_handler(CommandHandler("enviar_drive", enviar_manual_drive))
application.add_handler(CommandHandler("limpar_chat", limpar_chat))

application.add_handler(CommandHandler("addmsg", add_message))
application.add_handler(CommandHandler("listmsg", list_messages))
application.add_handler(CommandHandler("delmsg", delete_message))
application.add_handler(CommandHandler("setconfig", set_config))

application.add_handler(CommandHandler("testepagamento", testepagamento))
application.add_handler(CommandHandler("listvips", listar_vips))

@app.on_event("startup")
async def on_startup():
    logging.info(f"Bot iniciado. Versão PTB: {bot.__version__}")

    # Inicia verificação periódica de VIPs expirados
    asyncio.create_task(verificar_vips())

@app.get("/")
async def root():
    return {"status": "online"}

@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.process_update(update)
        return PlainTextResponse(status_code=200)
    except Exception as e:
        logging.error(f"Erro processando update Telegram: {e}")
        raise HTTPException(status_code=400, detail="Invalid update")
