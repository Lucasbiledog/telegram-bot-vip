"""
Script de Diagnóstico do Bot
=============================
Verifica se o bot está funcionando e testa conexão
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


async def test_bot_connection():
    """Testa conexão com o bot do Telegram"""
    print_header("TESTE 1: CONEXÃO COM BOT DO TELEGRAM")

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        print("❌ BOT_TOKEN não encontrado no .env")
        return False

    print(f"✅ BOT_TOKEN encontrado: {bot_token[:10]}...")

    try:
        from telegram import Bot
        bot = Bot(token=bot_token)

        print("⏳ Testando conexão com Telegram...")
        me = await bot.get_me()

        print(f"✅ BOT CONECTADO COM SUCESSO!")
        print(f"   Nome: @{me.username}")
        print(f"   ID: {me.id}")
        print(f"   Nome completo: {me.first_name}")
        return True

    except Exception as e:
        print(f"❌ ERRO ao conectar com bot: {e}")
        return False


async def test_pyrogram_session():
    """Testa sessão do Pyrogram (User API)"""
    print_header("TESTE 2: SESSÃO PYROGRAM (USER API)")

    api_id = os.getenv("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH")

    if not api_id or not api_hash:
        print("⚠️ TELEGRAM_API_ID ou TELEGRAM_API_HASH não configurados")
        print("   Isso é normal se você não usa scan de histórico")
        return True

    print(f"✅ API_ID: {api_id}")
    print(f"✅ API_HASH: {api_hash[:10]}...")

    try:
        from pyrogram import Client

        # Verificar se existe sessão
        session_file = "my_account.session"
        if os.path.exists(session_file):
            print(f"✅ Arquivo de sessão encontrado: {session_file}")
        else:
            print(f"⚠️ Arquivo de sessão NÃO encontrado: {session_file}")
            print(f"   Você precisará fazer login novamente")
            return False

        # Tentar conectar
        print("⏳ Testando conexão Pyrogram...")
        app = Client("my_account", api_id=api_id, api_hash=api_hash)

        await app.start()
        me = await app.get_me()

        print(f"✅ PYROGRAM CONECTADO!")
        print(f"   Nome: @{me.username if me.username else 'N/A'}")
        print(f"   ID: {me.id}")
        print(f"   Nome: {me.first_name}")

        await app.stop()
        return True

    except Exception as e:
        print(f"❌ ERRO no Pyrogram: {e}")
        print(f"\n💡 SOLUÇÃO:")
        print(f"   Execute: python auto_sender.py")
        print(f"   Faça login novamente quando solicitado")
        return False


async def test_database():
    """Testa conexão com banco de dados"""
    print_header("TESTE 3: BANCO DE DADOS")

    try:
        from main import SessionLocal, VipMembership

        print("⏳ Testando conexão com banco...")
        with SessionLocal() as s:
            count = s.query(VipMembership).count()
            print(f"✅ BANCO CONECTADO!")
            print(f"   Membros VIP registrados: {count}")
        return True

    except Exception as e:
        print(f"❌ ERRO no banco: {e}")
        return False


async def test_send_message():
    """Testa envio de mensagem"""
    print_header("TESTE 4: ENVIO DE MENSAGEM (TESTE)")

    bot_token = os.getenv("BOT_TOKEN")
    owner_id = os.getenv("OWNER_ID")

    if not owner_id:
        print("⚠️ OWNER_ID não configurado no .env")
        print("   Pulando teste de envio")
        return True

    try:
        from telegram import Bot
        bot = Bot(token=bot_token)

        print(f"⏳ Tentando enviar mensagem de teste para ID {owner_id}...")

        await bot.send_message(
            chat_id=int(owner_id),
            text="🧪 **TESTE DE CONEXÃO**\n\nSeu bot está funcionando perfeitamente!",
            parse_mode="Markdown"
        )

        print(f"✅ MENSAGEM ENVIADA COM SUCESSO!")
        print(f"   Verifique seu Telegram!")
        return True

    except Exception as e:
        print(f"❌ ERRO ao enviar mensagem: {e}")
        return False


async def check_processes():
    """Verifica se há processos do bot rodando"""
    print_header("TESTE 5: PROCESSOS RODANDO")

    try:
        import psutil

        python_processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
            try:
                if 'python' in proc.info['name'].lower():
                    cmdline = proc.info['cmdline']
                    if cmdline and any('main.py' in str(cmd) or 'auto_sender.py' in str(cmd) for cmd in cmdline):
                        python_processes.append({
                            'pid': proc.info['pid'],
                            'cmd': ' '.join(cmdline)
                        })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        if python_processes:
            print(f"✅ Encontrados {len(python_processes)} processo(s) do bot:")
            for proc in python_processes:
                print(f"   PID {proc['pid']}: {proc['cmd'][:80]}...")
        else:
            print(f"⚠️ NENHUM processo do bot encontrado rodando")
            print(f"   O bot precisa ser iniciado!")

        return len(python_processes) > 0

    except ImportError:
        print("⚠️ psutil não instalado - não é possível verificar processos")
        print("   Instale com: pip install psutil")
        return True
    except Exception as e:
        print(f"❌ Erro ao verificar processos: {e}")
        return True


async def main():
    print_header("DIAGNÓSTICO DO BOT")
    print("Verificando conexões e funcionalidades...\n")

    results = []

    # Teste 1: Conexão com bot
    results.append(("Bot Telegram", await test_bot_connection()))

    # Teste 2: Pyrogram
    results.append(("Pyrogram (User API)", await test_pyrogram_session()))

    # Teste 3: Banco de dados
    results.append(("Banco de Dados", await test_database()))

    # Teste 4: Envio de mensagem
    results.append(("Envio de Mensagem", await test_send_message()))

    # Teste 5: Processos
    results.append(("Processos Rodando", await check_processes()))

    # Resumo
    print_header("RESUMO DO DIAGNÓSTICO")

    all_ok = True
    for name, result in results:
        status = "✅" if result else "❌"
        print(f"{status} {name}")
        if not result:
            all_ok = False

    print("\n" + "=" * 70)

    if all_ok:
        print("\n🎉 TUDO FUNCIONANDO PERFEITAMENTE!")
        print("\nSe o bot ainda não está enviando mensagens:")
        print("  1. Reinicie o bot: Ctrl+C e depois 'python main.py'")
        print("  2. Verifique se há erros nos logs")
        print("  3. Teste com um comando simples no Telegram")
    else:
        print("\n⚠️ PROBLEMAS DETECTADOS!")
        print("\nAções recomendadas:")

        if not results[0][1]:  # Bot Telegram
            print("  1. Verifique BOT_TOKEN no arquivo .env")
            print("  2. Crie um novo bot com @BotFather se necessário")

        if not results[1][1]:  # Pyrogram
            print("  1. Execute: python auto_sender.py")
            print("  2. Faça login quando solicitado")
            print("  3. Ou desative o Pyrogram se não for usar")

        if not results[3][1]:  # Envio de mensagem
            print("  1. Configure OWNER_ID no .env")
            print("  2. Verifique se o bot não está bloqueado")
            print("  3. Inicie uma conversa com o bot primeiro")

        if not results[4][1]:  # Processos
            print("  1. Inicie o bot: python main.py")
            print("  2. Ou: python auto_sender.py")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Diagnóstico interrompido.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
