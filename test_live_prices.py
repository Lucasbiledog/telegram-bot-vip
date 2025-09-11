#!/usr/bin/env python3
"""
Teste do sistema de precos em tempo real
"""

import asyncio
import logging
from payments import resolve_payment_usd_autochain

# Configurar logging para ver os detalhes
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

async def test_live_pricing():
    print("=== TESTE DE PRECOS EM TEMPO REAL ===")
    print("Testando sistema que SEMPRE busca precos atuais na internet...")
    print()
    
    # Hash de exemplo (substitua por um hash real de teste se necessário)
    # Este é apenas um exemplo - pode não existir
    test_hash = "0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef"
    
    print(f"Testando hash: {test_hash}")
    print("IMPORTANTE: Este hash pode nao existir - o foco e ver se o sistema busca precos atuais")
    print()
    
    try:
        # Esta função vai tentar resolver o pagamento e mostrar logs dos preços
        ok, msg, usd_paid, details = await resolve_payment_usd_autochain(
            test_hash, force_refresh=True
        )
        
        print("=== RESULTADO ===")
        print(f"Sucesso: {ok}")
        print(f"Mensagem: {msg}")
        print(f"USD pago: ${usd_paid or 0:.2f}")
        print(f"Detalhes: {details}")
        
    except Exception as e:
        print(f"Erro esperado (hash pode nao existir): {e}")
        print("\nIMPORTANTE: Verifique os logs acima para ver se o sistema esta:")
        print("✅ [LIVE-PRICE] Buscando precos atuais da internet")
        print("✅ [LIVE-PRICE] Mostrando conversoes em tempo real")

if __name__ == "__main__":
    asyncio.run(test_live_pricing())