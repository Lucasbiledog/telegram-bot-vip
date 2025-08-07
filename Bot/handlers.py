# handlers.py
import logging
import random
from database import SessionLocal, NotificationMessage, Config
from googleapiclient.discovery import build
import os
from telegram import Update
from telegram.ext import ContextTypes

# Variáveis do Google Drive que devem ser configuradas no main ou importadas
drive_service = None  # Será injetado depois, para evitar circularidade
GROUP_FREE_ID = None  # Também será configurado externamente
bot = None  # Também será configurado externamente

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Fala! Esse bot te dá acesso a arquivos premium. Entre no grupo Free e veja como virar VIP. 🚀"
    )

async def criar_checkout_session(telegram_user_id: int):
    # Essa função provavelmente será movida para outro arquivo (pagamentos),
    # mas deixo aqui para você adaptar depois
    pass

async def pagar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Também deixo vazio para você integrar com a função real do checkout Stripe
    pass

async def get_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    await update.message.reply_text(f"O chat_id deste chat/grupo é: {chat_id}")

async def enviar_asset_drive():
    try:
        # Obtém a pasta principal do Google Drive via variável global
        folder_id = os.getenv("GOOGLE_DRIVE_FREE_FOLDER_ID")
        if not folder_id:
            logging.error("GOOGLE_DRIVE_FREE_FOLDER_ID não configurado")
            return
        
        query_subfolders = f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = drive_service.files().list(
            q=query_subfolders, fields="files(id, name)", pageSize=100
        ).execute()
        subfolders = results.get('files', [])
        if not subfolders:
            logging.warning("Nenhuma subpasta encontrada no Drive.")
            return

        chosen_folder = random.choice(subfolders)
        folder_id = chosen_folder['id']

        files_results = drive_service.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="files(id, name, mimeType, webContentLink)",
            pageSize=50
        ).execute()
        files = files_results.get('files', [])
        if not files:
            logging.warning(f"Nenhum arquivo encontrado na subpasta {chosen_folder['name']}")
            return

        preview_link = None
        file_link = None
        preview_folder_id = None

        for f in files:
            if f['mimeType'] == 'application/vnd.google-apps.folder' and f['name'].lower() == 'preview':
                preview_folder_id = f['id']
                break

        if preview_folder_id:
            previews_results = drive_service.files().list(
                q=f"'{preview_folder_id}' in parents and trashed=false",
                fields="files(id, name)", pageSize=10
            ).execute()
            previews = previews_results.get('files', [])
            if previews:
                chosen_preview = random.choice(previews)
                preview_link = f"https://drive.google.com/uc?id={chosen_preview['id']}"

        if not preview_link:
            for f in files:
                name = f['name'].lower()
                if any(name.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']):
                    preview_link = f"https://drive.google.com/uc?id={f['id']}"
                    break

        for f in files:
            if not f['mimeType'].startswith('application/vnd.google-apps.folder'):
                if f['mimeType'].startswith('application/') or f['name'].lower().endswith('.zip'):
                    file_link = f.get('webContentLink')
                    if file_link:
                        break

        if not file_link:
            logging.warning(f"Arquivo para download não encontrado em {chosen_folder['name']}")
            return

        texto = f"🎁 Asset gratuito do dia: *{chosen_folder['name']}*\n\nLink para download: {file_link}"

        if preview_link:
            await bot.send_photo(chat_id=GROUP_FREE_ID, photo=preview_link, caption=texto, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id=GROUP_FREE_ID, text=texto, parse_mode='Markdown')

        logging.info(f"Enviado asset '{chosen_folder['name']}' com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar asset do Drive: {e}")

async def enviar_manual_drive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("✅ Enviando asset do Drive no grupo Free...")
    await enviar_asset_drive()

async def limpar_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        logging.info(f"Usuário {update.effective_user.id} iniciou limpeza do grupo Free")
        chat = await bot.get_chat(GROUP_FREE_ID)
        async for message in chat.iter_history(limit=100):
            try:
                await bot.delete_message(chat_id=GROUP_FREE_ID, message_id=message.message_id)
            except Exception as e:
                logging.warning(f"Erro ao deletar mensagem {message.message_id}: {e}")
        await update.message.reply_text("✅ Limpeza do grupo Free concluída (últimas 100 mensagens).")
    except Exception as e:
        logging.error(f"Erro ao limpar grupo: {e}")
        await update.message.reply_text("❌ Erro ao tentar limpar o grupo.")

async def add_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /addmsg <categoria> <mensagem>")
        return
    category = context.args[0]
    message = " ".join(context.args[1:])
    if category not in ['pre_notification', 'unreal_news']:
        await update.message.reply_text("Categoria inválida. Use 'pre_notification' ou 'unreal_news'.")
        return
    db = SessionLocal()
    db.add(NotificationMessage(category=category, message=message))
    db.commit()
    db.close()
    await update.message.reply_text(f"Mensagem adicionada na categoria {category}.")

async def list_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /listmsg <categoria>")
        return
    category = context.args[0]
    if category not in ['pre_notification', 'unreal_news']:
        await update.message.reply_text("Categoria inválida. Use 'pre_notification' ou 'unreal_news'.")
        return
    db = SessionLocal()
    msgs = db.query(NotificationMessage).filter(NotificationMessage.category == category).all()
    db.close()
    if not msgs:
        await update.message.reply_text("Nenhuma mensagem encontrada.")
        return
    text = f"Mensagens na categoria {category}:\n\n"
    for msg in msgs:
        text += f"- (ID {msg.id}) {msg.message}\n"
    await update.message.reply_text(text)

async def delete_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /delmsg <id>")
        return
    try:
        msg_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("ID inválido.")
        return
    db = SessionLocal()
    msg = db.query(NotificationMessage).filter(NotificationMessage.id == msg_id).first()
    if not msg:
        await update.message.reply_text("Mensagem não encontrada.")
        db.close()
        return
    db.delete(msg)
    db.commit()
    db.close()
    await update.message.reply_text(f"Mensagem ID {msg_id} deletada.")
