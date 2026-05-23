#!/usr/bin/env python3
"""
Lista todos os grupos e canais que você administra para descobrir o ID correto.
"""

import asyncio
import sys

# Fix para Python 3.14+
try:
    loop = asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from dotenv import load_dotenv
from pyrogram import Client
import os

load_dotenv()

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

async def main():
    print("\n" + "="*70)
    print("🔍 DESCOBRIR ID DO GRUPO")
    print("="*70)
    print()

    client = Client(
        name="indexador_local",
        api_id=int(API_ID),
        api_hash=API_HASH,
        workdir="."
    )

    async with client:
        me = await client.get_me()
        print(f"👤 Conectado como: {me.first_name}\n")
        print("📋 SEUS GRUPOS E CANAIS:\n")

        async for dialog in client.get_dialogs():
            chat = dialog.chat

            # Apenas grupos e canais
            if chat.type in ["group", "supergroup", "channel"]:
                tipo_emoji = {
                    "group": "👥",
                    "supergroup": "👥",
                    "channel": "📢"
                }.get(chat.type, "📁")

                print(f"{tipo_emoji} {chat.title}")
                print(f"   🆔 ID: {chat.id}")
                print(f"   📝 Tipo: {chat.type}")

                # Verificar se é admin
                try:
                    member = await client.get_chat_member(chat.id, me.id)
                    if member.status in ["creator", "administrator"]:
                        print(f"   ⭐ Você é: {member.status}")
                except:
                    pass

                print()

        print("="*70)
        print("\n💡 COMO USAR:")
        print("\n1. Encontre o grupo/canal de arquivos na lista acima")
        print("2. Copie o ID (número que começa com -)")
        print("3. Atualize no .env:")
        print("   SOURCE_CHAT_ID=-100xxxxxxxxxxxx")
        print("\n4. Execute novamente: python indexar_historico_local.py\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ Cancelado.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
