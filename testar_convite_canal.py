"""
Testar Geração de Convite para o Canal VIP
===========================================
Verifica se o bot consegue gerar convites para o canal
"""

import os
import asyncio
import logging
from dotenv import load_dotenv
from telegram import Bot
from telegram.error import TelegramError

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
LOG = logging.getLogger(__name__)

load_dotenv()

async def testar_convite():
    """Testa a geração de convite para o canal VIP"""

    bot_token = os.getenv("BOT_TOKEN")
    group_vip_id = os.getenv("GROUP_VIP_ID")

    if not bot_token:
        print("❌ BOT_TOKEN não encontrado no .env")
        return

    if not group_vip_id:
        print("❌ GROUP_VIP_ID não encontrado no .env")
        return

    print("\n" + "=" * 80)
    print("  TESTE DE GERAÇÃO DE CONVITE")
    print("=" * 80 + "\n")

    print(f"🤖 Bot Token: {bot_token.split(':')[0]}...")
    print(f"📋 Canal VIP ID: {group_vip_id}\n")

    # Criar bot
    bot = Bot(token=bot_token)

    try:
        # 1. Verificar informações do chat
        print("📊 Verificando informações do canal...")
        chat = await bot.get_chat(chat_id=group_vip_id)
        print(f"   ✅ Nome: {chat.title}")
        print(f"   ✅ Tipo: {chat.type}")
        print(f"   ✅ Username: @{chat.username}" if chat.username else "   ⚠️ Sem username")
        print()

        # 2. Verificar permissões do bot
        print("🔑 Verificando permissões do bot...")
        try:
            bot_member = await bot.get_chat_member(chat_id=group_vip_id, user_id=bot.id)
            print(f"   ✅ Status do bot: {bot_member.status}")

            if hasattr(bot_member, 'can_invite_users'):
                if bot_member.can_invite_users:
                    print(f"   ✅ Can Invite Users: True")
                else:
                    print(f"   ❌ Can Invite Users: False")
            else:
                print(f"   ⚠️ Permissão 'can_invite_users' não disponível")
        except Exception as e:
            print(f"   ❌ Erro ao verificar permissões: {e}")

        print()

        # 3. Tentar criar convite
        print("🔗 Tentando criar convite de 1 uso...")
        try:
            from datetime import datetime, timedelta, timezone

            expire_dt = datetime.now(timezone.utc) + timedelta(hours=2)

            invite = await bot.create_chat_invite_link(
                chat_id=group_vip_id,
                creates_join_request=False,
                expire_date=expire_dt,
                member_limit=1
            )

            print(f"   ✅ Convite criado com sucesso!")
            print(f"   🔗 Link: {invite.invite_link}")
            print(f"   📅 Expira em: 2 horas")
            print(f"   👤 Limite de usos: 1")
            print()

            # Revogar o convite de teste
            print("🗑️ Revogando convite de teste...")
            await bot.revoke_chat_invite_link(
                chat_id=group_vip_id,
                invite_link=invite.invite_link
            )
            print("   ✅ Convite revogado")
            print()

            print("=" * 80)
            print("✅ TESTE CONCLUÍDO COM SUCESSO!")
            print("=" * 80)
            print("\nO bot PODE gerar convites para este canal.")
            print("O sistema de pagamento deve funcionar corretamente.\n")

        except TelegramError as e:
            print(f"   ❌ Erro ao criar convite: {e}")
            print()
            print("=" * 80)
            print("❌ TESTE FALHOU")
            print("=" * 80)
            print("\nPossíveis problemas:")
            print("1. Bot não é administrador no canal")
            print("2. Bot não tem permissão 'Invite Users'")
            print("3. Canal é privado e não permite convites por link")
            print("4. Configuração do canal impede criação de links\n")

            print("SOLUÇÃO:")
            print("1. Vá até o canal no Telegram")
            print("2. Adicione o bot como administrador")
            print("3. Dê permissão 'Invite users via link'")
            print("4. Se for um CANAL (não grupo), considere converter para SUPERGROUP\n")

    except Exception as e:
        print(f"❌ Erro ao verificar canal: {e}")
        print("\nVerifique se:")
        print("1. O GROUP_VIP_ID está correto no .env")
        print("2. O bot foi adicionado ao canal")
        print("3. O bot tem permissões de administrador\n")

async def main():
    try:
        await testar_convite()
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
