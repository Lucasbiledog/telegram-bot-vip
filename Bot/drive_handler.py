# drive_handler.py
import logging
import random

from googleapiclient.discovery import build

# Espera-se que 'drive_service' seja configurado e passado aqui para evitar dependência circular.

async def enviar_asset_drive(bot, group_free_id):
    try:
        # ID da pasta principal de assets gratuitos
        GOOGLE_DRIVE_FREE_FOLDER_ID = bot.application.bot_data.get("GOOGLE_DRIVE_FREE_FOLDER_ID")
        if not GOOGLE_DRIVE_FREE_FOLDER_ID:
            logging.error("GOOGLE_DRIVE_FREE_FOLDER_ID não definido no bot_data")
            return

        # Pega as subpastas do folder principal free
        query_subfolders = f"'{GOOGLE_DRIVE_FREE_FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false"
        results = bot.application.bot_data['drive_service'].files().list(
            q=query_subfolders, fields="files(id, name)", pageSize=100
        ).execute()
        subfolders = results.get('files', [])
        if not subfolders:
            logging.warning("Nenhuma subpasta encontrada no Drive.")
            return

        chosen_folder = random.choice(subfolders)
        folder_id = chosen_folder['id']

        files_results = bot.application.bot_data['drive_service'].files().list(
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

        # Procura pasta preview
        for f in files:
            if f['mimeType'] == 'application/vnd.google-apps.folder' and f['name'].lower() == 'preview':
                preview_folder_id = f['id']
                break

        if preview_folder_id:
            previews_results = bot.application.bot_data['drive_service'].files().list(
                q=f"'{preview_folder_id}' in parents and trashed=false",
                fields="files(id, name)", pageSize=10
            ).execute()
            previews = previews_results.get('files', [])
            if previews:
                chosen_preview = random.choice(previews)
                preview_link = f"https://drive.google.com/uc?id={chosen_preview['id']}"

        # Se não achar preview, tenta qualquer imagem na pasta
        if not preview_link:
            for f in files:
                name = f['name'].lower()
                if any(name.endswith(ext) for ext in ['.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif']):
                    preview_link = f"https://drive.google.com/uc?id={f['id']}"
                    break

        # Procura arquivo para download (não folder)
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
            await bot.send_photo(chat_id=group_free_id, photo=preview_link, caption=texto, parse_mode='Markdown')
        else:
            await bot.send_message(chat_id=group_free_id, text=texto, parse_mode='Markdown')

        logging.info(f"Enviado asset '{chosen_folder['name']}' com sucesso.")
    except Exception as e:
        logging.error(f"Erro ao enviar asset do Drive: {e}")
