"""
Script de Verificação de Configuração
======================================

Verifica se todas as configurações necessárias estão corretas
antes de usar o sistema de pagamento.
"""

import os
import sys
from dotenv import load_dotenv

# Carregar variáveis de ambiente
load_dotenv()


def print_header(text: str, char: str = "="):
    """Imprime cabeçalho formatado"""
    print("\n" + char * 70)
    print(f"  {text}")
    print(char * 70 + "\n")


def check_env_var(var_name: str, required: bool = True, secret: bool = False) -> tuple:
    """Verifica se variável de ambiente existe"""
    value = os.getenv(var_name)

    if value and value.strip():
        if secret:
            # Mostrar apenas parte do valor para secrets
            display_value = value[:8] + "..." if len(value) > 8 else "***"
        else:
            display_value = value
        return True, display_value
    else:
        return False, None


def check_wallet_format(wallet: str) -> bool:
    """Verifica formato da carteira Ethereum"""
    if not wallet:
        return False
    if not wallet.startswith("0x"):
        return False
    if len(wallet) != 42:  # 0x + 40 caracteres
        return False
    try:
        int(wallet[2:], 16)  # Verificar se é hexadecimal
        return True
    except ValueError:
        return False


def main():
    print_header("VERIFICAÇÃO DE CONFIGURAÇÃO DO SISTEMA")
    print("Este script verifica se todas as configurações necessárias")
    print("estão corretas antes de usar o sistema de pagamento.\n")

    all_ok = True
    warnings = []

    # ========================================
    # 1. VERIFICAR VARIÁVEIS ESSENCIAIS
    # ========================================
    print_header("1. VARIÁVEIS ESSENCIAIS", "-")

    # BOT_TOKEN
    exists, value = check_env_var("BOT_TOKEN", required=True, secret=True)
    if exists:
        print(f"✅ BOT_TOKEN: {value}")
    else:
        print(f"❌ BOT_TOKEN: NÃO CONFIGURADO")
        all_ok = False

    # WALLET_ADDRESS
    exists, wallet = check_env_var("WALLET_ADDRESS", required=True)
    if exists:
        if check_wallet_format(wallet):
            print(f"✅ WALLET_ADDRESS: {wallet}")
        else:
            print(f"❌ WALLET_ADDRESS: FORMATO INVÁLIDO ({wallet})")
            print(f"   Deve começar com 0x e ter 42 caracteres")
            all_ok = False
    else:
        print(f"❌ WALLET_ADDRESS: NÃO CONFIGURADO")
        all_ok = False

    # ========================================
    # 2. VERIFICAR IDs DO TELEGRAM
    # ========================================
    print_header("2. IDs DO TELEGRAM", "-")

    telegram_ids = [
        ("VIP_CHANNEL_ID", True),
        ("FREE_CHANNEL_ID", False),
        ("LOGS_GROUP_ID", False),
        ("SOURCE_CHAT_ID", False),
        ("OWNER_ID", False),
    ]

    for var_name, required in telegram_ids:
        exists, value = check_env_var(var_name, required=required)
        status = "✅" if exists else ("❌" if required else "⚠️")
        display = value if exists else "NÃO CONFIGURADO"

        if required and not exists:
            all_ok = False
        elif not exists:
            warnings.append(f"{var_name} não configurado (opcional)")

        print(f"{status} {var_name}: {display}")

    # ========================================
    # 3. VERIFICAR APIs OPCIONAIS
    # ========================================
    print_header("3. APIs OPCIONAIS", "-")

    apis = [
        ("TELEGRAM_API_ID", "Para scan completo do histórico", False),
        ("TELEGRAM_API_HASH", "Para scan completo do histórico", True),
        ("COINGECKO_API_KEY", "Para evitar rate limiting de preços", True),
    ]

    for var_name, description, secret in apis:
        exists, value = check_env_var(var_name, required=False, secret=secret)
        status = "✅" if exists else "⚠️"
        display = value if exists else "NÃO CONFIGURADO"

        print(f"{status} {var_name}: {display}")
        if not exists:
            warnings.append(f"{var_name} não configurado - {description}")

    # ========================================
    # 4. VERIFICAR CONFIGURAÇÕES DE SISTEMA
    # ========================================
    print_header("4. CONFIGURAÇÕES DE SISTEMA", "-")

    system_configs = [
        ("LOCAL_MODE", "Modo de execução", False),
        ("MAX_WORKERS", "Máximo de workers", False),
        ("MIN_CONFIRMATIONS", "Confirmações mínimas", False),
        ("USE_REDIS", "Usar Redis", False),
    ]

    for var_name, description, secret in system_configs:
        exists, value = check_env_var(var_name, required=False, secret=secret)
        status = "✅" if exists else "⚠️"
        display = value if exists else "PADRÃO"

        print(f"{status} {var_name}: {display} ({description})")

    # ========================================
    # 5. VERIFICAR ARQUIVOS NECESSÁRIOS
    # ========================================
    print_header("5. ARQUIVOS NECESSÁRIOS", "-")

    required_files = [
        "main.py",
        "payments.py",
        "utils.py",
        "config.py",
        "models.py",
        "db.py",
        ".env",
    ]

    for filename in required_files:
        filepath = os.path.join(os.path.dirname(__file__), filename)
        if os.path.exists(filepath):
            print(f"✅ {filename}")
        else:
            print(f"❌ {filename}: NÃO ENCONTRADO")
            all_ok = False

    # ========================================
    # 6. TESTAR IMPORTS
    # ========================================
    print_header("6. TESTAR MÓDULOS PYTHON", "-")

    modules_to_test = [
        ("telegram", "python-telegram-bot"),
        ("web3", "web3"),
        ("httpx", "httpx"),
        ("sqlalchemy", "SQLAlchemy"),
    ]

    for module_name, package_name in modules_to_test:
        try:
            __import__(module_name)
            print(f"✅ {package_name}")
        except ImportError:
            print(f"❌ {package_name}: NÃO INSTALADO")
            print(f"   Instale com: pip install {package_name}")
            all_ok = False

    # ========================================
    # 7. TESTAR CONEXÃO COM BLOCKCHAIN
    # ========================================
    print_header("7. TESTAR CONEXÃO COM BLOCKCHAIN", "-")

    try:
        from web3 import Web3

        # Testar conexão com BSC (mais rápido)
        w3 = Web3(Web3.HTTPProvider("https://bsc-dataseed.binance.org", request_kwargs={'timeout': 10}))
        if w3.is_connected():
            latest_block = w3.eth.block_number
            print(f"✅ Conexão com BSC: OK (bloco {latest_block})")
        else:
            print(f"❌ Conexão com BSC: FALHOU")
            warnings.append("Sem conexão com blockchain - verifique internet")
    except Exception as e:
        print(f"❌ Erro ao testar blockchain: {e}")
        warnings.append("Erro ao conectar com blockchain")

    # ========================================
    # 8. TESTAR APIS DE PREÇO
    # ========================================
    print_header("8. TESTAR APIs DE PREÇO", "-")

    try:
        import httpx
        import asyncio

        async def test_coingecko():
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.get(
                        "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
                    )
                    if response.status_code == 200:
                        data = response.json()
                        btc_price = data.get("bitcoin", {}).get("usd", "N/A")
                        print(f"✅ CoinGecko API: OK (BTC: ${btc_price})")
                        return True
                    elif response.status_code == 429:
                        print(f"⚠️ CoinGecko API: Rate limited (configure COINGECKO_API_KEY)")
                        warnings.append("CoinGecko rate limited - sistema usará preços de fallback")
                        return False
                    else:
                        print(f"❌ CoinGecko API: Erro {response.status_code}")
                        warnings.append("Erro na API do CoinGecko")
                        return False
            except Exception as e:
                print(f"❌ CoinGecko API: {e}")
                warnings.append("Erro ao conectar com CoinGecko")
                return False

        asyncio.run(test_coingecko())
    except Exception as e:
        print(f"❌ Erro ao testar APIs: {e}")

    # ========================================
    # RESUMO FINAL
    # ========================================
    print_header("RESUMO FINAL")

    if all_ok and not warnings:
        print("✅ TODAS AS VERIFICAÇÕES PASSARAM!")
        print("\n🚀 Seu sistema está pronto para uso!")
        print("\nPróximos passos:")
        print("  1. Execute: python test_payment.py")
        print("  2. Faça uma transação de teste")
        print("  3. Verifique se o sistema detecta corretamente")
        return 0

    elif all_ok and warnings:
        print("⚠️ CONFIGURAÇÃO OK COM AVISOS")
        print("\nSeu sistema funcionará, mas algumas funcionalidades podem estar limitadas:")
        for i, warning in enumerate(warnings, 1):
            print(f"  {i}. {warning}")
        print("\nPróximos passos:")
        print("  1. Revise os avisos acima")
        print("  2. Execute: python test_payment.py")
        print("  3. Faça uma transação de teste")
        return 0

    else:
        print("❌ CONFIGURAÇÃO INCOMPLETA")
        print("\nCorreja os erros acima antes de continuar.")
        print("\nPassos para corrigir:")
        print("  1. Edite o arquivo .env")
        print("  2. Configure as variáveis marcadas com ❌")
        print("  3. Execute este script novamente")
        return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n👋 Verificação interrompida.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERRO FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
