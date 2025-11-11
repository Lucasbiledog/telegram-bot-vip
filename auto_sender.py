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

from telegram import Bot, Message, Update
from telegram.error import TelegramError
from sqlalchemy.orm import Session

LOG = logging.getLogger(__name__)

# IDs dos canais/grupos
SOURCE_CHAT_ID = -1003080645605  # Grupo fonte com todos os arquivos
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

    return any(pattern in text_to_check for pattern in part_patterns)


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

    Args:
        bot: Instância do bot
        source_file: Objeto SourceFile do arquivo a enviar
        channel_id: ID do canal destino
        caption: Legenda opcional (sobrescreve a original)

    Returns:
        Message enviada ou None se falhar
    """
    try:
        file_type = source_file.file_type
        file_id = source_file.file_id

        # Usar legenda original se não especificada
        if caption is None:
            caption = source_file.caption

        LOG.info(f"[AUTO-SEND] Enviando {file_type} para canal {channel_id}")

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

        LOG.info(f"[AUTO-SEND] ✅ Arquivo enviado com sucesso para {channel_id}")
        return msg

    except TelegramError as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro ao enviar arquivo: {e}")
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


async def send_daily_vip_file(bot: Bot, session: Session):
    """
    Envia arquivo diário para o canal VIP (executa às 15h).
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

        # Preparar legenda
        caption = f"🔥 Conteúdo VIP Exclusivo\n📅 {datetime.now().strftime('%d/%m/%Y')}"
        if source_file.caption:
            caption += f"\n\n{source_file.caption}"

        # Enviar para canal VIP
        msg = await send_file_to_channel(bot, source_file, VIP_CHANNEL_ID, caption)

        if msg:
            # Marcar como enviado
            await mark_file_as_sent(session, source_file, 'vip')
            LOG.info("[AUTO-SEND] ✅ Envio VIP diário concluído com sucesso")
            LOG.info(f"[AUTO-SEND] Tipo: {source_file.file_type}, Message ID: {msg.message_id}")
        else:
            LOG.error("[AUTO-SEND] ❌ Falha no envio VIP diário")

    except Exception as e:
        LOG.error(f"[AUTO-SEND] ❌ Erro no envio VIP diário: {e}")
        import traceback
        LOG.error(traceback.format_exc())


async def send_weekly_free_file(bot: Bot, session: Session):
    """
    Envia arquivo semanal para o canal FREE (quartas às 15h).
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

        # Preparar legenda
        caption = f"🆓 Conteúdo Grátis da Semana\n📅 {datetime.now().strftime('%d/%m/%Y')}"
        if source_file.caption:
            caption += f"\n\n{source_file.caption}"

        # Enviar para canal FREE
        msg = await send_file_to_channel(bot, source_file, FREE_CHANNEL_ID, caption)

        if msg:
            # Marcar como enviado
            await mark_file_as_sent(session, source_file, 'free')
            LOG.info("[AUTO-SEND] ✅ Envio FREE semanal concluído com sucesso")
            LOG.info(f"[AUTO-SEND] Tipo: {source_file.file_type}, Message ID: {msg.message_id}")
        else:
            LOG.error("[AUTO-SEND] ❌ Falha no envio FREE semanal")

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
