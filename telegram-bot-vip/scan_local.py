#!/usr/bin/env python3
"""
Script para fazer scan completo do histórico do grupo fonte LOCALMENTE.
Este script usa Pyrogram (User API) que permite autenticação interativa.

COMO USAR:
1. Configure as variáveis abaixo com suas credenciais
2. Execute: python scan_local.py
3. Na primeira vez, digite o código SMS que você receberá
4. Os arquivos serão indexados diretamente no banco do Render

IMPORTANTE: Este script conecta no banco PostgreSQL do Render!
"""

import asyncio
import os
from datetime import datetime, timezone
from sqlalchemy import create_engine, Column, Integer, String, Text, BigInteger, Boolean, DateTime
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# ============================================
# CONFIGURAÇÕES - PREENCHA COM SUAS CREDENCIAIS
# ============================================

# 1. Pyrogram (User API) - Obtenha em: https://my.telegram.org/apps
TELEGRAM_API_ID = "21891661"  # Número (ex: 12345678)
TELEGRAM_API_HASH = "3011acf0afc4bff11cfa8fc5c42207f9"  # String (ex: "abc123...")

# 2. Database do Render - Copie do painel do Render (Environment Variables)
# IMPORTANTE: A URL deve ter o domínio completo .render.com
# Exemplo: postgresql://user:pass@dpg-xxxxx.oregon-postgres.render.com:5432/dbname
DATABASE_URL = "postgresql://telegram_user:OC5lMKFbG2a7JrDH4lYzDw8tHCmJZkOv@dpg-d44lvpripnbc73am3aog-a.oregon-postgres.render.com:5432/telegram_bot_nsgf"

# 3. ID do grupo fonte
# Você pode usar:
# - ID numérico: -1003080645605
# - Username do grupo: @nome_do_grupo (se o grupo for público)
# - Deixe em branco para listar todos os grupos disponíveis: None
SOURCE_CHAT_ID = -1003080645605

# ============================================
# NÃO MEXA DAQUI PRA BAIXO
# ============================================

# Validar configurações
if TELEGRAM_API_ID == "SEU_API_ID_AQUI" or TELEGRAM_API_HASH == "SEU_API_HASH_AQUI":
    print("❌ ERRO: Configure TELEGRAM_API_ID e TELEGRAM_API_HASH!")
    print("   Obtenha em: https://my.telegram.org/apps")
    exit(1)

if "user:password@host" in DATABASE_URL:
    print("❌ ERRO: Configure DATABASE_URL!")
    print("   Copie do painel do Render > Environment Variables")
    exit(1)

# Validar formato da URL
if ".render.com" not in DATABASE_URL and "localhost" not in DATABASE_URL:
    print("⚠️  AVISO: A URL do banco pode estar incompleta!")
    print("   A URL do Render deve ter .render.com no final do host")
    print()
    print("   Exemplo correto:")
    print("   postgresql://user:pass@dpg-xxxxx.oregon-postgres.render.com:5432/dbname")
    print()
    print("   Como obter a URL correta:")
    print("   1. Acesse o painel do Render")
    print("   2. Clique no seu serviço do bot")
    print("   3. Vá em 'Environment' no menu lateral")
    print("   4. Procure por 'DATABASE_URL'")
    print("   5. Clique em 'Copy' e cole no script")
    print()
    resposta = input("   Deseja continuar mesmo assim? (s/n): ").strip().lower()
    if resposta != 's':
        exit(1)
    print()

# Converter postgres:// para postgresql://
DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

# Configurar SQLAlchemy
Base = declarative_base()

class SourceFile(Base):
    """Indexa todos os arquivos disponíveis no grupo fonte"""
    __tablename__ = "source_files"

    id = Column(Integer, primary_key=True)
    file_id = Column(String, nullable=False)
    file_unique_id = Column(String, nullable=False, unique=True, index=True)
    file_type = Column(String, nullable=False)
    message_id = Column(Integer, nullable=False, index=True)
    source_chat_id = Column(BigInteger, nullable=False)
    caption = Column(Text, nullable=True)
    file_name = Column(String, nullable=True)
    file_size = Column(BigInteger, nullable=True)
    indexed_at = Column(DateTime(timezone=True), nullable=False)
    active = Column(Boolean, default=True)

# Conectar ao banco
print("🔌 Conectando ao banco do Render...")
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine)

# Verificar conexão
try:
    with SessionLocal() as session:
        from sqlalchemy import text
        session.execute(text("SELECT 1"))
    print("✅ Conectado ao banco com sucesso!\n")
except Exception as e:
    print(f"❌ Erro ao conectar no banco:")
    print(f"   {type(e).__name__}: {e}")
    print()
    print("💡 Possíveis causas:")
    print("   1. DATABASE_URL incompleta (falta .render.com)")
    print("   2. Credenciais incorretas")
    print("   3. Banco não acessível externamente")
    print()
    print("🔍 URL atual:")
    # Mascarar senha para exibir
    url_masked = DATABASE_URL.split('@')[0].split(':')[0] + ":***@" + DATABASE_URL.split('@')[1] if '@' in DATABASE_URL else "???"
    print(f"   {url_masked}")
    print()
    print("📋 Como obter a URL correta do Render:")
    print("   1. Acesse: https://dashboard.render.com")
    print("   2. Clique no seu serviço (bot)")
    print("   3. Menu lateral: Environment")
    print("   4. Procure: DATABASE_URL")
    print("   5. Clique em 'Copy' (ícone de copiar)")
    print("   6. Cole no script (linha 33)")
    print()
    exit(1)


async def listar_grupos_disponiveis(app):
    """Lista todos os grupos/canais que a conta tem acesso"""
    print()
    print("=" * 70)
    print("📋 GRUPOS E CANAIS DISPONÍVEIS")
    print("=" * 70)
    print()
    print("⏳ Carregando diálogos...")

    grupos = []
    total_dialogs = 0

    try:
        # Tentar com limite maior
        async for dialog in app.get_dialogs(limit=500):
            total_dialogs += 1
            chat = dialog.chat

            # Debug: mostrar todos os tipos
            # print(f"Debug: {chat.title} - Tipo: {chat.type}")

            # Aceitar todos os tipos de grupos e canais
            if chat.type.name in ["SUPERGROUP", "CHANNEL", "GROUP"]:
                grupos.append({
                    'id': chat.id,
                    'title': chat.title,
                    'username': chat.username,
                    'type': chat.type.name
                })
    except Exception as e:
        print(f"⚠️  Erro ao listar diálogos: {e}")

    print(f"📊 Total de diálogos processados: {total_dialogs}")
    print()

    if not grupos:
        print("❌ Nenhum grupo/canal encontrado nos diálogos!")
        print()
        print("💡 Possíveis causas:")
        print("   1. A conta é nova no Telegram")
        print("   2. Você não está em nenhum grupo/canal")
        print("   3. Os diálogos ainda não foram sincronizados")
        print()
        print("📝 Você pode tentar inserir o ID manualmente.")
        print()

        resposta = input("Deseja inserir o grupo manualmente? (s/n): ").strip().lower()
        if resposta == 's':
            print()
            print("Você pode usar:")
            print("1. Username do grupo: @nome_do_grupo")
            print("2. Link do grupo: https://t.me/nome_do_grupo")
            print("3. ID numérico: -1003080645605")
            print()
            manual_input = input("Digite: ").strip()

            # Tentar converter
            if manual_input.startswith('@'):
                return manual_input  # Username
            elif 't.me/' in manual_input:
                # Extrair username do link
                username = manual_input.split('t.me/')[-1].split('/')[0]
                return f"@{username}" if not username.startswith('@') else username
            else:
                try:
                    return int(manual_input)  # ID numérico
                except ValueError:
                    print("❌ Formato inválido!")
                    return None
        return None

    print(f"✅ Encontrados {len(grupos)} grupos/canais:\n")
    for i, g in enumerate(grupos, 1):
        username_str = f"@{g['username']}" if g['username'] else "(sem username)"
        print(f"{i}. {g['title']}")
        print(f"   ID: {g['id']}")
        print(f"   Username: {username_str}")
        print(f"   Tipo: {g['type']}")
        print()

    # Pedir escolha
    while True:
        try:
            escolha = input("Digite o número do grupo fonte (ou 0 para cancelar): ").strip()
            escolha = int(escolha)
            if escolha == 0:
                return None
            if 1 <= escolha <= len(grupos):
                return grupos[escolha - 1]['id']
            print("❌ Número inválido!\n")
        except ValueError:
            print("❌ Digite um número!\n")


async def scan_completo():
    """Faz scan completo do histórico usando Pyrogram"""
    print("=" * 70)
    print("🔍 SCAN COMPLETO DO HISTÓRICO - PYROGRAM")
    print("=" * 70)
    print()

    # Importar Pyrogram
    try:
        from pyrogram import Client
    except ImportError:
        print("❌ Pyrogram não instalado!")
        print("\nInstale com:")
        print("pip install pyrogram tgcrypto")
        exit(1)

    print("✅ Pyrogram OK!")
    print()
    print("🔄 Iniciando autenticação...")
    print("💡 Na primeira vez, você receberá um código SMS")
    print()

    total_processadas = 0
    total_indexadas = 0
    total_duplicadas = 0
    total_erros = 0
    tipos_encontrados = {}
    final_chat_id = None  # ID do chat que foi escaneado

    with SessionLocal() as session:
        # Criar cliente Pyrogram
        app = Client(
            "bot_scanner_local",
            api_id=int(TELEGRAM_API_ID),
            api_hash=TELEGRAM_API_HASH,
            workdir="."
        )

        async with app:
            # Verificar autenticação
            me = await app.get_me()
            print(f"👤 Autenticado como: {me.first_name}")
            print()

            # Determinar o chat ID correto
            chat_id = SOURCE_CHAT_ID

            # Se SOURCE_CHAT_ID é None, listar grupos
            if chat_id is None:
                chat_id = await listar_grupos_disponiveis(app)
                if chat_id is None:
                    print("❌ Scan cancelado.")
                    return

            # Tentar resolver o chat
            try:
                print(f"🔍 Verificando acesso ao grupo {chat_id}...")
                chat = await app.get_chat(chat_id)
                print(f"✅ Grupo encontrado: {chat.title}")
                print()
                final_chat_id = chat_id  # Salvar o ID que funcionou
            except Exception as e:
                print(f"❌ Erro ao acessar o grupo: {e}")
                print()
                print("💡 Possíveis soluções:")
                print("1. Certifique-se de que sua conta está no grupo")
                print("2. Tente usar o username do grupo (ex: @nome_grupo)")
                print("3. Execute o script novamente e escolha da lista")
                print()

                # Oferecer listar grupos
                resposta = input("Deseja listar todos os seus grupos? (s/n): ").strip().lower()
                if resposta == 's':
                    chat_id = await listar_grupos_disponiveis(app)
                    if chat_id is None:
                        print("❌ Scan cancelado.")
                        return
                    # Tentar novamente com o novo ID
                    chat = await app.get_chat(chat_id)
                    print(f"✅ Grupo encontrado: {chat.title}")
                    print()
                    final_chat_id = chat_id  # Salvar o ID escolhido
                else:
                    return

            print("🔍 Escaneando mensagens...")
            print("⏳ Isso pode demorar vários minutos dependendo do histórico...")
            print()

            # Iterar por TODAS as mensagens
            async for message in app.get_chat_history(chat_id):
                total_processadas += 1

                # Progress a cada 100 mensagens
                if total_processadas % 100 == 0:
                    print(f"📊 Progresso: {total_processadas} mensagens | "
                          f"Indexadas: {total_indexadas} | "
                          f"Duplicadas: {total_duplicadas}")

                # Verificar se tem arquivo
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
                        'file_name': message.audio.file_name
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
                    continue

                # Criar novo registro
                try:
                    source_file = SourceFile(
                        file_id=file_data['file_id'],
                        file_unique_id=file_data['file_unique_id'],
                        file_type=file_data['file_type'],
                        message_id=message.id,
                        source_chat_id=chat_id,
                        caption=message.caption,
                        file_name=file_data.get('file_name'),
                        file_size=file_data.get('file_size'),
                        indexed_at=datetime.now(timezone.utc),
                        active=True
                    )
                    session.add(source_file)
                    session.commit()

                    total_indexadas += 1

                except Exception as e:
                    session.rollback()
                    total_erros += 1
                    print(f"❌ Erro ao indexar mensagem {message.id}: {e}")

    # Relatório final
    print()
    print("=" * 70)
    print("📊 RELATÓRIO FINAL")
    print("=" * 70)
    print()
    print(f"📨 Mensagens processadas: {total_processadas}")
    print(f"✅ Novas indexadas: {total_indexadas}")
    print(f"⏭️  Já existentes: {total_duplicadas}")
    print(f"❌ Erros: {total_erros}")
    print()

    if tipos_encontrados:
        print("📁 Tipos de arquivo encontrados:")
        for tipo, count in tipos_encontrados.items():
            print(f"   • {tipo}: {count}")
        print()

    # Total no banco
    if final_chat_id:
        with SessionLocal() as session:
            total_banco = session.query(SourceFile).filter(
                SourceFile.source_chat_id == final_chat_id,
                SourceFile.active == True
            ).count()

            print(f"💾 Total no banco (grupo {final_chat_id}): {total_banco} arquivos")
    else:
        print(f"💾 Total no banco: Não foi possível verificar")

    print()
    print("=" * 70)
    print("✅ SCAN COMPLETO FINALIZADO!")
    print("=" * 70)
    print()
    print("💡 Próximos passos:")
    print("1. No bot do Telegram, use /check_files para ver os arquivos")
    print("2. Use /test_send vip para testar envio")
    print("3. O bot já vai enviar automaticamente às 15h!")
    print()


if __name__ == "__main__":
    print()
    print("🤖 BOT TELEGRAM - SCAN LOCAL COM PYROGRAM")
    print()

    try:
        asyncio.run(scan_completo())
    except KeyboardInterrupt:
        print("\n\n⚠️  Scan interrompido pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
