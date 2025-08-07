# scheduler.py
import asyncio
import datetime as dt
import logging
import pytz
from telegram.ext import ContextTypes, JobQueue

from database import SessionLocal, Config

async def verificar_vips(bot):
    """
    Verifica periodicamente os VIPs expirados e remove-os do grupo VIP.
    """
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
                        await bot.ban_chat_member(chat_id=int(bot.application.bot_data['GROUP_VIP_ID']), user_id=user_id)
                        logging.info(f"Usuário {user_id} removido do grupo VIP por validade expirada.")
                    except Exception as e:
                        logging.warning(f"Erro ao remover usuário {user_id} do grupo VIP: {e}")
            except Exception as e:
                logging.error(f"Erro processando validade VIP {cfg.key}: {e}")
        db_session.close()
        await asyncio.sleep(3600)  # Verifica a cada hora


async def send_preparation_message(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    group_free_id = int(bot.application.bot_data['GROUP_FREE_ID'])
    logging.info("Enviando mensagem de preparo para o grupo Free...")
    await bot.send_message(chat_id=group_free_id, text="🎁 Um novo asset gratuito será enviado em instantes! Fique ligado!")


async def send_daily_asset(context: ContextTypes.DEFAULT_TYPE):
    bot = context.bot
    group_free_id = int(bot.application.bot_data['GROUP_FREE_ID'])
    from drive_handler import enviar_asset_drive
    logging.info("Enviando asset gratuito para o grupo Free...")
    await enviar_asset_drive(bot, group_free_id)


def start_scheduler(application):
    """
    Configura os jobs agendados no JobQueue.
    """
    # Configura timezone São Paulo
    timezone = pytz.timezone("America/Sao_Paulo")

    # Salva IDs em bot_data para uso global
    application.bot_data['GROUP_FREE_ID'] = int(application.bot_data.get('GROUP_FREE_ID', 0)) or int(application.application_kwargs['group_free_id'])
    application.bot_data['GROUP_VIP_ID'] = int(application.bot_data.get('GROUP_VIP_ID', 0)) or int(application.application_kwargs['group_vip_id'])

    job_queue: JobQueue = application.job_queue

    # Agendar envio da mensagem de preparação 1 minuto antes do envio do asset (ex: 12:45)
    job_queue.run_daily(send_preparation_message, time=dt.time(hour=12, minute=45, tzinfo=timezone), name="prep_msg")

    # Agendar envio do asset às 12:50
    job_queue.run_daily(send_daily_asset, time=dt.time(hour=12, minute=50, tzinfo=timezone), name="daily_asset")

    # Rodar a verificação de VIPs em loop assíncrono (em background)
    asyncio.create_task(verificar_vips(application.bot))
