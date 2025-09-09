#!/usr/bin/env python3
"""
Script para testar a reorganização de IDs dos payments
"""

import sys
import os

# Add the bot directory to the path
sys.path.append(os.path.dirname(__file__))

def test_id_reorganization():
    """Testar a reorganização de IDs"""
    
    print("[TEST] Testando reorganização de IDs dos payments...")
    print("=" * 80)
    
    try:
        from main import SessionLocal, Payment, _reorganize_payment_ids
        
        with SessionLocal() as s:
            print("[BEFORE] Estado atual dos payments:")
            payments = s.query(Payment).order_by(Payment.id.asc()).all()
            
            if not payments:
                print("  Nenhum payment encontrado")
                return
            
            print(f"  Total: {len(payments)} payments")
            for p in payments:
                print(f"  ID: {p.id} | User: {p.user_id} | Hash: {p.tx_hash[:12]}...")
            
            print()
            print("[REORGANIZING] Executando reorganização...")
            
            # Executar reorganização
            _reorganize_payment_ids(s)
            s.commit()
            
            print("[AFTER] Estado após reorganização:")
            payments_after = s.query(Payment).order_by(Payment.id.asc()).all()
            
            print(f"  Total: {len(payments_after)} payments")
            for p in payments_after:
                print(f"  ID: {p.id} | User: {p.user_id} | Hash: {p.tx_hash[:12]}...")
                
            # Verificar se IDs são sequenciais
            expected_ids = list(range(1, len(payments_after) + 1))
            actual_ids = [p.id for p in payments_after]
            
            if actual_ids == expected_ids:
                print()
                print("✅ [SUCCESS] IDs estão sequenciais começando de 1!")
            else:
                print()
                print("❌ [ERROR] IDs não estão sequenciais!")
                print(f"  Esperado: {expected_ids}")
                print(f"  Atual: {actual_ids}")
                
    except Exception as e:
        print(f"[ERROR] Erro no teste: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    print("[TEST] TESTE DE REORGANIZACAO DE IDS")
    print("=" * 80)
    
    test_id_reorganization()
    
    print("=" * 80)
    print("Teste concluído!")