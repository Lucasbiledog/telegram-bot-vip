#!/usr/bin/env python3
"""
Diagnóstico de hash específica para verificar discrepância de preços
"""

import sys
import os
import asyncio

# Add the bot directory to the path
sys.path.append(os.path.dirname(__file__))

def check_database_payment():
    """Verificar o que está salvo no banco de dados"""
    
    tx_hash = "0x6bcbcebd5a585ce522c484acb14d5333c24fff12de3d2b7c2e622c4d72f2ae0a"
    
    print(f"[DB CHECK] Verificando hash no banco de dados...")
    print("=" * 80)
    
    try:
        # Importar modelos
        from main import SessionLocal, Payment, VipMembership
        
        with SessionLocal() as s:
            # Buscar payment por hash
            payment = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
            
            if payment:
                print("[PAYMENT FOUND] Payment encontrado no banco:")
                print(f"  ID: {payment.id}")
                print(f"  User ID: {payment.user_id}")
                print(f"  Username: {payment.username}")
                print(f"  Status: {payment.status}")
                print(f"  Chain: {payment.chain}")
                print(f"  Amount: {payment.amount}")
                print(f"  Token Symbol: {payment.token_symbol}")
                print(f"  USD Value: {payment.usd_value}")
                print(f"  VIP Days: {payment.vip_days}")
                print(f"  Created: {payment.created_at}")
                print()
                
                # Buscar VIP associado
                vip = s.query(VipMembership).filter(VipMembership.tx_hash == tx_hash).first()
                
                if vip:
                    print("[VIP FOUND] VIP Membership encontrado:")
                    print(f"  ID: {vip.id}")
                    print(f"  User ID: {vip.user_id}")
                    print(f"  Username: {vip.username}")
                    print(f"  Plan: {vip.plan}")
                    print(f"  Active: {vip.active}")
                    print(f"  Expires: {vip.expires_at}")
                    print(f"  Created: {vip.created_at}")
                    print()
                else:
                    print("[VIP NOT FOUND] Nenhum VIP associado a essa hash")
                    print()
                    
                # Verificar se há discrepância
                if payment.usd_value:
                    try:
                        saved_usd = float(payment.usd_value)
                        print(f"[ANALYSIS] Valor USD salvo: ${saved_usd:.4f}")
                        print(f"[ANALYSIS] VIP Days salvo: {payment.vip_days}")
                        
                        # Verificar qual seria o VIP correto baseado no valor salvo
                        from utils import choose_plan_from_usd
                        correct_days = choose_plan_from_usd(saved_usd)
                        print(f"[ANALYSIS] VIP Days correto baseado no valor: {correct_days}")
                        
                        if payment.vip_days != correct_days:
                            print("[DISCREPANCY] DISCREPÂNCIA ENCONTRADA!")
                            print(f"  Salvo: {payment.vip_days} dias")
                            print(f"  Correto: {correct_days} dias")
                        else:
                            print("[CONSISTENCY] Dados consistentes")
                            
                    except ValueError:
                        print(f"[ERROR] Erro ao converter valor USD: {payment.usd_value}")
                
            else:
                print("[PAYMENT NOT FOUND] Payment não encontrado no banco")
                
    except Exception as e:
        print(f"[ERROR] Erro ao acessar banco de dados: {e}")
        import traceback
        traceback.print_exc()

async def recheck_blockchain_value():
    """Verificar novamente o valor na blockchain"""
    
    tx_hash = "0x6bcbcebd5a585ce522c484acb14d5333c24fff12de3d2b7c2e622c4d72f2ae0a"
    
    print(f"[BLOCKCHAIN] Re-verificando valor na blockchain...")
    print("=" * 80)
    
    try:
        from payments import resolve_payment_usd_autochain
        
        # Forçar refresh para não usar cache
        ok, msg, usd_value, details = await resolve_payment_usd_autochain(tx_hash, force_refresh=True)
        
        print(f"[RESULT] Status: {'OK' if ok else 'FAIL'}")
        print(f"[RESULT] Mensagem: {msg}")
        print(f"[RESULT] Valor USD atual: ${usd_value:.4f}" if usd_value else "[RESULT] Valor USD: None")
        
        if details:
            print("[DETAILS] Detalhes técnicos:")
            for key, value in details.items():
                print(f"  {key}: {value}")
                
        if ok and usd_value:
            from utils import choose_plan_from_usd
            correct_days = choose_plan_from_usd(usd_value)
            print(f"[VIP] VIP correto baseado no valor atual: {correct_days} dias")
            
    except Exception as e:
        print(f"[ERROR] Erro ao verificar blockchain: {e}")
        import traceback
        traceback.print_exc()

def check_price_cache():
    """Verificar se há problemas no cache de preços"""
    
    print(f"[CACHE] Verificando cache de preços...")
    print("=" * 80)
    
    try:
        from payments import _PRICE_CACHE, FALLBACK_PRICES
        
        print("[CACHE] Cache atual de preços:")
        if _PRICE_CACHE:
            for key, (price, ts) in _PRICE_CACHE.items():
                import time
                age = time.time() - ts
                print(f"  {key}: ${price:.2f} (age: {age:.0f}s)")
        else:
            print("  Cache vazio")
            
        print()
        print("[FALLBACK] Preços de fallback:")
        for key, price in FALLBACK_PRICES.items():
            if "btcb" in key.lower() or "bitcoin" in key.lower():
                print(f"  {key}: ${price:.2f}")
                
    except Exception as e:
        print(f"[ERROR] Erro ao verificar cache: {e}")

if __name__ == "__main__":
    print("[DEBUG] DIAGNOSTICO DE HASH ESPECIFICA")
    print("=" * 80)
    print("Hash: 0x6bcbcebd5a585ce522c484acb14d5333c24fff12de3d2b7c2e622c4d72f2ae0a")
    print("=" * 80)
    
    # 1. Verificar o que está salvo no banco
    check_database_payment()
    
    # 2. Verificar cache de preços
    check_price_cache()
    
    # 3. Re-verificar valor atual na blockchain
    try:
        asyncio.run(recheck_blockchain_value())
    except Exception as e:
        print(f"[ERROR] Erro na verificação blockchain: {e}")
    
    print("=" * 80)
    print("[CONCLUSION] Diagnóstico concluído!")
    print("Se há discrepância, pode ser:")
    print("1. Preço do BTCB mudou entre o processamento e agora")
    print("2. Cache de preços estava diferente no momento do processamento")
    print("3. Erro no momento do processamento original")