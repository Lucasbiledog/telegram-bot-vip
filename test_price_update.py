#!/usr/bin/env python3
"""
Teste dos preços atualizados de criptomoedas
"""

import asyncio
from payments import FALLBACK_PRICES, _usd_native

async def test_bnb_conversion():
    """Testa conversão de 0.06 BNB para USD"""
    
    print("Testando conversao de precos atualizada...")
    print(f"Preco BNB (fallback): ${FALLBACK_PRICES['binancecoin']}")
    
    # Simular 0.06 BNB
    bnb_amount = 0.06
    usd_value = bnb_amount * FALLBACK_PRICES['binancecoin']
    
    print(f"Quantidade BNB: {bnb_amount}")
    print(f"Valor em USD: ${usd_value:.2f}")
    
    # Testar função de conversão para BSC (chain_id: 0x38)
    try:
        result = await _usd_native("0x38", bnb_amount, force_refresh=False)
        if result:
            price_usd, paid_usd = result
            print(f"Resultado da funcao _usd_native:")
            print(f"   • Preco por BNB: ${price_usd:.2f}")
            print(f"   • Valor total: ${paid_usd:.2f}")
            
            # Verificar qual plano seria elegível
            from utils import choose_plan_from_usd
            days = choose_plan_from_usd(paid_usd)
            if days:
                print(f"Plano elegivel: {days} dias")
            else:
                print(f"Valor insuficiente para qualquer plano")
        else:
            print("Falha na conversao")
            
    except Exception as e:
        print(f"Erro no teste: {e}")
        # Teste manual usando fallback
        print(f"Usando preco fallback: 0.06 BNB x ${FALLBACK_PRICES['binancecoin']} = ${usd_value:.2f}")
        
        from utils import choose_plan_from_usd
        days = choose_plan_from_usd(usd_value)
        if days:
            print(f"Plano elegivel (fallback): {days} dias")
        else:
            print(f"Valor insuficiente para qualquer plano (fallback)")

async def test_other_coins():
    """Testa outros valores de criptomoedas"""
    
    test_cases = [
        ("ETH", "ethereum", 0.001),  # 0.001 ETH
        ("MATIC", "polygon-pos", 10),  # 10 MATIC
        ("AVAX", "avalanche-2", 0.1),  # 0.1 AVAX
        ("BTC", "bitcoin", 0.00002),  # 0.00002 BTC
    ]
    
    print("\nTestando outras conversoes:")
    
    for symbol, cg_id, amount in test_cases:
        if cg_id in FALLBACK_PRICES:
            price = FALLBACK_PRICES[cg_id]
            usd_value = amount * price
            
            from utils import choose_plan_from_usd
            days = choose_plan_from_usd(usd_value)
            
            print(f"• {amount} {symbol} = ${usd_value:.2f} -> {days or 'Insuficiente'} dias")

if __name__ == "__main__":
    asyncio.run(test_bnb_conversion())
    asyncio.run(test_other_coins())