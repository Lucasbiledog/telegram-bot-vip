#!/usr/bin/env python3
"""
Script para verificar cotação de uma hash específica
"""

import sys
import os
import asyncio

# Add the bot directory to the path
sys.path.append(os.path.dirname(__file__))

async def check_hash_manually():
    """Verificar hash específica fornecida pelo usuário"""
    
    tx_hash = "0x6bcbcebd5a585ce522c484acb14d5333c24fff12de3d2b7c2e622c4d72f2ae0a"
    
    print(f"[CHECK] Verificando hash: {tx_hash}")
    print("=" * 80)
    
    try:
        # Import da função de resolução
        from payments import resolve_payment_usd_autochain
        
        print("[INFO] Resolvendo pagamento em todas as chains...")
        
        # Resolver o pagamento
        ok, msg, usd_value, details = await resolve_payment_usd_autochain(tx_hash, force_refresh=True)
        
        print(f"[RESULT] Status: {'OK' if ok else 'FAIL'}")
        print(f"[RESULT] Mensagem: {msg}")
        print(f"[RESULT] Valor USD: ${usd_value:.4f}" if usd_value else "[RESULT] Valor USD: None")
        print()
        
        if details:
            print("[DETAILS] Detalhes da transação:")
            for key, value in details.items():
                print(f"  {key}: {value}")
            print()
        
        if ok and usd_value:
            # Verificar qual VIP seria atribuído
            from utils import choose_plan_from_usd
            
            days = choose_plan_from_usd(usd_value)
            print(f"[VIP] Dias de VIP que seriam atribuídos: {days}")
            
            # Calcular faixas
            if usd_value < 0.1:
                faixa = "Abaixo do mínimo (< $0.10)"
            elif usd_value < 1.0:
                faixa = "30 dias ($0.10 - $0.99)"
            elif usd_value < 5.0:
                faixa = "60 dias ($1.00 - $4.99)"
            elif usd_value < 15.0:
                faixa = "180 dias ($5.00 - $14.99)"
            else:
                faixa = "365 dias ($15.00+)"
                
            print(f"[VIP] Faixa: {faixa}")
        else:
            print("[ERROR] Não foi possível resolver o pagamento")
            
    except Exception as e:
        print(f"[ERROR] Erro ao verificar hash: {e}")
        import traceback
        traceback.print_exc()

def check_hash_on_explorers():
    """Verificar hash nos explorers das blockchains"""
    
    tx_hash = "0x6bcbcebd5a585ce522c484acb14d5333c24fff12de3d2b7c2e622c4d72f2ae0a"
    
    print("\n[EXPLORERS] Links para verificar manualmente:")
    print("=" * 80)
    
    explorers = {
        "Ethereum": f"https://etherscan.io/tx/{tx_hash}",
        "BSC": f"https://bscscan.com/tx/{tx_hash}",
        "Polygon": f"https://polygonscan.com/tx/{tx_hash}",
        "Arbitrum": f"https://arbiscan.io/tx/{tx_hash}",
        "Optimism": f"https://optimistic.etherscan.io/tx/{tx_hash}",
        "Base": f"https://basescan.org/tx/{tx_hash}",
        "Avalanche": f"https://snowtrace.io/tx/{tx_hash}",
        "zkSync": f"https://explorer.zksync.io/tx/{tx_hash}",
        "Linea": f"https://lineascan.build/tx/{tx_hash}"
    }
    
    for chain, url in explorers.items():
        print(f"{chain:12}: {url}")

if __name__ == "__main__":
    print("[MANUAL] VERIFICACAO MANUAL DE HASH")
    print("=" * 80)
    
    # Verificar nos explorers
    check_hash_on_explorers()
    
    # Verificar programaticamente
    try:
        asyncio.run(check_hash_manually())
    except Exception as e:
        print(f"[ERROR] Erro na execução: {e}")
    
    print("\n" + "=" * 80)
    print("Verificacao concluida!")