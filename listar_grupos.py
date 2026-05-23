#!/usr/bin/env python3
"""
Script auxiliar para listar todos os grupos/canais acessíveis
e ajudar a descobrir o ID correto.
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

# Fix para encoding UTF-8 no Windows
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Fix para Python 3.14
try:
    asyncio.get_event_loop()
except RuntimeError:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

from pyrogram import Client

load_dotenv()

TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

async def listar_grupos():
    """Lista todos os grupos/canais acessíveis"""

    print("\n" + "="*70)
    print("📋 LISTANDO GRUPOS E CANAIS")
    print("="*70 + "\n")

    app = Client(
        "indexador_session",
        api_id=TELEGRAM_API_ID,
        api_hash=TELEGRAM_API_HASH
    )

    async with app:
        print("✅ Conectado!\n")
        print("🔍 Buscando grupos e canais...\n")

        grupos = []
        canais = []

        async for dialog in app.get_dialogs():
            chat = dialog.chat

            if chat.type.name in ['GROUP', 'SUPERGROUP']:
                grupos.append({
                    'id': chat.id,
                    'titulo': chat.title,
                    'username': chat.username or '(sem username)',
                    'membros': getattr(chat, 'members_count', 'N/A')
                })
            elif chat.type.name == 'CHANNEL':
                canais.append({
                    'id': chat.id,
                    'titulo': chat.title,
                    'username': chat.username or '(sem username)',
                    'membros': getattr(chat, 'members_count', 'N/A')
                })

        # Mostrar grupos
        if grupos:
            print("\n" + "="*70)
            print("👥 GRUPOS")
            print("="*70 + "\n")

            for grupo in grupos:
                print(f"📌 {grupo['titulo']}")
                print(f"   ID: {grupo['id']}")
                print(f"   Username: @{grupo['username']}")
                print(f"   Membros: {grupo['membros']}")
                print()

        # Mostrar canais
        if canais:
            print("\n" + "="*70)
            print("📢 CANAIS")
            print("="*70 + "\n")

            for canal in canais:
                print(f"📌 {canal['titulo']}")
                print(f"   ID: {canal['id']}")
                print(f"   Username: @{canal['username']}")
                print(f"   Membros: {canal['membros']}")
                print()

        print("\n" + "="*70)
        print(f"✅ Total: {len(grupos)} grupos, {len(canais)} canais")
        print("="*70 + "\n")

        print("💡 Agora você pode usar esses IDs no script ler_e_indexar_grupo.py")
        print()

if __name__ == "__main__":
    try:
        asyncio.run(listar_grupos())
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrompido pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
        import traceback
        traceback.print_exc()
