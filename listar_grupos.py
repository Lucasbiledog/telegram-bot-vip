#!/usr/bin/env python3
"""
Script para listar todos os grupos e canais do Telegram
e seus IDs correspondentes.

Uso:
    python listar_grupos.py

Requer:
    - BOT_TOKEN no arquivo .env ou variável de ambiente
"""

import asyncio
import os
from dotenv import load_dotenv
from telegram import Bot

# Carregar variáveis de ambiente
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    print("❌ Erro: BOT_TOKEN não encontrado!")
    print("Configure BOT_TOKEN no arquivo .env")
    exit(1)


async def listar_todos_grupos_canais():
    """
    Lista todos os grupos e canais que o bot está.
    """
    bot = Bot(token=BOT_TOKEN)

    print("\n" + "="*60)
    print("🔍 BUSCANDO GRUPOS E CANAIS DO BOT")
    print("="*60 + "\n")

    # IDs conhecidos para testar
    ids_conhecidos = [
        -1002791988432,  # GROUP_VIP_ID
        -1002932075976,  # GROUP_FREE_ID
        -1003080645605,  # PACK_ADMIN_CHAT_ID (Grupo Fonte)
        -4806334341,     # STORAGE_GROUP_ID
        -1002509364079,  # STORAGE_GROUP_FREE_ID
        -5028443973,     # LOGS_GROUP_ID
    ]

    print("📋 Verificando IDs conhecidos:\n")

    grupos_encontrados = []

    for chat_id in ids_conhecidos:
        try:
            chat = await bot.get_chat(chat_id)

            # Determinar tipo
            if chat.type == "channel":
                tipo = "📢 Canal"
            elif chat.type == "supergroup":
                tipo = "👥 Supergrupo"
            elif chat.type == "group":
                tipo = "👥 Grupo"
            else:
                tipo = f"❓ {chat.type}"

            grupos_encontrados.append({
                'id': chat_id,
                'titulo': chat.title,
                'tipo': tipo,
                'username': chat.username
            })

            print(f"{tipo}")
            print(f"  📝 Título: {chat.title}")
            print(f"  🆔 ID: {chat_id}")
            if chat.username:
                print(f"  🔗 Username: @{chat.username}")
            if chat.description:
                desc = chat.description[:100] + "..." if len(chat.description) > 100 else chat.description
                print(f"  📄 Descrição: {desc}")
            print()

        except Exception as e:
            print(f"❌ Erro ao acessar chat {chat_id}:")
            print(f"   {str(e)}\n")

    print("\n" + "="*60)
    print("📊 RESUMO")
    print("="*60 + "\n")

    if grupos_encontrados:
        print(f"✅ {len(grupos_encontrados)} grupos/canais encontrados\n")

        print("📋 LISTA DE IDs PARA COPIAR:\n")
        for grupo in grupos_encontrados:
            print(f"{grupo['tipo']} {grupo['titulo']}")
            print(f"ID: {grupo['id']}")
            print()

        print("\n💡 COMO USAR:")
        print("1. Copie o ID do canal/grupo desejado")
        print("2. Cole no arquivo .env ou no código")
        print("3. Exemplo: VIP_CHANNEL_ID=-1002791988432")
    else:
        print("⚠️ Nenhum grupo/canal encontrado!")
        print("\nPossíveis causas:")
        print("- Bot não foi adicionado aos grupos")
        print("- IDs estão incorretos")
        print("- Bot não tem permissões adequadas")

    print("\n" + "="*60 + "\n")


async def buscar_chat_por_username():
    """
    Permite buscar um chat pelo username (@nome).
    """
    bot = Bot(token=BOT_TOKEN)

    print("\n🔍 BUSCAR CANAL/GRUPO POR USERNAME")
    print("="*60)
    username = input("\nDigite o username (com ou sem @): ").strip()

    if not username:
        print("❌ Username vazio!")
        return

    # Remover @ se houver
    if username.startswith("@"):
        username = username[1:]

    print(f"\n🔄 Buscando @{username}...\n")

    try:
        chat = await bot.get_chat(f"@{username}")

        # Determinar tipo
        if chat.type == "channel":
            tipo = "📢 Canal"
        elif chat.type == "supergroup":
            tipo = "👥 Supergrupo"
        elif chat.type == "group":
            tipo = "👥 Grupo"
        else:
            tipo = f"❓ {chat.type}"

        print(f"✅ Encontrado!\n")
        print(f"{tipo}")
        print(f"  📝 Título: {chat.title}")
        print(f"  🆔 ID: {chat.id}")
        print(f"  🔗 Username: @{chat.username}")
        if chat.description:
            print(f"  📄 Descrição: {chat.description[:200]}")

        print(f"\n💡 Copie este ID: {chat.id}")
        print("="*60 + "\n")

    except Exception as e:
        print(f"❌ Erro ao buscar @{username}:")
        print(f"   {str(e)}\n")
        print("Possíveis causas:")
        print("- Canal/grupo não existe")
        print("- Bot não está no canal/grupo")
        print("- Username incorreto")
        print("="*60 + "\n")


async def main():
    """Menu principal"""
    print("\n" + "="*60)
    print("🤖 BOT TELEGRAM - LISTAR GRUPOS E CANAIS")
    print("="*60)

    while True:
        print("\nEscolha uma opção:")
        print("1 - Listar todos os grupos/canais conhecidos")
        print("2 - Buscar grupo/canal por username (@nome)")
        print("3 - Sair")

        opcao = input("\nOpção: ").strip()

        if opcao == "1":
            await listar_todos_grupos_canais()
        elif opcao == "2":
            await buscar_chat_por_username()
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
