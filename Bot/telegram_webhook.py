# telegram_webhook.py
import os
import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)

from bot import vip
from bot.drive_handler import enviar_manual_drive, limpar_chat, enviar_asset_drive  # Supondo que criamos esse arquivo depois

BOT_TOKEN = os.getenv("BOT_TOKEN")

application = ApplicationBuilder().token(BOT_TOKEN).build()

bot = None  # Para uso global depois

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Fala! Esse bot te dá acesso a arquivos premium. Entre no grupo Free e veja como virar VIP. 🚀"
    )

async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from bot.stripe_handler import criar_checkout_session  # Import dinâmico para evitar dependência circular
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

def setup_telegram_webhook(app):
    # Adiciona handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("pagar", pagar))
    application.add_handler(CommandHandler("get_chat_id", get_chat_id))
    application.add_handler(CommandHandler("enviar_drive", enviar_manual_drive))
    application.add_handler(CommandHandler("limpar_chat", limpar_chat))
    application.add_handler(CommandHandler("testepagamento", vip.comando_testepagamento))
    application.add_handler(CommandHandler("list_vip", vip.listar_vips))

    # Guardar referência global do bot para usar em outros módulos
    global bot
    bot = application.bot

    # Configura webhook no FastAPI
    @app.post("/webhook")
    async def telegram_webhook(request):
        from fastapi import Request
        try:
            data = await request.json()
            update = Update.de_json(data, bot)
            await application.process_update(update)
        except Exception as e:
            logging.error(f"Erro processando update Telegram: {e}")
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Invalid update")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("", status_code=200)
