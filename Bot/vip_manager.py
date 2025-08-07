import logging
import datetime
from database import SessionLocal, Config
from telegram import Update
from telegram.ext import ContextTypes

async def listar_vips(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = SessionLocal()
    try:
        now = datetime.datetime.utcnow()
        vip_configs = db.query(Config).filter(Config.key.like("vip_validade:%")).all()
        if not vip_configs:
            await update.message.reply_text("Nenhum VIP ativo no momento.")
            return

        texto = "Lista de VIPs ativos:\n\n"
        for cfg in vip_configs:
            validade = datetime.datetime.fromisoformat(cfg.value)
            if validade > now:
                vip_id = int(cfg.key.split(":", 1)[1])
                dias_restantes = (validade - now).days
                texto += f"- ID: {vip_id}, expira em {dias_restantes} dias\n"

        if texto == "Lista de VIPs ativos:\n\n":
            texto = "Nenhum VIP ativo no momento."

        await update.message.reply_text(texto)
    except Exception as e:
        logging.error(f"Erro ao listar VIPs: {e}")
        await update.message.reply_text("Erro ao buscar lista de VIPs.")
    finally:
        db.close()
