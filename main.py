import os
import logging
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import telegram  # <-- Import necessário para pegar a versão correta

# Configurações iniciais
load_dotenv()
logging.basicConfig(level=logging.INFO)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")

if not WEBHOOK_URL:
    raise ValueError("WEBHOOK_URL não está definido. Adicione ao .env ou variáveis de ambiente no Render.")

if not TELEGRAM_TOKEN:
    raise ValueError("TELEGRAM_TOKEN não está definido. Adicione ao .env ou variáveis de ambiente no Render.")

# FastAPI app
app = FastAPI()

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá! Bot funcionando com sucesso na Render!")

# Inicialização do Telegram Application
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))

logging.info(f"Bot Telegram online na Render. Versão PTB: {telegram.__version__}")

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

# Startup e Webhook
@app.on_event("startup")
async def on_startup():
    await application.bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"Webhook configurado: {WEBHOOK_URL}")
    asyncio.create_task(application.initialize())
    asyncio.create_task(application.start())
