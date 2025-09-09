#!/usr/bin/env python3
"""
Script para atualizar hash específica com preço atual
"""

import sys
import os
import asyncio

# Add the bot directory to the path
sys.path.append(os.path.dirname(__file__))

async def update_specific_hash():
    """Atualizar hash específica com preço atual"""
    
    tx_hash = "0x6bcbcebd5a585ce522c484acb14d5333c24fff12de3d2b7c2e622c4d72f2ae0a"
    
    print(f"[UPDATE] Atualizando hash para preço atual...")
    print("=" * 80)
    print(f"Hash: {tx_hash}")
    print("=" * 80)
    
    try:
        # Importar modelos e funções
        from main import SessionLocal, Payment, VipMembership, now_utc
        from payments import resolve_payment_usd_autochain
        from utils import choose_plan_from_usd, vip_upsert_and_get_until
        
        # Obter valor atual na blockchain
        print("[1] Verificando valor atual na blockchain...")
        ok, msg, current_usd, details = await resolve_payment_usd_autochain(
            tx_hash, force_refresh=True
        )
        
        if not ok or not current_usd:
            print(f"[ERROR] Falha ao obter valor atual: {msg}")
            return
            
        print(f"[SUCCESS] Valor atual: ${current_usd:.4f}")
        
        # Calcular VIP correto
        new_days = choose_plan_from_usd(current_usd)
        print(f"[VIP] VIP correto para ${current_usd:.4f}: {new_days} dias")
        
        if not new_days:
            print("[ERROR] Valor insuficiente para VIP")
            return
        
        with SessionLocal() as s:
            # Buscar payment no banco
            payment = s.query(Payment).filter(Payment.tx_hash == tx_hash).first()
            
            if not payment:
                print("[ERROR] Payment não encontrado no banco")
                return
                
            print(f"[FOUND] Payment ID: {payment.id}")
            print(f"[FOUND] User: {payment.user_id}")
            print(f"[FOUND] Valor antigo: ${float(payment.usd_value):.4f}")
            print(f"[FOUND] VIP antigo: {payment.vip_days} dias")
            
            # Verificar se precisa atualizar
            old_usd = float(payment.usd_value) if payment.usd_value else 0
            old_days = payment.vip_days or 0
            
            if new_days <= old_days:
                print(f"[NO UPDATE] VIP atual ({old_days} dias) >= VIP novo ({new_days} dias)")
                return
                
            # Calcular upgrade
            upgrade_days = new_days - old_days
            print(f"[UPGRADE] Upgrade disponível: +{upgrade_days} dias")
            
            # Atualizar payment no banco
            print("[2] Atualizando dados do payment...")
            payment.usd_value = str(current_usd)
            payment.vip_days = new_days
            
            # Buscar VIP membership
            vip = s.query(VipMembership).filter(VipMembership.user_id == payment.user_id).first()
            
            if vip and vip.active:
                print("[3] Estendendo VIP existente...")
                # Estender VIP existente
                from datetime import timedelta
                if vip.expires_at > now_utc():
                    # VIP ainda ativo - adicionar dias
                    vip.expires_at = vip.expires_at + timedelta(days=upgrade_days)
                else:
                    # VIP expirado - reativar com total de dias
                    vip.expires_at = now_utc() + timedelta(days=new_days)
                    vip.active = True
                    
                new_expiry = vip.expires_at
            else:
                print("[3] Criando novo VIP...")
                # Criar novo VIP
                new_expiry = await vip_upsert_and_get_until(
                    payment.user_id, 
                    payment.username, 
                    new_days
                )
            
            # Salvar mudanças
            s.commit()
            
            print("=" * 80)
            print("[SUCCESS] Hash atualizada com sucesso!")
            print(f"[MONEY] Valor: ${old_usd:.4f} -> ${current_usd:.4f}")
            print(f"[VIP] VIP: {old_days} dias -> {new_days} dias (+{upgrade_days})")
            print(f"[DATE] Expira: {new_expiry.strftime('%d/%m/%Y %H:%M')}")
            print("=" * 80)
            
    except Exception as e:
        print(f"[ERROR] Erro ao atualizar hash: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("[UPDATE] ATUALIZACAO DE HASH ESPECIFICA")
    print("=" * 80)
    
    try:
        asyncio.run(update_specific_hash())
    except Exception as e:
        print(f"[ERROR] Erro na execução: {e}")
    
    print("=" * 80)
    print("Atualização concluída!")