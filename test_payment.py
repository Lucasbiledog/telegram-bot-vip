"""
Script de Teste para Sistema de Pagamento
==========================================

Este script testa o sistema de pagamento em criptomoedas do bot.
Ele simula verificações de transações e testa os componentes principais.
"""

import asyncio
import sys
import os

# Adicionar diretório do projeto ao path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from payments import (
    resolve_payment_usd_autochain,
    approve_by_usd_and_invite,
    normalize_tx_hash,
    get_wallet_address,
    get_supported_chains,
    human_chain
)
from utils import choose_plan_from_usd


def print_header(text: str):
    """Imprime cabeçalho formatado"""
    print("\n" + "=" * 70)
    print(f"  {text}")
    print("=" * 70 + "\n")


def print_config():
    """Mostra configuração atual do sistema"""
    print_header("CONFIGURAÇÃO DO SISTEMA DE PAGAMENTO")

    wallet = get_wallet_address()
    print(f"💳 Carteira configurada: {wallet}")

    print(f"\n💰 PLANOS VIP DISPONÍVEIS:")
    print(f"  • 30 dias (Mensal):     $30.00 - $69.99")
    print(f"  • 90 dias (Trimestral): $70.00 - $109.99")
    print(f"  • 180 dias (Semestral): $110.00 - $178.99")
    print(f"  • 365 dias (Anual):     $179.00+")

    chains = get_supported_chains()
    print(f"\n🔗 BLOCKCHAINS SUPORTADAS ({len(chains)}):")
    for chain_id in sorted(chains.keys())[:10]:  # Mostrar primeiras 10
        print(f"  • {human_chain(chain_id)} ({chain_id})")
    if len(chains) > 10:
        print(f"  ... e mais {len(chains) - 10} chains")


def test_plan_calculation():
    """Testa o cálculo de planos baseado no valor em USD"""
    print_header("TESTE: CÁLCULO DE PLANOS VIP")

    test_values = [
        ("Insuficiente", 25.00),
        ("Mensal", 30.00),
        ("Mensal", 50.00),
        ("Trimestral", 70.00),
        ("Trimestral", 100.00),
        ("Semestral", 110.00),
        ("Semestral", 150.00),
        ("Anual", 179.00),
        ("Anual", 250.00),
    ]

    print("Testando conversão USD → Plano VIP:\n")

    for expected_plan, amount in test_values:
        days = choose_plan_from_usd(amount)

        if days is None:
            plan_name = "Nenhum"
            status = "✅" if expected_plan == "Insuficiente" else "❌"
        elif days == 30:
            plan_name = "Mensal (30 dias)"
            status = "✅" if expected_plan == "Mensal" else "❌"
        elif days == 90:
            plan_name = "Trimestral (90 dias)"
            status = "✅" if expected_plan == "Trimestral" else "❌"
        elif days == 180:
            plan_name = "Semestral (180 dias)"
            status = "✅" if expected_plan == "Semestral" else "❌"
        elif days == 365:
            plan_name = "Anual (365 dias)"
            status = "✅" if expected_plan == "Anual" else "❌"
        else:
            plan_name = f"{days} dias"
            status = "❓"

        print(f"{status} ${amount:>6.2f} USD → {plan_name}")

    print("\n✅ Teste de cálculo de planos concluído!")


async def test_tx_verification(tx_hash: str):
    """Testa verificação de uma transação real"""
    print_header(f"TESTE: VERIFICAÇÃO DE TRANSAÇÃO")

    # Normalizar hash
    normalized = normalize_tx_hash(tx_hash)
    if not normalized:
        print("❌ Hash de transação inválido!")
        print(f"   Formato esperado: 0x... (66 caracteres) ou 64 caracteres hex")
        return

    print(f"🔍 Hash normalizado: {normalized}")
    print(f"⏳ Buscando transação nas blockchains suportadas...\n")

    try:
        ok, message, usd_paid, details = await resolve_payment_usd_autochain(
            normalized, force_refresh=True
        )

        print("📊 RESULTADO DA VERIFICAÇÃO:")
        print(f"   Status: {'✅ APROVADO' if ok else '❌ REJEITADO'}")
        print(f"   Mensagem: {message}")

        if usd_paid:
            print(f"   Valor em USD: ${usd_paid:.2f}")

            # Calcular plano
            days = choose_plan_from_usd(usd_paid)
            if days:
                plan_names = {30: "Mensal", 90: "Trimestral", 180: "Semestral", 365: "Anual"}
                plan_name = plan_names.get(days, f"{days} dias")
                print(f"   Plano: {plan_name} ({days} dias)")
            else:
                print(f"   ⚠️ Valor insuficiente para qualquer plano")

        if details:
            print(f"\n📋 DETALHES DA TRANSAÇÃO:")
            if 'found_on_chain' in details:
                print(f"   Blockchain: {details['found_on_chain']}")
            if 'type' in details:
                print(f"   Tipo: {details['type'].upper()}")
            if 'token_symbol' in details:
                print(f"   Token: {details['token_symbol']}")
            if 'amount_human' in details:
                print(f"   Quantidade: {details['amount_human']:.6f}")
            if 'confirmations' in details:
                print(f"   Confirmações: {details['confirmations']}")

        return ok, usd_paid, details

    except Exception as e:
        print(f"❌ ERRO ao verificar transação: {e}")
        import traceback
        traceback.print_exc()
        return False, None, {}


async def test_complete_payment_flow(tx_hash: str, test_user_id: int = 123456789):
    """Testa o fluxo completo de pagamento (sem enviar mensagens reais)"""
    print_header("TESTE: FLUXO COMPLETO DE PAGAMENTO")

    print(f"👤 ID de teste: {test_user_id}")
    print(f"📝 Hash: {tx_hash}\n")

    # Normalizar hash
    normalized = normalize_tx_hash(tx_hash)
    if not normalized:
        print("❌ Hash inválido!")
        return

    print("⏳ Executando aprovação (modo teste)...\n")

    try:
        ok, message, payload = await approve_by_usd_and_invite(
            test_user_id,
            "test_user",
            normalized,
            notify_user=False  # Não enviar mensagem real
        )

        print("📊 RESULTADO:")
        print(f"   Status: {'✅ APROVADO' if ok else '❌ REJEITADO'}")
        print(f"   Mensagem: {message}")

        if ok and payload:
            print(f"\n📦 PAYLOAD:")
            for key, value in payload.items():
                print(f"   {key}: {value}")

        return ok, payload

    except Exception as e:
        print(f"❌ ERRO no fluxo de pagamento: {e}")
        import traceback
        traceback.print_exc()
        return False, {}


def print_menu():
    """Mostra menu de opções"""
    print_header("MENU DE TESTES")
    print("Escolha uma opção:")
    print()
    print("1. Mostrar configuração do sistema")
    print("2. Testar cálculo de planos VIP")
    print("3. Verificar transação (hash)")
    print("4. Testar fluxo completo de pagamento")
    print("5. Executar todos os testes")
    print("0. Sair")
    print()


async def main():
    """Função principal"""
    print_header("SISTEMA DE TESTE DE PAGAMENTOS")
    print("Este script permite testar o sistema de pagamento em criptomoedas")
    print("sem precisar fazer transações reais ou iniciar o bot completo.")

    while True:
        print_menu()
        choice = input("Digite sua escolha: ").strip()

        if choice == "0":
            print("\n👋 Encerrando testes...")
            break

        elif choice == "1":
            print_config()

        elif choice == "2":
            test_plan_calculation()

        elif choice == "3":
            print()
            tx_hash = input("Digite o hash da transação: ").strip()
            if tx_hash:
                await test_tx_verification(tx_hash)
            else:
                print("❌ Hash não fornecido!")

        elif choice == "4":
            print()
            tx_hash = input("Digite o hash da transação: ").strip()
            user_id = input("Digite o ID do usuário (ou Enter para usar ID de teste): ").strip()

            if not tx_hash:
                print("❌ Hash não fornecido!")
                continue

            test_user_id = int(user_id) if user_id.isdigit() else 123456789
            await test_complete_payment_flow(tx_hash, test_user_id)

        elif choice == "5":
            print("\n🔄 Executando todos os testes...\n")
            print_config()
            test_plan_calculation()

            print()
            tx_hash = input("Digite um hash de transação para teste completo (ou Enter para pular): ").strip()
            if tx_hash:
                await test_tx_verification(tx_hash)
                await test_complete_payment_flow(tx_hash)

        else:
            print("❌ Opção inválida! Tente novamente.")

        input("\n[Pressione Enter para continuar]")
        print("\n" * 2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Teste interrompido pelo usuário.")
    except Exception as e:
        print(f"\n❌ ERRO FATAL: {e}")
        import traceback
        traceback.print_exc()
