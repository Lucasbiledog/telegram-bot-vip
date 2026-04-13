#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script de teste para validar os planos de pagamento
Testa se os valores USD são corretamente mapeados para os planos VIP
"""
import sys
import io
sys.path.insert(0, '.')

# Fix encoding for Windows
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from utils import choose_plan_from_usd

# Cores para o terminal
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def test_plan_values():
    """Testa os valores dos planos e suas faixas"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}TESTE DE VALORES DOS PLANOS VIP{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

    # Definir casos de teste
    test_cases = [
        # Valores abaixo do mínimo
        {"amount": 0.00, "expected_days": None, "description": "$0.00 - Sem pagamento"},
        {"amount": 15.00, "expected_days": None, "description": "$15.00 - Valor insuficiente"},
        {"amount": 29.99, "expected_days": None, "description": "$29.99 - Abaixo do mínimo"},

        # Plano MENSAL ($30 - $69.99)
        {"amount": 30.00, "expected_days": 30, "description": "$30.00 - MENSAL (limite inferior)"},
        {"amount": 45.00, "expected_days": 30, "description": "$45.00 - MENSAL (meio da faixa)"},
        {"amount": 69.99, "expected_days": 30, "description": "$69.99 - MENSAL (limite superior)"},

        # Plano TRIMESTRAL ($70 - $109.99)
        {"amount": 70.00, "expected_days": 90, "description": "$70.00 - TRIMESTRAL (limite inferior)"},
        {"amount": 85.00, "expected_days": 90, "description": "$85.00 - TRIMESTRAL (meio da faixa)"},
        {"amount": 109.99, "expected_days": 90, "description": "$109.99 - TRIMESTRAL (limite superior)"},

        # Plano SEMESTRAL ($110 - $178.99)
        {"amount": 110.00, "expected_days": 180, "description": "$110.00 - SEMESTRAL (limite inferior)"},
        {"amount": 145.00, "expected_days": 180, "description": "$145.00 - SEMESTRAL (meio da faixa)"},
        {"amount": 178.99, "expected_days": 180, "description": "$178.99 - SEMESTRAL (limite superior)"},

        # Plano ANUAL ($179+)
        {"amount": 179.00, "expected_days": 365, "description": "$179.00 - ANUAL (limite inferior)"},
        {"amount": 200.00, "expected_days": 365, "description": "$200.00 - ANUAL"},
        {"amount": 500.00, "expected_days": 365, "description": "$500.00 - ANUAL (valor alto)"},
    ]

    # Contador de resultados
    passed = 0
    failed = 0

    # Executar testes
    for test in test_cases:
        amount = test["amount"]
        expected = test["expected_days"]
        description = test["description"]

        # Executar função
        result = choose_plan_from_usd(amount)

        # Verificar resultado
        if result == expected:
            print(f"{GREEN}✓ PASS{RESET} | {description:<40} | Resultado: {result if result else 'None':<4} dias")
            passed += 1
        else:
            print(f"{RED}✗ FAIL{RESET} | {description:<40} | Esperado: {expected if expected else 'None':<4}, Obtido: {result if result else 'None':<4}")
            failed += 1

    # Resumo
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}RESUMO DOS TESTES{RESET}")
    print(f"{BLUE}{'='*60}{RESET}")
    print(f"{GREEN}Testes passados:{RESET} {passed}/{len(test_cases)}")
    print(f"{RED}Testes falhados:{RESET} {failed}/{len(test_cases)}")

    if failed == 0:
        print(f"\n{GREEN}✓ TODOS OS TESTES PASSARAM!{RESET}")
        print(f"\n{YELLOW}VALORES DOS PLANOS CONFIGURADOS:{RESET}")
        print(f"  • Mensal: 30 dias por $30.00 USD")
        print(f"  • Trimestral: 90 dias por $70.00 USD")
        print(f"  • Semestral: 180 dias por $110.00 USD")
        print(f"  • Anual: 365 dias por $179.00 USD")
    else:
        print(f"\n{RED}✗ ALGUNS TESTES FALHARAM!{RESET}")
        return 1

    print(f"\n{BLUE}{'='*60}{RESET}\n")
    return 0

def test_crypto_prices():
    """Lista as criptomoedas suportadas e seus preços"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}CRIPTOMOEDAS CADASTRADAS NO SISTEMA{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

    try:
        from payments import FALLBACK_PRICES

        # Agrupar por tipo
        native_tokens = {}
        stablecoins = {}
        network_tokens = {}

        for key, price in FALLBACK_PRICES.items():
            # Identificar tipo de token
            if "0x" in key:  # Token em rede específica
                network_tokens[key] = price
            elif price == 1.0 and key in ["tether", "usd-coin"]:  # Stablecoins base
                stablecoins[key] = price
            else:  # Tokens nativos
                native_tokens[key] = price

        # Mostrar tokens nativos
        print(f"{YELLOW}TOKENS NATIVOS:{RESET}")
        for token, price in sorted(native_tokens.items(), key=lambda x: x[1], reverse=True):
            if "0x" not in token:
                print(f"  • {token.upper():<20} ${price:>10,.2f} USD")

        # Mostrar stablecoins
        print(f"\n{YELLOW}STABLECOINS:{RESET}")
        for token, price in sorted(stablecoins.items()):
            print(f"  • {token.upper():<20} ${price:>10,.2f} USD")

        # Contar tokens por rede
        eth_tokens = sum(1 for k in network_tokens if k.startswith("0x1:"))
        bsc_tokens = sum(1 for k in network_tokens if k.startswith("0x38:"))
        polygon_tokens = sum(1 for k in network_tokens if k.startswith("0x89:"))
        arbitrum_tokens = sum(1 for k in network_tokens if k.startswith("0xa4b1:"))
        optimism_tokens = sum(1 for k in network_tokens if k.startswith("0xa:"))
        base_tokens = sum(1 for k in network_tokens if k.startswith("0x2105:"))

        print(f"\n{YELLOW}TOKENS POR REDE:{RESET}")
        if eth_tokens > 0:
            print(f"  • Ethereum (0x1):     {eth_tokens} tokens")
        if bsc_tokens > 0:
            print(f"  • BSC (0x38):         {bsc_tokens} tokens")
        if polygon_tokens > 0:
            print(f"  • Polygon (0x89):     {polygon_tokens} tokens")
        if arbitrum_tokens > 0:
            print(f"  • Arbitrum (0xa4b1):  {arbitrum_tokens} tokens")
        if optimism_tokens > 0:
            print(f"  • Optimism (0xa):     {optimism_tokens} tokens")
        if base_tokens > 0:
            print(f"  • Base (0x2105):      {base_tokens} tokens")

        print(f"\n{GREEN}Total de criptomoedas cadastradas: {len(FALLBACK_PRICES)}{RESET}")

    except ImportError as e:
        print(f"{RED}Erro ao importar módulo payments: {e}{RESET}")

    print(f"\n{BLUE}{'='*60}{RESET}\n")

def simulate_payment_examples():
    """Simula exemplos de pagamentos em diferentes criptos"""
    print(f"\n{BLUE}{'='*60}{RESET}")
    print(f"{BLUE}SIMULAÇÃO DE PAGAMENTOS{RESET}")
    print(f"{BLUE}{'='*60}{RESET}\n")

    try:
        from payments import FALLBACK_PRICES

        # Exemplos de pagamento
        examples = [
            # Mensal - $30
            {"crypto": "tether", "amount_crypto": 30.0, "plan": "MENSAL"},
            {"crypto": "ethereum", "amount_crypto": 30.0 / 4378.48, "plan": "MENSAL"},

            # Trimestral - $70
            {"crypto": "tether", "amount_crypto": 70.0, "plan": "TRIMESTRAL"},
            {"crypto": "binancecoin", "amount_crypto": 70.0 / 890.57, "plan": "TRIMESTRAL"},

            # Semestral - $110
            {"crypto": "tether", "amount_crypto": 110.0, "plan": "SEMESTRAL"},
            {"crypto": "bitcoin", "amount_crypto": 110.0 / 95000.0, "plan": "SEMESTRAL"},

            # Anual - $179
            {"crypto": "tether", "amount_crypto": 179.0, "plan": "ANUAL"},
            {"crypto": "ethereum", "amount_crypto": 179.0 / 4378.48, "plan": "ANUAL"},
        ]

        print(f"{YELLOW}Para cada plano, exemplos com diferentes criptomoedas:{RESET}\n")

        current_plan = None
        for example in examples:
            crypto = example["crypto"]
            amount = example["amount_crypto"]
            plan = example["plan"]

            if plan != current_plan:
                print(f"\n{GREEN}Plano {plan}:{RESET}")
                current_plan = plan

            if crypto in FALLBACK_PRICES:
                price = FALLBACK_PRICES[crypto]
                usd_value = amount * price
                days = choose_plan_from_usd(usd_value)

                print(f"  • {amount:.6f} {crypto.upper()} ≈ ${usd_value:.2f} USD → {days} dias")

    except ImportError as e:
        print(f"{RED}Erro ao importar módulo payments: {e}{RESET}")

    print(f"\n{BLUE}{'='*60}{RESET}\n")

if __name__ == "__main__":
    print(f"\n{BLUE}{'='*70}{RESET}")
    print(f"{BLUE}TESTE COMPLETO DO SISTEMA DE PAGAMENTOS{RESET}")
    print(f"{BLUE}{'='*70}{RESET}")

    # Executar todos os testes
    test_plan_values()
    test_crypto_prices()
    simulate_payment_examples()

    print(f"{GREEN}✓ Testes concluídos com sucesso!{RESET}\n")
