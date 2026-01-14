#!/usr/bin/env python3
"""
Script para resolver e cachear grupos no Pyrogram.

Este script força o Pyrogram a buscar informações dos grupos
e adicioná-los ao cache local, resolvendo o erro "Peer id invalid".
"""

import asyncio
import os
import sys
import platform

# Fix para Windows + Python 3.14+
if platform.system() == 'Windows':
    try:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    except:
        pass

from dotenv import load_dotenv
from pyrogram import Client
from pyrogram.errors import ChannelPrivate, PeerIdInvalid

# Carregar variáveis de ambiente
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(parent_dir, '.env'))

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    print("❌ Erro: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados!")
    sys.exit(1)


async def resolver_grupo(app: Client, chat_id: int):
    """
    Tenta resolver um grupo usando múltiplos métodos.
    """
    print(f"\n🔍 Tentando resolver grupo {chat_id}...")

    # Método 1: Tentar buscar diretamente
    try:
        chat = await app.get_chat(chat_id)
        print(f"✅ Método 1 (get_chat) funcionou!")
        print(f"   Título: {chat.title}")
        print(f"   Tipo: {chat.type}")
        return True
    except PeerIdInvalid:
        print(f"   ❌ Método 1 falhou: Peer id invalid")
    except ChannelPrivate:
        print(f"   ❌ Método 1 falhou: Você não é membro deste grupo")
        return False
    except Exception as e:
        print(f"   ❌ Método 1 falhou: {e}")

    # Método 2: Forçar resolução do peer
    try:
        print(f"\n🔄 Tentando Método 2: resolve_peer...")
        peer = await app.resolve_peer(chat_id)
        print(f"✅ Peer resolvido: {peer}")

        # Tentar buscar novamente
        chat = await app.get_chat(chat_id)
        print(f"✅ Grupo encontrado!")
        print(f"   Título: {chat.title}")
        print(f"   Tipo: {chat.type}")
        return True
    except Exception as e:
        print(f"   ❌ Método 2 falhou: {e}")

    # Método 3: Buscar nos diálogos
    print(f"\n🔄 Tentando Método 3: buscar nos diálogos...")
    try:
        async for dialog in app.get_dialogs():
            if dialog.chat.id == chat_id:
                print(f"✅ Encontrado nos diálogos!")
                print(f"   Título: {dialog.chat.title}")
                print(f"   Tipo: {dialog.chat.type}")
                return True

        print(f"   ❌ Grupo não encontrado nos seus diálogos")
    except Exception as e:
        print(f"   ❌ Método 3 falhou: {e}")

    # Método 4: Tentar obter histórico (força o cache)
    print(f"\n🔄 Tentando Método 4: get_chat_history...")
    try:
        async for message in app.get_chat_history(chat_id, limit=1):
            print(f"✅ Consegui acessar o histórico!")
            chat = await app.get_chat(chat_id)
            print(f"   Título: {chat.title}")
            return True
    except PeerIdInvalid:
        print(f"   ❌ Método 4 falhou: Peer id invalid")
    except ChannelPrivate:
        print(f"   ❌ Você não é membro deste grupo ou não tem acesso")
        return False
    except Exception as e:
        print(f"   ❌ Método 4 falhou: {e}")

    return False


async def main():
    """Função principal."""
    print("\n" + "="*70)
    print("🔧 RESOLVER GRUPOS (Corrigir 'Peer id invalid')")
    print("="*70 + "\n")

    app = Client(
        "transferir_arquivos_session",
        api_id=int(API_ID),
        api_hash=API_HASH,
        workdir="."
    )

    try:
        await app.start()

        me = await app.get_me()
        print(f"✅ Conectado como: {me.first_name} (@{me.username or 'sem username'})")
        print(f"   ID: {me.id}\n")

        # Solicitar IDs dos grupos
        print("Digite os IDs dos grupos para resolver:\n")

        try:
            fonte_input = input("🔹 ID do grupo FONTE (ex: -1003080645605): ").strip()
            fonte_id = int(fonte_input)
        except ValueError:
            print("❌ ID inválido!")
            return

        try:
            dest_input = input("🔹 ID do grupo DESTINO (ex: -1003387303533): ").strip()
            dest_id = int(dest_input)
        except ValueError:
            print("❌ ID inválido!")
            return

        print("\n" + "="*70)
        print("RESOLVENDO GRUPO FONTE")
        print("="*70)

        fonte_ok = await resolver_grupo(app, fonte_id)

        print("\n" + "="*70)
        print("RESOLVENDO GRUPO DESTINO")
        print("="*70)

        dest_ok = await resolver_grupo(app, dest_id)

        # Resumo
        print("\n" + "="*70)
        print("📊 RESUMO")
        print("="*70 + "\n")

        if fonte_ok:
            print(f"✅ Grupo fonte ({fonte_id}): OK")
        else:
            print(f"❌ Grupo fonte ({fonte_id}): FALHOU")
            print(f"   Verifique se você é membro deste grupo!")

        if dest_ok:
            print(f"✅ Grupo destino ({dest_id}): OK")
        else:
            print(f"❌ Grupo destino ({dest_id}): FALHOU")
            print(f"   Verifique se você é membro e admin deste grupo!")

        print()

        if fonte_ok and dest_ok:
            print("🎉 Ambos os grupos foram resolvidos com sucesso!")
            print("   Agora você pode executar o transferir_arquivos_user.py")
        elif not fonte_ok:
            print("⚠️  PROBLEMA: Você precisa ser MEMBRO do grupo FONTE")
            print("   Entre no grupo antes de tentar transferir arquivos.")
        elif not dest_ok:
            print("⚠️  PROBLEMA: Você precisa ser MEMBRO do grupo DESTINO")
            print("   Entre no grupo e peça permissões de administrador.")

        print("\n" + "="*70)

    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()

    finally:
        await app.stop()
        print("\n👋 Desconectado.\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Programa encerrado.\n")
    except Exception as e:
        print(f"\n❌ Erro fatal: {e}\n")
        import traceback
        traceback.print_exc()
