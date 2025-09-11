#!/usr/bin/env python3
"""
Debug do fluxo completo de pagamento para identificar onde aparece $0.06
"""

def simulate_payment_processing():
    print("=== SIMULAÇÃO DO FLUXO DE PAGAMENTO ===")
    print()
    
    # Simular dados de transação BNB
    bnb_price = 891.67
    
    # Cenário 1: 0.00000067 BNB (valor mencionado)
    print("--- Cenário 1: 0.00000067 BNB ---")
    amount_native = 0.00000067
    paid_usd = amount_native * bnb_price
    
    print(f"amount_native: {amount_native}")
    print(f"paid_usd: {paid_usd}")
    print(f"paid_usd formatado: ${paid_usd:.2f}")
    
    # Como seria salvo no banco
    token_amount = amount_native  # amount_human from details
    amount_str = str(token_amount)
    usd_value_str = str(paid_usd)
    
    print(f"Salvo no banco:")
    print(f"  amount: '{amount_str}'")
    print(f"  usd_value: '{usd_value_str}'")
    
    # Verificar se há problema de formatação/arredondamento
    print(f"\nProblemas possíveis:")
    print(f"1. Formatação: ${paid_usd:.2f} = ${paid_usd:.2f}")
    print(f"2. String conversion: {float(usd_value_str)}")
    
    print("\n" + "="*50)
    
    # Cenário 2: Valor correto para $0.06
    print("\n--- Cenário 2: Valor correto para $0.06 ---")
    target_usd = 0.06
    correct_bnb = target_usd / bnb_price
    
    print(f"Para obter ${target_usd:.2f}:")
    print(f"amount_native: {correct_bnb:.8f}")
    print(f"paid_usd: {target_usd}")
    
    # Wei equivalente
    wei_value = int(correct_bnb * 10**18)
    print(f"Em Wei: {wei_value}")
    
    print("\n" + "="*50)
    
    # Cenário 3: Verificar se há confusão de casas decimais
    print("\n--- Cenário 3: Possível confusão de casas decimais ---")
    
    # Talvez o valor real seja 0.00006729 mas está sendo lido como 0.00000067?
    possible_error = 0.00006729  # valor que daria ~$0.06
    error_usd = possible_error * bnb_price
    
    print(f"Se o valor real fosse: {possible_error:.8f} BNB")
    print(f"Daria: ${error_usd:.4f} USD")
    print(f"Diferença: {possible_error / amount_native:.1f}x maior")

if __name__ == "__main__":
    simulate_payment_processing()