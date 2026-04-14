"""
Sistema de Suporte via Tickets.

Handlers para usuários abrirem tickets e admins responderem/fecharem.
Usa context.user_data para rastrear estado (sem ConversationHandler).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    CommandHandler,
    filters,
)

LOG = logging.getLogger("support")

# Chave no user_data para indicar que estamos esperando descrição
_SUPPORT_WAITING = "support_waiting_description"


# =====================
# Handlers do Usuário
# =====================

async def support_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback quando usuário clica no botão Suporte."""
    query = update.callback_query
    await query.answer()

    # Marcar que estamos esperando a descrição
    context.user_data[_SUPPORT_WAITING] = True

    await query.message.reply_text(
        "📩 <b>Suporte</b>\n\n"
        "Descreva seu problema ou dúvida na próxima mensagem.\n"
        "Nossa equipe responderá o mais breve possível.\n\n"
        "Para cancelar, envie /cancelar_suporte",
        parse_mode="HTML",
    )


async def support_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Captura texto do usuário em chat privado:
    - Se _SUPPORT_WAITING=True → cria novo ticket
    - Se já existe ticket aberto/respondido → adiciona follow-up e notifica admin
    """
    # Só funciona em chat privado
    if update.effective_chat.type != "private":
        return

    from main import SessionLocal, log_to_group
    from models import SupportTicket

    user = update.effective_user
    text = (update.message.text or "").strip()

    if not text:
        return

    # ── Caso 1: aguardando abertura de novo ticket ────────────────────────────
    if context.user_data.get(_SUPPORT_WAITING):
        context.user_data.pop(_SUPPORT_WAITING, None)

        with SessionLocal() as s:
            ticket = SupportTicket(
                user_id=user.id,
                username=user.username or "",
                first_name=user.first_name or "",
                description=text,
                status="open",
            )
            s.add(ticket)
            s.commit()
            ticket_id = ticket.id

        await update.message.reply_text(
            f"✅ <b>Ticket #{ticket_id} criado com sucesso!</b>\n\n"
            f"Sua solicitação foi registrada. Você receberá uma resposta em breve.\n"
            f"Para cancelar/fechar, envie /cancelar_suporte",
            parse_mode="HTML",
        )

        user_display = f"@{user.username}" if user.username else user.first_name
        await log_to_group(
            f"🎫 <b>Novo Ticket #{ticket_id}</b>\n"
            f"👤 Usuário: {user_display} (ID: <code>{user.id}</code>)\n"
            f"📝 {text[:300]}\n\n"
            f"Responder: <code>/reply {ticket_id} sua resposta aqui</code>\n"
            f"Fechar: <code>/close_ticket {ticket_id}</code>"
        )
        return

    # ── Caso 2: follow-up em ticket já aberto/respondido ─────────────────────
    with SessionLocal() as s:
        ticket = (
            s.query(SupportTicket)
            .filter(
                SupportTicket.user_id == user.id,
                SupportTicket.status.in_(["open", "answered"]),
            )
            .order_by(SupportTicket.created_at.desc())
            .first()
        )
        if not ticket:
            return  # Sem ticket ativo — deixa outros handlers processar

        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%d/%m %H:%M")
        ticket.description = ticket.description + f"\n\n[{now_str} UTC] {text}"
        ticket.status = "open"  # Reabre se estava em "answered"
        ticket.updated_at = datetime.now(timezone.utc)
        ticket_id = ticket.id
        s.commit()

    await update.message.reply_text(
        f"💬 <b>Mensagem enviada ao Ticket #{ticket_id}</b>\n"
        f"Nossa equipe responderá em breve.",
        parse_mode="HTML",
    )

    user_display = f"@{user.username}" if user.username else user.first_name
    await log_to_group(
        f"💬 <b>Follow-up Ticket #{ticket_id}</b>\n"
        f"👤 Usuário: {user_display} (ID: <code>{user.id}</code>)\n"
        f"📝 {text[:300]}\n\n"
        f"Responder: <code>/reply {ticket_id} sua resposta aqui</code>\n"
        f"Fechar: <code>/close_ticket {ticket_id}</code>"
    )


async def support_cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancela a criação de ticket."""
    if context.user_data.pop(_SUPPORT_WAITING, None):
        await update.effective_message.reply_text("Suporte cancelado.")


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
