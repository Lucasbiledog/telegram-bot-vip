#!/usr/bin/env python3
"""
Teste final da transação
"""

import os
import sys
import asyncio

# Configurar ANTES de importar
os.environ['WALLET_ADDRESS'] = '0x40dDBD27F878d07808339F9965f013F1CBc2F812'

# Remover módulo do cache
if 'payments' in sys.modules:
    del sys.modules['payments']

from payments import resolve_payment_usd_autochain, WALLET_ADDRESS

async def final_test():
    tx_hash = "0x3aae2db9c77d5c0bbe6a4ccbb9c2ac0dd92ac5e244c271785be1fa55d8fa8070"
    
    print("=== TESTE FINAL ===")
    print(f"WALLET_ADDRESS: {WALLET_ADDRESS}")
    print(f"Hash: {tx_hash}")
    print()
    
    try:
        ok, msg, usd_paid, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True
        )
        
        print("=== RESULTADO FINAL ===")
        print(f"Sucesso: {ok}")
        print(f"Mensagem: {msg}")
        
        if usd_paid:
            print(f"USD pago: ${usd_paid:.8f}")
            
            from utils import choose_plan_from_usd
            days = choose_plan_from_usd(usd_paid)
            print(f"Dias VIP: {days or 'Insuficiente'}")
            
            if days:
                print("PROBLEMA RESOLVIDO!")
                print(f"0.07 milesimons de BNB = ${usd_paid:.6f} = {days} dias VIP")
        else:
            print("USD pago: $0.00")
        
        print(f"Detalhes: {details}")
        
    except Exception as e:
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(final_test())