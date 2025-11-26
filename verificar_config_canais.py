"""
Verificar Configuração para Listar Canais
==========================================
Script rápido para verificar se tudo está pronto para listar canais
"""

import os
import sys
from dotenv import load_dotenv

def verificar_configuracao():
    """Verifica se todas as configurações necessárias estão presentes"""

    print("\n" + "=" * 80)
    print("  VERIFICAÇÃO DE CONFIGURAÇÃO - LISTAR CANAIS")
    print("=" * 80 + "\n")

    problemas = []
    avisos = []

    # 1. Verificar arquivo .env
    print("📄 Verificando arquivo .env...")
    if not os.path.exists(".env"):
        problemas.append("❌ Arquivo .env não encontrado!")
        print("   ❌ Arquivo .env não encontrado!")
    else:
        print("   ✅ Arquivo .env encontrado")
        load_dotenv()

    print()

    # 2. Verificar variáveis de ambiente
    print("🔑 Verificando variáveis de ambiente...")

    api_id = os.getenv("TELEGRAM_API_ID") or os.getenv("API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH") or os.getenv("API_HASH")
    bot_token = os.getenv("BOT_TOKEN")

    if not api_id or api_id == "0":
        problemas.append("❌ TELEGRAM_API_ID ou API_ID não configurado no .env")
        print("   ❌ TELEGRAM_API_ID/API_ID não configurado")
    else:
        print(f"   ✅ API_ID: {api_id}")

    if not api_hash:
        problemas.append("❌ TELEGRAM_API_HASH ou API_HASH não configurado no .env")
        print("   ❌ TELEGRAM_API_HASH/API_HASH não configurado")
    else:
        print(f"   ✅ API_HASH: {api_hash[:10]}...")

    if not bot_token:
        problemas.append("❌ BOT_TOKEN não configurado no .env")
        print("   ❌ BOT_TOKEN não configurado")
    else:
        bot_id = bot_token.split(":")[0]
        print(f"   ✅ BOT_TOKEN configurado (Bot ID: {bot_id})")

    print()

    # 3. Verificar Pyrogram
    print("📦 Verificando dependências...")
    try:
        import pyrogram
        print(f"   ✅ Pyrogram instalado (versão {pyrogram.__version__})")
    except ImportError:
        problemas.append("❌ Pyrogram não está instalado")
        print("   ❌ Pyrogram não está instalado")
        print("   💡 Instale com: py -m pip install pyrogram")

    try:
        import tgcrypto
        print(f"   ✅ TgCrypto instalado (acelera o Pyrogram)")
    except ImportError:
        avisos.append("⚠️ TgCrypto não instalado (opcional, mas recomendado)")
        print("   ⚠️ TgCrypto não instalado (recomendado para melhor performance)")
        print("   💡 Instale com: py -m pip install tgcrypto")

    print()

    # 4. Verificar arquivo de sessão
    print("💾 Verificando sessão existente...")
    if os.path.exists("my_account.session"):
        print("   ✅ Sessão encontrada (não precisará fazer login novamente)")
    else:
        avisos.append("⚠️ Nenhuma sessão encontrada (você precisará fazer login)")
        print("   ⚠️ Nenhuma sessão encontrada")
        print("   💡 Na primeira execução, você precisará:")
        print("      1. Informar seu número de telefone")
        print("      2. Inserir o código enviado pelo Telegram")
        print("      3. Inserir senha 2FA (se tiver)")

    print()

    # 5. Verificar script principal
    print("📜 Verificando script...")
    if os.path.exists("listar_canais_bot.py"):
        print("   ✅ Script listar_canais_bot.py encontrado")
    else:
        problemas.append("❌ Script listar_canais_bot.py não encontrado")
        print("   ❌ Script listar_canais_bot.py não encontrado")

    print()
    print("=" * 80)
    print()

    # Resumo final
    if problemas:
        print("❌ PROBLEMAS ENCONTRADOS:\n")
        for problema in problemas:
            print(f"   {problema}")
        print("\n⚠️ Corrija os problemas acima antes de executar o script.\n")
        return False
    elif avisos:
        print("✅ CONFIGURAÇÃO OK (com avisos):\n")
        for aviso in avisos:
            print(f"   {aviso}")
        print("\n✨ Você pode executar o script, mas leia os avisos acima.\n")
        return True
    else:
        print("✅ TUDO PRONTO!\n")
        print("   Você pode executar o script agora:\n")
        print("   • Windows: Clique duas vezes em listar_canais.bat")
        print("   • Ou execute: py listar_canais_bot.py\n")
        return True


def main():
    try:
        sucesso = verificar_configuracao()

        if sucesso:
            print("=" * 80)
            print("\n🎯 PRÓXIMOS PASSOS:\n")
            print("1. Execute: py listar_canais_bot.py (ou clique em listar_canais.bat)")
            print("2. Escolha opção 1 (listagem simples) ou 2 (detalhada)")
            print("3. Copie o ID do canal/grupo desejado")
            print("4. Configure no .env como GROUP_VIP_ID\n")
            print("=" * 80 + "\n")
        else:
            print("=" * 80)
            print("\n🔧 COMO CORRIGIR:\n")
            print("1. Abra o arquivo .env")
            print("2. Configure as variáveis faltantes:")
            print("   API_ID=seu_api_id")
            print("   API_HASH=seu_api_hash")
            print("   BOT_TOKEN=seu_bot_token")
            print("3. Instale dependências: py -m pip install pyrogram tgcrypto")
            print("4. Execute este script novamente para verificar\n")
            print("=" * 80 + "\n")

    except Exception as e:
        print(f"\n❌ ERRO DURANTE VERIFICAÇÃO: {e}\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
