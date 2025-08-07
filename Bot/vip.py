# vip.py
import logging
import datetime
import asyncio
from database import SessionLocal, Config
from bot.telegram_webhook import bot  # Para enviar mensagens via bot
from telegram import Update
from telegram.ext import ContextTypes

GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID"))

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

async def listar_vips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db_session = SessionLocal()
    vip_configs = db_session.query(Config).filter(Config.key.like("vip_validade:%")).all()
    if not vip_configs:
        await update.message.reply_text("Nenhum usuário VIP ativo no momento.")
        db_session.close()
        return
    text = "Usuários VIP ativos:\n\n"
    now = datetime.datetime.utcnow()
    for cfg in vip_configs:
        user_id = cfg.key.split(":", 1)[1]
        validade = datetime.datetime.fromisoformat(cfg.value)
        status = "✅ Ativo" if validade > now else "❌ Expirado"
        text += f"- ID: {user_id} - Validade: {validade.strftime('%Y-%m-%d %H:%M:%S')} UTC - {status}\n"
    await update.message.reply_text(text)
    db_session.close()
