#!/usr/bin/env python3
"""
Sistema de indexação automática usando Pyrogram com sessão persistente.
NÃO requer autenticação a cada vez - usa sessão salva.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from pyrogram import Client
from pyrogram.types import Message
from sqlalchemy.orm import Session

# Importar models
import sys
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

LOG = logging.getLogger("auto_indexer")

# Pyrogram client global (reutilizado)
_pyrogram_client: Optional[Client] = None


def get_pyrogram_client() -> Client:
    """Retorna instância global do Pyrogram client"""
    global _pyrogram_client

    if _pyrogram_client is None:
        from config import TELEGRAM_API_ID, TELEGRAM_API_HASH

        if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
            raise ValueError(
                "TELEGRAM_API_ID e TELEGRAM_API_HASH devem estar configurados no .env!\n"
                "Obtenha em: https://my.telegram.org/apps"
            )

        # Criar cliente com sessão persistente
        _pyrogram_client = Client(
            name="bot_indexer_session",
            api_id=int(TELEGRAM_API_ID),
            api_hash=TELEGRAM_API_HASH,
            workdir=".",  # Salva sessão no diretório atual
            no_updates=True  # Não precisa receber updates
        )

        LOG.info("[INDEXER] Cliente Pyrogram criado")

    return _pyrogram_client


async def index_group_history(
    session: Session,
    source_chat_id: int,
    limit: Optional[int] = None,
    progress_callback=None
) -> dict:
    """
    Indexa histórico completo de um grupo usando Pyrogram.

    Args:
        session: Sessão SQLAlchemy
        source_chat_id: ID do grupo fonte
        limit: Limite de mensagens (None = todas)
        progress_callback: Função callback(total, indexed, duplicated)

    Returns:
        dict com estatísticas da indexação
    """
    from main import SourceFile

    client = get_pyrogram_client()

    stats = {
        'total_processed': 0,
        'newly_indexed': 0,
        'duplicated': 0,
        'errors': 0,
        'file_types': {}
    }

    LOG.info(f"[INDEXER] Iniciando indexação do grupo {source_chat_id}")

    try:
        async with client:
            # Verificar acesso ao grupo
            try:
                chat = await client.get_chat(source_chat_id)
                LOG.info(f"[INDEXER] Grupo encontrado: {chat.title}")
            except Exception as e:
                LOG.error(f"[INDEXER] Erro ao acessar grupo {source_chat_id}: {e}")
                stats['errors'] += 1
                return stats

            # Iterar histórico
            async for message in client.get_chat_history(source_chat_id, limit=limit):
                stats['total_processed'] += 1

                # Progress a cada 100 mensagens
                if stats['total_processed'] % 100 == 0:
                    LOG.info(
                        f"[INDEXER] Progresso: {stats['total_processed']} mensagens | "
                        f"Indexadas: {stats['newly_indexed']} | "
                        f"Duplicadas: {stats['duplicated']}"
                    )
                    if progress_callback:
                        progress_callback(
                            stats['total_processed'],
                            stats['newly_indexed'],
                            stats['duplicated']
                        )

                # Extrair dados do arquivo
                file_data = extract_file_data(message)

                if not file_data:
                    continue

                # Contar tipos
                file_type = file_data['file_type']
                stats['file_types'][file_type] = stats['file_types'].get(file_type, 0) + 1

                # Verificar se já existe
                existing = session.query(SourceFile).filter(
                    SourceFile.file_unique_id == file_data['file_unique_id']
                ).first()

                if existing:
                    stats['duplicated'] += 1
                    continue

                # Criar novo registro
                try:
                    source_file = SourceFile(
                        file_id=file_data['file_id'],
                        file_unique_id=file_data['file_unique_id'],
                        file_type=file_data['file_type'],
                        message_id=message.id,
                        source_chat_id=source_chat_id,
                        caption=message.caption,
                        file_name=file_data.get('file_name'),
                        file_size=file_data.get('file_size'),
                        indexed_at=datetime.now(timezone.utc),
                        active=True
                    )
                    session.add(source_file)
                    session.commit()

                    stats['newly_indexed'] += 1

                except Exception as e:
                    session.rollback()
                    stats['errors'] += 1
                    LOG.error(f"[INDEXER] Erro ao indexar mensagem {message.id}: {e}")

        LOG.info(f"[INDEXER] ✅ Indexação concluída: {stats}")
        return stats

    except Exception as e:
        LOG.error(f"[INDEXER] Erro na indexação: {e}")
        stats['errors'] += 1
        return stats


def extract_file_data(message: Message) -> Optional[dict]:
    """Extrai dados de arquivo de uma mensagem"""

    if message.photo:
        return {
            'file_id': message.photo.file_id,
            'file_unique_id': message.photo.file_unique_id,
            'file_type': 'photo',
            'file_size': message.photo.file_size,
            'file_name': None
        }

    elif message.video:
        return {
            'file_id': message.video.file_id,
            'file_unique_id': message.video.file_unique_id,
            'file_type': 'video',
            'file_size': message.video.file_size,
            'file_name': message.video.file_name
        }

    elif message.document:
        return {
            'file_id': message.document.file_id,
            'file_unique_id': message.document.file_unique_id,
            'file_type': 'document',
            'file_size': message.document.file_size,
            'file_name': message.document.file_name
        }

    elif message.audio:
        return {
            'file_id': message.audio.file_id,
            'file_unique_id': message.audio.file_unique_id,
            'file_type': 'audio',
            'file_size': message.audio.file_size,
            'file_name': message.audio.file_name
        }

    elif message.animation:
        return {
            'file_id': message.animation.file_id,
            'file_unique_id': message.animation.file_unique_id,
            'file_type': 'animation',
            'file_size': message.animation.file_size,
            'file_name': message.animation.file_name
        }

    return None


async def index_on_startup(session: Session, source_chat_id: int):
    """
    Indexação rápida no startup (apenas arquivos novos recentes).
    Busca últimas 1000 mensagens para pegar arquivos novos.
    """
    LOG.info("[INDEXER] Executando indexação rápida no startup...")

    stats = await index_group_history(
        session=session,
        source_chat_id=source_chat_id,
        limit=1000  # Apenas últimas 1000 mensagens
    )

    if stats['newly_indexed'] > 0:
        LOG.info(f"[INDEXER] ✅ {stats['newly_indexed']} novos arquivos indexados no startup")
    else:
        LOG.info("[INDEXER] ℹ️ Nenhum arquivo novo no startup")

    return stats


# Para ser chamado via comando no bot
async def index_full_history_command(session: Session, source_chat_id: int, update_message_func=None):
    """
    Indexação completa via comando do bot.

    Args:
        update_message_func: Função para atualizar mensagem com progresso
    """
    LOG.info("[INDEXER] Iniciando indexação completa via comando...")

    # Callback de progresso
    last_update_count = 0

    def progress_callback(total, indexed, duplicated):
        nonlocal last_update_count
        # Atualizar mensagem a cada 500 mensagens
        if total - last_update_count >= 500 and update_message_func:
            asyncio.create_task(update_message_func(
                f"🔍 Indexando...\n\n"
                f"📨 Processadas: {total}\n"
                f"✅ Indexadas: {indexed}\n"
                f"⏭️ Duplicadas: {duplicated}"
            ))
            last_update_count = total

    stats = await index_group_history(
        session=session,
        source_chat_id=source_chat_id,
        limit=None,  # TODAS as mensagens
        progress_callback=progress_callback
    )

    return stats
