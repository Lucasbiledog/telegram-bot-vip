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
from waitress import serve  # Para rodar o Flask em produção (Windows recomendado)

nest_asyncio.apply()
load_dotenv()

# --- Configurações ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID"))
GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID"))
STRIPE_API_KEY = os.getenv("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")
GOOGLE_DRIVE_FREE_FOLDER_LINKS = os.getenv("GOOGLE_DRIVE_FREE_FOLDER_LINKS", "").split(",")

stripe.api_key = STRIPE_API_KEY
bot = telegram_api.Bot(token=BOT_TOKEN)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# --- Handlers Telegram ---

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

# --- Envio Diário ---

async def enviar_asset_gratuito():
    if not GOOGLE_DRIVE_FREE_FOLDER_LINKS:
        logging.warning("Nenhum link gratuito configurado.")
        return
    asset_link = random.choice(GOOGLE_DRIVE_FREE_FOLDER_LINKS)
    try:
        bot.send_message(chat_id=GROUP_FREE_ID, text=f"🎁 Asset gratuito do dia:\n{asset_link}")
        logging.info("Asset gratuito enviado com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar asset gratuito: {e}")

# --- Flask webhook Stripe ---

app = Flask(__name__)

@app.route("/webhook", methods=["POST"])
def webhook():
    payload = request.data
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
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
                bot.send_message(
                    chat_id=int(telegram_user_id),
                    text="✅ Pagamento confirmado! Você será adicionado ao grupo VIP."
                )
                invite_link = bot.export_chat_invite_link(chat_id=GROUP_VIP_ID)
                bot.send_message(
                    chat_id=int(telegram_user_id),
                    text=f"Aqui está o seu link para entrar no grupo VIP:\n{invite_link}"
                )
                logging.info(f"Link enviado com sucesso para o usuário {telegram_user_id}")
            except Exception as e:
                logging.error(f"Erro ao enviar link convite para grupo VIP: {e}")
        else:
            logging.warning("Webhook recebido sem telegram_user_id no metadata.")

    return '', 200

# --- Função para rodar Flask em thread separada ---

def run_flask():
    serve(app, host="0.0.0.0", port=4242)

# --- Função principal async ---

async def main():
    application = ApplicationBuilder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pagar", pagar))
    application.add_handler(CommandHandler("get_chat_id", get_chat_id))

    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    # Dispara a tarefa de envio diário (exemplo: todo dia às 9h)
    async def daily_task():
        while True:
            await enviar_asset_gratuito()
            await asyncio.sleep(86400)  # 24 horas

    asyncio.create_task(daily_task())

    logging.info("Bot iniciado e webhook ouvindo na porta 4242")
    await application.run_polling()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot encerrado manualmente.")
