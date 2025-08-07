import os
import json
import logging
import random
from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, ContextTypes
)
import stripe
from google.oauth2 import service_account
from googleapiclient.discovery import build
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Local imports
from database import init_db, SessionLocal, NotificationMessage

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

# Configurações
BOT_TOKEN = os.getenv("BOT_TOKEN")
VIP_GROUP_ID = get_env_int("VIP_GROUP_ID")
GROUP_FREE_ID = get_env_int("GROUP_FREE_ID")
STRIPE_SECRET_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
stripe.api_key = STRIPE_SECRET_KEY

FOLDER_ID = os.getenv("GOOGLE_DRIVE_FREE_FOLDER_ID")
SERVICE_ACCOUNT_INFO = json.loads(os.getenv("SERVICE_ACCOUNT_JSON"))
SCOPES = ["https://www.googleapis.com/auth/drive"]
creds = service_account.Credentials.from_service_account_info(SERVICE_ACCOUNT_INFO, scopes=SCOPES)
drive_service = build("drive", "v3", credentials=creds)

# Inicializa FastAPI e banco
app = FastAPI()
db = init_db()

# Cria a aplicação do telegram (sem Updater)
application = ApplicationBuilder().token(BOT_TOKEN).build()

scheduler = AsyncIOScheduler()

# Função para escolher asset do Google Drive
def escolher_asset():
    response = drive_service.files().list(
        q=f"'{FOLDER_ID}' in parents and mimeType = 'application/vnd.google-apps.folder'",
        fields="files(id, name)").execute()
    pastas = response.get('files', [])
    if not pastas:
        return None
    pasta = random.choice(pastas)
    arquivos_resp = drive_service.files().list(
        q=f"'{pasta['id']}' in parents and mimeType != 'application/vnd.google-apps.folder'",
        fields="files(id, name, mimeType, webContentLink)").execute()
    arquivos = arquivos_resp.get('files', [])
    if not arquivos:
        return None
    arquivo = next((f for f in arquivos if not f['name'].endswith('.jpg')), None)
    previews = [f for f in arquivos if f['name'].endswith('.jpg')]
    return pasta['name'], arquivo, previews

# Função para enviar asset
async def enviar_asset_drive():
    session = SessionLocal()
    ja_enviados = [n.asset_name for n in session.query(NotificationMessage).all()]
    tentativa = 0
    while tentativa < 5:
        resultado = escolher_asset()
        if resultado is None:
            logger.warning("Nenhum asset encontrado.")
            return
        nome, arquivo, previews = resultado
        if nome in ja_enviados:
            tentativa += 1
            continue
        for preview in previews:
            await application.bot.send_photo(chat_id=GROUP_FREE_ID, photo=preview['webContentLink'])
        await application.bot.send_document(chat_id=GROUP_FREE_ID, document=arquivo['webContentLink'], caption=f"🔹 {nome}")
        session.add(NotificationMessage(asset_name=nome))
        session.commit()
        logger.info(f"Asset enviado: {nome}")
        return
    logger.warning("Nenhum asset novo para enviar hoje.")

# Comando /start
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bem-vindo ao bot!")

application.add_handler(CommandHandler("start", start))

# Rota webhook Stripe
@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except (ValueError, stripe.error.SignatureVerificationError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    if event['type'] == 'checkout.session.completed':
        session_obj = event['data']['object']
        cliente_id = session_obj.get('client_reference_id')
        if cliente_id:
            await application.bot.send_message(chat_id=cliente_id, text="✅ Pagamento confirmado! Você será adicionado ao grupo VIP.")

    return PlainTextResponse("ok")

# Rota webhook Telegram para receber updates
@app.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return PlainTextResponse("OK")

# Eventos startup/shutdown do FastAPI
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    # Defina sua URL pública correta aqui:
    webhook_url = "https://telegram-bot-vip-hfn7.onrender.com/telegram"
    await application.bot.set_webhook(webhook_url)
    await application.start()
    scheduler.add_job(enviar_asset_drive, 'cron', hour=9, minute=0)
    scheduler.start()

@app.on_event("shutdown")
async def on_shutdown():
    await application.stop()
    await application.shutdown()
    scheduler.shutdown()

# Executar via uvicorn
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)
