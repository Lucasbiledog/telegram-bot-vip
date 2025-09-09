#!/usr/bin/env python3
"""
Script de teste para validar a conversão dinâmica de preços
Testa diferentes valores USD e verifica se os planos VIP são atribuídos corretamente
"""

import sys
import os
sys.path.append(os.path.dirname(__file__))

from utils import choose_plan_from_usd

def test_price_conversion():
    """Testa a conversão de valores USD para planos VIP"""
    
    print("[TEST] TESTANDO CONVERSAO DINAMICA DE PRECOS")
    print("=" * 50)
    
    # Casos de teste: valor_usd -> dias_esperados
    test_cases = [
        (0.05, None),    # Muito baixo
        (0.09, None),    # Abaixo do mínimo
        (0.10, 30),      # Mínimo para 1 mês
        (0.50, 30),      # Dentro da faixa de 1 mês
        (0.99, 30),      # Limite superior de 1 mês
        (1.00, 60),      # Mínimo para 2 meses
        (2.50, 60),      # Dentro da faixa de 2 meses
        (4.99, 60),      # Limite superior de 2 meses
        (5.00, 180),     # Mínimo para 6 meses
        (10.00, 180),    # Dentro da faixa de 6 meses
        (14.99, 180),    # Limite superior de 6 meses
        (15.00, 365),    # Mínimo para 1 ano
        (25.00, 365),    # Valor alto
        (100.00, 365),   # Valor muito alto
    ]
    
    print("Testando faixas de valor:")
    print("* $0.10 - $0.99  -> 30 dias  (1 mes)")
    print("* $1.00 - $4.99  -> 60 dias  (2 meses)")
    print("* $5.00 - $14.99 -> 180 dias (6 meses)")
    print("* $15.00+        -> 365 dias (1 ano)")
    print()
    
    passed = 0
    failed = 0
    
    for value_usd, expected_days in test_cases:
        result_days = choose_plan_from_usd(value_usd)
        
        if result_days == expected_days:
            status = "[PASS]"
            passed += 1
        else:
            status = "[FAIL]"
            failed += 1
            
        print(f"{status} ${value_usd:6.2f} -> {result_days or 'None':>3} dias (esperado: {expected_days or 'None':>3})")
    
    print()
    print("=" * 50)
    print(f"RESULTADO: {passed} passou, {failed} falharam")
    
    if failed == 0:
        print("[SUCCESS] Todos os testes passaram! Conversao dinamica funcionando corretamente.")
        return True
    else:
        print("[WARNING] Alguns testes falharam. Verifique a logica de conversao.")
        return False

def test_real_scenario():
    """Testa cenários reais de uso"""
    
    print("\n[REAL] TESTANDO CENARIOS REAIS")
    print("=" * 50)
    
    # Simulando valores típicos de criptomoedas
    real_scenarios = [
        ("0.001 ETH @ $2500", 0.001 * 2500, "valor muito baixo"),
        ("0.01 ETH @ $2500", 0.01 * 2500, "25 USD - deve dar 1 ano"), 
        ("1 USDC", 1.0, "1 dólar - deve dar 2 meses"),
        ("5 USDC", 5.0, "5 dólares - deve dar 6 meses"),
        ("0.0001 BTC @ $43000", 0.0001 * 43000, "4.3 USD - deve dar 2 meses"),
        ("10 MATIC @ $0.90", 10 * 0.90, "9 USD - deve dar 6 meses"),
    ]
    
    for description, value_usd, expectation in real_scenarios:
        days = choose_plan_from_usd(value_usd)
        
        if days:
            months = days // 30
            print(f"[DATA] {description:<25} -> ${value_usd:6.2f} -> {days:3d} dias ({months} meses)")
        else:
            print(f"[DATA] {description:<25} -> ${value_usd:6.2f} -> Nao elegivel")
        
        print(f"   Expectativa: {expectation}")
        print()

if __name__ == "__main__":
    success = test_price_conversion()
    test_real_scenario()
    
    if success:
        print("\n[OK] Sistema de conversao dinamica esta funcionando corretamente!")
        print("   Agora o bot atribuira VIP baseado no valor real das transacoes.")
    else:
        print("\n[ERROR] Encontrados problemas no sistema de conversao.")
        sys.exit(1)