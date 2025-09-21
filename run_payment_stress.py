#!/usr/bin/env python3
"""
🚀 Executor de Teste de Stress para Pagamentos Multi-Chain
Interface de linha de comando para testar todos os tipos de pagamento suportados
"""

import asyncio
import argparse
import sys
import os
from payment_stress_test import PaymentStressTest

# Cenários predefinidos por moeda/chain
PREDEFINED_SCENARIOS = {
    "ethereum_native": {
        "name": "Ethereum Native (ETH)",
        "description": "Testa pagamentos nativos em Ethereum",
        "tests": 20,
        "concurrency": 5,
        "focus_chains": ["0x1"],
        "token_types": ["native"]
    },
    "bsc_ecosystem": {
        "name": "BSC Ecosystem",
        "description": "Testa BNB, USDC, USDT e BTCB na BSC",
        "tests": 30,
        "concurrency": 8,
        "focus_chains": ["0x38"],
        "token_types": ["native", "token"]
    },
    "polygon_tokens": {
        "name": "Polygon Tokens",
        "description": "Testa MATIC, USDC e USDT na Polygon",
        "tests": 25,
        "concurrency": 6,
        "focus_chains": ["0x89"],
        "token_types": ["native", "token"]
    },
    "layer2_stress": {
        "name": "Layer 2 Stress Test",
        "description": "Testa Arbitrum, Optimism, Base, Polygon",
        "tests": 40,
        "concurrency": 10,
        "focus_chains": ["0xa4b1", "0xa", "0x2105", "0x89"],
        "token_types": ["native", "token"]
    },
    "stablecoin_focus": {
        "name": "Stablecoin Focus",
        "description": "Testa apenas USDC/USDT em todas as chains",
        "tests": 50,
        "concurrency": 12,
        "focus_chains": ["0x1", "0x38", "0x89", "0xa4b1", "0xa", "0x2105"],
        "token_types": ["token"],
        "token_filter": ["USDC", "USDT"]
    },
    "all_chains": {
        "name": "All Chains Test",
        "description": "Testa todas as 22 chains suportadas",
        "tests": 100,
        "concurrency": 15,
        "focus_chains": "all",
        "token_types": ["native", "token"]
    },
    "quick_validation": {
        "name": "Quick Validation",
        "description": "Teste rápido para validar sistema",
        "tests": 10,
        "concurrency": 3,
        "focus_chains": ["0x38", "0x89"],
        "token_types": ["native"]
    },
    "high_volume": {
        "name": "High Volume Test",
        "description": "Teste de alto volume - 500 transações",
        "tests": 500,
        "concurrency": 25,
        "focus_chains": "all",
        "token_types": ["native", "token"]
    }
}

class EnhancedPaymentStressTest(PaymentStressTest):
    """Versão melhorada com cenários específicos"""

    def generate_focused_scenarios(self, config: dict) -> list:
        """Gera cenários focados baseado na configuração"""
        scenarios = []
        num_tests = config["tests"]
        focus_chains = config["focus_chains"]
        token_types = config["token_types"]
        token_filter = config.get("token_filter", [])

        # Determinar chains a usar
        if focus_chains == "all":
            target_chains = list(self.chains.keys())
        else:
            target_chains = focus_chains

        for i in range(num_tests):
            # Escolher chain (distribuição uniforme)
            chain_id = target_chains[i % len(target_chains)]
            chain_info = self.chains[chain_id]

            # Decidir tipo de token
            if len(token_types) == 1:
                token_type = token_types[0]
            else:
                # 60% nativo, 40% token se ambos estão disponíveis
                token_type = "native" if (i % 5) < 3 else "token"

            if token_type == "native":
                scenario = {
                    "type": "native",
                    "chain_id": chain_id,
                    "chain_name": chain_info["name"],
                    "token_symbol": chain_info["symbol"],
                    "tx_hash": self.generate_fake_tx_hash(),
                    "amount_usd": self._choose_test_amount()
                }
            else:
                # Token ERC-20
                tokens = self.test_tokens.get(chain_id, [])

                # Aplicar filtro de token se especificado
                if token_filter:
                    tokens = [t for t in tokens if t["symbol"] in token_filter]

                if tokens:
                    token = tokens[i % len(tokens)]
                    scenario = {
                        "type": "token",
                        "chain_id": chain_id,
                        "chain_name": chain_info["name"],
                        "token_symbol": token["symbol"],
                        "token_address": token["address"],
                        "tx_hash": self.generate_fake_tx_hash(),
                        "amount_usd": self._choose_test_amount()
                    }
                else:
                    # Fallback para nativo
                    scenario = {
                        "type": "native",
                        "chain_id": chain_id,
                        "chain_name": chain_info["name"],
                        "token_symbol": chain_info["symbol"],
                        "tx_hash": self.generate_fake_tx_hash(),
                        "amount_usd": self._choose_test_amount()
                    }

            scenarios.append(scenario)

        return scenarios

    def _choose_test_amount(self) -> float:
        """Escolhe valor de teste com distribuição mais realística"""
        import random

        # 40% valores pequenos (0.1-1.0), 40% médios (1.0-3.0), 20% altos (3.0-10.0)
        rand = random.random()
        if rand < 0.4:
            return round(random.uniform(0.1, 1.0), 2)
        elif rand < 0.8:
            return round(random.uniform(1.0, 3.0), 2)
        else:
            return round(random.uniform(3.0, 10.0), 2)

def list_scenarios():
    """Lista todos os cenários disponíveis"""
    print("📋 CENÁRIOS DE TESTE DISPONÍVEIS:\n")

    for key, config in PREDEFINED_SCENARIOS.items():
        print(f"🎯 {key}:")
        print(f"   Nome: {config['name']}")
        print(f"   Descrição: {config['description']}")
        print(f"   Testes: {config['tests']}")
        print(f"   Concorrência: {config['concurrency']}")

        if config['focus_chains'] == "all":
            print(f"   Chains: Todas (22 chains)")
        else:
            chains_count = len(config['focus_chains'])
            print(f"   Chains: {chains_count} chains específicas")

        print(f"   Tipos: {', '.join(config['token_types'])}")
        print()

async def run_scenario(scenario_key: str, base_url: str, custom_tests: int = None):
    """Executa um cenário específico"""
    if scenario_key not in PREDEFINED_SCENARIOS:
        print(f"❌ Cenário '{scenario_key}' não encontrado!")
        print("Use --list para ver cenários disponíveis.")
        return False

    config = PREDEFINED_SCENARIOS[scenario_key].copy()

    # Override número de testes se especificado
    if custom_tests:
        config["tests"] = custom_tests

    print(f"🚀 Executando cenário: {config['name']}")
    print(f"📝 Descrição: {config['description']}")
    print(f"🔢 Testes: {config['tests']}")
    print(f"⚡ Concorrência: {config['concurrency']}")
    print(f"🌐 URL: {base_url}\n")

    # Criar instância do teste
    tester = EnhancedPaymentStressTest(base_url=base_url)

    # Gerar cenários focados
    scenarios = tester.generate_focused_scenarios(config)
    print(f"📋 Gerados {len(scenarios)} cenários específicos")

    # Log das chains que serão testadas
    chains_to_test = set(s["chain_id"] for s in scenarios)
    print(f"🌐 Chains no teste: {len(chains_to_test)}")

    chain_counts = {}
    for scenario in scenarios:
        chain_id = scenario["chain_id"]
        chain_counts[chain_id] = chain_counts.get(chain_id, 0) + 1

    for chain_id, count in sorted(chain_counts.items(), key=lambda x: x[1], reverse=True):
        chain_name = tester.chains.get(chain_id, {}).get("name", chain_id)
        print(f"   {chain_name}: {count} testes")

    print()

    # Executar testes
    tester.start_time = time.time()
    await tester.run_concurrent_tests(scenarios, config["concurrency"])
    tester.end_time = time.time()

    # Gerar e exibir relatório
    report = tester.generate_report()
    print(report)

    # Salvar resultados
    import time
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    filename = f"payment_stress_{scenario_key}_{timestamp}.json"
    tester.save_results(filename)

    return True

def main():
    parser = argparse.ArgumentParser(
        description="🚀 Teste de Stress para Pagamentos Multi-Chain",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos de uso:

  # Listar cenários disponíveis
  python run_payment_stress.py --list

  # Teste rápido
  python run_payment_stress.py --scenario quick_validation

  # Teste completo BSC
  python run_payment_stress.py --scenario bsc_ecosystem

  # Teste de todas as chains
  python run_payment_stress.py --scenario all_chains

  # Teste customizado
  python run_payment_stress.py --scenario stablecoin_focus --tests 100

  # Teste com URL específica
  python run_payment_stress.py --scenario layer2_stress --url http://production-bot.com
        """
    )

    parser.add_argument(
        "--list", "-l",
        action="store_true",
        help="Lista todos os cenários disponíveis"
    )

    parser.add_argument(
        "--scenario", "-s",
        type=str,
        help="Cenário de teste a executar"
    )

    parser.add_argument(
        "--tests", "-t",
        type=int,
        help="Número de testes a executar (override do cenário)"
    )

    parser.add_argument(
        "--url", "-u",
        type=str,
        default="http://localhost:8000",
        help="URL base da API (padrão: http://localhost:8000)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Modo verboso"
    )

    args = parser.parse_args()

    if args.list:
        list_scenarios()
        return

    if not args.scenario:
        print("❌ Especifique um cenário com --scenario ou use --list para ver opções")
        parser.print_help()
        return

    if args.verbose:
        import logging
        logging.getLogger().setLevel(logging.DEBUG)

    # Executar cenário
    import time
    success = asyncio.run(run_scenario(args.scenario, args.url, args.tests))

    if success:
        print("\n✅ Teste concluído com sucesso!")
    else:
        print("\n❌ Erro durante execução do teste")
        sys.exit(1)

if __name__ == "__main__":
    main()