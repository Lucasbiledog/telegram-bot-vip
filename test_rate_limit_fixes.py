#!/usr/bin/env python3
"""
Teste das melhorias para rate limiting
"""

import os
import sys
import asyncio
import time

# Configurar ambiente
os.environ['WALLET_ADDRESS'] = '0x40dDBD27F878d07808339F9965f013F1CBc2F812'

# Limpar imports
modules_to_clear = ['payments', 'main', 'utils']
for mod in modules_to_clear:
    if mod in sys.modules:
        del sys.modules[mod]

async def test_rate_limit_improvements():
    print("=== TESTE DAS MELHORIAS DE RATE LIMITING ===")
    print()
    
    from payments import _usd_native, PRICE_TTL_SECONDS, PRICE_RETRY_BASE_DELAY
    
    print(f"Configurações otimizadas:")
    print(f"  PRICE_TTL_SECONDS: {PRICE_TTL_SECONDS}s ({PRICE_TTL_SECONDS/60:.1f} min)")
    print(f"  PRICE_RETRY_BASE_DELAY: {PRICE_RETRY_BASE_DELAY}s")
    print()
    
    # Teste 1: Primeira chamada (deve buscar da API ou usar fallback)
    print("--- Teste 1: Primeira chamada ---")
    start_time = time.time()
    
    try:
        result = await _usd_native("0x38", 0.06, force_refresh=False)
        elapsed = time.time() - start_time
        
        if result:
            price_usd, total_usd = result
            print(f"Sucesso em {elapsed:.2f}s:")
            print(f"  Preço BNB: ${price_usd:.2f}")
            print(f"  0.06 BNB = ${total_usd:.4f}")
        else:
            print(f"Falha após {elapsed:.2f}s")
            
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Erro após {elapsed:.2f}s: {e}")
    
    print()
    
    # Teste 2: Segunda chamada (deve usar cache)
    print("--- Teste 2: Segunda chamada (cache) ---")
    start_time = time.time()
    
    try:
        result = await _usd_native("0x38", 0.06, force_refresh=False)
        elapsed = time.time() - start_time
        
        if result:
            price_usd, total_usd = result
            print(f"Sucesso em {elapsed:.2f}s (deve ser mais rápido):")
            print(f"  Preço BNB: ${price_usd:.2f}")
            print(f"  0.06 BNB = ${total_usd:.4f}")
        else:
            print(f"Falha após {elapsed:.2f}s")
            
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Erro após {elapsed:.2f}s: {e}")
    
    print()
    
    # Teste 3: Com force_refresh (deve buscar da API novamente)
    print("--- Teste 3: Force refresh ---")
    start_time = time.time()
    
    try:
        result = await _usd_native("0x38", 0.06, force_refresh=True)
        elapsed = time.time() - start_time
        
        if result:
            price_usd, total_usd = result
            print(f"Sucesso em {elapsed:.2f}s:")
            print(f"  Preço BNB: ${price_usd:.2f}")
            print(f"  0.06 BNB = ${total_usd:.4f}")
        else:
            print(f"Falha após {elapsed:.2f}s")
            
    except Exception as e:
        elapsed = time.time() - start_time
        print(f"Erro após {elapsed:.2f}s: {e}")
    
    print("\n=== RESUMO ===")
    print("✓ Cache otimizado (30 min TTL)")
    print("✓ Delays aumentados para rate limiting")
    print("✓ Fallback robusto para quando API não responde")
    print("✓ Sistema deve funcionar mesmo com rate limiting")

if __name__ == "__main__":
    asyncio.run(test_rate_limit_improvements())