"""
Gerenciar Hashes de Pagamento
==============================
Lista e exclui hashes cadastradas no sistema
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
load_dotenv()


def listar_hashes():
    """Lista todas as hashes cadastradas"""
    try:
        from main import SessionLocal, Payment
    except ImportError:
        print("❌ Erro ao importar módulos. Certifique-se que as dependências estão instaladas.")
        return

    print("\n" + "=" * 80)
    print("  HASHES DE PAGAMENTO CADASTRADAS")
    print("=" * 80 + "\n")

    with SessionLocal() as s:
        pagamentos = s.query(Payment).order_by(Payment.created_at.desc()).all()

        if not pagamentos:
            print("✅ Nenhuma hash cadastrada no sistema.\n")
            return

        print(f"📊 Total: {len(pagamentos)} hashes cadastradas\n")
        print("=" * 80 + "\n")

        for i, p in enumerate(pagamentos, 1):
            status_emoji = {
                "approved": "✅",
                "pending": "⏳",
                "rejected": "❌"
            }.get(p.status, "❓")

            print(f"[{i}] Hash: {p.tx_hash}")
            print(f"    Status: {status_emoji} {p.status.upper()}")
            print(f"    User ID: {p.user_id}")
            if p.username:
                print(f"    Username: @{p.username}")
            if p.usd_value:
                print(f"    Valor: ${p.usd_value} USD")
            if p.token_symbol:
                print(f"    Token: {p.token_symbol}")
            if p.vip_days:
                print(f"    Plano: {p.vip_days} dias VIP")
            if p.chain:
                print(f"    Blockchain: {p.chain}")
            print(f"    Data: {p.created_at.strftime('%d/%m/%Y %H:%M:%S')}")
            print("-" * 80 + "\n")

        print("=" * 80 + "\n")


def excluir_hash(tx_hash: str):
    """Exclui uma hash específica"""
    try:
        from main import SessionLocal, Payment
    except ImportError:
        print("❌ Erro ao importar módulos.")
        return

    # Normalizar hash
    clean_hash = tx_hash.lower().replace('0x', '')
    normalized_hash = '0x' + clean_hash

    print("\n" + "=" * 80)
    print("  EXCLUIR HASH")
    print("=" * 80 + "\n")

    with SessionLocal() as s:
        p = s.query(Payment).filter(Payment.tx_hash == normalized_hash).first()

        if not p:
            print(f"❌ Hash não encontrada: {normalized_hash}\n")
            return

        print(f"📋 Hash encontrada:")
        print(f"    Hash: {p.tx_hash}")
        print(f"    User ID: {p.user_id}")
        print(f"    Status: {p.status}")
        print(f"    Valor: ${p.usd_value} USD")
        print(f"    Data: {p.created_at.strftime('%d/%m/%Y %H:%M:%S')}\n")

        confirm = input("⚠️ Deseja REALMENTE excluir esta hash? (s/N): ").strip().lower()

        if confirm == 's':
            s.delete(p)
            s.commit()
            print(f"\n✅ Hash excluída com sucesso!\n")
            print("⚠️ NOTA: O VIP do usuário NÃO foi removido, apenas a hash.")
        else:
            print("\n❌ Operação cancelada.\n")


def excluir_todas():
    """Exclui TODAS as hashes (uso com cuidado!)"""
    try:
        from main import SessionLocal, Payment
    except ImportError:
        print("❌ Erro ao importar módulos.")
        return

    print("\n" + "=" * 80)
    print("  ⚠️ EXCLUIR TODAS AS HASHES ⚠️")
    print("=" * 80 + "\n")

    with SessionLocal() as s:
        count = s.query(Payment).count()

        if count == 0:
            print("✅ Nenhuma hash para excluir.\n")
            return

        print(f"⚠️ ATENÇÃO: Você está prestes a excluir {count} hashes!\n")
        print("Esta ação NÃO PODE SER DESFEITA!\n")

        confirm1 = input("Digite 'CONFIRMO' para continuar: ").strip()

        if confirm1 != "CONFIRMO":
            print("\n❌ Operação cancelada.\n")
            return

        confirm2 = input("Tem CERTEZA ABSOLUTA? (s/N): ").strip().lower()

        if confirm2 == 's':
            s.query(Payment).delete()
            s.commit()
            print(f"\n✅ {count} hashes excluídas com sucesso!\n")
            print("⚠️ NOTA: Os VIPs dos usuários NÃO foram removidos.")
        else:
            print("\n❌ Operação cancelada.\n")


def limpar_hashes_duplicadas():
    """Remove hashes duplicadas (mantém apenas a primeira)"""
    try:
        from main import SessionLocal, Payment
        from sqlalchemy import func
    except ImportError:
        print("❌ Erro ao importar módulos.")
        return

    print("\n" + "=" * 80)
    print("  LIMPAR HASHES DUPLICADAS")
    print("=" * 80 + "\n")

    with SessionLocal() as s:
        # Encontrar hashes duplicadas
        duplicates = s.query(
            Payment.tx_hash,
            func.count(Payment.tx_hash).label('count')
        ).group_by(Payment.tx_hash).having(func.count(Payment.tx_hash) > 1).all()

        if not duplicates:
            print("✅ Nenhuma hash duplicada encontrada.\n")
            return

        print(f"⚠️ Encontradas {len(duplicates)} hashes duplicadas:\n")

        for tx_hash, count in duplicates:
            print(f"    • {tx_hash} - {count}x duplicada")

        print()
        confirm = input("Deseja remover as duplicatas (mantendo apenas a primeira)? (s/N): ").strip().lower()

        if confirm == 's':
            removed = 0
            for tx_hash, _ in duplicates:
                # Pegar todos os registros com esta hash
                payments = s.query(Payment).filter(Payment.tx_hash == tx_hash).order_by(Payment.created_at.asc()).all()

                # Manter o primeiro, excluir os demais
                for p in payments[1:]:
                    s.delete(p)
                    removed += 1

            s.commit()
            print(f"\n✅ {removed} hashes duplicadas removidas!\n")
        else:
            print("\n❌ Operação cancelada.\n")


def main():
    """Menu principal"""

    while True:
        print("\n" + "=" * 80)
        print("  GERENCIADOR DE HASHES DE PAGAMENTO")
        print("=" * 80 + "\n")
        print("1. Listar todas as hashes")
        print("2. Excluir uma hash específica")
        print("3. Excluir TODAS as hashes (cuidado!)")
        print("4. Limpar hashes duplicadas")
        print("0. Sair")
        print()

        choice = input("Escolha uma opção: ").strip()

        if choice == "1":
            listar_hashes()
            input("\nPressione Enter para continuar...")

        elif choice == "2":
            tx_hash = input("\nDigite a hash (com ou sem 0x): ").strip()
            if tx_hash:
                excluir_hash(tx_hash)
            else:
                print("❌ Hash inválida!")
            input("\nPressione Enter para continuar...")

        elif choice == "3":
            excluir_todas()
            input("\nPressione Enter para continuar...")

        elif choice == "4":
            limpar_hashes_duplicadas()
            input("\nPressione Enter para continuar...")

        elif choice == "0":
            print("\n👋 Até logo!\n")
            break

        else:
            print("\n❌ Opção inválida!\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n👋 Programa encerrado.")
    except Exception as e:
        print(f"\n❌ ERRO: {e}")
        import traceback
        traceback.print_exc()
