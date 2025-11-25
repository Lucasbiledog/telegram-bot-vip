"""
Script para Descobrir ID de Grupos/Canais
==========================================
Use este script para descobrir o ID de qualquer grupo ou canal
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


async def descobrir_id():
    """Descobre IDs de grupos/canais onde o bot está"""

    print("\n" + "=" * 70)
    print("  DESCOBRIR ID DE GRUPOS/CANAIS")
    print("=" * 70 + "\n")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        print("❌ BOT_TOKEN não encontrado no .env")
        return

    try:
        from telegram import Bot

        bot = Bot(token=bot_token)

        print("🤖 Bot conectado com sucesso!\n")
        print("=" * 70)
        print("  COMO USAR")
        print("=" * 70 + "\n")
        print("1️⃣ Adicione o bot ao grupo/canal VIP")
        print("2️⃣ No grupo, envie qualquer mensagem (pode ser apenas 'teste')")
        print("3️⃣ O bot vai receber e MOSTRAR O ID aqui\n")
        print("OU\n")
        print("1️⃣ Encaminhe qualquer mensagem do grupo para o bot no privado")
        print("2️⃣ O ID será exibido\n")
        print("=" * 70 + "\n")

        print("⏳ Aguardando atualizações...\n")
        print("Pressione Ctrl+C para sair\n")

        # Buscar últimas atualizações
        offset = 0
        found_chats = set()

        while True:
            try:
                updates = await bot.get_updates(offset=offset, timeout=30)

                for update in updates:
                    offset = update.update_id + 1

                    # Verificar mensagens em grupos
                    if update.message:
                        chat = update.message.chat
                        chat_id = chat.id

                        if chat_id not in found_chats:
                            found_chats.add(chat_id)

                            chat_type = chat.type
                            chat_title = getattr(chat, 'title', 'N/A')
                            chat_username = getattr(chat, 'username', 'N/A')

                            print("\n" + "🔍 " + "=" * 65)
                            print(f"✅ GRUPO/CANAL ENCONTRADO!")
                            print("=" * 68)
                            print(f"📋 Título: {chat_title}")
                            print(f"🆔 ID: {chat_id}")
                            print(f"📂 Tipo: {chat_type}")
                            if chat_username and chat_username != 'N/A':
                                print(f"👤 Username: @{chat_username}")
                            print("=" * 68)

                            # Sugestão de configuração
                            if chat_type in ['group', 'supergroup', 'channel']:
                                print(f"\n💡 Para usar este como grupo VIP, adicione no .env:")
                                print(f"   VIP_CHANNEL_ID={chat_id}\n")

                await asyncio.sleep(1)

            except asyncio.TimeoutError:
                continue
            except KeyboardInterrupt:
                print("\n\n👋 Encerrando...")
                break

    except Exception as e:
        print(f"❌ ERRO: {e}")
        import traceback
        traceback.print_exc()


async def listar_chats_conhecidos():
    """Lista grupos/canais configurados no .env"""

    print("\n" + "=" * 70)
    print("  GRUPOS/CANAIS CONFIGURADOS NO .ENV")
    print("=" * 70 + "\n")

    configs = {
        "VIP_CHANNEL_ID": "Grupo/Canal VIP",
        "FREE_CHANNEL_ID": "Grupo/Canal FREE",
        "LOGS_GROUP_ID": "Grupo de Logs",
        "SOURCE_CHAT_ID": "Grupo Fonte (arquivos)",
        "GROUP_VIP_ID": "Grupo VIP (alternativo)"
    }

    found_any = False
    for key, desc in configs.items():
        value = os.getenv(key)
        if value:
            found_any = True
            print(f"✅ {desc}")
            print(f"   Variável: {key}")
            print(f"   ID: {value}\n")

    if not found_any:
        print("⚠️ Nenhum grupo/canal configurado no .env\n")

    print("=" * 70 + "\n")


async def main():
    """Função principal"""

    print("\n" + "=" * 70)
    print("  MENU")
    print("=" * 70 + "\n")
    print("1. Ver grupos/canais configurados no .env")
    print("2. Descobrir ID de novo grupo/canal (modo ao vivo)")
    print("0. Sair")
    print()

    choice = input("Escolha uma opção: ").strip()

    if choice == "1":
        await listar_chats_conhecidos()
        input("\nPressione Enter para continuar...")
        await main()

    elif choice == "2":
        print("\n" + "=" * 70)
        print("  MODO AO VIVO")
        print("=" * 70 + "\n")
        print("⚠️ INSTRUÇÕES:")
        print("1. Adicione o bot ao grupo/canal")
        print("2. Torne o bot ADMINISTRADOR (importante!)")
        print("3. Envie uma mensagem no grupo")
        print("4. O ID aparecerá aqui automaticamente\n")

        input("Pressione Enter para iniciar...")
        await descobrir_id()

    elif choice == "0":
        print("\n👋 Até logo!")

    else:
        print("\n❌ Opção inválida!")
        await main()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Programa encerrado.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
