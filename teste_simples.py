"""
Teste Simples - Sem Emojis
===========================
"""

import os
import asyncio
import sys
from dotenv import load_dotenv

# Forçar UTF-8
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv()

async def teste_basico():
    from telegram import Bot
    from utils import create_invite_link_flexible

    bot_token = os.getenv("BOT_TOKEN")
    group_vip_id = os.getenv("GROUP_VIP_ID")

    print("\n" + "=" * 70)
    print("TESTE SIMPLES - GERACAO DE CONVITE")
    print("=" * 70 + "\n")

    if not all([bot_token, group_vip_id]):
        print("ERRO: Configuracoes faltando no .env\n")
        return

    print(f"Bot Token: {bot_token.split(':')[0]}...")
    print(f"Canal VIP ID: {group_vip_id}\n")

    bot = Bot(token=bot_token)

    try:
        # Verificar canal
        print("1. Verificando canal...")
        chat = await bot.get_chat(chat_id=group_vip_id)
        print(f"   OK - Canal: {chat.title}")
        print(f"   OK - Tipo: {chat.type}\n")

        # Verificar bot
        print("2. Verificando permissoes do bot...")
        me = await bot.get_me()
        bot_member = await bot.get_chat_member(chat_id=group_vip_id, user_id=me.id)
        print(f"   OK - Status: {bot_member.status}")

        if hasattr(bot_member, 'can_invite_users'):
            print(f"   OK - Can Invite Users: {bot_member.can_invite_users}\n")
        else:
            print(f"   OK - Permissao nao verificavel\n")

        # Gerar convite
        print("3. Gerando convite com multiplas estrategias...")
        link = await create_invite_link_flexible(bot, group_vip_id, retries=3)

        if link:
            print(f"   SUCESSO!")
            print(f"   Link: {link}\n")

            # Revogar
            try:
                await bot.revoke_chat_invite_link(chat_id=group_vip_id, invite_link=link)
                print("   Convite de teste revogado\n")
            except:
                print("   (Convite nao revogado - pode ser permanente)\n")

            print("=" * 70)
            print("TESTE CONCLUIDO COM SUCESSO!")
            print("=" * 70)
            print("\nO sistema esta funcionando corretamente.")
            print("Reinicie o bot e teste um pagamento real.\n")

        else:
            print("   FALHA ao gerar convite!\n")
            print("Verifique:")
            print("1. Bot e administrador no canal")
            print("2. Bot tem permissao 'Invite users via link'")
            print("3. Canal permite criacao de links\n")

    except Exception as e:
        print(f"\nERRO: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(teste_basico())
    except KeyboardInterrupt:
        print("\n\nTeste cancelado.\n")
    except Exception as e:
        print(f"\nERRO: {e}\n")
