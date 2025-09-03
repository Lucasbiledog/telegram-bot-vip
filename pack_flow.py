from __future__ import annotations
import os
import re
from telegram import (
    Update,
    InputMediaPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from telegram.ext import (
    CommandHandler,
    ConversationHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from db import pack_create

GROUP_VIP_ID = int(os.getenv("GROUP_VIP_ID", "0"))
GROUP_FREE_ID = int(os.getenv("GROUP_FREE_ID", "0"))

TITLE, KIND, PREVIEWS, FILES, CONFIRM = range(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.effective_message.reply_text("Informe o título do pack:")
    return TITLE

async def handle_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["title"] = update.effective_message.text.strip()
    await update.effective_message.reply_text("Este pack é VIP ou free?")
    return KIND

async def handle_kind(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kind = update.effective_message.text.strip().lower()
    if kind not in ("vip", "free"):
        await update.effective_message.reply_text("Responda com 'VIP' ou 'free'.")
        return KIND
    context.user_data["kind"] = kind
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
    await update.effective_message.reply_text(summary)
    return CONFIRM

async def confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text.lower()
    if text.startswith("s"):
        is_vip = context.user_data.get("kind", "free") == "vip"
        title = context.user_data.get("title", "")
        previews = context.user_data.get("previews", [])
        files = context.user_data.get("files", [])
        is_vip=context.user_data.get("kind", "free") == "vip",
        await pack_create(title, previews, files, is_vip)
        await update.effective_message.reply_text("Pack salvo!")
        target_group = GROUP_VIP_ID if is_vip else GROUP_FREE_ID
        if target_group:
            if title:
                await context.bot.send_message(chat_id=target_group, text=title)
            for fid in files:
                await context.bot.send_document(chat_id=target_group, document=fid)

        if is_vip and GROUP_FREE_ID:
            if previews:
                media = [InputMediaPhoto(p) for p in previews[:10]]
                await context.bot.send_media_group(chat_id=GROUP_FREE_ID, media=media)
            bot = await context.bot.get_me()
            kb = InlineKeyboardMarkup(
                [[InlineKeyboardButton("Assinar VIP", url=f"https://t.me/{bot.username}?start=checkout")]]
            )
            await context.bot.send_message(
                chat_id=GROUP_FREE_ID,
                text="Curtiu as previews? Assine VIP para acessar o pack completo!",
                reply_markup=kb,
            )


    else:
        await update.effective_message.reply_text("Operação cancelada.")
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("Operação cancelada.")
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
