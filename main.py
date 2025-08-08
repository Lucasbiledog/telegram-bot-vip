import os
import json
import logging
import asyncio
import random
import datetime
import datetime as dt
import pytz
import telegram  # para telegram.__version__

from dotenv import load_dotenv
from database import init_db, SessionLocal, NotificationMessage, Config
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

import stripe
from google.oauth2 import service_account
from googleapiclient.discovery import build

import uvicorn


# --- Configurações iniciais ---
load_dotenv()
init_db()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID"))
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID"))
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

stripe.api_key = STRIPE_API_KEY

# === Google Drive setup ===
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
GOOGLE_DRIVE_FREE_FOLDER_ID = "19MVALjrVBC5foWSUyb27qPPlbkDdSt3j"

service_account_info = json.loads(os.environ['SERVICE_ACCOUNT_JSON'])
credentials = service_account.Credentials.from_service_account_info(service_account_info, scopes=SCOPES)
drive_service = build('drive', 'v3', credentials=credentials)

# Variáveis globais do bot
application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None
db = SessionLocal()


# ===== HANDLERS =====

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Fala! Esse bot te dá acesso a arquivos premium. Entre no grupo Free e veja como virar VIP. 🚀"
    )


async def criar_checkout_session(telegram_user_id: int):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "Assinatura VIP Packs Unreal"},
                    "unit_amount": 1000,  # valor em centavos (ex: 1000 = $10.00)
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url="https://seu-site.com/sucesso?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://seu-site.com/cancelado",
            metadata={"telegram_user_id": str(telegram_user_id)},
        )
        return session.url
    except Exception as e:
        logging.error(f"Erro ao criar sessão de checkout: {e}")
        return None


async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session_url = await criar_checkout_session(user_id)
    if session_url:
        await update.message.reply_text(
            f"Para pagar, acesse o link abaixo e finalize seu pagamento na página segura da Stripe:\n\n{session_url}"
        )
    else:
        await update.message.reply_text("Erro ao gerar link de pagamento, tente novamente mais tarde.")


async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"O chat_id deste chat/grupo é: {chat_id}")


async def enviar_asset_drive():
    try:
        # Pega as subpastas do folder principal free
        query_subfolders = f"'{GOOGLE_DRIVE_FREE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(
            q=query_subfolders, fields="files(id, name)", pageSize=100
        ).execute()
        subfolders = results.get('files', [])
        if not subfolders:
            logging.warning("Nenhuma subpasta encontrada no Drive.")
            return

        chosen_folder = random.choice(subfolders)
        folder_id = chosen_folder['id']

        files_results = drive_service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType, webContentLink)",
            pageSize=50
        ).execute()
        files = files_results.get('files', [])
        if not files:
            logging.warning(f"Nenhum arquivo encontrado na subpasta {chosen_folder['name']}")
            return

        preview_link = None
        file_link = None
        preview_folder_id = None

        # Procura pasta preview
        for f in files:
            if f['mimeType'] == 'application/vnd.google-apps.folder' and f['name'].lower() == 'preview':
                preview_folder_id = f['id']
                break

        if preview_folder_id:
            previews_results = drive_service.files().list(
                q=f"'{preview_folder_id}' in parents and trashed=false",
                fields="files(id, name)", pageSize=10
            ).execute()
            previews = previews_results.get('files', [])
            if previews:
                chosen_preview = random.choice(previews)
                preview_link = f"https://drive.google.com/uc?id={chosen_preview['id']}"

        # Se não achar preview, tenta qualquer imagem na pasta
        if not preview_link:
            for f in files:
                name = f['name'].lower()
                if any(name.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']):
                    preview_link = f"https://drive.google.com/uc?id={f['id']}"
                    break

        # Procura arquivo para download (não folder)
        for f in files:
            if not f['mimeType'].startswith('application/vnd.google-apps.folder'):
                if f['mimeType'].startswith('application/') or f['name'].lower().endswith('.zip'):
                    file_link = f.get('webContentLink')
                    if file_link:
                        break

        if not file_link:
            logging.warning(f"Arquivo para download não encontrado em {chosen_folder['name']}")
            return

        texto = f"🎁 Asset gratuito do dia: *{chosen_folder['name']}*\n\nLink para download: {file_link}"

        if preview_link:
            await bot.send_photo(chat_id=GROUP_FREE_ID, photo=preview_link, caption=texto, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id=GROUP_FREE_ID, text=texto, parse_mode='Markdown')

        logging.info(f"Enviado asset '{chosen_folder['name']}' com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar asset do Drive: {e}")


async def enviar_manual_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Enviando asset do Drive no grupo Free...")
    await enviar_asset_drive()


async def limpar_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logging.info(f"Usuário {update.effective_user.id} iniciou limpeza do grupo Free")
        chat = await bot.get_chat(GROUP_FREE_ID)
        async for message in chat.iter_history(limit=100):
            try:
                await bot.delete_message(chat_id=GROUP_FREE_ID, message_id=message.message_id)
            except Exception as e:
                logging.warning(f"Erro ao deletar mensagem {message.message_id}: {e}")
        await update.message.reply_text("✅ Limpeza do grupo Free concluída (últimas 100 mensagens).")
    except Exception as e:
        logging.error(f"Erro ao limpar grupo: {e}")
        await update.message.reply_text("❌ Erro ao tentar limpar o grupo.")


# Mensagens e config banco

async def add_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /addmsg <categoria> <mensagem>")
        return
    category = context.args[0]
    message = " ".join(context.args[1:])
    if category not in ['pre_notification', 'unreal_news']:
        await update.message.reply_text("Categoria inválida. Use 'pre_notification' ou 'unreal_news'.")
        return
    db.add(NotificationMessage(category=category, message=message))
    db.commit()
    await update.message.reply_text(f"Mensagem adicionada na categoria {category}.")


async def list_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /listmsg <categoria>")
        return
    category = context.args[0]
    if category not in ['pre_notification', 'unreal_news']:
        await update.message.reply_text("Categoria inválida. Use 'pre_notification' ou 'unreal_news'.")
        return
    msgs = db.query(NotificationMessage).filter(NotificationMessage.category == category).all()
    if not msgs:
        await update.message.reply_text("Nenhuma mensagem encontrada.")
        return
    text = f"Mensagens na categoria {category}:\n\n"
    for msg in msgs:
        text += f"- (ID {msg.id}) {msg.message}\n"
    await update.message.reply_text(text)


async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /delmsg <id>")
        return
    try:
        msg_id = int(context.args[0])
    except:
        await update.message.reply_text("ID inválido.")
        return
    msg = db.query(NotificationMessage).filter(NotificationMessage.id == msg_id).first()
    if not msg:
        await update.message.reply_text("Mensagem não encontrada.")
        return
    db.delete(msg)
    db.commit()
    await update.message.reply_text(f"Mensagem ID {msg_id} deletada.")


async def set_config(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /setconfig <chave> <valor>")
        return
    key = context.args[0]
    value = context.args[1]
    config = db.query(Config).filter(Config.key == key).first()
    if config:
        config.value = value
    else:
        config = Config(key=key, value=value)
        db.add(config)
    db.commit()
    await update.message.reply_text(f"Configuração '{key}' atualizada para '{value}'.")


# Teste pagamento fictício
async def testepagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_session = SessionLocal()
    validade = datetime.datetime.utcnow() + datetime.timedelta(days=7)
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


# Verificar validade VIPs e kickar expirados
async def verificar_vips():
    while True:
        db_session = SessionLocal()
        now = datetime.datetime.utcnow()
        vip_configs = db_session.query(Config).filter(Config.key.like("vip_validade:%")).all()
        for cfg in vip_configs:
            try:
                validade = datetime.datetime.fromisoformat(cfg.value)
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
        await asyncio.sleep(3600)


# ===== WEBHOOKS =====

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


# ===== COMANDOS E HANDLERS =====
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


# ===== ENVIO DIÁRIO AUTOMÁTICO USANDO JOBQUEUE =====

async def send_preparation_message(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enviando mensagem de preparo para o grupo Free...")
    await bot.send_message(chat_id=GROUP_FREE_ID, text="🎁 Um novo asset gratuito será enviado em instantes! Fique ligado!")


async def send_daily_asset(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enviando asset gratuito para o grupo Free...")
    await enviar_asset_drive()


@app.on_event("startup")
async def on_startup():
    global bot

    await application.initialize()
    await application.start()

    bot = application.bot  # guarda referência global para uso

    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("WEBHOOK_URL não definido no ambiente")

    await bot.set_webhook(url=webhook_url)

    logging.info(f"Bot iniciado com sucesso. Versão PTB: {telegram.__version__}")

    # Start VIP expiration checker
    asyncio.create_task(verificar_vips())

    # Configura timezone para São Paulo
    timezone = pytz.timezone("America/Sao_Paulo")

    # Pega o JobQueue da aplicação
    job_queue: JobQueue = application.job_queue

    # Agendar envio da mensagem de preparação 1 minuto antes (ex: 12:25)
    job_queue.run_daily(send_preparation_message, time=dt.time(hour=18, minute=15, tzinfo=timezone), name="prep_msg")

    # Agendar envio do asset às 12:26
    job_queue.run_daily(send_daily_asset, time=dt.time(hour=18, minute=16, tzinfo=timezone), name="daily_asset")


# ===== RODAR SERVIDOR =====
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
