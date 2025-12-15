#!/usr/bin/env python3
"""
Cria sessão do Pyrogram localmente.
Execute UMA VEZ no seu computador para criar o arquivo de sessão.
Depois faça commit do arquivo .session gerado.
"""

import asyncio
import os
from dotenv import load_dotenv
from pyrogram import Client

load_dotenv()

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

if not API_ID or not API_HASH:
    print("\n❌ ERRO: Variáveis não encontradas no .env")
    print("\nAdicione no arquivo .env:")
    print("TELEGRAM_API_ID=seu_api_id")
    print("TELEGRAM_API_HASH=seu_api_hash")
    print("\nObtenha em: https://my.telegram.org/apps\n")
    exit(1)

async def main():
    print("\n" + "="*60)
    print("CRIANDO SESSÃO DO PYROGRAM")
    print("="*60)
    print("\n📱 Você receberá um código SMS no Telegram.")
    print("💡 Digite o código quando solicitado.\n")

    client = Client(
        name="bot_indexer_session",
        api_id=int(API_ID),
        api_hash=API_HASH,
        workdir="."
    )

    async with client:
        me = await client.get_me()

        print("\n" + "="*60)
        print("✅ SESSÃO CRIADA COM SUCESSO!")
        print("="*60)
        print(f"\n👤 Conta: {me.first_name} (@{me.username or 'sem_username'})")
        print(f"🆔 ID: {me.id}")
        print(f"\n📁 Arquivo criado: bot_indexer_session.session")
        print("\n🚀 PRÓXIMOS PASSOS:")
        print("\n1. Faça commit do arquivo:")
        print("   git add bot_indexer_session.session")
        print('   git commit -m "Add Pyrogram session"')
        print("   git push origin master")
        print("\n2. Aguarde redeploy no Render (2-3 min)")
        print("\n3. Execute /index_files no bot do Telegram")
        print("\n⚠️  SEGURANÇA: Não compartilhe o arquivo .session!\n")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n❌ Cancelado pelo usuário.\n")
    except Exception as e:
        print(f"\n❌ Erro: {e}\n")
