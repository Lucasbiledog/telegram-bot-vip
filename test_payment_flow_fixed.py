#!/usr/bin/env python3
"""
Teste do fluxo de pagamento corrigido
"""

import os
import sys
import asyncio

# Configurar ambiente
os.environ['WALLET_ADDRESS'] = '0x40dDBD27F878d07808339F9965f013F1CBc2F812'

# Limpar imports
modules_to_clear = ['payments', 'main', 'utils']
for mod in modules_to_clear:
    if mod in sys.modules:
        del sys.modules[mod]

async def test_payment_flow():
    print("=== TESTE DO FLUXO DE PAGAMENTO CORRIGIDO ===")
    print()
    
    # Simular pagamento via API sem bot inicializado
    try:
        # Primeiro, testar aprovação direta
        from payments import approve_by_usd_and_invite
        
        tx_hash = "0x3aae2db9c77d5c0bbe6a4ccbb9c2ac0dd92ac5e244c271785be1fa55d8fa8070"
        uid = "123456789"
        username = "test_user"
        
        print(f"Testando aprovacao com:")
        print(f"  Hash: {tx_hash}")
        print(f"  UID: {uid}")
        print(f"  Username: {username}")
        print()
        
        ok, msg, payload = await approve_by_usd_and_invite(
            uid, username, tx_hash, notify_user=False
        )
        
        print("=== RESULTADO DA APROVACAO ===")
        print(f"Sucesso: {ok}")
        print(f"Mensagem: {msg}")
        print(f"Payload: {payload}")
        print()
        
        if ok:
            print("=== ANALISE DO PAYLOAD ===")
            if 'invite' in payload:
                print(f"✓ Convite automatico gerado: {payload['invite']}")
                print("✓ Usuario sera redirecionado automaticamente")
            elif 'no_auto_invite' in payload:
                print("⚠ VIP ativado mas sem convite automatico")
                print("✓ Usuario vera mensagem para entrar em contato")
            else:
                print("? Caso nao tratado")
            
            if 'until' in payload:
                print(f"✓ VIP valido ate: {payload['until']}")
            if 'days' in payload:
                print(f"✓ Plano: {payload['days']} dias")
            if 'usd' in payload:
                print(f"✓ Valor pago: ${payload['usd']:.6f}")
        
        print("\n" + "="*50)
        print("RESUMO DO TESTE:")
        
        if ok:
            print("✓ Pagamento aprovado com sucesso")
            print("✓ VIP ativado no sistema")
            print("✓ Mensagem informativa para o usuario")
            
            if 'invite' in payload:
                print("✓ Redirecionamento automatico funcionara")
            else:
                print("⚠ Usuario precisara entrar em contato para convite")
                print("  (Normal quando BOT_TOKEN nao esta configurado)")
        else:
            print("✗ Falha na aprovacao:", msg)
    
    except Exception as e:
        print(f"Erro no teste: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_payment_flow())