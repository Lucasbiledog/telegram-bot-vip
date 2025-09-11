#!/usr/bin/env python3
"""
Forçar reload e testar
"""

import os
import sys

# Configurar ANTES de importar
os.environ['WALLET_ADDRESS'] = '0x40dDBD27F878d07808339F9965f013F1CBc2F812'

# Remover módulo do cache se já foi importado
if 'payments' in sys.modules:
    del sys.modules['payments']

import asyncio
from payments import resolve_payment_usd_autochain, WALLET_ADDRESS

async def test_with_reload():
    tx_hash = "0x3aae2db9c77d5c0bbe6a4ccbb9c2ac0dd92ac5e244c271785be1fa55d8fa8070"
    
    print("=== TESTE COM RELOAD FORÇADO ===")
    print(f"WALLET_ADDRESS no payments: '{WALLET_ADDRESS}'")
    print(f"Esperado: '0x40dDBD27F878d07808339F9965f013F1CBc2F812'")
    print()
    
    if WALLET_ADDRESS.lower() == "0x40dDBD27F878d07808339F9965f013F1CBc2F812".lower():
        print("✓ Endereço configurado corretamente!")
    else:
        print("✗ Endereço ainda incorreto")
        print("Tentando configurar manualmente...")
        
        # Modificar diretamente a variável global
        import payments
        payments.WALLET_ADDRESS = "0x40dDBD27F878d07808339F9965f013F1CBc2F812"
        print(f"payments.WALLET_ADDRESS agora: {payments.WALLET_ADDRESS}")
    
    print(f"\nTestando hash: {tx_hash}")
    
    try:
        ok, msg, usd_paid, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True
        )
        
        print(f"\n=== RESULTADO ===")
        print(f"Sucesso: {ok}")
        print(f"Mensagem: {msg}")
        print(f"USD pago: ${usd_paid or 0:.8f}")
        
        if ok and usd_paid:
            from utils import choose_plan_from_usd
            days = choose_plan_from_usd(usd_paid)
            print(f"Plano VIP: {days or 'Insuficiente'} dias")
            
            if days:
                print(f"SUCESSO! ${usd_paid:.6f} USD = {days} dias VIP")
                
    except Exception as e:
        print(f"Erro: {e}")

if __name__ == "__main__":
    asyncio.run(test_with_reload())