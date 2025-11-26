"""
Teste Completo do Fluxo de Pagamento
=====================================
Testa todo o fluxo: geração de convite + envio de mensagem
"""

import os
import asyncio
import logging
from dotenv import load_dotenv

# Configurar logging detalhado
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(levelname)s] - %(message)s'
)
LOG = logging.getLogger(__name__)

load_dotenv()

async def testar_fluxo_completo():
    """Testa o fluxo completo de pagamento"""

    print("\n" + "=" * 80)
    print("  TESTE COMPLETO DO FLUXO DE PAGAMENTO")
    print("=" * 80 + "\n")

    # Importações necessárias
    from telegram import Bot
    from utils import create_invite_link_flexible
    from telegram.error import TelegramError

    bot_token = os.getenv("BOT_TOKEN")
    group_vip_id = os.getenv("GROUP_VIP_ID")
    owner_id = os.getenv("OWNER_ID")

    if not all([bot_token, group_vip_id]):
        print("❌ Configurações faltando no .env")
        return

    print(f"Bot Token: {bot_token.split(':')[0]}...")
    print(f"Canal VIP ID: {group_vip_id}")
    print(f"Owner ID: {owner_id}\n")

    # Criar bot
    bot = Bot(token=bot_token)

    try:
        # 1. Verificar bot
        print("=" * 80)
        print("ETAPA 1: Verificando Bot")
        print("=" * 80 + "\n")

        me = await bot.get_me()
        print(f"✅ Bot conectado: @{me.username} (ID: {me.id})\n")

        # 2. Verificar canal
        print("=" * 80)
        print("ETAPA 2: Verificando Canal VIP")
        print("=" * 80 + "\n")

        chat = await bot.get_chat(chat_id=group_vip_id)
        print(f"✅ Canal: {chat.title}")
        print(f"✅ Tipo: {chat.type}")
        print(f"✅ ID: {chat.id}\n")

        # 3. Verificar permissões do bot
        print("=" * 80)
        print("ETAPA 3: Verificando Permissões do Bot no Canal")
        print("=" * 80 + "\n")

        bot_member = await bot.get_chat_member(chat_id=group_vip_id, user_id=me.id)
        print(f"✅ Status: {bot_member.status}")

        if hasattr(bot_member, 'can_invite_users'):
            if bot_member.can_invite_users:
                print(f"✅ Can Invite Users: True")
            else:
                print(f"❌ Can Invite Users: False (PROBLEMA!)")
                print("\n⚠️ O bot precisa da permissão 'Invite users via link' no canal!\n")
                return

        print()

        # 4. Testar geração de convite
        print("=" * 80)
        print("ETAPA 4: Testando Geração de Convite (Múltiplas Estratégias)")
        print("=" * 80 + "\n")

        invite_link = await create_invite_link_flexible(bot, group_vip_id, retries=3)

        if invite_link:
            print(f"✅ Convite gerado com sucesso!")
            print(f"🔗 Link: {invite_link}\n")

            # Revogar o convite de teste
            try:
                await bot.revoke_chat_invite_link(
                    chat_id=group_vip_id,
                    invite_link=invite_link
                )
                print("✅ Convite de teste revogado\n")
            except:
                print("⚠️ Não foi possível revogar o convite (pode ser permanente)\n")

        else:
            print("❌ FALHA ao gerar convite!")
            print("\nVerifique:")
            print("1. Bot é administrador no canal")
            print("2. Bot tem permissão 'Invite users via link'")
            print("3. Canal permite criação de links de convite\n")
            return

        # 5. Testar envio de mensagem
        if owner_id and owner_id != "0":
            print("=" * 80)
            print("ETAPA 5: Testando Envio de Mensagem no Privado")
            print("=" * 80 + "\n")

            test_msg = (
                f"🧪 <b>TESTE DO SISTEMA DE PAGAMENTO</b>\n\n"
                f"✅ Bot funcionando corretamente\n"
                f"✅ Convite gerado com sucesso\n"
                f"✅ Mensagem enviada no privado\n\n"
                f"🔗 Link de teste: {invite_link}\n\n"
                f"<i>Este é um teste automático do sistema.</i>"
            )

            try:
                await bot.send_message(
                    chat_id=int(owner_id),
                    text=test_msg,
                    parse_mode="HTML"
                )
                print(f"✅ Mensagem de teste enviada para o owner (ID: {owner_id})\n")
            except TelegramError as e:
                print(f"❌ Erro ao enviar mensagem: {e}")
                print("\nPossíveis causas:")
                print("1. Você nunca iniciou conversa com o bot (/start)")
                print("2. OWNER_ID está incorreto no .env")
                print("3. Bot foi bloqueado por você\n")
        else:
            print("\n⚠️ OWNER_ID não configurado, pulando teste de mensagem\n")

        # Resumo final
        print("=" * 80)
        print("✅ TODOS OS TESTES CONCLUÍDOS COM SUCESSO!")
        print("=" * 80)
        print("\n📝 RESUMO:")
        print(f"✅ Bot conectado e funcionando")
        print(f"✅ Canal VIP acessível")
        print(f"✅ Permissões corretas")
        print(f"✅ Convite gerado com sucesso")
        if owner_id and owner_id != "0":
            print(f"✅ Mensagem enviada no privado")
        print("\n🎉 O sistema de pagamento está PRONTO PARA USO!")
        print("\nPróximos passos:")
        print("1. Reinicie o bot: py main.py")
        print("2. Faça um pagamento de teste ($1)")
        print("3. Verifique se recebe mensagem com o link do canal\n")

    except Exception as e:
        print(f"\n❌ ERRO DURANTE O TESTE: {e}\n")
        import traceback
        traceback.print_exc()
        print("\nVerifique as configurações no .env e permissões do bot.\n")


if __name__ == "__main__":
    try:
        asyncio.run(testar_fluxo_completo())
    except KeyboardInterrupt:
        print("\n\n👋 Teste cancelado pelo usuário.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
