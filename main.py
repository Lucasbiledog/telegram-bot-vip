import os
import json
import logging
import asyncio
import random
import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, Request, UploadFile, Form, HTTPException, Depends
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from sqlalchemy.orm import Session
from database import init_db, SessionLocal, NotificationMessage, Config

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

import stripe
from google.oauth2 import service_account
from googleapiclient.discovery import build

import uvicorn

# === Config Google Drive ===
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
GOOGLE_DRIVE_FREE_FOLDER_ID = os.getenv("GOOGLE_DRIVE_FREE_FOLDER_ID")

# === Load env vars ===
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID"))
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID"))
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
PORT = int(os.getenv("PORT", 4242))

stripe.api_key = STRIPE_API_KEY

# === Google Drive setup ===
service_account_info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=credentials)

# === FastAPI app ===
app = FastAPI()
# === app.mount("/static", StaticFiles(directory="static"), name="static") === 
security = HTTPBasic()

# === Telegram Bot ===
application = ApplicationBuilder().token(BOT_TOKEN).build()

# === Database ===
init_db()
db = SessionLocal()

# === Global bot ===
bot = None

# === Painel Upload de Assets VIP ===
@app.post("/upload_asset")
async def upload_asset(
    file: UploadFile,
    title: str = Form(...),
    description: str = Form(...),
    credentials: HTTPBasicCredentials = Depends(security),
):
    if credentials.username != "admin" or credentials.password != "123":
        raise HTTPException(status_code=401, detail="Unauthorized")

    folder = "static/vip_assets"
    os.makedirs(folder, exist_ok=True)
    file_location = f"{folder}/{file.filename}"

    with open(file_location, "wb") as f:
        f.write(await file.read())

    db.add(Config(key=f"vip_asset:{title}", value=json.dumps({
        "filename": file.filename,
        "description": description,
        "path": file_location
    })))
    db.commit()
    return {"message": "Asset salvo com sucesso!"}

@app.get("/vip_assets")
async def list_assets():
    assets = db.query(Config).filter(Config.key.like("vip_asset:%")).all()
    return [{"title": a.key.split(":", 1)[1], **json.loads(a.value)} for a in assets]

# === Comandos Bot ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Fala! Use /pagar para assinar VIP e acessar os assets exclusivos.")

async def criar_checkout_subscription(telegram_user_id: int):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price": os.getenv("STRIPE_PRICE_ID"),
                "quantity": 1,
            }],
            mode="subscription",
            success_url="https://seu-site.com/sucesso?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://seu-site.com/cancelado",
            metadata={"telegram_user_id": str(telegram_user_id)}
        )
        return session.url
    except Exception as e:
        logging.error(f"Erro criando checkout: {e}")
        return None

async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    url = await criar_checkout_subscription(user_id)
    if url:
        await update.message.reply_text(f"Assine o VIP aqui:\n{url}")
    else:
        await update.message.reply_text("Erro ao gerar checkout. Tente novamente.")

async def enviar_asset_drive():
    try:
        query = f"'{GOOGLE_DRIVE_FREE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(q=query, fields="files(id, name)").execute()
        subfolders = results.get('files', [])
        if not subfolders:
            return

        folder = random.choice(subfolders)
        folder_id = folder['id']
        files = drive_service.files().list(q=f"'{folder_id}' in parents and trashed=false", fields="files(id, name, mimeType, webContentLink)").execute().get('files', [])

        preview_link = next((f"https://drive.google.com/uc?id={f['id']}" for f in files if f['name'].lower().endswith(('.jpg', '.png'))), None)
        download_link = next((f['webContentLink'] for f in files if not f['mimeType'].startswith('application/vnd.google-apps.folder')), None)

        if not download_link:
            return

        caption = f"🎁 Asset gratuito do dia: *{folder['name']}*\n\nLink: {download_link}"
        if preview_link:
            await bot.send_photo(chat_id=GROUP_FREE_ID, photo=preview_link, caption=caption, parse_mode="Markdown")
        else:
            await bot.send_message(chat_id=GROUP_FREE_ID, text=caption, parse_mode="Markdown")
    except Exception as e:
        logging.error(f"Erro ao enviar asset: {e}")

# === Webhook Stripe ===
@app.post("/stripe_webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        event = stripe.Webhook.construct_event(payload, sig, STRIPE_WEBHOOK_SECRET)
    except Exception as e:
        logging.error(f"Erro webhook Stripe: {e}")
        raise HTTPException(status_code=400)

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        telegram_user_id = session.get("metadata", {}).get("telegram_user_id")
        if telegram_user_id:
            validade = datetime.datetime.utcnow() + datetime.timedelta(days=30)
            db_sess = SessionLocal()
            db_sess.merge(Config(key=f"vip_validade:{telegram_user_id}", value=validade.isoformat()))
            db_sess.commit()
            db_sess.close()
            try:
                await bot.send_message(chat_id=int(telegram_user_id), text="✅ Assinatura confirmada!")
                link = await bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                await bot.send_message(chat_id=int(telegram_user_id), text=f"Acesse o grupo VIP:\n{link}")
            except Exception as e:
                logging.error(f"Erro ao enviar link VIP: {e}")

    return PlainTextResponse("", status_code=200)

# === Webhook Telegram ===
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
        update = Update.de_json(data, bot)
        await application.process_update(update)
    except Exception as e:
        logging.error(f"Erro no webhook Telegram: {e}")
        raise HTTPException(status_code=400)
    return PlainTextResponse("", status_code=200)

# === Health Check ===
@app.get("/")
async def root():
    return {"status": "online", "message": "Bot rodando com upload VIP + envio Drive gratuito"}

# === Verifica e remove VIPs expirados ===
async def verificar_vips():
    while True:
        db_sess = SessionLocal()
        now = datetime.datetime.utcnow()
        vips = db_sess.query(Config).filter(Config.key.like("vip_validade:%")).all()
        for vip in vips:
            try:
                user_id = int(vip.key.split(":")[1])
                validade = datetime.datetime.fromisoformat(vip.value)
                if validade < now:
                    try:
                        await bot.ban_chat_member(chat_id=GROUP_VIP_ID, user_id=user_id)
                        logging.info(f"VIP expirado removido: {user_id}")
                    except Exception as e:
                        logging.warning(f"Erro ao remover {user_id}: {e}")
            except Exception as e:
                logging.error(f"Erro processando VIP expirado: {e}")
        db_sess.close()
        await asyncio.sleep(3600)

# === Tarefa diária de envio ===
async def daily_task():
    while True:
        now = datetime.datetime.now()
        target = now.replace(hour=9, minute=55, second=0, microsecond=0)
        if now >= target:
            target += datetime.timedelta(days=1)
        wait_time = (target - now).total_seconds()
        logging.info(f"Aguardando {wait_time:.0f}s para enviar asset gratuito")
        await asyncio.sleep(wait_time)
        await enviar_asset_drive()

# === Startup ===
@app.on_event("startup")
async def on_startup():
    await application.initialize()
    await application.start()
    global bot
    bot = application.bot
    await bot.set_webhook(url="https://telegram-bot-vip-hfn7.onrender.com/webhook")
    asyncio.create_task(daily_task())
    asyncio.create_task(verificar_vips())
    logging.info("Bot iniciado com sucesso")

# === Comandos ===
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("pagar", pagar))

# === Rodar servidor ===
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
