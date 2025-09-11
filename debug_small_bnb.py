#!/usr/bin/env python3
"""
Debug do problema: 0.00000067 BNB = $0.06
"""

import asyncio
from payments import _usd_native, FALLBACK_PRICES

async def debug_small_bnb():
    print("=== DEBUG: 0.00000067 BNB -> USD ===")
    
    bnb_amount = 0.00000067
    print(f"Valor sendo testado: {bnb_amount} BNB")
    
    # Preço atual do BNB
    print(f"Preço BNB no fallback: ${FALLBACK_PRICES['binancecoin']}")
    
    # Cálculo manual
    manual_calc = bnb_amount * FALLBACK_PRICES['binancecoin']
    print(f"Cálculo manual: {bnb_amount} × ${FALLBACK_PRICES['binancecoin']} = ${manual_calc:.8f}")
    
    # Usando função do sistema
    try:
        result = await _usd_native("0x38", bnb_amount, force_refresh=True)
        if result:
            price_per_bnb, total_usd = result
            print(f"Resultado da função:")
            print(f"  - Preço por BNB: ${price_per_bnb:.2f}")
            print(f"  - Total USD: ${total_usd:.8f}")
            
            # Análise do problema
            print(f"\n=== ANÁLISE ===")
            print(f"Se está mostrando $0.06, pode ser:")
            print(f"1. Problema de arredondamento/formatação")
            print(f"2. Valor real é diferente (talvez em Wei)")
            print(f"3. Bug na conversão de unidades")
            
            # Teste reverso: que quantidade de BNB daria $0.06?
            target_usd = 0.06
            required_bnb = target_usd / price_per_bnb
            print(f"\nPara obter $0.06 seria necessário: {required_bnb:.8f} BNB")
            
            # Diferença
            difference = required_bnb / bnb_amount
            print(f"Diferença: {difference:.2f}x maior")
            
        else:
            print("Função retornou None")
            
    except Exception as e:
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_small_bnb())