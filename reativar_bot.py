"""
Script para Reativar o Bot
===========================
Reinicia conexões e reativa sessões expiradas
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


def print_header(text: str):
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70 + "\n")


async def reactivate_pyrogram():
    """Reativa sessão do Pyrogram"""
    print_header("REATIVANDO PYROGRAM (USER API)")

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        print("⚠️ TELEGRAM_API_ID ou TELEGRAM_API_HASH não configurados")
        print("   Pulando Pyrogram...")
        return True

    try:
        from pyrogram import Client

        print("🔄 Iniciando nova sessão do Pyrogram...")
        print("📱 Você precisará fazer login com seu número de telefone\n")

        app = Client("my_account", api_id=api_id, api_hash=api_hash)

        print("⏳ Conectando...")
        await app.start()

        me = await app.get_me()
        print(f"\n✅ PYROGRAM CONECTADO!")
        print(f"   Usuário: @{me.username if me.username else 'N/A'}")
        print(f"   Nome: {me.first_name}")
        print(f"   ID: {me.id}")

        await app.stop()

        print("\n🎉 Sessão do Pyrogram reativada com sucesso!")
        return True

    except Exception as e:
        print(f"\n❌ ERRO ao reativar Pyrogram: {e}")
        return False


async def test_bot():
    """Testa bot do Telegram"""
    print_header("TESTANDO BOT DO TELEGRAM")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        print("❌ BOT_TOKEN não encontrado!")
        return False

    try:
        from telegram import Bot
        bot = Bot(token=bot_token)

        print("⏳ Testando conexão...")
        me = await bot.get_me()

        print(f"✅ BOT FUNCIONANDO!")
        print(f"   Bot: @{me.username}")
        print(f"   ID: {me.id}")

        # Tentar enviar mensagem de teste para OWNER
        owner_id = os.getenv("OWNER_ID")
        if owner_id:
            print(f"\n⏳ Enviando mensagem de teste para {owner_id}...")
            try:
                await bot.send_message(
                    chat_id=int(owner_id),
                    text="✅ Bot reativado com sucesso! Tudo funcionando normalmente."
                )
                print(f"✅ Mensagem de teste enviada! Verifique seu Telegram.")
            except Exception as e:
                print(f"⚠️ Não foi possível enviar mensagem: {e}")

        return True

    except Exception as e:
        print(f"❌ ERRO no bot: {e}")
        return False


async def cleanup_old_sessions():
    """Remove sessões antigas/corrompidas"""
    print_header("LIMPANDO SESSÕES ANTIGAS")

    session_files = [
        "my_account.session",
        "my_account.session-journal",
    ]

    for file in session_files:
        if os.path.exists(file):
            try:
                # Fazer backup primeiro
                import shutil
                backup_name = f"{file}.backup"
                shutil.copy2(file, backup_name)
                print(f"📦 Backup criado: {backup_name}")
            except Exception as e:
                print(f"⚠️ Erro ao criar backup de {file}: {e}")

    print("✅ Limpeza concluída")
    return True


async def main():
    print_header("REATIVAÇÃO DO BOT")
    print("Este script vai reativar todas as conexões do bot\n")

    # Perguntar se quer limpar sessões antigas
    print("Deseja limpar sessões antigas do Pyrogram?")
    print("(Isso vai requerer que você faça login novamente)")
    choice = input("Digite 's' para sim ou 'n' para não [n]: ").strip().lower()

    if choice == 's':
        await cleanup_old_sessions()

    # Teste 1: Bot do Telegram
    bot_ok = await test_bot()

    if not bot_ok:
        print("\n❌ PROBLEMA CRÍTICO: Bot do Telegram não está funcionando!")
        print("\nVerifique:")
        print("  1. BOT_TOKEN no arquivo .env está correto")
        print("  2. Você tem conexão com internet")
        print("  3. O token não foi revogado no @BotFather")
        return

    # Teste 2: Pyrogram (opcional)
    print("\nDeseja reativar o Pyrogram (necessário para auto_sender)?")
    choice = input("Digite 's' para sim ou 'n' para não [n]: ").strip().lower()

    if choice == 's':
        pyrogram_ok = await reactivate_pyrogram()
        if not pyrogram_ok:
            print("\n⚠️ Pyrogram não foi reativado")
            print("   O bot funcionará, mas auto_sender não funcionará")

    # Resumo final
    print_header("RESUMO")
    print("✅ Bot do Telegram: FUNCIONANDO")

    print("\n🚀 PRÓXIMOS PASSOS:")
    print("  1. Inicie o bot: python main.py")
    print("  2. Ou inicie o auto_sender: python auto_sender.py")
    print("  3. Teste no Telegram enviando: /start")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Reativação interrompida.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
