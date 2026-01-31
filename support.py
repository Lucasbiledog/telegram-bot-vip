"""
Sistema de Suporte via Tickets.

Handlers para usuários abrirem tickets e admins responderem/fecharem.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
)

LOG = logging.getLogger("support")

# Estado da conversa de suporte
WAITING_DESCRIPTION = 0


# =====================
# Handlers do Usuário
# =====================

async def support_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Callback quando usuário clica no botão Suporte."""
    query = update.callback_query
    await query.answer()
    await query.message.reply_text(
        "📩 <b>Suporte</b>\n\n"
        "Descreva seu problema ou dúvida em uma mensagem.\n"
        "Nossa equipe responderá o mais breve possível.",
        parse_mode="HTML",
    )
    return WAITING_DESCRIPTION


async def support_receive_description(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Recebe a descrição do problema e cria o ticket."""
    from main import SessionLocal, is_admin, log_to_group
    from models import SupportTicket

    user = update.effective_user
    description = update.message.text.strip()

    if not description:
        await update.message.reply_text("Por favor, descreva seu problema.")
        return WAITING_DESCRIPTION

    # Criar ticket no banco
    with SessionLocal() as s:
        ticket = SupportTicket(
            user_id=user.id,
            username=user.username or "",
            first_name=user.first_name or "",
            description=description,
            status="open",
        )
        s.add(ticket)
        s.commit()
        ticket_id = ticket.id

    await update.message.reply_text(
        f"✅ <b>Ticket #{ticket_id} criado com sucesso!</b>\n\n"
        f"Sua solicitação foi registrada. Você receberá uma resposta em breve.",
        parse_mode="HTML",
    )

    # Notificar admins no grupo de logs
    user_display = f"@{user.username}" if user.username else user.first_name
    await log_to_group(
        f"🎫 <b>Novo Ticket #{ticket_id}</b>\n"
        f"👤 Usuário: {user_display} (ID: <code>{user.id}</code>)\n"
        f"📝 {description[:300]}\n\n"
        f"Responder: <code>/reply {ticket_id} sua resposta aqui</code>"
    )

    return ConversationHandler.END


async def support_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancela a criação de ticket."""
    await update.message.reply_text("Suporte cancelado.")
    return ConversationHandler.END


# =====================
# Handlers do Admin
# =====================

async def tickets_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Lista tickets abertos. Uso: /tickets"""
    from main import SessionLocal, is_admin
    from models import SupportTicket

    if not (update.effective_user and is_admin(update.effective_user.id)):
        return

    with SessionLocal() as s:
        tickets = (
            s.query(SupportTicket)
            .filter(SupportTicket.status.in_(["open", "answered"]))
            .order_by(SupportTicket.created_at.desc())
            .limit(20)
            .all()
        )

    if not tickets:
        await update.effective_message.reply_text("Nenhum ticket aberto.")
        return

    lines = ["📋 <b>Tickets Abertos</b>\n"]
    for t in tickets:
        user_display = f"@{t.username}" if t.username else (t.first_name or str(t.user_id))
        status_emoji = "🟢" if t.status == "open" else "🟡"
        created = t.created_at.strftime("%d/%m %H:%M") if t.created_at else "?"
        lines.append(
            f"{status_emoji} <b>#{t.id}</b> | {user_display} | {created}\n"
            f"   {t.description[:80]}"
        )

    await update.effective_message.reply_text("\n\n".join(lines), parse_mode="HTML")


async def reply_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Responde a um ticket. Uso: /reply <ticket_id> <mensagem>"""
    from main import SessionLocal, is_admin, application
    from models import SupportTicket

    if not (update.effective_user and is_admin(update.effective_user.id)):
        return

    args = update.message.text.split(maxsplit=2)
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Uso: <code>/reply &lt;ticket_id&gt; &lt;mensagem&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        ticket_id = int(args[1])
    except ValueError:
        await update.effective_message.reply_text("ID do ticket inválido.")
        return

    reply_text = args[2]

    with SessionLocal() as s:
        ticket = s.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
        if not ticket:
            await update.effective_message.reply_text(f"Ticket #{ticket_id} não encontrado.")
            return

        if ticket.status == "closed":
            await update.effective_message.reply_text(f"Ticket #{ticket_id} já está fechado.")
            return

        ticket.admin_reply = reply_text
        ticket.status = "answered"
        ticket.replied_at = datetime.now(timezone.utc)
        ticket.updated_at = datetime.now(timezone.utc)
        user_id = ticket.user_id
        s.commit()

    # Enviar resposta ao usuário via bot
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=(
                f"📩 <b>Resposta ao Ticket #{ticket_id}</b>\n\n"
                f"{reply_text}"
            ),
            parse_mode="HTML",
        )
        await update.effective_message.reply_text(
            f"✅ Resposta enviada ao usuário (Ticket #{ticket_id})."
        )
    except Exception as e:
        LOG.warning(f"Falha ao enviar resposta do ticket #{ticket_id}: {e}")
        await update.effective_message.reply_text(
            f"⚠️ Ticket atualizado, mas não foi possível enviar mensagem ao usuário: {e}"
        )


async def close_ticket_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fecha um ticket. Uso: /close_ticket <ticket_id>"""
    from main import SessionLocal, is_admin, application
    from models import SupportTicket

    if not (update.effective_user and is_admin(update.effective_user.id)):
        return

    args = update.message.text.split(maxsplit=1)
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Uso: <code>/close_ticket &lt;ticket_id&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        ticket_id = int(args[1].strip())
    except ValueError:
        await update.effective_message.reply_text("ID do ticket inválido.")
        return

    with SessionLocal() as s:
        ticket = s.query(SupportTicket).filter(SupportTicket.id == ticket_id).first()
        if not ticket:
            await update.effective_message.reply_text(f"Ticket #{ticket_id} não encontrado.")
            return

        if ticket.status == "closed":
            await update.effective_message.reply_text(f"Ticket #{ticket_id} já está fechado.")
            return

        ticket.status = "closed"
        ticket.updated_at = datetime.now(timezone.utc)
        user_id = ticket.user_id
        s.commit()

    # Notificar usuário
    try:
        await application.bot.send_message(
            chat_id=user_id,
            text=(
                f"📩 <b>Ticket #{ticket_id} encerrado</b>\n\n"
                f"Seu ticket foi fechado pela equipe de suporte.\n"
                f"Se precisar de mais ajuda, abra um novo ticket com /start."
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        LOG.warning(f"Falha ao notificar fechamento do ticket #{ticket_id}: {e}")

    await update.effective_message.reply_text(f"✅ Ticket #{ticket_id} fechado.")


async def msg_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envia mensagem direta para um usuário. Uso: /msg <user_id> <texto>"""
    from main import is_admin, application

    if not (update.effective_user and is_admin(update.effective_user.id)):
        return

    args = update.message.text.split(maxsplit=2)
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Uso: <code>/msg &lt;user_id&gt; &lt;texto&gt;</code>",
            parse_mode="HTML",
        )
        return

    try:
        target_user_id = int(args[1])
    except ValueError:
        await update.effective_message.reply_text("ID do usuário inválido.")
        return

    text = args[2]

    try:
        await application.bot.send_message(
            chat_id=target_user_id,
            text=text,
            parse_mode="HTML",
        )
        await update.effective_message.reply_text(
            f"✅ Mensagem enviada para o usuário {target_user_id}."
        )
    except Exception as e:
        LOG.warning(f"Falha ao enviar mensagem para {target_user_id}: {e}")
        await update.effective_message.reply_text(
            f"❌ Falha ao enviar mensagem: {e}"
        )


# =====================
# ConversationHandler de Suporte
# =====================

def get_support_conversation_handler() -> ConversationHandler:
    """Retorna o ConversationHandler do sistema de suporte."""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(support_start_callback, pattern="^support_start$"),
        ],
        states={
            WAITING_DESCRIPTION: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, support_receive_description),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", support_cancel),
        ],
        per_message=False,
        name="support_conversation",
    )
