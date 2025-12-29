"""
Sistema de Gerenciamento VIP
==============================
- Envio de mensagens pendentes no /start
- Log de membros entrando/saindo do grupo
- Avisos de expiração (5 dias antes)
- Remoção automática ao expirar
"""

import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional
from telegram import Update, Bot, ChatMemberUpdated
from telegram.ext import ContextTypes
from sqlalchemy import and_, or_

LOG = logging.getLogger(__name__)

# =========================
# Enviar Mensagens Pendentes
# =========================
async def send_pending_to_user(bot: Bot, user_id: int, username: Optional[str] = None):
    """
    Envia mensagens pendentes para um usuário específico
    Chamado automaticamente quando usuário entra no grupo VIP
    """
    try:
        from main import SessionLocal, LOGS_GROUP_ID
        from models import PendingNotification

        with SessionLocal() as s:
            # Buscar mensagens pendentes
            pending = s.query(PendingNotification).filter(
                and_(
                    PendingNotification.user_id == user_id,
                    PendingNotification.sent == False
                )
            ).all()

            if not pending:
                return  # Sem mensagens pendentes

            LOG.info(f"[PENDING] 📨 Encontradas {len(pending)} mensagens pendentes para user {user_id}")

            sent_count = 0
            for notif in pending:
                try:
                    await bot.send_message(
                        chat_id=user_id,
                        text=notif.message,
                        parse_mode="HTML"
                    )

                    # Marcar como enviada
                    notif.sent = True
                    notif.sent_at = datetime.now(timezone.utc)
                    sent_count += 1
                    LOG.info(f"[PENDING] ✅ Mensagem pendente enviada (ID: {notif.id})")

                except Exception as e:
                    LOG.error(f"[PENDING] ❌ Erro ao enviar mensagem pendente: {e}")

            s.commit()

            # Enviar log para grupo de logs
            if sent_count > 0:
                try:
                    log_msg = (
                        f"📬 <b>MENSAGENS PENDENTES ENVIADAS</b>\n"
                        f"👤 User: <code>{user_id}</code> (@{username or 'sem_username'})\n"
                        f"📨 Quantidade: {sent_count} mensagem(ns)\n"
                        f"🔔 Enviadas automaticamente ao entrar no grupo VIP"
                    )
                    await bot.send_message(
                        chat_id=LOGS_GROUP_ID,
                        text=log_msg,
                        parse_mode="HTML"
                    )
                except Exception as log_error:
                    LOG.warning(f"[PENDING] Erro ao enviar log: {log_error}")

    except Exception as e:
        LOG.error(f"[PENDING] Erro ao processar mensagens pendentes: {e}")


async def send_pending_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Envia mensagens pendentes quando o usuário dá /start
    (mantido para compatibilidade, mas agora é automático ao entrar no grupo)
    """
    user_id = update.effective_user.id
    username = update.effective_user.username if update.effective_user else None
    await send_pending_to_user(context.bot, user_id, username)


# =========================
# Log de Membros Entrando/Saindo
# =========================
async def log_member_change(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Registra entrada/saída de membros no grupo VIP e envia para grupo de logs"""
    try:
        from main import SessionLocal, GROUP_VIP_ID, LOGS_GROUP_ID
        from models import MemberLog, User

        result: ChatMemberUpdated = update.chat_member

        # Verificar se é no grupo VIP
        if result.chat.id != GROUP_VIP_ID:
            return

        user = result.new_chat_member.user
        old_status = result.old_chat_member.status
        new_status = result.new_chat_member.status

        # Determinar ação
        action = None
        if old_status in ["left", "kicked"] and new_status in ["member", "administrator"]:
            action = "joined"
        elif old_status in ["member", "administrator"] and new_status in ["left", "kicked"]:
            action = "left" if new_status == "left" else "removed"

        if not action:
            return  # Sem mudança relevante

        with SessionLocal() as s:
            # Buscar VIP do usuário
            vip_user = s.query(User).filter(User.tg_id == user.id).first()
            vip_until = vip_user.vip_until if vip_user else None

            # Criar log
            log = MemberLog(
                user_id=user.id,
                username=user.username,
                first_name=user.first_name,
                action=action,
                vip_until=vip_until
            )
            s.add(log)
            s.commit()

            LOG.info(
                f"[MEMBER-LOG] {action.upper()}: {user.first_name} (@{user.username or 'sem_username'}) "
                f"| VIP até: {vip_until.strftime('%d/%m/%Y %H:%M') if vip_until else 'N/A'}"
            )

            # Enviar log para grupo de logs
            try:
                action_emoji = {
                    "joined": "✅",
                    "left": "👋",
                    "removed": "🚫"
                }.get(action, "❓")

                log_msg = (
                    f"{action_emoji} <b>{action.upper()}</b>\n"
                    f"👤 {user.first_name or 'N/A'} (@{user.username or 'sem_username'})\n"
                    f"🆔 ID: <code>{user.id}</code>\n"
                    f"📅 {log.created_at.strftime('%d/%m/%Y %H:%M:%S')}\n"
                )

                if vip_until:
                    now = datetime.now(timezone.utc)
                    if vip_until > now:
                        days_left = (vip_until - now).days
                        log_msg += f"⏰ VIP até: {vip_until.strftime('%d/%m/%Y %H:%M')} ({days_left} dias)\n"
                    else:
                        log_msg += f"⏰ VIP expirado em: {vip_until.strftime('%d/%m/%Y %H:%M')}\n"

                await context.bot.send_message(
                    chat_id=LOGS_GROUP_ID,
                    text=log_msg,
                    parse_mode="HTML"
                )
                LOG.info(f"[MEMBER-LOG] 📬 Log enviado para grupo {LOGS_GROUP_ID}")

            except Exception as log_error:
                LOG.warning(f"[MEMBER-LOG] Erro ao enviar para grupo de logs: {log_error}")

            # SE O USUÁRIO ENTROU, tentar enviar mensagens pendentes
            if action == "joined":
                await send_pending_to_user(context.bot, user.id, user.username)

    except Exception as e:
        LOG.error(f"[MEMBER-LOG] Erro ao registrar mudança de membro: {e}")


# =========================
# Sistema de Verificação de Expirações
# =========================
async def check_expirations(context: ContextTypes.DEFAULT_TYPE):
    """
    Verifica VIPs expirando e toma ações:
    - 5 dias antes: envia aviso
    - Após expirar: remove do grupo
    """
    try:
        from main import SessionLocal, GROUP_VIP_ID
        from models import User, MemberLog

        now = datetime.now(timezone.utc)
        five_days_later = now + timedelta(days=5)

        with SessionLocal() as s:
            # 1. Buscar VIPs expirando em 5 dias
            expiring_soon = s.query(User).filter(
                and_(
                    User.is_vip == True,
                    User.vip_until != None,
                    User.vip_until > now,
                    User.vip_until <= five_days_later
                )
            ).all()

            for user in expiring_soon:
                await send_expiration_warning(context.bot, user)

            # 2. Buscar VIPs expirados
            expired = s.query(User).filter(
                and_(
                    User.is_vip == True,
                    User.vip_until != None,
                    User.vip_until <= now
                )
            ).all()

            for user in expired:
                await remove_expired_vip(context.bot, user, GROUP_VIP_ID, s)

            s.commit()

    except Exception as e:
        LOG.error(f"[EXPIRATION-CHECK] Erro ao verificar expirações: {e}")


async def send_expiration_warning(bot: Bot, user):
    """Envia aviso de que VIP está expirando em breve"""
    try:
        from main import LOGS_GROUP_ID

        days_left = (user.vip_until - datetime.now(timezone.utc)).days

        if days_left < 0:
            return  # Já expirou

        msg = (
            f"⚠️ <b>AVISO DE EXPIRAÇÃO VIP</b>\n\n"
            f"Olá! Seu acesso VIP está expirando em breve.\n\n"
            f"⏰ <b>Expira em: {days_left} dia(s)</b>\n"
            f"📅 Data de expiração: <b>{user.vip_until.strftime('%d/%m/%Y às %H:%M')}</b>\n\n"
            f"💎 Para renovar seu VIP, faça um novo pagamento!\n\n"
            f"Obrigado por fazer parte do nosso grupo VIP! 🙏"
        )

        await bot.send_message(
            chat_id=user.tg_id,
            text=msg,
            parse_mode="HTML"
        )

        LOG.info(f"[EXPIRATION-WARNING] ⚠️ Aviso enviado para user {user.tg_id} ({days_left} dias restantes)")

        # Enviar log para grupo de logs
        try:
            log_msg = (
                f"⚠️ <b>AVISO DE EXPIRAÇÃO ENVIADO</b>\n"
                f"👤 User: <code>{user.tg_id}</code> (@{user.username or 'sem_username'})\n"
                f"⏰ Expira em: <b>{days_left} dia(s)</b>\n"
                f"📅 Data: {user.vip_until.strftime('%d/%m/%Y %H:%M')}"
            )
            await bot.send_message(
                chat_id=LOGS_GROUP_ID,
                text=log_msg,
                parse_mode="HTML"
            )
        except Exception as log_error:
            LOG.warning(f"[EXPIRATION-WARNING] Erro ao enviar log: {log_error}")

    except Exception as e:
        LOG.warning(f"[EXPIRATION-WARNING] Erro ao enviar aviso para user {user.tg_id}: {e}")


async def remove_expired_vip(bot: Bot, user, group_id: int, session):
    """Remove usuário do grupo quando VIP expira"""
    try:
        from main import LOGS_GROUP_ID

        # Remover do grupo
        await bot.ban_chat_member(chat_id=group_id, user_id=user.tg_id)
        await bot.unban_chat_member(chat_id=group_id, user_id=user.tg_id)

        # Atualizar banco de dados
        user.is_vip = False

        # Criar log
        log = MemberLog(
            user_id=user.tg_id,
            username=user.username,
            first_name="",
            action="removed",
            vip_until=user.vip_until
        )
        session.add(log)

        LOG.info(f"[EXPIRATION] 🚫 User {user.tg_id} removido do grupo VIP (expirado)")

        # Enviar mensagem informando
        msg = (
            f"⏰ <b>VIP EXPIRADO</b>\n\n"
            f"Seu acesso VIP expirou e você foi removido do grupo.\n\n"
            f"📅 Data de expiração: <b>{user.vip_until.strftime('%d/%m/%Y às %H:%M')}</b>\n\n"
            f"💎 Para renovar seu acesso VIP, faça um novo pagamento!\n\n"
            f"Obrigado por ter feito parte do nosso grupo! 🙏"
        )

        await bot.send_message(
            chat_id=user.tg_id,
            text=msg,
            parse_mode="HTML"
        )

        LOG.info(f"[EXPIRATION] 📬 Mensagem de expiração enviada para user {user.tg_id}")

        # Enviar log para grupo de logs
        try:
            log_msg = (
                f"🚫 <b>VIP EXPIRADO - USUÁRIO REMOVIDO</b>\n"
                f"👤 User: <code>{user.tg_id}</code> (@{user.username or 'sem_username'})\n"
                f"📅 Expirou em: {user.vip_until.strftime('%d/%m/%Y %H:%M')}\n"
                f"❌ Removido do grupo VIP"
            )
            await bot.send_message(
                chat_id=LOGS_GROUP_ID,
                text=log_msg,
                parse_mode="HTML"
            )
            LOG.info(f"[EXPIRATION] 📬 Log de remoção enviado para grupo {LOGS_GROUP_ID}")
        except Exception as log_error:
            LOG.warning(f"[EXPIRATION] Erro ao enviar log: {log_error}")

    except Exception as e:
        LOG.error(f"[EXPIRATION] Erro ao remover user {user.tg_id}: {e}")


# =========================
# Comando Admin para Ver Logs
# =========================
async def view_member_logs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /logs para ver logs de entrada/saída (admin apenas)"""
    try:
        from main import SessionLocal
        from config import OWNER_ID
        from models import MemberLog

        # Verificar se é admin
        if update.effective_user.id != OWNER_ID:
            return

        # Pegar limite (padrão 20)
        limit = 20
        if context.args and context.args[0].isdigit():
            limit = int(context.args[0])
            limit = min(limit, 100)  # Máximo 100

        with SessionLocal() as s:
            logs = s.query(MemberLog).order_by(
                MemberLog.created_at.desc()
            ).limit(limit).all()

            if not logs:
                await update.message.reply_text("📝 Nenhum log encontrado.")
                return

            msg = f"📊 <b>ÚLTIMOS {len(logs)} LOGS DE MEMBROS</b>\n\n"

            for log in logs:
                action_emoji = {
                    "joined": "✅",
                    "left": "👋",
                    "removed": "🚫"
                }.get(log.action, "❓")

                msg += (
                    f"{action_emoji} <b>{log.action.upper()}</b>\n"
                    f"👤 {log.first_name or 'N/A'} (@{log.username or 'sem_username'})\n"
                    f"🆔 ID: {log.user_id}\n"
                    f"📅 {log.created_at.strftime('%d/%m/%Y %H:%M:%S')}\n"
                )

                if log.vip_until:
                    msg += f"⏰ VIP até: {log.vip_until.strftime('%d/%m/%Y %H:%M')}\n"

                msg += "\n"

            # Telegram tem limite de 4096 caracteres
            if len(msg) > 4000:
                msg = msg[:4000] + "\n\n... (truncado)"

            await update.message.reply_text(msg, parse_mode="HTML")

    except Exception as e:
        LOG.error(f"[LOGS-CMD] Erro: {e}")
        await update.message.reply_text(f"❌ Erro ao buscar logs: {e}")


async def check_vip_status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /meu_vip para usuário verificar status"""
    try:
        from main import SessionLocal
        from models import User

        user_id = update.effective_user.id

        with SessionLocal() as s:
            user = s.query(User).filter(User.tg_id == user_id).first()

            if not user or not user.is_vip:
                await update.message.reply_text(
                    "❌ Você não possui VIP ativo.\n\n"
                    "💎 Faça um pagamento para ativar seu VIP!"
                )
                return

            if user.vip_until:
                now = datetime.now(timezone.utc)
                days_left = (user.vip_until - now).days

                msg = (
                    f"✅ <b>SEU STATUS VIP</b>\n\n"
                    f"📅 VIP ativo até: <b>{user.vip_until.strftime('%d/%m/%Y às %H:%M')}</b>\n"
                    f"⏰ Tempo restante: <b>{days_left} dia(s)</b>\n\n"
                )

                if days_left <= 5:
                    msg += "⚠️ <b>Seu VIP está expirando em breve! Renove agora!</b>\n\n"

                msg += "💎 Aproveite o conteúdo exclusivo!"

                await update.message.reply_text(msg, parse_mode="HTML")
            else:
                await update.message.reply_text("✅ Você possui VIP ativo (sem data de expiração).")

    except Exception as e:
        LOG.error(f"[CHECK-VIP] Erro: {e}")
        await update.message.reply_text(f"❌ Erro ao verificar status: {e}")
