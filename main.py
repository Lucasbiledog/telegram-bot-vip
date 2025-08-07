# === Versão compatível para Render ===
import os
import json
import logging
import asyncio
import random
import datetime

from dotenv import load_dotenv
from database import init_db, SessionLocal, NotificationMessage, Config
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from telegram import Update, Bot
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import stripe
from google.oauth2 import service_account
from googleapiclient.discovery import build

import uvicorn

# === Config Google Drive ===
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
GOOGLE_DRIVE_FREE_FOLDER_ID = "19MVALjrVBC5foWSUyb27qPPlbkDdSt3j"

# === Load env vars ===
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID"))
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID"))
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_API_KEY

# === Google Drive setup ===
service_account_info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=credentials)

# === FastAPI app ===
app = FastAPI()

# === Telegram Bot ===
application = ApplicationBuilder().token(BOT_TOKEN).build()

# === Database init ===
init_db()
db = SessionLocal()

# === Handlers e outras funções ficam iguais ===
# Coloque aqui todo o código anterior igual, SEM alterar o conteúdo das funções.
# Apenas certifique-se de que o bot seja definido após iniciar a aplicacao:

# ==== Evento de startup (Render-ready) ====
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()

    global bot
    bot = application.bot

    # Adiciona essa mensagem para confirmar execução correta no Render
    logging.info(f"Bot Telegram online na Render. Versão PTB: {application.__version__}")

    webhook_url = os.getenv("RENDER_EXTERNAL_URL", "https://telegram-bot-vip-hfn7.onrender.com") + "/webhook"
    await bot.set_webhook(url=webhook_url)
    logging.info(f"Webhook Telegram definido em {webhook_url}")

    asyncio.create_task(daily_task())
    asyncio.create_task(verificar_vips())

# ==== Rota simples de versão para debug Render ====
@app.get("/version")
async def version():
    import telegram
    return {
        "status": "ok",
        "python_telegram_bot_version": telegram.__version__,
        "render_env": os.getenv("RENDER") or "not in render"
    }

# ===== Run uvicorn =====
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("PORT", 4242))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
