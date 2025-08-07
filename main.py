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

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import stripe
from google.oauth2 import service_account
from googleapiclient.discovery import build

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Carrega variáveis de ambiente do .env
load_dotenv()

# Variáveis do ambiente
BOT_TOKEN = os.getenv("BOT_TOKEN")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PRICE_ID = os.getenv("STRIPE_PRICE_ID")
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID"))
VIP_GROUP_ID = int(os.getenv("VIP_GROUP_ID"))
CANCELAR_LINK = os.getenv("CANCELAR_LINK")
CHECKOUT_LINK = os.getenv("CHECKOUT_LINK")

# Inicializa Stripe
stripe.api_key = STRIPE_SECRET_KEY

# Inicializa DB
init_db()

# Inicializa FastAPI
app = FastAPI()

# Inicializa Google Drive
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/drive']
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=credentials)

# Agenda
application = None
scheduler = AsyncIOScheduler()


def get_random_asset():
    asset_folder_id = os.getenv("DRIVE_ASSET_FOLDER_ID")
    query = f"'{asset_folder_id}' in parents and mimeType = 'application/vnd.google-apps.folder'"
    results = drive_service.files().list(q=query, fields="files(id, name)").execute()
    folders = results.get('files', [])
    if not folders:
        return None

    folder = random.choice(folders)
    folder_id = folder['id']
    asset_name = folder['name']

    files = drive_service.files().list(q=f"'{folder_id}' in parents", fields="files(id, name, mimeType)").execute().get('files', [])
    preview_images = [f for f in files if f['mimeType'].startswith('image/')]
    download_files = [f for f in files if not f['mimeType'].startswith('image/')]

    if not download_files:
        return None

    chosen_file = random.choice(download_files)
    download_url = f"https://drive.google.com/uc?id={chosen_file['id']}&export=download"
    preview_file_id = preview_images[0]['id'] if preview_images else None

    return {
        "name": asset_name,
        "download_url": download_url,
        "preview_file_id": preview_file_id
    }


async def enviar_asset_drive():
    asset = get_random_asset()
    if not asset:
        return

    caption = f"🆓 Asset Grátis do Dia: {asset['name']}\n⬇️ Download: {asset['download_url']}"
    if asset['preview_file_id']:
        preview_url = f"https://drive.google.com/uc?id={asset['preview_file_id']}"
        caption += f"\n🖼 Preview: {preview_url}"

    try:
        await application.bot.send_message(chat_id=GROUP_FREE_ID, text=caption)
    except Exception as e:
        logging.error(f"Erro ao enviar asset: {e}")


@app.post("/telegram")
async def telegram_webhook(req: Request):
    data = await req.json()
    await application.update_queue.put(Update.de_json(data, application.bot))
    return PlainTextResponse("OK")


@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        customer_email = session.get("customer_email")
        db = SessionLocal()
        db.add(NotificationMessage(email=customer_email, sent=False))
        db.commit()
        db.close()

    return {"status": "success"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá! Use /pagar para assinar o VIP ou /cancelar para cancelar sua assinatura.")


async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Para assinar o grupo VIP, clique no link abaixo:\n\n{CHECKOUT_LINK}")


async def cancelar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Para cancelar sua assinatura, clique no link abaixo:\n\n{CANCELAR_LINK}")


@app.on_event("startup")
async def startup_event():
    global application
    application = await ApplicationBuilder().token(BOT_TOKEN).build_async()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pagar", pagar))
    application.add_handler(CommandHandler("cancelar", cancelar))
    await application.initialize()
    await application.bot.set_webhook(url="https://telegram-bot-vip-hfn7.onrender.com/telegram")
    await application.start()

    scheduler.add_job(enviar_asset_drive, trigger='cron', hour=9, minute=0)
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_event():
    await application.stop()
