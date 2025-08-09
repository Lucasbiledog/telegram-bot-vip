import os
import logging
import asyncio
import random
import datetime as dt
import pytz

from dotenv import load_dotenv
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

from telegram import Update, InputFile
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    JobQueue,
)

import stripe

from database import init_db, SessionLocal, NotificationMessage, Config

import uvicorn

# --- Configurações iniciais ---
load_dotenv()
init_db()

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

app = FastAPI()

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID", "-1002791988432"))  # grupo Free (exemplo)
VIP_GROUP_ID = int(os.getenv("VIP_GROUP_ID", "-1002791988432"))    # grupo VIP
STORAGE_GROUP_ID = int(os.getenv("STORAGE_GROUP_ID", "-4806334341"))  # grupo onde estão os arquivos grandes

STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Defina seus admins aqui para liberar comandos restritos
ADMIN_IDS = [int(os.getenv("ADMIN_ID", "123456789"))]  # substitua 123456789 pelo seu ID

application = ApplicationBuilder().token(BOT_TOKEN).build()
bot = None
db = SessionLocal()

# --- Comandos básicos ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Fala! Esse bot te dá acesso a arquivos premium. Entre no grupo Free e veja como virar VIP. 🚀"
    )

async def get_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    msg = f"🆔 Seu ID de usuário: `{user_id}`\n💬 ID do chat: `{chat_id}`"
    await update.message.reply_text(msg, parse_mode="Markdown")

async def criar_checkout_session(telegram_user_id: int):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {"name": "Assinatura VIP Packs Unreal"},
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

# --- Testar pagamento restrito a admins ---
async def testar_pagamento(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    db_session = SessionLocal()
    validade = dt.datetime.utcnow() + dt.timedelta(days=7)
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
        invite_link = await bot.export_chat_invite_link(chat_id=VIP_GROUP_ID)
        await bot.send_message(chat_id=user_id, text=f"Aqui está o seu link para entrar no grupo VIP:\n{invite_link}")
    except Exception as e:
        await update.message.reply_text(f"Erro ao enviar link convite para grupo VIP: {e}")
        return
    await update.message.reply_text("Teste de pagamento simulado com sucesso!")

# --- Verificar validade dos VIPs e remover expirados ---
async def verificar_vips():
    while True:
        db_session = SessionLocal()
        now = dt.datetime.utcnow()
        vip_configs = db_session.query(Config).filter(Config.key.like("vip_validade:%")).all()
        for cfg in vip_configs:
            try:
                validade = dt.datetime.fromisoformat(cfg.value)
                user_id = int(cfg.key.split(":", 1)[1])
                if validade < now:
                    try:
                        await bot.ban_chat_member(chat_id=VIP_GROUP_ID, user_id=user_id)
                        logging.info(f"Usuário {user_id} removido do grupo VIP por validade expirada.")
                        # Opcional: remover do banco
                        db_session.delete(cfg)
                        db_session.commit()
                    except Exception as e:
                        logging.warning(f"Erro ao remover usuário {user_id} do grupo VIP: {e}")
            except Exception as e:
                logging.error(f"Erro processando validade VIP {cfg.key}: {e}")
        db_session.close()
        await asyncio.sleep(3600)  # roda a cada 1 hora

# --- Listar VIPs ---
async def listar_vips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ Você não tem permissão para usar este comando.")
        return

    db_session = SessionLocal()
    vip_configs = db_session.query(Config).filter(Config.key.like("vip_validade:%")).all()
    if not vip_configs:
        await update.message.reply_text("Nenhum VIP ativo no momento.")
        db_session.close()
        return

    texto = "Lista de VIPs ativos:\n\n"
    now = dt.datetime.utcnow()
    for cfg in vip_configs:
        user_id_vip = int(cfg.key.split(":", 1)[1])
        validade = dt.datetime.fromisoformat(cfg.value)
        restante = validade - now
        texto += f"- User ID: {user_id_vip} | Expira em: {restante.days} dias\n"

    db_session.close()
    await update.message.reply_text(texto)

# --- Enviar asset do grupo de armazenamento (pela mensagem com arquivo) ---
async def enviar_asset_grupo_armazenamento():
    try:
        # Busca as últimas 50 mensagens do grupo de armazenamento
        messages = []
        async for message in bot.get_chat(STORAGE_GROUP_ID).iter_history(limit=50):
            messages.append(message)

        if not messages:
            logging.warning("Nenhuma mensagem com arquivo encontrada no grupo de armazenamento.")
            return

        # Filtra mensagens que possuem arquivo (documento, foto, vídeo ou áudio)
        mensagens_com_arquivo = [m for m in messages if (m.document or m.photo or m.video or m.audio)]

        if not mensagens_com_arquivo:
            logging.warning("Nenhuma mensagem com arquivo encontrada no grupo de armazenamento.")
            return

        # Escolhe aleatoriamente um arquivo para enviar
        mensagem_escolhida = random.choice(mensagens_com_arquivo)

        chat_id = VIP_GROUP_ID
        texto = f"🎁 Asset do dia: enviado diretamente do grupo de armazenamento."

        # Envia conforme tipo de arquivo
        if mensagem_escolhida.document:
            await bot.send_document(chat_id=chat_id, document=mensagem_escolhida.document.file_id, caption=texto)
        elif mensagem_escolhida.photo:
            # photo é lista, pegar maior qualidade
            await bot.send_photo(chat_id=chat_id, photo=mensagem_escolhida.photo[-1].file_id, caption=texto)
        elif mensagem_escolhida.video:
            await bot.send_video(chat_id=chat_id, video=mensagem_escolhida.video.file_id, caption=texto)
        elif mensagem_escolhida.audio:
            await bot.send_audio(chat_id=chat_id, audio=mensagem_escolhida.audio.file_id, caption=texto)
        else:
            # fallback
            await bot.send_message(chat_id=chat_id, text=texto)

        logging.info("Asset enviado com sucesso para o grupo VIP.")
    except Exception as e:
        logging.error(f"Erro ao enviar asset do grupo de armazenamento: {e}")

# --- Enviar mensagem preparatória no grupo Free ---
async def send_preparation_message(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enviando mensagem de preparo para o grupo Free...")
    await bot.send_message(chat_id=GROUP_FREE_ID, text="🎁 Um novo asset gratuito será enviado em instantes! Fique ligado!")

# --- Enviar asset diário para o grupo Free ---
async def send_daily_asset_free(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enviando asset gratuito para o grupo Free...")

    # Vamos buscar o mesmo asset que foi enviado no VIP para manter consistência
    # Ou, se preferir, pode mudar para escolher do grupo de armazenamento ou outra lógica
    await enviar_asset_grupo_armazenamento()

# --- Enviar mensagem de bom dia diária ---
async def send_good_morning(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Enviando mensagem de bom dia para o grupo Free...")
    await bot.send_message(chat_id=GROUP_FREE_ID, text="☀️ Bom dia, galera! Que hoje seja um dia produtivo e cheio de aprendizado! 🚀")

# --- Webhooks Stripe e Telegram ---

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
                invite_link = await bot.export_chat_invite_link(chat_id=VIP_GROUP_ID)
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

# --- Registro dos comandos ---
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("getid", get_id))
application.add_handler(CommandHandler("pagar", pagar))
application.add_handler(CommandHandler("testarpagamento", testar_pagamento))
application.add_handler(CommandHandler("listarvips", listar_vips))

# --- Startup do bot ---
@app.on_event("startup")
async def on_startup():
    global bot

    await application.initialize()
    await application.start()

    bot = application.bot

    webhook_url = os.getenv("WEBHOOK_URL")
    if not webhook_url:
        raise RuntimeError("WEBHOOK_URL não definido no ambiente")

    await bot.set_webhook(url=webhook_url)

    import telegram  # garantir import aqui

    logging.info(f"Bot iniciado com sucesso. Versão PTB: {telegram.__version__}")

    # Start VIP expiration checker
    asyncio.create_task(verificar_vips())

    timezone = pytz.timezone("America/Sao_Paulo")
    job_queue: JobQueue = application.job_queue

    # Mensagem de bom dia todo dia às 8h
    job_queue.run_daily(send_good_morning, time=dt.time(hour=8, minute=0, tzinfo=timezone), name="bom_dia")

    # Mensagem preparatória 1 min antes do envio asset no grupo free às 12:45
    job_queue.run_daily(send_preparation_message, time=dt.time(hour=12, minute=44, tzinfo=timezone), name="prep_msg")

    # Envio do asset diário às 12:45
    job_queue.run_daily(send_daily_asset_free, time=dt.time(hour=12, minute=45, tzinfo=timezone), name="daily_asset")


# --- Rodar servidor ---
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)), reload=False)
