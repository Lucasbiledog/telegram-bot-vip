# main_launcher.py - Gerenciador de VIPs e inicialização do bot

import os
import logging
import asyncio
from datetime import datetime, timedelta
import pytz
from dotenv import load_dotenv

from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, JobQueue
from telegram import Update

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse

# Carregar variáveis de ambiente e configurar logging
load_dotenv()
logging.basicConfig(level=logging.INFO)

WEBHOOK_URL = os.getenv("WEBHOOK_URL")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GROUP_ID = int(os.getenv("FREE_GROUP_ID", "0"))

if not WEBHOOK_URL or not TELEGRAM_TOKEN or GROUP_ID == 0:
    raise ValueError("Configure WEBHOOK_URL, TELEGRAM_TOKEN e FREE_GROUP_ID corretamente no .env")

# Banco de dados simulado para VIPs
vips = {
    123456789: {"nome": "João", "expira_em": datetime(2025, 8, 10)},
    987654321: {"nome": "Maria", "expira_em": datetime(2025, 8, 5)},
}

# Funções VIP
async def listar_vips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type != "private":
        await update.message.reply_text("Envie este comando no privado do bot.")
        return

    if not vips:
        await update.message.reply_text("Nenhum VIP encontrado.")
        return

    mensagem = "👑 Lista de VIPs Ativos:\n"
    for user_id, info in vips.items():
        nome = info["nome"]
        expira_em = info["expira_em"].strftime("%d/%m/%Y")
        mensagem += f"• {nome} (expira em {expira_em})\n"

    await update.message.reply_text(mensagem)

async def remover_vips_expirados(context: ContextTypes.DEFAULT_TYPE):
    logging.info("Verificando VIPs expirados...")
    agora = datetime.now()
    expirados = [user_id for user_id, info in vips.items() if info["expira_em"] < agora]

    for user_id in expirados:
        logging.info(f"Removendo VIP expirado: {user_id}")
        del vips[user_id]
        # Aqui você pode usar: await context.bot.ban_chat_member(vip_group_id, user_id) se desejar

# Função start simples
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Olá! Bot funcionando com sucesso na Render!")

# Inicialização FastAPI e Telegram
app = FastAPI()
application = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
application.add_handler(CommandHandler("start", start))
application.add_handler(CommandHandler("listarvips", listar_vips))

@app.post("/webhook")
async def telegram_webhook(req: Request):
    try:
        json_data = await req.json()
        update = Update.de_json(json_data, application.bot)
        await application.update_queue.put(update)
        return PlainTextResponse("ok")
    except Exception as e:
        logging.exception("Erro ao processar update do Telegram:")
        raise HTTPException(status_code=400, detail=str(e))

@app.on_event("startup")
async def on_startup():
    await application.bot.set_webhook(url=WEBHOOK_URL)
    logging.info(f"Webhook configurado: {WEBHOOK_URL}")

    # Agendar verificação de VIPs expirados diariamente às 03:00 (horário de Brasília)
    timezone = pytz.timezone("America/Sao_Paulo")
    agora = datetime.now(timezone)
    run_time = agora.replace(hour=3, minute=0, second=0, microsecond=0)
    if run_time < agora:
        run_time += timedelta(days=1)

    job_queue: JobQueue = application.job_queue
    job_queue.run_daily(remover_vips_expirados, time=run_time.timetz(), name="verificacao_vips")

    asyncio.create_task(application.initialize())
    asyncio.create_task(application.start())
