#!/usr/bin/env python3
"""
Script para verificar se você é membro de um grupo específico.
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
from pyrogram.enums import ChatType

# Carregar variáveis de ambiente
parent_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(parent_dir, '.env'))

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    print("❌ Erro: TELEGRAM_API_ID e TELEGRAM_API_HASH não encontrados!")
    sys.exit(1)


async def verificar_membro(chat_id: int):
    """Verifica se você é membro de um grupo."""

    app = Client(
        "transferir_arquivos_session",  # Mesma sessão
        api_id=int(API_ID),
        api_hash=API_HASH,
        workdir="."
    )

    try:
        await app.start()

        me = await app.get_me()
        print(f"\n✅ Conectado como: {me.first_name}\n")

        print("="*70)
        print("🔍 VERIFICANDO GRUPO")
        print("="*70 + "\n")

        # Primeiro: buscar nos diálogos
        print("📋 Buscando nos seus grupos...\n")

        encontrado = False
        async for dialog in app.get_dialogs():
            if dialog.chat.id == chat_id:
                print(f"✅ ENCONTRADO nos seus diálogos!")
                print(f"   📌 Nome: {dialog.chat.title}")
                print(f"   🆔 ID: {dialog.chat.id}")
                print(f"   📝 Tipo: {dialog.chat.type}")

                if dialog.chat.type in [ChatType.SUPERGROUP, ChatType.GROUP]:
                    print(f"   👥 Você É MEMBRO deste grupo!")
                encontrado = True
                break

        if not encontrado:
            print(f"❌ Grupo {chat_id} NÃO encontrado nos seus diálogos!")
            print(f"\n💡 SOLUÇÃO:")
            print(f"   1. Verifique se o ID está correto")
            print(f"   2. Entre no grupo se ainda não for membro")
            print(f"   3. Execute este script novamente\n")

            # Mostrar alguns grupos para comparar
            print("="*70)
            print("📋 SEUS GRUPOS (primeiros 10):")
            print("="*70 + "\n")

            count = 0
            async for dialog in app.get_dialogs():
                if dialog.chat.type in [ChatType.SUPERGROUP, ChatType.GROUP]:
                    print(f"📌 {dialog.chat.title}")
                    print(f"   ID: {dialog.chat.id}\n")
                    count += 1
                    if count >= 10:
                        break

        print("="*70)

    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()

    finally:
        await app.stop()


async def main():
    """Função principal."""
    print("\n" + "="*70)
    print("🔍 VERIFICAR SE É MEMBRO DE UM GRUPO")
    print("="*70 + "\n")

    try:
        chat_id_input = input("Digite o ID do grupo (ex: -1003080645605): ").strip()
        chat_id = int(chat_id_input)
    except ValueError:
        print("\n❌ ID inválido!\n")
        return

    await verificar_membro(chat_id)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Encerrado.\n")
