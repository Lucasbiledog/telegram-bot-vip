"""
Script Simples para Testar Hash de Transação
=============================================
USO: python testar_hash.py <hash_da_transacao>
"""

import asyncio
import sys
import os

# Adicionar diretório ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from payments import resolve_payment_usd_autochain, normalize_tx_hash, human_chain
from utils import choose_plan_from_usd


async def testar_hash(tx_hash: str):
    """Testa uma transação de forma simples e direta"""

    print("\n" + "=" * 70)
    print("  TESTE DE TRANSAÇÃO")
    print("=" * 70 + "\n")

    # Normalizar hash
    normalized = normalize_tx_hash(tx_hash)
    if not normalized:
        print("❌ Hash inválido!")
        print(f"   Formato esperado: 0x... (66 caracteres) ou 64 hex")
        return False

    print(f"🔍 Hash: {normalized}")
    print(f"⏳ Procurando transação...\n")

    try:
        # Verificar transação
        ok, message, usd_paid, details = await resolve_payment_usd_autochain(
            normalized, force_refresh=True
        )

        print("=" * 70)
        print("  RESULTADO")
        print("=" * 70 + "\n")

        if ok:
            print("✅ PAGAMENTO APROVADO!\n")
            print(f"💰 Valor pago: ${usd_paid:.2f} USD")

            # Calcular plano
            days = choose_plan_from_usd(usd_paid)
            if days:
                plan_names = {
                    30: "Mensal",
                    90: "Trimestral",
                    180: "Semestral",
                    365: "Anual"
                }
                plan_name = plan_names.get(days, f"{days} dias")
                print(f"🎁 Plano: {plan_name} ({days} dias de VIP)")

            print(f"\n📋 Detalhes:")
            if 'found_on_chain' in details:
                print(f"   Blockchain: {details['found_on_chain']}")
            if 'token_symbol' in details:
                print(f"   Token: {details['token_symbol']}")
            if 'amount_human' in details:
                print(f"   Quantidade: {details['amount_human']:.8f}")
            if 'confirmations' in details:
                print(f"   Confirmações: {details['confirmations']}")

            print(f"\n✅ {message}")
            print("\n🚀 SISTEMA FUNCIONANDO PERFEITAMENTE!")
            return True

        else:
            print("❌ PAGAMENTO NÃO APROVADO\n")
            print(f"Motivo: {message}")

            if details.get('confirmations'):
                print(f"\nConfirmações: {details['confirmations']}")

            if 'searched_chains' in details:
                chains_searched = [human_chain(c) for c in details['searched_chains'][:5]]
                print(f"\nBlockchains verificadas: {', '.join(chains_searched)}...")

            return False

    except Exception as e:
        print("❌ ERRO ao verificar transação!\n")
        print(f"Erro: {e}")
        import traceback
        traceback.print_exc()
        return False


def main():
    """Função principal"""

    if len(sys.argv) < 2:
        print("\n" + "=" * 70)
        print("  COMO USAR")
        print("=" * 70 + "\n")
        print("Uso: python testar_hash.py <hash_da_transacao>\n")
        print("Exemplo:")
        print("  python testar_hash.py 0x1234567890abcdef...\n")
        print("Ou simplesmente:")
        print("  python testar_hash.py 1234567890abcdef...\n")

        # Modo interativo
        print("=" * 70)
        tx_hash = input("\nCole o hash da transação aqui: ").strip()
        if not tx_hash:
            print("\n❌ Nenhum hash fornecido. Encerrando.")
            return 1
    else:
        tx_hash = sys.argv[1]

    # Executar teste
    result = asyncio.run(testar_hash(tx_hash))

    print("\n" + "=" * 70 + "\n")

    return 0 if result else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\n👋 Teste interrompido.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERRO FATAL: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
