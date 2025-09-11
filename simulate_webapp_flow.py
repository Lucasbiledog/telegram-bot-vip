#!/usr/bin/env python3
"""
Simular exatamente o que acontece na webapp
"""

import os
import sys
import asyncio
import json

# Configurar ambiente
os.environ['WALLET_ADDRESS'] = '0x40dDBD27F878d07808339F9965f013F1CBc2F812'

# Limpar imports
modules_to_clear = ['payments', 'main', 'utils']
for mod in modules_to_clear:
    if mod in sys.modules:
        del sys.modules[mod]

async def simulate_api_validate():
    print("=== SIMULAÇÃO EXATA DO /api/validate ===")
    print()
    
    # Cenário 1: UID temporário (como está acontecendo)
    print("--- Cenário 1: UID temporário ---")
    tx_hash = "0x3aae2db9c77d5c0bbe6a4ccbb9c2ac0dd92ac5e244c271785be1fa55d8fa8070"
    temp_uid = "temp_1234567890"  # Simular UID temporário gerado pela webapp
    
    try:
        from payments import approve_by_usd_and_invite
        
        ok, msg, payload = await approve_by_usd_and_invite(
            temp_uid, None, tx_hash, notify_user=False
        )
        
        print(f"UID enviado: {temp_uid}")
        print(f"Resultado: ok={ok}")
        print(f"Mensagem: {msg}")
        print(f"Payload: {json.dumps(payload, indent=2, default=str)}")
        
        # Simular o que a webapp faria
        if ok:
            print("\n=== RESPOSTA DA API ===")
            api_response = {
                "ok": True,
                "message": msg,
                **payload
            }
            print(json.dumps(api_response, indent=2, default=str))
            
            print("\n=== COMPORTAMENTO DA WEBAPP ===")
            if "invite" in payload:
                print("✓ Redirecionamento automático para:", payload["invite"])
            elif "no_auto_invite" in payload:
                print("⚠ Mensagem: VIP ativado mas sem convite automático")
            elif "temp_uid" in payload:
                print("⚠ Mensagem: Forneça ID do Telegram válido")
                print("❌ Não recebemos o link de convite. Tente novamente.")
    
    except Exception as e:
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*60)
    
    # Cenário 2: UID real
    print("\n--- Cenário 2: UID real ---")
    real_uid = "123456789"  # Simular UID real
    
    try:
        ok, msg, payload = await approve_by_usd_and_invite(
            real_uid, "test_user", tx_hash, notify_user=False
        )
        
        print(f"UID enviado: {real_uid}")
        print(f"Resultado: ok={ok}")
        print(f"Mensagem: {msg}")
        print(f"Payload: {json.dumps(payload, indent=2, default=str)}")
        
        # Simular o que a webapp faria
        if ok:
            print("\n=== RESPOSTA DA API ===")
            api_response = {
                "ok": True,
                "message": msg,
                **payload
            }
            print(json.dumps(api_response, indent=2, default=str))
            
            print("\n=== COMPORTAMENTO DA WEBAPP ===")
            if "invite" in payload:
                print("✓ Redirecionamento automático para:", payload["invite"])
            elif "no_auto_invite" in payload:
                print("⚠ Mensagem: VIP ativado mas sem convite automático")
            elif "temp_uid" in payload:
                print("⚠ Mensagem: Forneça ID do Telegram válido")
    
    except Exception as e:
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(simulate_api_validate())