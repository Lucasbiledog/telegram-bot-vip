#!/usr/bin/env python3
"""
Verificar configuração da carteira
"""

import os
from payments import WALLET_ADDRESS

def check_wallet_config():
    print("=== CONFIGURAÇÃO DA CARTEIRA ===")
    
    print(f"WALLET_ADDRESS (env): '{os.getenv('WALLET_ADDRESS', 'NÃO DEFINIDA')}'")
    print(f"WALLET_ADDRESS (parsed): '{WALLET_ADDRESS}'")
    
    # Endereço da transação real
    tx_destination = "0x40dDBD27F878d07808339F9965f013F1CBc2F812"
    print(f"Destino da transação: '{tx_destination}'")
    
    # Comparação
    if WALLET_ADDRESS:
        if WALLET_ADDRESS.lower() == tx_destination.lower():
            print("✅ ENDEREÇOS COINCIDEM!")
        else:
            print("❌ ENDEREÇOS DIFERENTES!")
            print(f"Sistema espera: {WALLET_ADDRESS.lower()}")
            print(f"Transação para: {tx_destination.lower()}")
    else:
        print("❌ WALLET_ADDRESS NÃO CONFIGURADA!")
        print("Configure a variável de ambiente WALLET_ADDRESS")
    
    # Sugestão de correção
    print(f"\n=== CORREÇÃO ===")
    print(f"Execute: set WALLET_ADDRESS={tx_destination}")
    print(f"Ou adicione ao .env: WALLET_ADDRESS={tx_destination}")

if __name__ == "__main__":
    check_wallet_config()