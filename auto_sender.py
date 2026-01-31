# auto_sender.py
"""
Sistema de envio automático de arquivos para canais VIP e FREE
- VIP: 1 arquivo por dia às 15h
- FREE: 1 arquivo por semana (quartas às 15h)

Sistema de 2 tabelas:
- SourceFile: Indexa TODOS os arquivos disponíveis no grupo fonte
- SentFile: Rastreia arquivos já enviados (histórico)

VERSION: 2.0.1 - Fixed SQL query bug with empty sets (2025-11-04 02:45)
"""

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

# Version marker to force module reload
__version__ = "2.0.2"
__updated__ = "2025-11-11 11:00:00"

from telegram import Bot, Message, Update, InputMediaVideo, InputMediaPhoto, InputMediaDocument
from telegram.error import TelegramError
from sqlalchemy.orm import Session
from config import SOURCE_CHAT_ID

LOG = logging.getLogger(__name__)

# IDs dos canais/grupos (SOURCE_CHAT_ID importado de config.py)
VIP_CHANNEL_ID = None  # Será configurado via variável de ambiente
FREE_CHANNEL_ID = None  # Será configurado via variável de ambiente

# Tipos de arquivo suportados
SUPPORTED_TYPES = ['photo', 'video', 'document', 'animation', 'audio']

# IMPORTANTE: SourceFile e SentFile são importadas de main.py
# Elas são definidas lá e herdam corretamente de Base
# Não redefinir aqui para evitar conflitos!
SourceFile = None
SentFile = None


async def index_message_file(update: Update, session: Session) -> bool:
    """
    Handler para indexar arquivos do grupo fonte automaticamente.
    Deve ser registrado como MessageHandler no bot.

    Args:
        update: Update do Telegram
        session: Sessão do banco de dados

    Returns:
        True se indexado, False caso contrário
    """
    msg = update.effective_message
    if not msg or msg.chat.id != SOURCE_CHAT_ID:
        return False

    try:
        file_data = None

        # Extrair dados do arquivo baseado no tipo
        if msg.photo:
            photo = msg.photo[-1]  # Maior resolução
            file_data = {
                'file_id': photo.file_id,
                'file_unique_id': photo.file_unique_id,
                'file_type': 'photo',
                'file_size': photo.file_size,
                'file_name': None
            }
        elif msg.video:
            file_data = {
                'file_id': msg.video.file_id,
                'file_unique_id': msg.video.file_unique_id,
                'file_type': 'video',
                'file_size': msg.video.file_size,
                'file_name': msg.video.file_name
            }
        elif msg.document:
            file_data = {
                'file_id': msg.document.file_id,
                'file_unique_id': msg.document.file_unique_id,
                'file_type': 'document',
                'file_size': msg.document.file_size,
                'file_name': msg.document.file_name
            }
        elif msg.animation:
            file_data = {
                'file_id': msg.animation.file_id,
                'file_unique_id': msg.animation.file_unique_id,
                'file_type': 'animation',
                'file_size': msg.animation.file_size,
                'file_name': msg.animation.file_name
            }
        elif msg.audio:
            file_data = {
                'file_id': msg.audio.file_id,
                'file_unique_id': msg.audio.file_unique_id,
                'file_type': 'audio',
                'file_size': msg.audio.file_size,
                'file_name': msg.audio.file_name
            }

        if not file_data:
            return False

        # Verificar se já existe
        existing = session.query(SourceFile).filter(
            SourceFile.file_unique_id == file_data['file_unique_id']
        ).first()

        if existing:
            LOG.debug(f"[INDEX] Arquivo já indexado: {file_data['file_unique_id']}")
            return False

        # Criar novo registro
        source_file = SourceFile(
            file_id=file_data['file_id'],
            file_unique_id=file_data['file_unique_id'],
            file_type=file_data['file_type'],
            message_id=msg.message_id,
            source_chat_id=msg.chat.id,
            caption=msg.caption,
            file_name=file_data.get('file_name'),
            file_size=file_data.get('file_size'),
            indexed_at=datetime.now(timezone.utc),
            active=True
        )
        session.add(source_file)
        session.commit()

        LOG.info(f"[INDEX] ✅ Arquivo indexado: {file_data['file_type']} - ID {msg.message_id}")
        return True

    except Exception as e:
        LOG.error(f"[INDEX] ❌ Erro ao indexar arquivo: {e}")
        session.rollback()
        return False


def is_part_file(file_name: Optional[str], caption: Optional[str]) -> bool:
    """
    Verifica se arquivo é uma parte (part 1, part 2, etc).
    Detecta padrões como: 001, 002, part1, parte 1, etc.
    """
    if not file_name and not caption:
        return False

    text_to_check = f"{file_name or ''} {caption or ''}".lower()

    # Padrões comuns de parts
    part_patterns = [
        'part', 'parte', 'pt.', 'pt',
        'part1', 'part2', 'part3', 'part4', 'part5',
        'parte1', 'parte2', 'parte3', 'parte4', 'parte5',
        'pt1', 'pt2', 'pt3', 'pt4', 'pt5',
        'p1', 'p2', 'p3', 'p4', 'p5',
        'cd1', 'cd2', 'cd3',
        'disc1', 'disc2', 'disc3',
        'disco1', 'disco2', 'disco3',
    ]

    # Verificar padrões de texto
    if any(pattern in text_to_check for pattern in part_patterns):
        return True

    # Verificar padrões numéricos: 001, 002, 003, etc (3 dígitos)
    import re
    if re.search(r'\b\d{3}\b', file_name or ''):
        return True

    return False


def extract_base_name(file_name: Optional[str]) -> Optional[str]:
    """
    Extrai o nome base de um arquivo com partes, removendo números de parte.
    Exemplo:
        "Movie.2024.1080p.001.mkv" -> "Movie.2024.1080p"
        "Game.part1.rar" -> "Game"
    """
    if not file_name:
        return None

    import re

    # Remover extensão
    base = file_name.rsplit('.', 1)[0] if '.' in file_name else file_name

    # Remover padrões de parte:
    # - .001, .002, etc (3 dígitos no final)
    base = re.sub(r'\.\d{3}$', '', base)
    # - .part1, .part2, etc
    base = re.sub(r'\.part\d+$', '', base, flags=re.IGNORECASE)
    # - .parte1, .parte2, etc
    base = re.sub(r'\.parte\d+$', '', base, flags=re.IGNORECASE)
    # - _001, _002, etc
    base = re.sub(r'_\d{3}$', '', base)
    # - -001, -002, etc
    base = re.sub(r'-\d{3}$', '', base)

    return base


def get_all_parts(session: Session, source_file: SourceFile) -> list:
    """
    Busca todas as partes relacionadas a um arquivo.
    Retorna lista ordenada de SourceFile (todas as partes).
    """
    # Se não for arquivo com partes, retorna só ele mesmo
    if not is_part_file(source_file.file_name, source_file.caption):
        return [source_file]

    # Extrair nome base
    base_name = extract_base_name(source_file.file_name)
    if not base_name:
        return [source_file]

    LOG.info(f"[AUTO-SEND] Detectado arquivo com partes. Base: {base_name}")

    # Buscar todos os arquivos com o mesmo nome base
    all_files = session.query(SourceFile).filter(
        SourceFile.source_chat_id == source_file.source_chat_id,
        SourceFile.active == True,
        SourceFile.file_name.like(f"{base_name}%")
    ).order_by(SourceFile.file_name).all()

    if len(all_files) > 1:
        LOG.info(f"[AUTO-SEND] Encontradas {len(all_files)} partes para enviar juntas")
        return all_files
    else:
        return [source_file]


async def get_random_file_from_source(
    session: Session,
    tier: str
) -> Optional[SourceFile]:
    """
    Busca um arquivo aleatório do índice que ainda não foi enviado para o tier.

    Filtros aplicados:
    - VIP: Todos os arquivos (sem restrições)
    - FREE: Apenas arquivos até 500MB, SEM parts

    Args:
        session: Sessão do banco de dados
        tier: 'vip' ou 'free'

    Returns:
        Objeto SourceFile ou None se não encontrar
    """
    try:
        LOG.info(f"[AUTO-SEND] Buscando arquivo aleatório para tier={tier}")

        # Buscar IDs de arquivos já enviados para este tier
        sent_file_ids = {
            f.file_unique_id for f in session.query(SentFile.file_unique_id).filter(
                SentFile.sent_to_tier == tier,
                SentFile.source_chat_id == SOURCE_CHAT_ID
            ).all()
        }

        LOG.info(f"[AUTO-SEND] {len(sent_file_ids)} arquivos já enviados para {tier}")

        # Buscar arquivos disponíveis (não enviados ainda)
        query = session.query(SourceFile).filter(
            SourceFile.source_chat_id == SOURCE_CHAT_ID,
            SourceFile.active == True
        )

        # FILTRO: Excluir fotos (apenas documents, videos, audios, animations)
        query = query.filter(
            SourceFile.file_type.in_(['document', 'video', 'audio', 'animation'])
        )

        # Só aplica filtro de "já enviados" se houver arquivos enviados
        if sent_file_ids:
            query = query.filter(~SourceFile.file_unique_id.in_(sent_file_ids))

        # Filtros específicos para FREE
        if tier == 'free':
            # Limite de 500MB (em bytes)
            MAX_SIZE_FREE = 500 * 1024 * 1024  # 500MB
            query = query.filter(
                (SourceFile.file_size <= MAX_SIZE_FREE) | (SourceFile.file_size == None)
            )

        available_files = query.all()

        # Filtro adicional para FREE: remover arquivos com "part" no nome
        if tier == 'free':
            available_files = [
                f for f in available_files
                if not is_part_file(f.file_name, f.caption)
            ]

        if not available_files:
            LOG.warning(f"[AUTO-SEND] ⚠️ Nenhum arquivo novo disponível para {tier}")

            # Verificar se há arquivos indexados
            total_indexed = session.query(SourceFile).filter(
                SourceFile.source_chat_id == SOURCE_CHAT_ID,
                SourceFile.active == True
            ).count()

            if total_indexed == 0:
                LOG.error("[AUTO-SEND] ❌ Nenhum arquivo indexado! Verifique o grupo fonte.")
            else:
                LOG.info(f"[AUTO-SEND] Todos os {total_indexed} arquivos já foram enviados para {tier}")
                LOG.info("[AUTO-SEND] 💡 Considere resetar o histórico ou adicionar mais arquivos")

            return None

        # Selecionar arquivo aleatório
        selected_file = random.choice(available_files)

        LOG.info(
            f"[AUTO-SEND] ✅ Arquivo selecionado: {selected_file.file_type} "
            f"(ID: {selected_file.message_id}, {len(available_files)} disponíveis)"
        )

        return selected_file

    except Exception as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro ao buscar arquivo aleatório: {e}")
        return None


async def send_file_to_channel(
    bot: Bot,
    source_file: SourceFile,
    channel_id: int,
    caption: Optional[str] = None
) -> Optional[Message]:
    """
    Envia um arquivo indexado para o canal.

    IMPORTANTE: Usa copy_message para copiar diretamente do grupo fonte,
    pois file_id pode não funcionar se o arquivo foi indexado por outra conta.

    Args:
        bot: Instância do bot
        source_file: Objeto SourceFile do arquivo a enviar
        channel_id: ID do canal destino
        caption: Legenda opcional (sobrescreve a original)

    Returns:
        Message enviada ou None se falhar
    """
    try:
        # Método 1: Copiar mensagem diretamente do grupo fonte (RECOMENDADO)
        # Este método funciona independente de quem indexou o arquivo
        LOG.info(
            f"[AUTO-SEND] Copiando mensagem {source_file.message_id} "
            f"de {source_file.source_chat_id} para {channel_id}"
        )

        try:
            # Tentar copiar a mensagem
            msg = await bot.copy_message(
                chat_id=channel_id,
                from_chat_id=source_file.source_chat_id,
                message_id=source_file.message_id,
                caption=caption if caption else source_file.caption
            )

            LOG.info(f"[AUTO-SEND] ✅ Mensagem copiada com sucesso para {channel_id}")
            return msg

        except TelegramError as copy_error:
            # Se copy_message falhar, tentar com file_id (fallback)
            LOG.warning(f"[AUTO-SEND] ⚠️ Falha ao copiar mensagem: {copy_error}")
            LOG.info(f"[AUTO-SEND] Tentando método alternativo com file_id...")

            file_type = source_file.file_type
            file_id = source_file.file_id

            # Usar legenda original se não especificada
            if caption is None:
                caption = source_file.caption

            # Enviar baseado no tipo
            if file_type == 'photo':
                msg = await bot.send_photo(
                    chat_id=channel_id,
                    photo=file_id,
                    caption=caption
                )
            elif file_type == 'video':
                msg = await bot.send_video(
                    chat_id=channel_id,
                    video=file_id,
                    caption=caption
                )
            elif file_type == 'document':
                msg = await bot.send_document(
                    chat_id=channel_id,
                    document=file_id,
                    caption=caption
                )
            elif file_type == 'animation':
                msg = await bot.send_animation(
                    chat_id=channel_id,
                    animation=file_id,
                    caption=caption
                )
            elif file_type == 'audio':
                msg = await bot.send_audio(
                    chat_id=channel_id,
                    audio=file_id,
                    caption=caption
                )
            else:
                LOG.error(f"[AUTO-SEND] Tipo de arquivo não suportado: {file_type}")
                return None

            LOG.info(f"[AUTO-SEND] ✅ Arquivo enviado com sucesso (fallback) para {channel_id}")
            return msg

    except TelegramError as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro ao enviar arquivo: {e}")
        LOG.error(f"[AUTO-SEND] Detalhes: message_id={source_file.message_id}, source_chat={source_file.source_chat_id}")
        return None


async def mark_file_as_sent(
    session: Session,
    source_file: SourceFile,
    tier: str
):
    """
    Marca um arquivo como já enviado no banco de dados.
    """
    try:
        sent_file = SentFile(
            file_unique_id=source_file.file_unique_id,
            file_type=source_file.file_type,
            message_id=source_file.message_id,
            source_chat_id=source_file.source_chat_id,
            sent_to_tier=tier,
            sent_at=datetime.now(timezone.utc),
            caption=source_file.caption
        )
        session.add(sent_file)
        session.commit()
        LOG.info(f"[AUTO-SEND] Arquivo marcado como enviado: {source_file.file_unique_id} para {tier}")
    except Exception as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro ao marcar arquivo como enviado: {e}")
        session.rollback()


async def send_teaser_to_free(bot: Bot, all_parts: list):
    """
    Envia um arquivo .txt com informações do arquivo VIP para o canal FREE.
    Serve como "teaser" para incentivar assinatura VIP.

    Args:
        bot: Instância do bot
        all_parts: Lista de SourceFile (todas as partes do arquivo)
    """
    if not FREE_CHANNEL_ID:
        LOG.warning("[AUTO-SEND] FREE_CHANNEL_ID não configurado, pulando teaser")
        return

    try:
        import tempfile
        import os

        # Criar conteúdo do arquivo .txt
        first_part = all_parts[0]

        # Nome base do arquivo (sem extensão de parte)
        if len(all_parts) > 1:
            base_name = extract_base_name(first_part.file_name) or "Arquivo"
            file_list = "\n".join([f"  • {p.file_name}" for p in all_parts])
            txt_name = f"{base_name}.txt"
        else:
            file_name = first_part.file_name or "Arquivo.txt"
            txt_name = file_name.rsplit('.', 1)[0] + ".txt" if '.' in file_name else file_name + ".txt"
            file_list = f"  • {first_part.file_name}"

        # Calcular tamanho total
        total_size = sum(p.file_size or 0 for p in all_parts)
        size_mb = total_size / (1024 * 1024)

        # Conteúdo do .txt
        content = f"""╔════════════════════════════════════════╗
║   🔒 CONTEÚDO EXCLUSIVO VIP 🔒        ║
╚════════════════════════════════════════╝

📅 Data: {datetime.now().strftime('%d/%m/%Y')}
📦 Arquivo: {first_part.file_name or 'Arquivo'}
📊 Tipo: {first_part.file_type.upper()}
💾 Tamanho: {size_mb:.2f} MB

"""

        if len(all_parts) > 1:
            content += f"""🗂️ PARTES ({len(all_parts)} arquivos):
{file_list}

"""

        if first_part.caption:
            content += f"""📝 DESCRIÇÃO:
{first_part.caption}

"""

        content += """════════════════════════════════════════

💎 QUER TER ACESSO A ESTE E OUTROS CONTEÚDOS?

✅ Assine o canal VIP e receba:
   • Conteúdos diários exclusivos
   • Arquivos completos (sem limites)
   • Acesso vitalício
   • Suporte prioritário

🔗 Para assinar, clique no link do canal!

════════════════════════════════════════
"""

        # Criar arquivo temporário
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            temp_path = f.name

        try:
            # Enviar arquivo .txt para o canal FREE
            with open(temp_path, 'rb') as f:
                await bot.send_document(
                    chat_id=FREE_CHANNEL_ID,
                    document=f,
                    filename=txt_name,
                    caption=(
                        f"👀 <b>Preview do conteúdo VIP de hoje!</b>\n\n"
                        f"💎 Quer ter acesso completo? Assine o VIP!\n\n"
                        f"👉 Envie <b>/start</b> no privado do bot para assinar."
                    ),
                    parse_mode='HTML'
                )

            LOG.info(f"[AUTO-SEND] ✅ Teaser enviado para o canal FREE: {txt_name}")

        finally:
            # Deletar arquivo temporário
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    except Exception as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro ao enviar teaser para FREE: {e}")
        import traceback
        LOG.error(traceback.format_exc())


async def send_as_media_group(
    bot: Bot,
    source_files: List,  # Lista de SourceFile
    channel_id: int,
    tier: str
) -> bool:
    """
    Envia múltiplos arquivos como media group (álbum/sanfona).
    Funciona apenas para videos e photos (até 10 arquivos).

    Args:
        bot: Bot instance
        source_files: Lista de SourceFile (as parts)
        channel_id: ID do canal destino
        tier: 'vip' ou 'free'

    Returns:
        bool: True se enviou com sucesso
    """
    try:
        # Preparar lista de InputMedia
        media_list = []

        for i, source_file in enumerate(source_files):
            # Caption apenas no primeiro arquivo
            if i == 0:
                if tier == 'vip':
                    caption = f"🔥 Conteúdo VIP Exclusivo\n📅 {datetime.now().strftime('%d/%m/%Y')}"
                else:
                    caption = f"🆓 Conteúdo Grátis da Semana\n📅 {datetime.now().strftime('%d/%m/%Y')}"

                if source_file.caption:
                    caption += f"\n\n{source_file.caption}"

                caption += f"\n\n📦 Álbum com {len(source_files)} partes"
            else:
                caption = None

            # Criar InputMedia apropriado
            if source_file.file_type == 'video':
                media_list.append(
                    InputMediaVideo(
                        media=source_file.file_id,
                        caption=caption,
                        parse_mode='HTML'
                    )
                )
            elif source_file.file_type == 'photo':
                media_list.append(
                    InputMediaPhoto(
                        media=source_file.file_id,
                        caption=caption,
                        parse_mode='HTML'
                    )
                )
            else:
                # Documents não suportam media group bem
                # Será enviado sequencialmente
                return False

        # Enviar media group
        LOG.info(f"[AUTO-SEND] 📤 Enviando media group com {len(media_list)} itens")

        messages = await bot.send_media_group(
            chat_id=channel_id,
            media=media_list
        )

        if messages and len(messages) > 0:
            LOG.info(f"[AUTO-SEND] ✅ Media group enviado com sucesso ({len(messages)} mensagens)")
            return True
        else:
            LOG.error("[AUTO-SEND] ❌ Falha ao enviar media group (sem mensagens retornadas)")
            return False

    except TelegramError as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro do Telegram ao enviar media group: {e}")
        return False
    except Exception as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro ao enviar media group: {e}")
        import traceback
        LOG.error(traceback.format_exc())
        return False


async def send_daily_vip_file(bot: Bot, session: Session):
    """
    Envia arquivo diário para o canal VIP (executa às 15h).
    Se o arquivo tiver partes (001, 002, etc), envia todas as partes juntas.
    """
    LOG.info("[AUTO-SEND] 🎯 Iniciando envio diário VIP")

    if not VIP_CHANNEL_ID:
        LOG.error("[AUTO-SEND] ❌ VIP_CHANNEL_ID não configurado!")
        return

    try:
        # Buscar arquivo aleatório não enviado
        source_file = await get_random_file_from_source(session, 'vip')

        if not source_file:
            LOG.warning("[AUTO-SEND] ⚠️ Nenhum arquivo novo disponível para VIP")
            # Enviar notificação ao admin se necessário
            return

        # Buscar todas as partes relacionadas (se houver)
        all_parts = get_all_parts(session, source_file)

        LOG.info(f"[AUTO-SEND] Enviando {len(all_parts)} parte(s) para VIP")

        # Verificar se pode enviar como media group (máximo 10 arquivos, apenas videos/photos)
        can_use_media_group = (
            len(all_parts) > 1 and
            len(all_parts) <= 10 and
            all(p.file_type in ['video', 'photo'] for p in all_parts)
        )

        success_count = 0

        if can_use_media_group:
            # ENVIAR COMO ÁLBUM/SANFONA (media group)
            LOG.info(f"[AUTO-SEND] 📦 Enviando {len(all_parts)} partes como álbum (media group)")

            success = await send_as_media_group(bot, all_parts, VIP_CHANNEL_ID, tier='vip')

            if success:
                # Marcar todas as partes como enviadas
                for part in all_parts:
                    await mark_file_as_sent(session, part, 'vip')
                success_count = len(all_parts)
                LOG.info(f"[AUTO-SEND] ✅ Álbum com {len(all_parts)} partes enviado!")
            else:
                LOG.error("[AUTO-SEND] ❌ Falha ao enviar álbum")

        else:
            # ENVIAR SEQUENCIALMENTE (documents ou + de 10 parts)
            LOG.info(f"[AUTO-SEND] 📤 Enviando {len(all_parts)} partes sequencialmente")

            for i, part in enumerate(all_parts, 1):
                # Preparar legenda (apenas na primeira parte)
                if i == 1:
                    caption = f"🔥 Conteúdo VIP Exclusivo\n📅 {datetime.now().strftime('%d/%m/%Y')}"
                    if part.caption:
                        caption += f"\n\n{part.caption}"
                    if len(all_parts) > 1:
                        caption += f"\n\n📦 Arquivo com {len(all_parts)} partes"
                else:
                    caption = f"📦 Parte {i} de {len(all_parts)}"
                    if part.caption:
                        caption += f"\n{part.caption}"

                # Enviar para canal VIP
                msg = await send_file_to_channel(bot, part, VIP_CHANNEL_ID, caption)

                if msg:
                    # Marcar como enviado
                    await mark_file_as_sent(session, part, 'vip')
                    success_count += 1
                    LOG.info(f"[AUTO-SEND] ✅ Parte {i}/{len(all_parts)} enviada")
                else:
                    LOG.error(f"[AUTO-SEND] ❌ Falha ao enviar parte {i}/{len(all_parts)}")

                # Pequeno delay entre parts (evitar flood)
                if i < len(all_parts):
                    await asyncio.sleep(0.5)

        if success_count == len(all_parts):
            LOG.info(f"[AUTO-SEND] ✅ Envio VIP diário concluído: {success_count} parte(s)")

            # Enviar teaser para o canal FREE
            LOG.info("[AUTO-SEND] 📤 Enviando teaser para canal FREE...")
            await send_teaser_to_free(bot, all_parts)

        elif success_count > 0:
            LOG.warning(f"[AUTO-SEND] ⚠️ Envio parcial: {success_count}/{len(all_parts)} partes")
        else:
            LOG.error("[AUTO-SEND] ❌ Falha total no envio VIP diário")

    except Exception as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro no envio VIP diário: {e}")
        import traceback
        LOG.error(traceback.format_exc())


async def send_weekly_free_file(bot: Bot, session: Session):
    """
    Envia arquivo semanal para o canal FREE (quartas às 15h).
    Se o arquivo tiver partes (001, 002, etc), envia todas as partes juntas.
    """
    LOG.info("[AUTO-SEND] 🎯 Verificando envio semanal FREE")

    # Verificar se é quarta-feira
    if datetime.now().weekday() != 2:  # 0=segunda, 2=quarta
        LOG.info(f"[AUTO-SEND] Hoje não é quarta-feira (dia: {datetime.now().strftime('%A')}), pulando envio FREE")
        return

    LOG.info("[AUTO-SEND] ✅ É quarta-feira! Iniciando envio FREE")

    if not FREE_CHANNEL_ID:
        LOG.error("[AUTO-SEND] ❌ FREE_CHANNEL_ID não configurado!")
        return

    try:
        # Buscar arquivo aleatório não enviado
        source_file = await get_random_file_from_source(session, 'free')

        if not source_file:
            LOG.warning("[AUTO-SEND] ⚠️ Nenhum arquivo novo disponível para FREE")
            return

        # Buscar todas as partes relacionadas (se houver)
        all_parts = get_all_parts(session, source_file)

        LOG.info(f"[AUTO-SEND] Enviando {len(all_parts)} parte(s) para FREE")

        # Verificar se pode enviar como media group
        can_use_media_group = (
            len(all_parts) > 1 and
            len(all_parts) <= 10 and
            all(p.file_type in ['video', 'photo'] for p in all_parts)
        )

        success_count = 0

        if can_use_media_group:
            # ENVIAR COMO ÁLBUM/SANFONA (media group)
            LOG.info(f"[AUTO-SEND] 📦 Enviando {len(all_parts)} partes como álbum (media group)")

            success = await send_as_media_group(bot, all_parts, FREE_CHANNEL_ID, tier='free')

            if success:
                # Marcar todas as partes como enviadas
                for part in all_parts:
                    await mark_file_as_sent(session, part, 'free')
                success_count = len(all_parts)
                LOG.info(f"[AUTO-SEND] ✅ Álbum com {len(all_parts)} partes enviado!")
            else:
                LOG.error("[AUTO-SEND] ❌ Falha ao enviar álbum")

        else:
            # ENVIAR SEQUENCIALMENTE (documents ou + de 10 parts)
            LOG.info(f"[AUTO-SEND] 📤 Enviando {len(all_parts)} partes sequencialmente")

            for i, part in enumerate(all_parts, 1):
                # Preparar legenda (apenas na primeira parte)
                if i == 1:
                    caption = f"🆓 Conteúdo Grátis da Semana\n📅 {datetime.now().strftime('%d/%m/%Y')}"
                    if part.caption:
                        caption += f"\n\n{part.caption}"
                    if len(all_parts) > 1:
                        caption += f"\n\n📦 Arquivo com {len(all_parts)} partes"
                else:
                    caption = f"📦 Parte {i} de {len(all_parts)}"
                    if part.caption:
                        caption += f"\n{part.caption}"

                # Enviar para canal FREE
                msg = await send_file_to_channel(bot, part, FREE_CHANNEL_ID, caption)

                if msg:
                    # Marcar como enviado
                    await mark_file_as_sent(session, part, 'free')
                    success_count += 1
                    LOG.info(f"[AUTO-SEND] ✅ Parte {i}/{len(all_parts)} enviada")
                else:
                    LOG.error(f"[AUTO-SEND] ❌ Falha ao enviar parte {i}/{len(all_parts)}")

                # Pequeno delay entre parts
                if i < len(all_parts):
                    await asyncio.sleep(0.5)

        if success_count == len(all_parts):
            LOG.info(f"[AUTO-SEND] ✅ Envio FREE semanal concluído: {success_count} parte(s)")

            # === REPLICAR NO VIP (mesmo arquivo nos 2 grupos) ===
            if VIP_CHANNEL_ID:
                LOG.info("[AUTO-SEND] 📤 Replicando arquivo FREE no canal VIP...")

                # Verificar quais partes já foram enviadas ao VIP
                sent_vip_ids = {
                    r.file_unique_id for r in session.query(SentFile.file_unique_id).filter(
                        SentFile.sent_to_tier == 'vip',
                        SentFile.source_chat_id == SOURCE_CHAT_ID
                    ).all()
                }

                parts_to_send_vip = [p for p in all_parts if p.file_unique_id not in sent_vip_ids]

                if not parts_to_send_vip:
                    LOG.info("[AUTO-SEND] Arquivo já existe no VIP, pulando replicação")
                else:
                    vip_success = 0
                    for i, part in enumerate(parts_to_send_vip, 1):
                        if i == 1:
                            vip_caption = f"🔥 Conteúdo VIP Exclusivo\n📅 {datetime.now().strftime('%d/%m/%Y')}"
                            if part.caption:
                                vip_caption += f"\n\n{part.caption}"
                            if len(parts_to_send_vip) > 1:
                                vip_caption += f"\n\n📦 Arquivo com {len(parts_to_send_vip)} partes"
                        else:
                            vip_caption = f"📦 Parte {i} de {len(parts_to_send_vip)}"
                            if part.caption:
                                vip_caption += f"\n{part.caption}"

                        msg_vip = await send_file_to_channel(bot, part, VIP_CHANNEL_ID, vip_caption)
                        if msg_vip:
                            await mark_file_as_sent(session, part, 'vip')
                            vip_success += 1

                        if i < len(parts_to_send_vip):
                            await asyncio.sleep(0.5)

                    LOG.info(f"[AUTO-SEND] ✅ Replicado no VIP: {vip_success}/{len(parts_to_send_vip)} parte(s)")

                # === BÔNUS VIP: enviar +1 arquivo extra para não interromper o fluxo diário ===
                LOG.info("[AUTO-SEND] 🎁 Buscando arquivo bônus para o VIP...")
                bonus_file = await get_random_file_from_source(session, 'vip')

                if bonus_file:
                    bonus_parts = get_all_parts(session, bonus_file)
                    LOG.info(f"[AUTO-SEND] 🎁 Enviando bônus VIP: {len(bonus_parts)} parte(s)")

                    bonus_success = 0
                    for i, part in enumerate(bonus_parts, 1):
                        if i == 1:
                            bonus_caption = f"🎁 Bônus VIP Exclusivo\n📅 {datetime.now().strftime('%d/%m/%Y')}"
                            if part.caption:
                                bonus_caption += f"\n\n{part.caption}"
                            if len(bonus_parts) > 1:
                                bonus_caption += f"\n\n📦 Arquivo com {len(bonus_parts)} partes"
                        else:
                            bonus_caption = f"📦 Parte {i} de {len(bonus_parts)}"
                            if part.caption:
                                bonus_caption += f"\n{part.caption}"

                        msg_bonus = await send_file_to_channel(bot, part, VIP_CHANNEL_ID, bonus_caption)
                        if msg_bonus:
                            await mark_file_as_sent(session, part, 'vip')
                            bonus_success += 1

                        if i < len(bonus_parts):
                            await asyncio.sleep(0.5)

                    LOG.info(f"[AUTO-SEND] 🎁 Bônus VIP enviado: {bonus_success}/{len(bonus_parts)} parte(s)")

                    # Enviar teaser do bônus para o FREE
                    await send_teaser_to_free(bot, bonus_parts)
                else:
                    LOG.warning("[AUTO-SEND] ⚠️ Nenhum arquivo disponível para bônus VIP")

        elif success_count > 0:
            LOG.warning(f"[AUTO-SEND] ⚠️ Envio parcial: {success_count}/{len(all_parts)} partes")
        else:
            LOG.error("[AUTO-SEND] ❌ Falha total no envio FREE semanal")

    except Exception as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro no envio FREE semanal: {e}")
        import traceback
        LOG.error(traceback.format_exc())


def setup_auto_sender(vip_channel: int, free_channel: int, source_file_class=None, sent_file_class=None):
    """
    Configura os IDs dos canais e classes de modelo para o sistema de envio automático.

    Args:
        vip_channel: ID do canal VIP
        free_channel: ID do canal FREE
        source_file_class: Classe SourceFile do main.py (herda de Base)
        sent_file_class: Classe SentFile do main.py (herda de Base)
    """
    global VIP_CHANNEL_ID, FREE_CHANNEL_ID, SourceFile, SentFile

    VIP_CHANNEL_ID = vip_channel
    FREE_CHANNEL_ID = free_channel

    # Atribuir classes de modelo (se fornecidas)
    if source_file_class:
        SourceFile = source_file_class
    if sent_file_class:
        SentFile = sent_file_class

    LOG.info(f"[AUTO-SEND] Configurado - VIP: {vip_channel}, FREE: {free_channel}")
    if source_file_class and sent_file_class:
        LOG.info(f"[AUTO-SEND] Classes de modelo configuradas corretamente")


# ===== COMANDOS DE ADMINISTRAÇÃO =====

async def reset_sent_history(session: Session, tier: Optional[str] = None):
    """
    Reseta histórico de arquivos enviados.
    Útil quando todos os arquivos já foram enviados e você quer recomeçar.

    Args:
        session: Sessão do banco de dados
        tier: 'vip', 'free' ou None (todos)
    """
    try:
        query = session.query(SentFile)
        if tier:
            query = query.filter(SentFile.sent_to_tier == tier)

        count = query.count()
        query.delete()
        session.commit()

        LOG.info(f"[ADMIN] ✅ Histórico resetado: {count} registros removidos (tier={tier or 'all'})")
        return count

    except Exception as e:
        LOG.error(f"[ADMIN] ❌ Erro ao resetar histórico: {e}")
        session.rollback()
        return 0


async def get_stats(session: Session) -> Dict[str, Any]:
    """
    Retorna estatísticas do sistema de envio automático.
    """
    try:
        # Verificar se tabelas existem
        from sqlalchemy import inspect
        inspector = inspect(session.bind)
        tables = inspector.get_table_names()

        if 'source_files' not in tables or 'sent_files' not in tables:
            LOG.warning("[STATS] Tabelas ainda não criadas")
            return {
                'indexed_files': 0,
                'vip': {'total_sent': 0, 'available': 0, 'last_sent': None},
                'free': {'total_sent': 0, 'available': 0, 'last_sent': None}
            }

        total_indexed = session.query(SourceFile).filter(
            SourceFile.source_chat_id == SOURCE_CHAT_ID,
            SourceFile.active == True
        ).count()

        total_sent_vip = session.query(SentFile).filter(
            SentFile.sent_to_tier == 'vip',
            SentFile.source_chat_id == SOURCE_CHAT_ID
        ).count()

        total_sent_free = session.query(SentFile).filter(
            SentFile.sent_to_tier == 'free',
            SentFile.source_chat_id == SOURCE_CHAT_ID
        ).count()

        # Arquivos disponíveis - corrigir para retornar apenas o valor único
        sent_vip_query = session.query(SentFile.file_unique_id).filter(
            SentFile.sent_to_tier == 'vip'
        ).all()
        sent_vip_ids = {row[0] if isinstance(row, tuple) else row.file_unique_id for row in sent_vip_query}

        sent_free_query = session.query(SentFile.file_unique_id).filter(
            SentFile.sent_to_tier == 'free'
        ).all()
        sent_free_ids = {row[0] if isinstance(row, tuple) else row.file_unique_id for row in sent_free_query}

        # Contar arquivos disponíveis para VIP
        query_vip = session.query(SourceFile).filter(
            SourceFile.source_chat_id == SOURCE_CHAT_ID,
            SourceFile.active == True
        )
        if sent_vip_ids:  # Só aplica filtro se houver IDs enviados
            query_vip = query_vip.filter(~SourceFile.file_unique_id.in_(sent_vip_ids))
        available_vip = query_vip.count()

        # Contar arquivos disponíveis para FREE
        query_free = session.query(SourceFile).filter(
            SourceFile.source_chat_id == SOURCE_CHAT_ID,
            SourceFile.active == True
        )
        if sent_free_ids:  # Só aplica filtro se houver IDs enviados
            query_free = query_free.filter(~SourceFile.file_unique_id.in_(sent_free_ids))
        available_free = query_free.count()

        # Últimos envios
        last_vip = session.query(SentFile).filter(
            SentFile.sent_to_tier == 'vip'
        ).order_by(SentFile.sent_at.desc()).first()

        last_free = session.query(SentFile).filter(
            SentFile.sent_to_tier == 'free'
        ).order_by(SentFile.sent_at.desc()).first()

        return {
            'indexed_files': total_indexed,
            'vip': {
                'total_sent': total_sent_vip,
                'available': available_vip,
                'last_sent': last_vip.sent_at if last_vip else None
            },
            'free': {
                'total_sent': total_sent_free,
                'available': available_free,
                'last_sent': last_free.sent_at if last_free else None
            }
        }

    except Exception as e:
        LOG.error(f"[STATS] ❌ Erro ao obter estatísticas: {e}")
        return {}


async def deactivate_file(session: Session, file_unique_id: str) -> bool:
    """
    Desativa um arquivo (não será mais selecionado para envio).
    """
    try:
        source_file = session.query(SourceFile).filter(
            SourceFile.file_unique_id == file_unique_id
        ).first()

        if not source_file:
            LOG.warning(f"[ADMIN] Arquivo não encontrado: {file_unique_id}")
            return False

        source_file.active = False
        session.commit()

        LOG.info(f"[ADMIN] ✅ Arquivo desativado: {file_unique_id}")
        return True

    except Exception as e:
        LOG.error(f"[ADMIN] ❌ Erro ao desativar arquivo: {e}")
        session.rollback()
        return False


async def reactivate_file(session: Session, file_unique_id: str) -> bool:
    """
    Reativa um arquivo previamente desativado.
    """
    try:
        source_file = session.query(SourceFile).filter(
            SourceFile.file_unique_id == file_unique_id
        ).first()

        if not source_file:
            LOG.warning(f"[ADMIN] Arquivo não encontrado: {file_unique_id}")
            return False

        source_file.active = True
        session.commit()

        LOG.info(f"[ADMIN] ✅ Arquivo reativado: {file_unique_id}")
        return True

    except Exception as e:
        LOG.error(f"[ADMIN] ❌ Erro ao reativar arquivo: {e}")
        session.rollback()
        return False


# ===== CATÁLOGO VIP (Lista de arquivos disponíveis) =====

# Referências para cfg_get/cfg_set — serão injetadas via setup_catalog()
_cfg_get = None
_cfg_set = None


def setup_catalog(cfg_get_func, cfg_set_func):
    """Injeta funções cfg_get/cfg_set do main.py para persistir message_id do catálogo."""
    global _cfg_get, _cfg_set
    _cfg_get = cfg_get_func
    _cfg_set = cfg_set_func
    LOG.info("[CATALOG] Funções de config injetadas com sucesso")


def _build_catalog_content(session: Session) -> str:
    """
    Gera o conteúdo do catálogo .txt com:
    1. Todos os arquivos já enviados ao VIP (com data)
    2. Arquivos futuros que ainda serão enviados (sem data)
    Sem limite de tamanho — será enviado como arquivo.
    """
    from config import SOURCE_CHAT_ID as src_id

    # === ARQUIVOS JÁ ENVIADOS ===
    sent_records = session.query(SentFile).filter(
        SentFile.sent_to_tier == 'vip',
        SentFile.source_chat_id == src_id
    ).order_by(SentFile.sent_at.desc()).all()

    sent_unique_ids = {r.file_unique_id for r in sent_records}

    # === ARQUIVOS FUTUROS (indexados mas não enviados) ===
    future_query = session.query(SourceFile).filter(
        SourceFile.source_chat_id == src_id,
        SourceFile.active == True,
        SourceFile.file_type.in_(['document', 'video', 'audio', 'animation'])
    )
    if sent_unique_ids:
        future_query = future_query.filter(~SourceFile.file_unique_id.in_(sent_unique_ids))
    future_files = future_query.order_by(SourceFile.file_name).all()

    total_geral = len(sent_records) + len(future_files)

    header = (
        "╔════════════════════════════════════════╗\n"
        "║     CATÁLOGO VIP — ARQUIVOS            ║\n"
        "╚════════════════════════════════════════╝\n\n"
        f"Atualizado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}\n"
        f"Arquivos já enviados: {len(sent_records)}\n"
        f"Arquivos a caminho: {len(future_files)}\n"
        f"Total geral: {total_geral}\n\n"
        "Use Ctrl+F para pesquisar pelo nome do arquivo.\n"
        "════════════════════════════════════════\n\n"
    )

    # --- Seção: Arquivos já enviados ---
    lines = []
    lines.append("┌────────────────────────────────────────┐")
    lines.append("│  ARQUIVOS JÁ ENVIADOS                  │")
    lines.append("└────────────────────────────────────────┘\n")

    if not sent_records:
        lines.append("  Nenhum arquivo enviado ainda.\n")
    else:
        # Buscar detalhes dos arquivos enviados
        source_map = {}
        if sent_unique_ids:
            sources = session.query(SourceFile).filter(
                SourceFile.file_unique_id.in_(sent_unique_ids)
            ).all()
            source_map = {s.file_unique_id: s for s in sources}

        # Agrupar por mês de envio
        months = {}
        for rec in sent_records:
            src = source_map.get(rec.file_unique_id)
            name = src.file_name if src and src.file_name else rec.caption or "Arquivo sem nome"
            size_str = ""
            if src and src.file_size:
                size_mb = src.file_size / (1024 * 1024)
                size_str = f" [{size_mb:.1f} MB]" if size_mb >= 1 else f" [{src.file_size / 1024:.0f} KB]"

            month_key = rec.sent_at.strftime('%m/%Y') if rec.sent_at else "Desconhecido"
            day_str = rec.sent_at.strftime('%d/%m') if rec.sent_at else "??"

            if month_key not in months:
                months[month_key] = []
            months[month_key].append(f"  [{day_str}] {name}{size_str}")

        for month, items in months.items():
            lines.append(f"--- {month} ({len(items)} arquivo(s)) ---")
            lines.extend(items)
            lines.append("")

    # --- Seção: Arquivos futuros ---
    lines.append("")
    lines.append("┌────────────────────────────────────────┐")
    lines.append("│  EM BREVE — PRÓXIMOS ARQUIVOS          │")
    lines.append("└────────────────────────────────────────┘\n")

    if not future_files:
        lines.append("  Todos os arquivos já foram enviados!\n")
    else:
        for f in future_files:
            name = f.file_name or f.caption or "Arquivo sem nome"
            size_str = ""
            if f.file_size:
                size_mb = f.file_size / (1024 * 1024)
                size_str = f" [{size_mb:.1f} MB]" if size_mb >= 1 else f" [{f.file_size / 1024:.0f} KB]"
            lines.append(f"  - {name}{size_str}")

    lines.append("")

    footer = (
        "════════════════════════════════════════\n"
        "Esta lista é atualizada diariamente.\n"
        "Novos arquivos são adicionados todo dia às 15h.\n"
    )

    return header + "\n".join(lines) + "\n" + footer


async def _send_catalog_to_channel(bot: Bot, channel_id: int, config_key: str, catalog_content: str, caption: str):
    """
    Envia o catálogo .txt para um canal específico.
    Deleta o anterior, envia o novo e fixa no topo.
    """
    import tempfile
    import os

    # Deletar catálogo anterior (se existir)
    saved_msg_id = _cfg_get(config_key)
    if saved_msg_id:
        try:
            await bot.delete_message(
                chat_id=channel_id,
                message_id=int(saved_msg_id)
            )
            LOG.info(f"[CATALOG] Catálogo anterior deletado de {channel_id} (message_id={saved_msg_id})")
        except TelegramError as e:
            LOG.warning(f"[CATALOG] Não foi possível deletar catálogo anterior de {channel_id}: {e}")

    # Criar arquivo .txt temporário e enviar
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(catalog_content)
            temp_path = f.name

        with open(temp_path, 'rb') as f:
            msg = await bot.send_document(
                chat_id=channel_id,
                document=f,
                filename=f"Catalogo_VIP_{datetime.now().strftime('%d_%m_%Y')}.txt",
                caption=caption,
                parse_mode='HTML'
            )

        if msg:
            _cfg_set(config_key, str(msg.message_id))
            LOG.info(f"[CATALOG] ✅ Catálogo enviado para {channel_id} (message_id={msg.message_id})")

            # Fixar no topo do grupo
            try:
                await bot.pin_chat_message(
                    chat_id=channel_id,
                    message_id=msg.message_id,
                    disable_notification=True
                )
                LOG.info(f"[CATALOG] 📌 Catálogo fixado no topo de {channel_id}")
            except TelegramError as pin_err:
                LOG.warning(f"[CATALOG] Não foi possível fixar catálogo em {channel_id}: {pin_err}")

    except TelegramError as e:
        LOG.error(f"[CATALOG] ❌ Erro ao enviar catálogo para {channel_id}: {e}")
    finally:
        if temp_path and os.path.exists(temp_path):
            os.unlink(temp_path)


async def send_or_update_vip_catalog(bot: Bot, session: Session):
    """
    Envia o catálogo de arquivos VIP como .txt para os grupos VIP e FREE.
    - Deleta os catálogos anteriores
    - Envia novo .txt atualizado em ambos os grupos
    - Fixa (pin) no topo de cada grupo
    """
    if not _cfg_get or not _cfg_set:
        LOG.error("[CATALOG] Funções cfg_get/cfg_set não configuradas! Chame setup_catalog() primeiro.")
        return

    catalog_content = _build_catalog_content(session)

    # Enviar para o grupo VIP
    if VIP_CHANNEL_ID:
        await _send_catalog_to_channel(
            bot, VIP_CHANNEL_ID,
            "vip_catalog_message_id",
            catalog_content,
            caption=(
                "📋 <b>CATÁLOGO VIP — LISTA DE ARQUIVOS</b>\n\n"
                f"📦 Atualizado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}\n"
                "🔍 Baixe o arquivo e use Ctrl+F para pesquisar!\n"
                "📌 Esta lista é atualizada diariamente."
            )
        )
    else:
        LOG.error("[CATALOG] VIP_CHANNEL_ID não configurado!")

    # Enviar para o grupo FREE
    if FREE_CHANNEL_ID:
        await _send_catalog_to_channel(
            bot, FREE_CHANNEL_ID,
            "free_catalog_message_id",
            catalog_content,
            caption=(
                "📋 <b>CATÁLOGO — TODOS OS ARQUIVOS DISPONÍVEIS</b>\n\n"
                f"📦 Atualizado em {datetime.now().strftime('%d/%m/%Y às %H:%M')}\n"
                "🔍 Baixe o arquivo e use Ctrl+F para pesquisar!\n"
                "💎 Assine o VIP para receber conteúdo diário!"
            )
        )
    else:
        LOG.warning("[CATALOG] FREE_CHANNEL_ID não configurado, catálogo FREE não enviado")
