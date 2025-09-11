#!/usr/bin/env python3
"""
Debug da conversao BNB -> USD
"""

import asyncio
from payments import FALLBACK_PRICES, _usd_native, CHAINS

async def debug_bnb_conversion():
    print("=== DEBUG CONVERSAO BNB ===")
    
    # Valores de teste
    bnb_amounts = [0.06, 0.1, 1.0]
    
    print(f"Preco BNB no fallback: ${FALLBACK_PRICES['binancecoin']}")
    print(f"Chain info BSC: {CHAINS['0x38']}")
    
    for bnb_amount in bnb_amounts:
        print(f"\n--- Testando {bnb_amount} BNB ---")
        
        # Calculo manual
        manual_usd = bnb_amount * FALLBACK_PRICES['binancecoin']
        print(f"Calculo manual: {bnb_amount} x ${FALLBACK_PRICES['binancecoin']} = ${manual_usd:.2f}")
        
        # Usando funcao do sistema
        try:
            result = await _usd_native("0x38", bnb_amount, force_refresh=False)
            if result:
                price_per_bnb, total_usd = result
                print(f"Funcao _usd_native:")
                print(f"  - Preco por BNB: ${price_per_bnb:.2f}")
                print(f"  - Total USD: ${total_usd:.2f}")
                print(f"  - Diferenca: manual=${manual_usd:.2f} vs sistema=${total_usd:.2f}")
            else:
                print("Funcao _usd_native retornou None")
        except Exception as e:
            print(f"Erro na funcao _usd_native: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_bnb_conversion())