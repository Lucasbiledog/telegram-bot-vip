#!/usr/bin/env python3
"""
Testar transação com WALLET_ADDRESS correto
"""

import os
import asyncio
from payments import resolve_payment_usd_autochain

# Definir a variável de ambiente
os.environ['WALLET_ADDRESS'] = '0x40dDBD27F878d07808339F9965f013F1CBc2F812'

async def test_real_transaction():
    tx_hash = "0x3aae2db9c77d5c0bbe6a4ccbb9c2ac0dd92ac5e244c271785be1fa55d8fa8070"
    
    print("=== TESTE COM WALLET_ADDRESS CORRETO ===")
    print(f"WALLET_ADDRESS: {os.environ.get('WALLET_ADDRESS')}")
    print(f"Hash: {tx_hash}")
    print()
    
    try:
        ok, msg, usd_paid, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True
        )
        
        print("=== RESULTADO ===")
        print(f"Sucesso: {ok}")
        print(f"Mensagem: {msg}")
        print(f"USD pago: ${usd_paid or 0:.8f}")
        print(f"Detalhes: {details}")
        
        if ok and usd_paid:
            from utils import choose_plan_from_usd
            days = choose_plan_from_usd(usd_paid)
            print(f"\nPlano VIP elegivel: {days or 'Insuficiente'} dias")
            
            if days:
                print(f"PROBLEMA RESOLVIDO! 0.07 milesimons de BNB = ${usd_paid:.2f} = {days} dias VIP")
            else:
                print(f"Valor ainda insuficiente: ${usd_paid:.8f}")
        
    except Exception as e:
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_real_transaction())