#!/usr/bin/env python3
"""
Script para descobrir IDs de grupos e canais do Telegram.

Lista todos os chats (grupos, canais, privados) que você participa
e mostra os IDs de cada um.

Uso:
    python descobrir_ids.py

Requer:
    pip install pyrogram tgcrypto
"""

import asyncio
import os
import sys
import platform

# Fix para Windows + Python 3.14+ (ANTES de importar pyrogram!)
if platform.system() == 'Windows':
    # Criar um event loop antes do pyrogram ser importado
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except:
        pass

from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.enums import ChatType

# Carregar variáveis de ambiente do diretório pai
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(parent_dir, '.env'))

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    print("❌ Erro: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados no .env!")
    print("💡 Obtenha em: https://my.telegram.org/apps")
    sys.exit(1)


async def listar_todos_chats():
    """Lista todos os chats do usuário com seus IDs."""

    app = Client(
        "descobrir_ids_session",
        api_id=int(API_ID),
        api_hash=API_HASH,
        workdir="."
    )

    try:
        await app.start()

        print("\n" + "="*70)
        print("🔍 DESCOBRIR IDs DE GRUPOS E CANAIS")
        print("="*70 + "\n")

        me = await app.get_me()
        print(f"✅ Conectado como: {me.first_name} (@{me.username or 'sem username'})")
        print(f"   Seu ID: {me.id}\n")

        print("📋 Listando todos os seus chats...\n")

        # Separar por tipo
        grupos = []
        supergrupos = []
        canais = []
        privados = []

        async for dialog in app.get_dialogs():
            chat = dialog.chat

            chat_info = {
                'id': chat.id,
                'title': chat.title or f"{chat.first_name or ''} {chat.last_name or ''}".strip(),
                'username': f"@{chat.username}" if chat.username else "sem username",
                'type': chat.type,
                'members_count': getattr(chat, 'members_count', None)
            }

            if chat.type == ChatType.GROUP:
                grupos.append(chat_info)
            elif chat.type == ChatType.SUPERGROUP:
                supergrupos.append(chat_info)
            elif chat.type == ChatType.CHANNEL:
                canais.append(chat_info)
            elif chat.type == ChatType.PRIVATE:
                privados.append(chat_info)

        # Exibir resultados
        print("="*70)
        print("📊 RESULTADOS")
        print("="*70 + "\n")

        # Grupos Normais
        if grupos:
            print(f"👥 GRUPOS NORMAIS ({len(grupos)}):")
            print("-" * 70)
            for g in grupos:
                members = f"({g['members_count']} membros)" if g['members_count'] else ""
                print(f"\n📌 {g['title']}")
                print(f"   ID: {g['id']}")
                print(f"   Username: {g['username']}")
                print(f"   Membros: {members}")
            print()

        # Supergrupos
        if supergrupos:
            print(f"👥 SUPERGRUPOS ({len(supergrupos)}):")
            print("-" * 70)
            for g in supergrupos:
                members = f"({g['members_count']} membros)" if g['members_count'] else ""
                print(f"\n📌 {g['title']}")
                print(f"   ID: {g['id']}")
                print(f"   Username: {g['username']}")
                print(f"   Membros: {members}")
            print()

        # Canais
        if canais:
            print(f"📢 CANAIS ({len(canais)}):")
            print("-" * 70)
            for c in canais:
                members = f"({c['members_count']} membros)" if c['members_count'] else ""
                print(f"\n📌 {c['title']}")
                print(f"   ID: {c['id']}")
                print(f"   Username: {c['username']}")
                print(f"   Membros: {members}")
            print()

        # Resumo
        print("="*70)
        print("📈 RESUMO:")
        print(f"   • Grupos normais: {len(grupos)}")
        print(f"   • Supergrupos: {len(supergrupos)}")
        print(f"   • Canais: {len(canais)}")
        print(f"   • Conversas privadas: {len(privados)}")
        print(f"   • Total: {len(grupos) + len(supergrupos) + len(canais) + len(privados)}")
        print("="*70 + "\n")

        # Dica
        print("💡 DICA:")
        print("   Use os IDs acima no script transferir_arquivos_user.py")
        print("   Os IDs de grupos/canais geralmente são negativos")
        print("   Exemplo: -1003080645605\n")

    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()

    finally:
        await app.stop()
        print("👋 Desconectado.\n")


async def buscar_chat_especifico():
    """Busca informações de um chat específico pelo username ou ID."""

    app = Client(
        "descobrir_ids_session",
        api_id=int(API_ID),
        api_hash=API_HASH,
        workdir="."
    )

    try:
        await app.start()

        print("\n" + "="*70)
        print("🔍 BUSCAR CHAT ESPECÍFICO")
        print("="*70 + "\n")

        busca = input("Digite o username (com @) ou ID do chat: ").strip()

        if not busca:
            print("❌ Nenhum valor informado.")
            return

        # Tentar buscar
        try:
            # Se for número, buscar por ID
            if busca.lstrip('-').isdigit():
                chat_id = int(busca)
                chat = await app.get_chat(chat_id)
            else:
                # Buscar por username
                chat = await app.get_chat(busca)

            print("\n✅ Chat encontrado!\n")
            print(f"📌 Título: {chat.title or 'Chat Privado'}")
            print(f"🆔 ID: {chat.id}")
            print(f"👤 Username: @{chat.username}" if chat.username else "   Sem username")
            print(f"📝 Tipo: {chat.type}")

            if hasattr(chat, 'members_count') and chat.members_count:
                print(f"👥 Membros: {chat.members_count}")

            if hasattr(chat, 'description') and chat.description:
                desc = chat.description[:100] + "..." if len(chat.description) > 100 else chat.description
                print(f"📄 Descrição: {desc}")

            print()

        except Exception as e:
            print(f"\n❌ Chat não encontrado ou sem acesso: {e}\n")

    except Exception as e:
        print(f"\n❌ Erro: {e}\n")

    finally:
        await app.stop()


async def main():
    """Menu principal."""
    print("\n" + "="*70)
    print("🔎 DESCOBRIR IDs DO TELEGRAM")
    print("="*70)

    while True:
        print("\n🔹 Escolha uma opção:")
        print("1 - Listar TODOS os seus chats (grupos, canais, etc)")
        print("2 - Buscar chat específico (por username ou ID)")
        print("3 - Sair")

        opcao = input("\nOpção: ").strip()

        if opcao == "1":
            await listar_todos_chats()
            break  # Sair após listar

        elif opcao == "2":
            await buscar_chat_especifico()

            continuar = input("\nBuscar outro? (s/n): ").strip().lower()
            if continuar not in ['s', 'sim']:
                break

        elif opcao == "3":
            print("\n👋 Até logo!\n")
            break

        else:
            print("\n❌ Opção inválida!\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Programa encerrado.\n")
    except Exception as e:
        print(f"\n❌ Erro fatal: {e}\n")
        import traceback
        traceback.print_exc()
