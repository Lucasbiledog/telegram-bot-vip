#!/usr/bin/env python3
"""
Script para fazer scan completo do histórico do grupo fonte
e indexar todos os arquivos existentes.

Uso:
    python scan_historico.py

Este script deve ser executado UMA VEZ após o deploy inicial
para indexar todo o histórico de arquivos do grupo fonte.
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv
from telegram import Bot
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Adicionar diretório pai ao path para imports
sys.path.insert(0, os.path.dirname(__file__))

# Carregar variáveis de ambiente
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
SOURCE_CHAT_ID = -1003080645605  # Grupo fonte padrão

# Configuração do banco
from db import engine, SessionLocal
from auto_sender import SourceFile

if not BOT_TOKEN:
    print("❌ Erro: BOT_TOKEN não encontrado!")
    exit(1)


async def scan_message(bot: Bot, chat_id: int, message_id: int, session):
    """
    Tenta buscar e indexar uma mensagem específica.
    """
    try:
        # Tentar copiar mensagem para obter informações
        # (método mais confiável que forward)
        copied = await bot.copy_message(
            chat_id=chat_id,
            from_chat_id=chat_id,
            message_id=message_id
        )

        # Se conseguiu copiar, buscar a mensagem original
        msg = await bot.forward_message(
            chat_id=chat_id,
            from_chat_id=chat_id,
            message_id=message_id
        )

        # Deletar mensagem copiada
        await bot.delete_message(chat_id=chat_id, message_id=copied.message_id)

        return msg
    except Exception:
        return None


async def scan_historico_completo(chat_id: int = SOURCE_CHAT_ID, limit: int = 1000):
    """
    Faz scan do histórico completo do grupo fonte e indexa todos os arquivos.

    Args:
        chat_id: ID do grupo fonte
        limit: Número máximo de mensagens para processar (0 = ilimitado)
    """
    bot = Bot(token=BOT_TOKEN)

    print("\n" + "="*70)
    print("🔍 SCAN DO HISTÓRICO DO GRUPO FONTE")
    print("="*70 + "\n")

    try:
        chat = await bot.get_chat(chat_id)
        print(f"📋 Grupo: {chat.title}")
        print(f"🆔 ID: {chat_id}\n")
    except Exception as e:
        print(f"❌ Erro ao acessar grupo {chat_id}: {e}")
        return

    print("⏳ Iniciando scan... (isso pode demorar alguns minutos)\n")

    with SessionLocal() as session:
        # Estatísticas
        total_processadas = 0
        total_indexadas = 0
        total_duplicadas = 0
        total_erros = 0

        # Tipos encontrados
        tipos_encontrados = {}

        # Tentar diferentes estratégias de scan
        print("🔄 Método 1: Tentando buscar mensagens recentes...\n")

        # Estratégia: Buscar updates recentes
        try:
            # Obter últimas atualizações
            updates = await bot.get_updates(limit=100, timeout=30)

            for update in updates:
                if not update.message or update.message.chat_id != chat_id:
                    continue

                msg = update.message
                total_processadas += 1

                # Verificar se tem arquivo
                file_data = None

                if msg.photo:
                    photo = msg.photo[-1]
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
                    continue

                # Contar tipos
                tipo = file_data['file_type']
                tipos_encontrados[tipo] = tipos_encontrados.get(tipo, 0) + 1

                # Verificar se já existe
                existing = session.query(SourceFile).filter(
                    SourceFile.file_unique_id == file_data['file_unique_id']
                ).first()

                if existing:
                    total_duplicadas += 1
                    print(f"⏭️  Já indexado: {tipo} - {msg.message_id}")
                    continue

                # Criar novo registro
                try:
                    source_file = SourceFile(
                        file_id=file_data['file_id'],
                        file_unique_id=file_data['file_unique_id'],
                        file_type=file_data['file_type'],
                        message_id=msg.message_id,
                        source_chat_id=chat_id,
                        caption=msg.caption,
                        file_name=file_data.get('file_name'),
                        file_size=file_data.get('file_size'),
                        indexed_at=datetime.now(timezone.utc),
                        active=True
                    )
                    session.add(source_file)
                    session.commit()

                    total_indexadas += 1
                    size_mb = file_data.get('file_size', 0) / (1024*1024)
                    print(f"✅ Indexado: {tipo} - {msg.message_id} ({size_mb:.1f} MB)")

                except Exception as e:
                    session.rollback()
                    total_erros += 1
                    print(f"❌ Erro ao indexar {msg.message_id}: {e}")

                if limit > 0 and total_processadas >= limit:
                    break

        except Exception as e:
            print(f"⚠️  Método 1 falhou: {e}\n")

        # Método 2: Scan manual (se disponível via pyrogram ou telethon)
        print(f"\n💡 Dica: Para scan completo do histórico, use pyrogram ou telethon")
        print(f"   O Bot API do Telegram tem limitações para acessar histórico completo.\n")

        # Relatório final
        print("\n" + "="*70)
        print("📊 RELATÓRIO DO SCAN")
        print("="*70 + "\n")

        print(f"📨 Mensagens processadas: {total_processadas}")
        print(f"✅ Arquivos indexados: {total_indexadas}")
        print(f"⏭️  Duplicados (já existiam): {total_duplicadas}")
        print(f"❌ Erros: {total_erros}\n")

        if tipos_encontrados:
            print("📁 Tipos de arquivo encontrados:")
            for tipo, count in tipos_encontrados.items():
                print(f"   • {tipo}: {count}")
        print()

        # Estatísticas do banco
        total_banco = session.query(SourceFile).filter(
            SourceFile.source_chat_id == chat_id,
            SourceFile.active == True
        ).count()

        print(f"💾 Total no banco (grupo {chat_id}): {total_banco} arquivos")

        print("\n" + "="*70)

        if total_indexadas > 0:
            print("✅ Scan concluído com sucesso!")
        elif total_duplicadas > 0:
            print("ℹ️  Scan concluído - todos os arquivos já estavam indexados")
        else:
            print("⚠️  Nenhum arquivo encontrado")

        print("\n💡 PRÓXIMOS PASSOS:")
        print("1. Use /stats_auto no bot para ver estatísticas")
        print("2. Use /test_send vip para testar envio")
        print("3. O bot agora indexará novos arquivos automaticamente\n")


async def listar_arquivos_indexados():
    """
    Lista todos os arquivos já indexados no banco.
    """
    print("\n" + "="*70)
    print("📋 ARQUIVOS INDEXADOS NO BANCO")
    print("="*70 + "\n")

    with SessionLocal() as session:
        arquivos = session.query(SourceFile).filter(
            SourceFile.source_chat_id == SOURCE_CHAT_ID,
            SourceFile.active == True
        ).order_by(SourceFile.indexed_at.desc()).limit(50).all()

        if not arquivos:
            print("⚠️  Nenhum arquivo indexado ainda.\n")
            print("Execute o scan primeiro com a opção 1.\n")
            return

        print(f"📁 Total: {len(arquivos)} arquivos (mostrando últimos 50)\n")

        for arq in arquivos:
            size_mb = (arq.file_size or 0) / (1024*1024)
            caption = arq.caption[:50] + "..." if arq.caption and len(arq.caption) > 50 else arq.caption or "(sem legenda)"

            print(f"🆔 ID: {arq.id}")
            print(f"   Tipo: {arq.file_type}")
            print(f"   Tamanho: {size_mb:.1f} MB")
            print(f"   Caption: {caption}")
            print(f"   Message ID: {arq.message_id}")
            print(f"   Indexado: {arq.indexed_at.strftime('%d/%m/%Y %H:%M')}")
            print()


async def main():
    """Menu principal"""
    print("\n" + "="*70)
    print("🤖 BOT TELEGRAM - SCAN DE HISTÓRICO")
    print("="*70)

    while True:
        print("\nEscolha uma opção:")
        print("1 - Fazer scan do histórico (indexar arquivos)")
        print("2 - Listar arquivos já indexados")
        print("3 - Sair")

        opcao = input("\nOpção: ").strip()

        if opcao == "1":
            limite = input("\nLimite de mensagens (0 = sem limite): ").strip()
            try:
                limite = int(limite) if limite else 1000
            except:
                limite = 1000

            await scan_historico_completo(SOURCE_CHAT_ID, limite)

        elif opcao == "2":
            await listar_arquivos_indexados()

        elif opcao == "3":
            print("\n👋 Até logo!\n")
            break
        else:
            print("\n❌ Opção inválida!\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Programa interrompido pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
