#!/usr/bin/env python3
"""
Script para criar sessão do Pyrogram localmente.
Execute UMA VEZ no seu computador para criar o arquivo de sessão.
"""

import asyncio
import os
from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

TELEGRAM_API_ID = os.getenv("TELEGRAM_API_ID")
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH")

if not TELEGRAM_API_ID or not TELEGRAM_API_HASH:
    print("❌ TELEGRAM_API_ID e TELEGRAM_API_HASH devem estar no .env!")
    print("\nObtenha em: https://my.telegram.org/apps")
    exit(1)

async def criar_sessao():
    print("=" * 60)
    print("CRIANDO SESSÃO DO PYROGRAM")
    print("=" * 60)
    print()
    print("📱 Você vai receber um código SMS no Telegram.")
    print("💡 Digite o código quando solicitado.")
    print()

    # Criar cliente com o mesmo nome usado no auto_indexer.py
    client = Client(
        name="bot_indexer_session",
        api_id=int(TELEGRAM_API_ID),
        api_hash=TELEGRAM_API_HASH,
        workdir="."  # Salva na pasta atual
    )

    async with client:
        me = await client.get_me()
        print()
        print("✅ SESSÃO CRIADA COM SUCESSO!")
        print()
        print(f"👤 Conectado como: {me.first_name} (@{me.username or 'sem_username'})")
        print(f"🆔 ID: {me.id}")
        print()
        print("📁 ARQUIVOS CRIADOS:")
        print(f"   • bot_indexer_session.session")
        print()
        print("🚀 PRÓXIMO PASSO:")
        print("   1. Faça upload de 'bot_indexer_session.session' para o Render")
        print("   2. Use o comando: fly deploy ou git push")
        print()
        print("⚠️  IMPORTANTE: Não compartilhe o arquivo .session com ninguém!")
        print()

if __name__ == "__main__":
    asyncio.run(criar_sessao())
