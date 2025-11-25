"""
Lista Grupos Onde o Bot é Administrador
========================================
Mostra todos os grupos/canais onde o bot está e suas permissões
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


async def listar_grupos_admin():
    """Lista todos os grupos onde o bot é administrador"""

    print("\n" + "=" * 80)
    print("  GRUPOS/CANAIS ONDE O BOT É ADMINISTRADOR")
    print("=" * 80 + "\n")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        print("❌ BOT_TOKEN não encontrado no .env")
        return

    try:
        from telegram import Bot
        from telegram.error import TelegramError

        bot = Bot(token=bot_token)

        # Obter informações do bot
        me = await bot.get_me()
        print(f"🤖 Bot: @{me.username} (ID: {me.id})")
        print(f"📝 Nome: {me.first_name}\n")
        print("=" * 80)
        print("  BUSCANDO GRUPOS/CANAIS...")
        print("=" * 80 + "\n")

        # Pegar últimas atualizações para encontrar chats
        print("⏳ Analisando histórico de mensagens...\n")

        updates = await bot.get_updates(limit=100)

        chat_ids = set()
        for update in updates:
            if update.message and update.message.chat:
                chat = update.message.chat
                if chat.type in ['group', 'supergroup', 'channel']:
                    chat_ids.add(chat.id)

        if not chat_ids:
            print("⚠️ Nenhum grupo encontrado no histórico recente.\n")
            print("💡 DICAS:")
            print("   1. Envie uma mensagem em cada grupo onde o bot está")
            print("   2. Execute este script novamente")
            print("   3. Ou use o método alternativo abaixo\n")
            return

        print(f"📊 Encontrados {len(chat_ids)} grupos/canais no histórico\n")
        print("=" * 80 + "\n")

        grupos_admin = []
        grupos_nao_admin = []

        for chat_id in chat_ids:
            try:
                # Obter informações do chat
                chat = await bot.get_chat(chat_id)

                # Verificar se o bot é administrador
                try:
                    member = await bot.get_chat_member(chat_id, me.id)
                    is_admin = member.status in ['administrator', 'creator']
                except Exception:
                    is_admin = False

                chat_info = {
                    'id': chat_id,
                    'title': chat.title or 'Sem título',
                    'type': chat.type,
                    'username': getattr(chat, 'username', None),
                    'is_admin': is_admin,
                    'member_status': member.status if is_admin else 'member'
                }

                if is_admin:
                    # Verificar permissões específicas
                    permissions = {
                        'can_invite_users': getattr(member, 'can_invite_users', False),
                        'can_manage_chat': getattr(member, 'can_manage_chat', False),
                        'can_delete_messages': getattr(member, 'can_delete_messages', False),
                        'can_restrict_members': getattr(member, 'can_restrict_members', False),
                    }
                    chat_info['permissions'] = permissions
                    grupos_admin.append(chat_info)
                else:
                    grupos_nao_admin.append(chat_info)

            except TelegramError as e:
                print(f"⚠️ Erro ao acessar chat {chat_id}: {e}")
                continue

        # Exibir grupos onde é admin
        if grupos_admin:
            print("✅ GRUPOS/CANAIS ONDE O BOT É ADMINISTRADOR:")
            print("=" * 80 + "\n")

            for i, grupo in enumerate(grupos_admin, 1):
                print(f"📌 [{i}] {grupo['title']}")
                print(f"    🆔 ID: {grupo['id']}")
                print(f"    📂 Tipo: {grupo['type']}")
                if grupo['username']:
                    print(f"    👤 Username: @{grupo['username']}")
                print(f"    👑 Status: {grupo['member_status']}")

                # Exibir permissões
                perms = grupo.get('permissions', {})
                print(f"    🔐 Permissões:")
                print(f"       • Convidar usuários: {'✅' if perms.get('can_invite_users') else '❌'}")
                print(f"       • Gerenciar chat: {'✅' if perms.get('can_manage_chat') else '❌'}")
                print(f"       • Deletar mensagens: {'✅' if perms.get('can_delete_messages') else '❌'}")
                print(f"       • Restringir membros: {'✅' if perms.get('can_restrict_members') else '❌'}")

                print(f"\n    💡 Para usar como grupo VIP, adicione no .env:")
                print(f"       VIP_CHANNEL_ID={grupo['id']}\n")
                print("-" * 80 + "\n")
        else:
            print("❌ Bot NÃO é administrador em nenhum grupo encontrado!\n")

        # Exibir grupos onde NÃO é admin
        if grupos_nao_admin:
            print("\n⚠️ GRUPOS ONDE O BOT NÃO É ADMINISTRADOR:")
            print("=" * 80 + "\n")
            for grupo in grupos_nao_admin:
                print(f"📌 {grupo['title']}")
                print(f"    🆔 ID: {grupo['id']}")
                print(f"    ⚠️ Bot não tem permissões de admin aqui\n")

        # Resumo final
        print("=" * 80)
        print("  RESUMO")
        print("=" * 80)
        print(f"✅ Admin em: {len(grupos_admin)} grupos/canais")
        print(f"⚠️ Membro em: {len(grupos_nao_admin)} grupos/canais")
        print(f"📊 Total: {len(chat_ids)} grupos/canais")
        print("=" * 80 + "\n")

        # Verificar configuração atual
        print("=" * 80)
        print("  CONFIGURAÇÃO ATUAL NO .ENV")
        print("=" * 80 + "\n")

        vip_id = os.getenv("VIP_CHANNEL_ID")
        free_id = os.getenv("FREE_CHANNEL_ID")

        if vip_id:
            vip_id_int = int(vip_id)
            esta_na_lista = any(g['id'] == vip_id_int for g in grupos_admin)
            status = "✅ ENCONTRADO" if esta_na_lista else "❌ NÃO ENCONTRADO"
            print(f"VIP_CHANNEL_ID={vip_id} - {status}")
            if not esta_na_lista:
                print("   ⚠️ Este ID não está na lista de grupos onde o bot é admin!")
        else:
            print("VIP_CHANNEL_ID: ❌ NÃO CONFIGURADO")

        if free_id:
            free_id_int = int(free_id)
            esta_na_lista = any(g['id'] == free_id_int for g in grupos_admin)
            status = "✅ ENCONTRADO" if esta_na_lista else "❌ NÃO ENCONTRADO"
            print(f"FREE_CHANNEL_ID={free_id} - {status}")
            if not esta_na_lista:
                print("   ⚠️ Este ID não está na lista de grupos onde o bot é admin!")
        else:
            print("FREE_CHANNEL_ID: ❌ NÃO CONFIGURADO")

        print("\n" + "=" * 80 + "\n")

    except Exception as e:
        print(f"❌ ERRO: {e}")
        import traceback
        traceback.print_exc()


async def main():
    """Função principal"""
    await listar_grupos_admin()

    print("\n💡 DICAS:")
    print("   • Se algum grupo não apareceu, envie uma mensagem lá")
    print("   • Execute este script novamente após enviar mensagens")
    print("   • Certifique-se que o bot é ADMINISTRADOR nos grupos\n")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Programa encerrado.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
