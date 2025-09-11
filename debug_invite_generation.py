#!/usr/bin/env python3
"""
Debug da geração de convites VIP
"""

import os
import sys
import asyncio
import logging

# Configurar logging detalhado
logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)s - %(name)s - %(message)s'
)

# Configurar ambiente
os.environ['WALLET_ADDRESS'] = '0x40dDBD27F878d07808339F9965f013F1CBc2F812'

# Remover módulo do cache
if 'payments' in sys.modules:
    del sys.modules['payments']

async def debug_invite_generation():
    print("=== DEBUG GERAÇÃO DE CONVITES VIP ===")
    print()
    
    # Testar se conseguimos gerar um convite diretamente
    try:
        from utils import create_one_time_invite
        from main import application, GROUP_VIP_ID
        
        print(f"GROUP_VIP_ID: {GROUP_VIP_ID}")
        print(f"application.bot disponível: {application.bot is not None}")
        
        print("\nTentando gerar convite de teste...")
        
        invite_link = await create_one_time_invite(
            application.bot, 
            GROUP_VIP_ID, 
            expire_seconds=7200, 
            member_limit=1
        )
        
        if invite_link:
            print(f"✓ Convite gerado com sucesso!")
            print(f"Link: {invite_link}")
        else:
            print("✗ Convite retornou None")
            
    except Exception as e:
        print(f"✗ Erro ao gerar convite: {e}")
        import traceback
        traceback.print_exc()
    
    print("\n" + "="*50)
    
    # Testar o fluxo completo de aprovação
    tx_hash = "0x3aae2db9c77d5c0bbe6a4ccbb9c2ac0dd92ac5e244c271785be1fa55d8fa8070"
    uid = "123456789"  # ID de teste
    
    print(f"\nTestando fluxo completo com UID real: {uid}")
    
    try:
        from payments import approve_by_usd_and_invite
        
        ok, msg, payload = await approve_by_usd_and_invite(
            uid, "test_user", tx_hash, notify_user=False
        )
        
        print(f"Resultado:")
        print(f"  ok: {ok}")
        print(f"  msg: {msg}")
        print(f"  payload: {payload}")
        
        if ok and 'invite' in payload:
            print(f"\n✓ SUCESSO! Convite gerado: {payload['invite']}")
        elif ok:
            print(f"\n✗ PROBLEMA: Aprovado mas sem convite no payload")
        else:
            print(f"\n✗ FALHA: {msg}")
            
    except Exception as e:
        print(f"✗ Erro no fluxo completo: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_invite_generation())