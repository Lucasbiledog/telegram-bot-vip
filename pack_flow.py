from __future__ import annotations
import re
from telegram import Update
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import ADMIN_IDS
from db import pack_create
from utils import reply_with_retry


TITLE, KIND, PREVIEWS, FILES, CONFIRM = range(5)

async def _check_admin(update: Update) -> bool:
    if update.effective_user.id not in ADMIN_IDS:
        await reply_with_retry(
            update.effective_message,
            "Você não tem permissão para usar este comando."
        )
        return False
    return True

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    context.user_data.clear()
    await reply_with_retry(update.effective_message, "Informe o título do pack:")
    return TITLE

async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    context.user_data["title"] = update.effective_message.text.strip()
    await reply_with_retry(update.effective_message, "Este pack é VIP ou free?")
    return KIND

async def handle_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    kind = update.effective_message.text.strip().lower()
    if kind not in ("vip", "free"):
        await reply_with_retry(update.effective_message, "Responda com 'VIP' ou 'free'.")
        return KIND
    context.user_data["kind"] = kind
    context.user_data["previews"] = []
    await reply_with_retry(update.effective_message, "Envie as imagens de preview. Envie /done quando terminar ou /skip para pular.")
    return PREVIEWS


async def add_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    photos = update.effective_message.photo
    if photos:
        file_id = photos[-1].file_id
        context.user_data["previews"].append(file_id)
    return PREVIEWS

async def skip_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    context.user_data["previews"] = []
    context.user_data["files"] = []
    await reply_with_retry(
        update.effective_message,
        "Agora envie os arquivos do pack. Envie /done quando terminar.",
    )
    return FILES

async def previews_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    context.user_data["files"] = []
    await reply_with_retry(
        update.effective_message,
        "Agora envie os arquivos do pack. Envie /done quando terminar.",
    )
    return FILES

async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    doc = update.effective_message.document
    if doc:
        context.user_data["files"].append(doc.file_id)
    return FILES

async def files_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = context.user_data.get("title", "")
    kind = context.user_data.get("kind", "free")

    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])
    summary = (
        f"Título: {title}\n"
        f"Tipo: {'VIP' if kind == 'vip' else 'Free'}\n"
        f"Previews: {len(previews)}\n"
        f"Arquivos: {len(files)}\n"
        "Confirmar? (sim/não)"
    )
    await reply_with_retry(update.effective_message, summary)
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await _check_admin(update):
        return ConversationHandler.END
    text = update.effective_message.text.lower()
    if text.startswith("s"):
        is_vip = context.user_data.get("kind", "free") == "vip"
        title = context.user_data.get("title", "")
        previews = context.user_data.get("previews", [])
        files = context.user_data.get("files", [])
        await pack_create(title, previews, files, is_vip)
        await reply_with_retry(
            update.effective_message,
            "Pack salvo para envio futuro.",
        )

    else:
        await reply_with_retry(update.effective_message, "Operação cancelada.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await reply_with_retry(update.effective_message, "Operação cancelada.")
    return ConversationHandler.END

pack_conv_handler = ConversationHandler(
    entry_points=[CommandHandler(["pack", "p", "novopack"], start)],
    states={
        TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
        KIND: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_kind)],
        PREVIEWS: [
            CommandHandler("skip", skip_previews),
            CommandHandler("done", previews_done),
            MessageHandler(filters.PHOTO, add_preview),
        ],
        FILES: [
            CommandHandler("done", files_done),
            MessageHandler(filters.Document.ALL, add_file),
        ],
        CONFIRM: [
            MessageHandler(
                filters.Regex(re.compile(r"^(sim|s|yes|y)$", re.IGNORECASE)),
                confirm,
            ),
            MessageHandler(
                filters.Regex(re.compile(r"^(nao|não|n)$", re.IGNORECASE)),
                cancel,
            )
        ]
    },
    fallbacks=[CommandHandler("cancel", cancel)],
)

__all__ = ["pack_conv_handler"]
