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

from db import pack_create

TITLE, PREVIEWS, FILES, CONFIRM = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Informe o título do pack:")
    return TITLE

async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.effective_message.text.strip()
    context.user_data["previews"] = []
    await update.effective_message.reply_text(
        "Envie as imagens de preview. Envie /done quando terminar ou /skip para pular.")
    return PREVIEWS

async def add_preview(update: Update, context: ContextTypes.DEFAULT_TYPE):
    photos = update.effective_message.photo
    if photos:
        file_id = photos[-1].file_id
        context.user_data["previews"].append(file_id)
    return PREVIEWS

async def skip_previews(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["previews"] = []
    context.user_data["files"] = []
    await update.effective_message.reply_text(
        "Agora envie os arquivos do pack. Envie /done quando terminar.")
    return FILES

async def previews_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["files"] = []
    await update.effective_message.reply_text(
        "Agora envie os arquivos do pack. Envie /done quando terminar.")
    return FILES

async def add_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.effective_message.document
    if doc:
        context.user_data["files"].append(doc.file_id)
    return FILES

async def files_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = context.user_data.get("title", "")
    previews = context.user_data.get("previews", [])
    files = context.user_data.get("files", [])
    summary = (
        f"Título: {title}\n"
        f"Previews: {len(previews)}\n"
        f"Arquivos: {len(files)}\n"
        "Confirmar? (sim/não)"
    )
    await update.effective_message.reply_text(summary)
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text.lower()
    if text.startswith("s"):
        await pack_create(
            context.user_data.get("title", ""),
            context.user_data.get("previews", []),
            context.user_data.get("files", []),
        )
        await update.effective_message.reply_text("Pack salvo!")
    else:
        await update.effective_message.reply_text("Operação cancelada.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Operação cancelada.")
    return ConversationHandler.END

pack_conv_handler = ConversationHandler(
    entry_points=[CommandHandler(["pack", "p"], start)],
    states={
        TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_title)],
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
