#!/usr/bin/env python3
"""
Debug conversão Wei -> BNB para diferentes cenários
"""

def debug_wei_scenarios():
    print("=== DEBUG CONVERSÃO WEI -> BNB ===")
    print()
    
    # Cenários possíveis
    scenarios = [
        ("Valor muito pequeno", 670),  # 670 wei
        ("0.00000067 BNB em wei", int(0.00000067 * 10**18)),  # correto
        ("0.06 BNB em wei", int(0.06 * 10**18)),  # correto
        ("670000000000000", 670000000000000),  # possível confusão
    ]
    
    bnb_price = 891.67  # preço atual
    
    for description, wei_value in scenarios:
        bnb_amount = wei_value / (10**18)
        usd_value = bnb_amount * bnb_price
        
        print(f"--- {description} ---")
        print(f"Wei: {wei_value}")
        print(f"BNB: {bnb_amount:.8f}")
        print(f"USD: ${usd_value:.8f}")
        
        if abs(usd_value - 0.06) < 0.001:
            print("🎯 ESTE CENÁRIO DÁ ~$0.06!")
            
        print()
    
    # Teste reverso: que valor em Wei daria $0.06?
    target_usd = 0.06
    target_bnb = target_usd / bnb_price
    target_wei = int(target_bnb * 10**18)
    
    print("=== VALOR CORRETO PARA $0.06 ===")
    print(f"Para obter $0.06 seria necessário:")
    print(f"Wei: {target_wei}")
    print(f"BNB: {target_bnb:.8f}")
    print(f"USD: ${target_usd:.2f}")

if __name__ == "__main__":
    debug_wei_scenarios()