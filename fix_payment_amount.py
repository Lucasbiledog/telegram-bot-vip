#!/usr/bin/env python3
"""
Script para corrigir quantidade de BTCB no payment
"""

import sys
import os
import asyncio

# Add the bot directory to the path
sys.path.append(os.path.dirname(__file__))

async def fix_payment_amount():
    """Corrigir quantidade de BTCB no payment específico"""
    
    tx_hash = "0x6bcbcebd5a585ce522c484acb14d5333c24fff12de3d2b7c2e622c4d72f2ae0a"
    
    print(f"[FIX] Corrigindo quantidade BTCB no payment...")
    print("=" * 80)
    
    try:
        from main import SessionLocal, Payment
        from payments import resolve_payment_usd_autochain
        
        # Obter detalhes da blockchain
        ok, msg, usd_value, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True
        )
        
        if not ok or not details:
            print(f"[ERROR] Falha ao obter detalhes: {msg}")
            return
            
        print(f"[BLOCKCHAIN] Detalhes obtidos:")
        print(f"  Token: {details.get('token_symbol', 'N/A')}")
        print(f"  Quantidade: {details.get('amount_human', 'N/A')}")
        print(f"  USD: ${usd_value:.4f}")
        
        with SessionLocal() as s:
            payment = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
            
            if not payment:
                print("[ERROR] Payment não encontrado")
                return
                
            print(f"[CURRENT] Dados atuais:")
            print(f"  ID: {payment.id}")
            print(f"  Amount: {payment.amount}")
            print(f"  Token: {payment.token_symbol}")
            print(f"  USD: {payment.usd_value}")
            
            # Atualizar com dados corretos da blockchain
            if 'amount_human' in details:
                payment.amount = str(details['amount_human'])
            if 'token_symbol' in details:
                payment.token_symbol = details['token_symbol']
                
            s.commit()
            
            print(f"[UPDATED] Dados atualizados:")
            print(f"  Amount: {payment.amount}")
            print(f"  Token: {payment.token_symbol}")
            print("[SUCCESS] Payment corrigido!")
            
    except Exception as e:
        print(f"[ERROR] Erro ao corrigir payment: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("[FIX] CORRECAO DE PAYMENT")
    print("=" * 80)
    
    try:
        asyncio.run(fix_payment_amount())
    except Exception as e:
        print(f"[ERROR] Erro na execução: {e}")
    
    print("=" * 80)
    print("Correção concluída!")