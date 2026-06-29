#!/usr/bin/env python3
"""
Script simples para ler todos os arquivos do grupo no Telegram
e indexá-los no banco de dados para envio programado.

O bot enviará automaticamente:
- VIP: 1 arquivo por dia às 15h
- FREE: 1 arquivo por semana (quartas às 15h)

Uso:
    python ler_e_indexar_grupo.py
"""

import asyncio
import os
import sys
from datetime import datetime, timezone
from dotenv import load_dotenv

# Fix para encoding UTF-8 no Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Fix para Python 3.14 - criar event loop antes de importar pyrogram
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Carregar variáveis de ambiente
load_dotenv()

# Configurações do Telegram
TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")
# Grupo onde os arquivos estão armazenados
SOURCE_CHAT_ID = -1003387303533

# Configuração do banco (usar SQLite por padrão)
DATABASE_URL = os.getenv("DATABASE_URL", "")

# Se DATABASE_URL estiver vazio ou for PostgreSQL, usar SQLite
if not DATABASE_URL or "postgresql" in DATABASE_URL:
    DATABASE_URL = "sqlite:///bot.db"
    print("💾 Usando banco SQLite local: bot.db")
else:
    print(f"💾 Usando banco: {DATABASE_URL[:30]}...")

if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
    print("❌ Erro: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados!")
    print("Configure essas variáveis no arquivo .env")
    exit(1)

# Criar engine e sessão
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)

# Importar models depois de configurar o engine
from main import SourceFile, Base

# Criar tabelas se não existirem
Base.metadata.create_all(bind=engine)


async def indexar_todos_arquivos():
    """
    Lê todos os arquivos do grupo fonte e indexa no banco de dados.
    """
    print("\n" + "="*70)
    print("📥 INDEXADOR DE ARQUIVOS DO GRUPO")
    print("="*70 + "\n")

    # Criar cliente Pyrogram
    app = Client(
        "indexador_session",
        api_id=TELEGRAM_API_ID,
        api_hash=TELEGRAM_API_HASH
    )

    print("🔐 Conectando ao Telegram...")

    async with app:
        print("✅ Conectado com sucesso!\n")

        # Obter informações do chat
        try:
            chat = await app.get_chat(SOURCE_CHAT_ID)
            print(f"📋 Grupo: {chat.title}")
            print(f"🆔 ID: {SOURCE_CHAT_ID}\n")
        except Exception as e:
            print(f"❌ Erro ao acessar grupo {SOURCE_CHAT_ID}: {e}")
            print("\n💡 Dicas:")
            print("   1. Certifique-se de que você é membro do grupo")
            print("   2. Verifique se o ID do grupo está correto")
            print("   3. Tente enviar uma mensagem no grupo primeiro para 'ativar' a conexão")
            print(f"\n🔗 Link de acesso: https://t.me/c/{str(SOURCE_CHAT_ID)[4:]}/1")
            return

        print("🔍 Lendo histórico de mensagens...\n")
        print("⏳ Isso pode demorar alguns minutos dependendo da quantidade de arquivos.\n")

        # Estatísticas
        total_mensagens = 0
        total_arquivos_novos = 0
        total_duplicados = 0
        tipos_encontrados = {}

        # Abrir sessão do banco
        with SessionLocal() as session:
            # Iterar por todas as mensagens do grupo
            async for message in app.get_chat_history(SOURCE_CHAT_ID, limit=0):
                total_mensagens += 1

                # Mostrar progresso a cada 100 mensagens
                if total_mensagens % 100 == 0:
                    print(f"📊 Processadas {total_mensagens} mensagens... "
                          f"({total_arquivos_novos} arquivos indexados)")

                # Verificar se a mensagem tem arquivo
                file_data = None

                if message.photo:
                    file_data = {
                        'file_id': message.photo.file_id,
                        'file_unique_id': message.photo.file_unique_id,
                        'file_type': 'photo',
                        'file_size': message.photo.file_size,
                        'file_name': None
                    }

                elif message.video:
                    file_data = {
                        'file_id': message.video.file_id,
                        'file_unique_id': message.video.file_unique_id,
                        'file_type': 'video',
                        'file_size': message.video.file_size,
                        'file_name': message.video.file_name
                    }

                elif message.document:
                    file_data = {
                        'file_id': message.document.file_id,
                        'file_unique_id': message.document.file_unique_id,
                        'file_type': 'document',
                        'file_size': message.document.file_size,
                        'file_name': message.document.file_name
                    }

                elif message.animation:
                    file_data = {
                        'file_id': message.animation.file_id,
                        'file_unique_id': message.animation.file_unique_id,
                        'file_type': 'animation',
                        'file_size': message.animation.file_size,
                        'file_name': message.animation.file_name
                    }

                elif message.audio:
                    file_data = {
                        'file_id': message.audio.file_id,
                        'file_unique_id': message.audio.file_unique_id,
                        'file_type': 'audio',
                        'file_size': message.audio.file_size,
                        'file_name': message.audio.file_name or 'audio'
                    }

                # Se não tem arquivo, pular
                if not file_data:
                    continue

                # Contar tipo de arquivo
                tipo = file_data['file_type']
                tipos_encontrados[tipo] = tipos_encontrados.get(tipo, 0) + 1

                # Verificar se já existe no banco
                existing = session.query(SourceFile).filter(
                    SourceFile.file_unique_id == file_data['file_unique_id']
                ).first()

                if existing:
                    total_duplicados += 1
                    continue

                # Adicionar ao banco
                try:
                    source_file = SourceFile(
                        file_id=file_data['file_id'],
                        file_unique_id=file_data['file_unique_id'],
                        file_type=file_data['file_type'],
                        message_id=message.id,
                        source_chat_id=SOURCE_CHAT_ID,
                        caption=message.caption,
                        file_name=file_data.get('file_name'),
                        file_size=file_data.get('file_size'),
                        indexed_at=datetime.now(timezone.utc),
                        active=True
                    )
                    session.add(source_file)
                    session.commit()

                    total_arquivos_novos += 1

                    # Mostrar arquivo indexado
                    size_mb = (file_data.get('file_size') or 0) / (1024*1024)
                    nome = file_data.get('file_name') or tipo
                    print(f"✅ {tipo:10} | {size_mb:6.1f} MB | {nome[:40]}")

                except Exception as e:
                    print(f"❌ Erro ao indexar mensagem {message.id}: {e}")
                    session.rollback()

        # Relatório final
        print("\n" + "="*70)
        print("📊 RELATÓRIO FINAL")
        print("="*70 + "\n")

        print(f"📨 Mensagens processadas: {total_mensagens}")
        print(f"✅ Arquivos novos indexados: {total_arquivos_novos}")
        print(f"⏭️  Arquivos já existentes: {total_duplicados}\n")

        if tipos_encontrados:
            print("📁 Tipos de arquivo encontrados:")
            for tipo, count in sorted(tipos_encontrados.items()):
                print(f"   • {tipo:10} : {count:4} arquivo(s)")

        print("\n" + "="*70)
        print("✅ INDEXAÇÃO CONCLUÍDA!")
        print("="*70 + "\n")

        print("💡 O que acontece agora?")
        print("   • VIP: 1 arquivo será enviado automaticamente TODO DIA às 15h")
        print("   • FREE: 1 arquivo será enviado automaticamente TODA QUARTA às 15h")
        print("\n   O bot escolherá arquivos aleatórios que ainda não foram enviados.")
        print("\n📌 Use /stats_auto no bot para ver estatísticas\n")


if __name__ == "__main__":
    try:
        asyncio.run(indexar_todos_arquivos())
    except KeyboardInterrupt:
        print("\n\n⚠️  Processo interrompido pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
