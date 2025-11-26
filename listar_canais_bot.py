"""
Listar Canais/Grupos do Bot
=============================
Conecta na conta do Telegram e lista todos os canais/grupos onde o bot está presente
"""

import sys
import os
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()

async def listar_canais_bot():
    """Lista todos os canais/grupos onde o bot está presente"""
    from pyrogram import Client
    from pyrogram.enums import ChatType, ChatMemberStatus

    # Dados da API do Telegram (mesmos usados no bot)
    api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH") or ""
    bot_token = os.getenv("BOT_TOKEN", "")

    if not all([api_id, api_hash, bot_token]):
        print("❌ Erro: API_ID, API_HASH ou BOT_TOKEN não configurados no .env")
        return

    # Extrair bot username do token
    bot_id = bot_token.split(":")[0]

    print("\n" + "=" * 80)
    print("  CONECTANDO À SUA CONTA DO TELEGRAM")
    print("=" * 80 + "\n")

    # Cliente de usuário (sua conta)
    app = Client(
        "my_account",
        api_id=api_id,
        api_hash=api_hash,
        workdir="."
    )

    try:
        await app.start()
        print("✅ Conectado com sucesso!\n")

        print("=" * 80)
        print("  BUSCANDO CANAIS/GRUPOS ONDE O BOT ESTÁ PRESENTE")
        print("=" * 80 + "\n")

        print("⏳ Carregando diálogos... (pode demorar um pouco)\n")

        canais_bot = []

        # Iterar por todos os diálogos (chats)
        async for dialog in app.get_dialogs():
            chat = dialog.chat

            # Ignorar chats privados
            if chat.type in [ChatType.SUPERGROUP, ChatType.GROUP, ChatType.CHANNEL]:
                try:
                    # Tentar pegar informações do bot neste chat
                    member = await app.get_chat_member(chat.id, bot_id)

                    # Se chegou aqui, o bot está no chat
                    is_admin = member.status in [ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]

                    canais_bot.append({
                        "title": chat.title,
                        "id": chat.id,
                        "type": chat.type.name,
                        "username": f"@{chat.username}" if chat.username else "Sem username",
                        "is_admin": is_admin,
                        "bot_status": member.status.name
                    })

                except Exception:
                    # Bot não está neste chat ou sem permissão para verificar
                    pass

        # Mostrar resultados
        if not canais_bot:
            print("⚠️ Nenhum canal/grupo encontrado onde o bot esteja presente.\n")
            print("Certifique-se de que:")
            print("1. O bot foi adicionado aos canais/grupos")
            print("2. Você está conectado com a conta correta")
        else:
            print(f"📊 Encontrados {len(canais_bot)} canais/grupos com o bot:\n")
            print("=" * 80 + "\n")

            # Separar administradores e membros
            admins = [c for c in canais_bot if c["is_admin"]]
            members = [c for c in canais_bot if not c["is_admin"]]

            if admins:
                print("👑 BOT É ADMINISTRADOR:")
                print("-" * 80 + "\n")
                for i, canal in enumerate(admins, 1):
                    print(f"[{i}] {canal['title']}")
                    print(f"    ID: {canal['id']}")
                    print(f"    Tipo: {canal['type']}")
                    print(f"    Username: {canal['username']}")
                    print(f"    Status do Bot: {canal['bot_status']}")
                    print()

            if members:
                print("\n📝 BOT É MEMBRO (NÃO ADMIN):")
                print("-" * 80 + "\n")
                for i, canal in enumerate(members, 1):
                    print(f"[{i}] {canal['title']}")
                    print(f"    ID: {canal['id']}")
                    print(f"    Tipo: {canal['type']}")
                    print(f"    Username: {canal['username']}")
                    print(f"    Status do Bot: {canal['bot_status']}")
                    print()

            print("=" * 80 + "\n")
            print("💡 DICA: Para usar como GROUP_VIP_ID no .env, copie o ID do canal desejado.")
            print("⚠️ IMPORTANTE: O bot precisa ser ADMINISTRADOR para gerar convites!\n")

        await app.stop()

    except Exception as e:
        print(f"❌ Erro ao conectar/listar: {e}")
        import traceback
        traceback.print_exc()


async def listar_com_detalhes():
    """Lista canais com detalhes de permissões do bot"""
    from pyrogram import Client
    from pyrogram.enums import ChatType

    api_id = int(os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID") or "0")
    api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH") or ""
    bot_token = os.getenv("BOT_TOKEN", "")

    if not all([api_id, api_hash, bot_token]):
        print("❌ Erro: Configurações faltando no .env")
        return

    bot_id = bot_token.split(":")[0]

    print("\n" + "=" * 80)
    print("  LISTAGEM DETALHADA - PERMISSÕES DO BOT")
    print("=" * 80 + "\n")

    app = Client("my_account", api_id=api_id, api_hash=api_hash, workdir=".")

    try:
        await app.start()
        print("✅ Conectado!\n")

        print("⏳ Analisando canais/grupos...\n")

        canais_detalhados = []

        async for dialog in app.get_dialogs():
            chat = dialog.chat

            if chat.type in [ChatType.SUPERGROUP, ChatType.GROUP, ChatType.CHANNEL]:
                try:
                    member = await app.get_chat_member(chat.id, bot_id)

                    # Verificar permissões específicas
                    permissions = {}
                    if hasattr(member, 'privileges') and member.privileges:
                        priv = member.privileges
                        permissions = {
                            "can_invite_users": priv.can_invite_users,
                            "can_delete_messages": priv.can_delete_messages,
                            "can_restrict_members": priv.can_restrict_members,
                            "can_promote_members": priv.can_promote_members,
                            "can_change_info": priv.can_change_info,
                            "can_post_messages": priv.can_post_messages if hasattr(priv, 'can_post_messages') else None,
                        }

                    canais_detalhados.append({
                        "title": chat.title,
                        "id": chat.id,
                        "type": chat.type.name,
                        "username": f"@{chat.username}" if chat.username else "Sem username",
                        "status": member.status.name,
                        "permissions": permissions
                    })

                except Exception:
                    pass

        if canais_detalhados:
            print(f"📊 {len(canais_detalhados)} canais/grupos encontrados:\n")
            print("=" * 80 + "\n")

            for i, canal in enumerate(canais_detalhados, 1):
                status_emoji = "👑" if "ADMIN" in canal["status"] or "OWNER" in canal["status"] else "📝"

                print(f"{status_emoji} [{i}] {canal['title']}")
                print(f"    ID: {canal['id']}")
                print(f"    Tipo: {canal['type']}")
                print(f"    Username: {canal['username']}")
                print(f"    Status do Bot: {canal['status']}")

                if canal["permissions"]:
                    print(f"    Permissões:")
                    for perm, value in canal["permissions"].items():
                        if value is not None:
                            emoji = "✅" if value else "❌"
                            perm_name = perm.replace("can_", "").replace("_", " ").title()
                            print(f"      {emoji} {perm_name}")

                print()

            print("=" * 80 + "\n")
        else:
            print("⚠️ Nenhum canal encontrado.\n")

        await app.stop()

    except Exception as e:
        print(f"❌ Erro: {e}")
        import traceback
        traceback.print_exc()


def main():
    """Menu principal"""
    import asyncio

    print("\n" + "=" * 80)
    print("  LISTAR CANAIS/GRUPOS DO BOT")
    print("=" * 80 + "\n")
    print("Este script irá:")
    print("• Conectar na sua conta do Telegram")
    print("• Listar todos os canais/grupos onde o bot está presente")
    print("• Mostrar se o bot é admin ou membro")
    print("• Exibir o ID de cada canal (para usar no .env)")
    print("\n" + "=" * 80 + "\n")

    print("Escolha uma opção:\n")
    print("1. Listagem simples (rápida)")
    print("2. Listagem detalhada com permissões (mais lenta)")
    print("0. Sair\n")

    choice = input("Opção: ").strip()

    if choice == "1":
        asyncio.run(listar_canais_bot())
    elif choice == "2":
        asyncio.run(listar_com_detalhes())
    elif choice == "0":
        print("\n👋 Até logo!\n")
    else:
        print("\n❌ Opção inválida!\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Programa encerrado.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
