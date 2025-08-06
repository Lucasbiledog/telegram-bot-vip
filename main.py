import os
import logging
import asyncio
import threading
import random
import nest_asyncio
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
import stripe
from flask import Flask, request
import telegram as telegram_api
from waitress import serve

from google.oauth2 import service_account
from googleapiclient.discovery import build

nest_asyncio.apply()
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID"))
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID"))
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
GOOGLE_DRIVE_FREE_FOLDER_ID = "19MVALjrVBC5foWSUyb27qPPlbkDdSt3j"  # Pasta principal do Drive

SERVICE_ACCOUNT_FILE = "service_account.json"  # Seu JSON de conta de serviço
SCOPES = ['https://www.googleapis.com/auth/drive.readonly']

stripe.api_key = STRIPE_API_KEY
bot = telegram_api.Bot(token=BOT_TOKEN)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# Inicializar serviço Google Drive
credentials = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=credentials)


# Handlers do Telegram
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
                    "product_data": {
                        "name": "Assinatura VIP Packs Unreal",
                    },
                    "unit_amount": 1000,
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

# Função para enviar asset gratuito aleatório da pasta Google Drive
async def enviar_asset_drive(application):
    try:
        # Listar subpastas na pasta principal
        query_subfolders = f"'{GOOGLE_DRIVE_FREE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(
            q=query_subfolders,
            fields="files(id, name)",
            pageSize=100
        ).execute()
        subfolders = results.get('files', [])

        if not subfolders:
            logging.warning("Nenhuma subpasta encontrada na pasta principal do Drive.")
            return

        chosen_folder = random.choice(subfolders)
        folder_id = chosen_folder['id']

        # Buscar arquivos dentro da subpasta
        query_files = f"'{folder_id}' in parents and trashed=false"
        files_results = drive_service.files().list(
            q=query_files,
            fields="files(id, name, mimeType, webViewLink, webContentLink)",
            pageSize=50
        ).execute()

        files = files_results.get('files', [])
        if not files:
            logging.warning(f"Nenhum arquivo encontrado na subpasta {chosen_folder['name']}")
            return

        preview_link = None
        file_link = None

        # Procurar pela pasta preview dentro da subpasta para achar imagens
        preview_folder_id = None
        for f in files:
            if f['mimeType'] == 'application/vnd.google-apps.folder' and f['name'].lower() == 'preview':
                preview_folder_id = f['id']
                break

        if preview_folder_id:
            previews_results = drive_service.files().list(
                q=f"'{preview_folder_id}' in parents and trashed=false",
                fields="files(id, name, mimeType)",
                pageSize=10
            ).execute()
            previews = previews_results.get('files', [])
            if previews:
                chosen_preview = random.choice(previews)
                preview_id = chosen_preview['id']
                preview_link = f"https://drive.google.com/uc?id={preview_id}"
        if not preview_link:
            for f in files:
                if f['mimeType'].startswith('image/'):
                    preview_id = f['id']
                    preview_link = f"https://drive.google.com/uc?id={preview_id}"
                    break

        # Buscar arquivo para download (não pasta, não imagem)
        for f in files:
            if not f['mimeType'].startswith('application/vnd.google-apps.folder') and not f['mimeType'].startswith('image/'):
                file_link = f.get('webContentLink')
                if file_link:
                    break

        if not file_link:
            logging.warning(f"Arquivo para download não encontrado na subpasta {chosen_folder['name']}")
            return

        texto = f"🎁 Asset gratuito do dia: *{chosen_folder['name']}*\n\nLink para download: {file_link}"

        if preview_link:
            await application.bot.send_photo(chat_id=GROUP_FREE_ID, photo=preview_link, caption=texto, parse_mode='Markdown')
        else:
            await application.bot.send_message(chat_id=GROUP_FREE_ID, text=texto, parse_mode='Markdown')

        logging.info(f"Enviado asset '{chosen_folder['name']}' com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar asset do Drive: {e}")

# Comando para enviar asset do Drive manualmente
async def enviar_manual_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Enviando asset do Drive no grupo Free...")
    await enviar_asset_drive(context.application)

# Comando para limpar o grupo Free
async def limpar_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logging.info(f"Usuário {update.effective_user.id} iniciou limpeza do grupo Free")
        chat = await context.bot.get_chat(GROUP_FREE_ID)
        async for message in chat.iter_history(limit=100):
            try:
                await context.bot.delete_message(chat_id=GROUP_FREE_ID, message_id=message.message_id)
            except Exception as e:
                logging.warning(f"Erro ao deletar mensagem {message.message_id}: {e}")
        await update.message.reply_text("✅ Limpeza do grupo Free concluída (últimas 100 mensagens).")
    except Exception as e:
        logging.error(f"Erro ao limpar grupo: {e}")
        await update.message.reply_text("❌ Erro ao tentar limpar o grupo.")

# Flask webhook Stripe
app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.data
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except ValueError as e:
        logging.error(f"Payload inválido: {e}")
        return f"Invalid payload: {e}", 400
    except stripe.error.SignatureVerificationError as e:
        logging.error(f"Assinatura inválida: {e}")
        return f"Invalid signature: {e}", 400

    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        telegram_user_id = session.get('metadata', {}).get('telegram_user_id')

        if telegram_user_id:
            try:
                bot.send_message(chat_id=int(telegram_user_id), text="✅ Pagamento confirmado! Você será adicionado ao grupo VIP.")
                invite_link = bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                bot.send_message(chat_id=int(telegram_user_id), text=f"Aqui está o seu link para entrar no grupo VIP:\n{invite_link}")
                logging.info(f"Link enviado com sucesso para o usuário {telegram_user_id}")
            except Exception as e:
                logging.error(f"Erro ao enviar link convite para grupo VIP: {e}")
        else:
            logging.warning("Webhook recebido sem telegram_user_id no metadata.")

    return '', 200

def run_flask():
    port = int(os.environ.get("PORT", 4242))
    serve(app, host="0.0.0.0", port=port)

# Função principal async
async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pagar", pagar))
    application.add_handler(CommandHandler("get_chat_id", get_chat_id))
    application.add_handler(CommandHandler("enviar_drive", enviar_manual_drive))
    application.add_handler(CommandHandler("limpar_chat", limpar_chat))

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    logging.info("Bot iniciado e webhook ouvindo na porta %d", int(os.environ.get("PORT", 4242)))

    async def daily_task():
        while True:
            await enviar_asset_drive(application)
            await asyncio.sleep(86400)  # 24 horas

    asyncio.create_task(daily_task())

    await application.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot encerrado manualmente.")
