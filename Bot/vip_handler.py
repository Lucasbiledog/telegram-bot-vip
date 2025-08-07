# vip_handler.py
import logging
import datetime
import asyncio

from database import SessionLocal, Config

# Comando para testar pagamento fictício e adicionar VIP
async def testepagamento(update, context):
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
        await context.bot.send_message(chat_id=user_id, text="✅ Pagamento fictício confirmado! Você será adicionado ao grupo VIP.")
        invite_link = await context.bot.export_chat_invite_link(chat_id=context.bot.application.bot_data['GROUP_VIP_ID'])
        await context.bot.send_message(chat_id=user_id, text=f"Aqui está o seu link para entrar no grupo VIP:\n{invite_link}")
    except Exception as e:
        await update.message.reply_text(f"Erro ao enviar link convite para grupo VIP: {e}")
        return
    await update.message.reply_text("Teste de pagamento simulado com sucesso!")

# Verificar validade VIPs e remover expirados
async def verificar_vips(bot):
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
                        await bot.ban_chat_member(chat_id=bot.application.bot_data['GROUP_VIP_ID'], user_id=user_id)
                        logging.info(f"Usuário {user_id} removido do grupo VIP por validade expirada.")
                    except Exception as e:
                        logging.warning(f"Erro ao remover usuário {user_id} do grupo VIP: {e}")
            except Exception as e:
                logging.error(f"Erro processando validade VIP {cfg.key}: {e}")
        db_session.close()
        await asyncio.sleep(3600)

# Comando para listar VIPs no privado do bot
async def listar_vips(update, context):
    db_session = SessionLocal()
    vip_configs = db_session.query(Config).filter(Config.key.like("vip_validade:%")).all()
    if not vip_configs:
        await update.message.reply_text("Nenhum VIP ativo encontrado.")
        db_session.close()
        return
    texto = "Lista de VIPs ativos:\n\n"
    for cfg in vip_configs:
        try:
            user_id = int(cfg.key.split(":", 1)[1])
            validade = datetime.datetime.fromisoformat(cfg.value)
            texto += f"- ID: {user_id} - Validade até: {validade.strftime('%d/%m/%Y %H:%M UTC')}\n"
        except Exception:
            continue
    db_session.close()
    await update.message.reply_text(texto)
