import os
import json
import logging
import random
import asyncio
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import telegram
import stripe
from google.oauth2 import service_account
from googleapiclient.discovery import build
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

def get_env_int(var_name: str, default=None):
    val = os.getenv(var_name)
    if val is None:
        if default is not None:
            return default
        raise ValueError(f"Variável de ambiente {var_name} não definida.")
    try:
        return int(val)
    except Exception:
        raise ValueError(f"Variável {var_name} deve ser um inteiro válido.")

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN não definido no .env")

VIP_GROUP_ID = get_env_int("VIP_GROUP_ID")
GROUP_FREE_ID = get_env_int("GROUP_FREE_ID")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_SECRET_KEY

FOLDER_ID = os.getenv("GOOGLE_DRIVE_FREE_FOLDER_ID")
SERVICE_ACCOUNT_JSON = os.getenv("SERVICE_ACCOUNT_JSON")
if not SERVICE_ACCOUNT_JSON:
    raise ValueError("SERVICE_ACCOUNT_JSON não definido no .env")

SERVICE_ACCOUNT_INFO = json.loads(SERVICE_ACCOUNT_JSON)
SCOPES = ["https://www.googleapis.com/auth/drive"]
creds = service_account.Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
drive_service = build("drive", "v3", credentials=creds)

app = FastAPI()
application = ApplicationBuilder().token(BOT_TOKEN).build()
scheduler = AsyncIOScheduler()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bem-vindo ao bot!")

application.add_handler(CommandHandler("start", start))

def escolher_asset():
    try:
        response = drive_service.files().list(
            q=f"'{FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder'",
            fields="files(id, name)").execute()
        pastas = response.get('files', [])
        if not pastas:
            logger.warning("Nenhuma pasta encontrada no Drive.")
            return None
        pasta = random.choice(pastas)
        arquivos_resp = drive_service.files().list(
            q=f"'{pasta['id']}' in parents and mimeType != 'application/vnd.google-apps.folder'",
            fields="files(id, name, mimeType, webContentLink)").execute()
        arquivos = arquivos_resp.get('files', [])
        if not arquivos:
            logger.warning(f"Nenhum arquivo na pasta {pasta['name']}.")
            return None
        arquivo = next((f for f in arquivos if not f['name'].endswith('.jpg')), None)
        previews = [f for f in arquivos if f['name'].endswith('.jpg')]
        return pasta['name'], arquivo, previews
    except Exception as e:
        logger.error(f"Erro ao buscar asset no Google Drive: {e}")
        return None

async def enviar_asset_drive():
    resultado = escolher_asset()
    if resultado is None:
        logger.warning("Nenhum asset disponível para envio.")
        return
    nome, arquivo, previews = resultado
    try:
        for preview in previews:
            await application.bot.send_photo(chat_id=GROUP_FREE_ID, photo=preview['webContentLink'])
        if arquivo:
            await application.bot.send_document(chat_id=GROUP_FREE_ID, document=arquivo['webContentLink'], caption=f"🔹 {nome}")
        else:
            logger.warning(f"Arquivo principal não encontrado para asset {nome}")
        logger.info(f"Asset enviado: {nome}")
    except Exception as e:
        logger.error(f"Erro ao enviar asset: {e}")

def job_wrapper():
    asyncio.create_task(enviar_asset_drive())

@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        logger.error(f"Webhook Stripe inválido: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    if event['type'] == 'checkout.session.completed':
        session_obj = event['data']['object']
        cliente_id = session_obj.get('client_reference_id')
        if cliente_id:
            await application.bot.send_message(chat_id=cliente_id, text="✅ Pagamento confirmado! Você será adicionado ao grupo VIP.")
    return PlainTextResponse("ok")

@app.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return PlainTextResponse("OK")

@app.on_event("startup")
async def on_startup():
    logger.info(f"python-telegram-bot version: {telegram.__version__}")
    await application.initialize()

    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise ValueError("WEBHOOK_URL não definido no .env")

    await application.bot.set_webhook(webhook_url)
    await application.start()
    scheduler.add_job(job_wrapper, 'cron', hour=9, minute=0)
    scheduler.start()
    logger.info("Bot iniciado e scheduler configurado.")

@app.on_event("shutdown")
async def on_shutdown():
    scheduler.shutdown()
    await application.stop()
    await application.shutdown()
    logger.info("Bot desligado.")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, log_level="info")
