#!/usr/bin/env python3
"""
Análise da transação real
"""

import asyncio
import logging
from payments import resolve_payment_usd_autochain, CHAINS
from web3 import Web3

# Configurar logging para ver os detalhes
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)

async def analyze_real_transaction():
    tx_hash = "0x3aae2db9c77d5c0bbe6a4ccbb9c2ac0dd92ac5e244c271785be1fa55d8fa8070"
    
    print("=== ANÁLISE DA TRANSAÇÃO REAL ===")
    print(f"Hash: {tx_hash}")
    print()
    
    # Primeiro, vamos tentar encontrar a transação manualmente em diferentes chains
    print("Procurando transacao nas chains...")
    
    for chain_id, meta in CHAINS.items():
        print(f"\n--- Tentando chain {chain_id} ({meta.get('sym', 'Unknown')}) ---")
        
        try:
            w3 = Web3(Web3.HTTPProvider(meta["rpc"]))
            tx = w3.eth.get_transaction(tx_hash)
            
            if tx:
                print(f"TRANSACAO ENCONTRADA em {chain_id}!")
                print(f"From: {tx.get('from')}")
                print(f"To: {tx.get('to')}")
                print(f"Value (Wei): {tx.get('value')}")
                
                if tx.get('value'):
                    value_wei = int(tx['value'])
                    value_native = value_wei / (10**18)
                    print(f"Value (Native): {value_native:.10f} {meta['sym']}")
                    
                    # Calcular USD aproximado
                    if chain_id == "0x38":  # BSC
                        from payments import FALLBACK_PRICES
                        bnb_price = FALLBACK_PRICES.get('binancecoin', 0)
                        if bnb_price:
                            usd_approx = value_native * bnb_price
                            print(f"USD aproximado: ${usd_approx:.8f}")
                
                # Verificar se há receipt (logs de tokens)
                try:
                    receipt = w3.eth.get_transaction_receipt(tx_hash)
                    if receipt and receipt.get('logs'):
                        print(f"Logs encontrados: {len(receipt['logs'])}")
                        print("Esta pode ser uma transação de token ERC-20/BEP-20")
                except:
                    print("Sem receipt disponível")
                
                break
                
        except Exception as e:
            print(f"Erro ao verificar {chain_id}: {str(e)[:50]}...")
    
    print(f"\n{'='*50}")
    print("Testando sistema de resolucao automatica...")
    
    # Agora usar o sistema automático
    try:
        ok, msg, usd_paid, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True
        )
        
        print(f"\n=== RESULTADO DO SISTEMA ===")
        print(f"Sucesso: {ok}")
        print(f"Mensagem: {msg}")
        print(f"USD pago: ${usd_paid or 0:.8f}")
        print(f"Detalhes: {details}")
        
        if details:
            print(f"\nDetalhes expandidos:")
            for key, value in details.items():
                print(f"  {key}: {value}")
        
        # Verificar plano VIP
        if usd_paid:
            from utils import choose_plan_from_usd
            days = choose_plan_from_usd(usd_paid)
            print(f"\nPlano VIP elegivel: {days or 'Insuficiente'} dias")
            
    except Exception as e:
        print(f"ERRO no sistema: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(analyze_real_transaction())