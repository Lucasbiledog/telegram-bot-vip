#!/usr/bin/env python3
"""
🚀 Executor Principal de Testes de Pagamento
Script principal para executar todos os tipos de teste de pagamento
"""

import asyncio
import sys
import time
import argparse
from typing import Dict, List
import json

# Fix import time
import time as time_module

# Imports locais
from payment_stress_test import PaymentStressTest
from payment_test_config import (
    PAYMENT_TEST_SCENARIOS,
    get_environment_config,
    get_scenario_config,
    calculate_benchmark_grade,
    REALISTIC_AMOUNT_DISTRIBUTION
)

class PaymentTestRunner:
    """Executor principal de testes de pagamento"""

    def __init__(self, environment: str = "development"):
        self.environment = environment
        self.config = get_environment_config(environment)
        self.results_summary = []

    async def run_scenario_test(self, scenario_name: str, custom_config: Dict = None) -> Dict:
        """Executa um cenário específico de teste"""
        scenario_config = get_scenario_config(scenario_name)
        if not scenario_config:
            raise ValueError(f"Cenário '{scenario_name}' não encontrado")

        # Aplicar configurações customizadas se fornecidas
        if custom_config:
            scenario_config.update(custom_config)

        print(f"\nExecutando cenario: {scenario_config['name']}")
        print(f"{scenario_config['description']}")

        # Configurar teste
        tester = PaymentStressTest(base_url=self.config['base_url'])

        # Gerar cenários baseados na configuração
        test_scenarios = self._generate_scenarios_from_config(scenario_config, tester)

        print(f"Total de testes: {len(test_scenarios)}")
        print(f"Concorrencia: {scenario_config.get('concurrency', 10)}")

        # Executar testes
        start_time = time_module.time()
        tester.start_time = start_time

        await tester.run_concurrent_tests(
            test_scenarios,
            scenario_config.get('concurrency', 10)
        )

        end_time = time_module.time()
        tester.end_time = end_time

        # Calcular métricas
        total_tests = len(tester.results)
        successful_tests = sum(1 for r in tester.results if r.success)
        success_rate = (successful_tests / total_tests * 100) if total_tests > 0 else 0
        avg_response_time = sum(r.response_time for r in tester.results) / total_tests if total_tests > 0 else 0
        duration = end_time - start_time

        metrics = {
            "scenario": scenario_name,
            "total_tests": total_tests,
            "successful_tests": successful_tests,
            "success_rate": success_rate,
            "avg_response_time": avg_response_time,
            "duration": duration,
            "throughput": total_tests / duration if duration > 0 else 0
        }

        # Calcular nota
        grade = calculate_benchmark_grade(metrics)
        metrics["grade"] = grade

        # Salvar resultados
        timestamp = time_module.strftime("%Y%m%d_%H%M%S")
        filename = f"payment_test_{scenario_name}_{timestamp}.json"
        tester.save_results(filename)

        return {
            "metrics": metrics,
            "tester": tester,
            "filename": filename
        }

    def _generate_scenarios_from_config(self, config: Dict, tester: PaymentStressTest) -> List[Dict]:
        """Gera cenários de teste baseado na configuração"""
        scenarios = []

        chains = config.get('chains', ['0x38', '0x89'])  # Default BSC e Polygon
        tests_per_chain = config.get('tests_per_chain', 10)
        amount_range = config.get('amount_range', [1.0, 3.0])

        native_only = config.get('native_only', False)
        token_filter = config.get('token_filter', [])

        for chain_id in chains:
            chain_info = tester.chains.get(chain_id)
            if not chain_info:
                continue

            # Testes nativos
            if not token_filter:  # Se não há filtro específico de token
                for i in range(tests_per_chain):
                    scenarios.append({
                        "type": "native",
                        "chain_id": chain_id,
                        "chain_name": chain_info["name"],
                        "token_symbol": chain_info["symbol"],
                        "tx_hash": tester.generate_fake_tx_hash(),
                        "amount_usd": self._generate_realistic_amount(amount_range)
                    })

            # Testes de tokens (se não for native_only)
            if not native_only:
                tokens = tester.test_tokens.get(chain_id, [])

                # Aplicar filtro se especificado
                if token_filter:
                    tokens = [t for t in tokens if t["symbol"] in token_filter]

                for token in tokens:
                    for i in range(tests_per_chain // 2 if tests_per_chain > 1 else 1):
                        scenarios.append({
                            "type": "token",
                            "chain_id": chain_id,
                            "chain_name": chain_info["name"],
                            "token_symbol": token["symbol"],
                            "token_address": token["address"],
                            "tx_hash": tester.generate_fake_tx_hash(),
                            "amount_usd": self._generate_realistic_amount(amount_range)
                        })

        return scenarios

    def _generate_realistic_amount(self, range_limits: List[float]) -> float:
        """Gera valores realísticos baseados na distribuição configurada"""
        import random

        # Usar distribuição realística se estiver dentro da faixa
        min_val, max_val = range_limits

        # Escolher categoria baseada nos pesos
        rand = random.random()
        cumulative = 0

        for category, dist in REALISTIC_AMOUNT_DISTRIBUTION.items():
            cumulative += dist["weight"]
            if rand <= cumulative:
                cat_min, cat_max = dist["range"]
                # Ajustar para os limites especificados
                actual_min = max(cat_min, min_val)
                actual_max = min(cat_max, max_val)

                if actual_min <= actual_max:
                    return round(random.uniform(actual_min, actual_max), 2)
                break

        # Fallback para distribuição uniforme
        return round(random.uniform(min_val, max_val), 2)

    async def run_comprehensive_test(self) -> Dict:
        """Executa um teste abrangente de todos os cenários importantes"""
        print("INICIANDO TESTE ABRANGENTE DE PAGAMENTOS")
        print("=" * 60)

        # Cenários a executar em ordem
        test_scenarios = [
            "connectivity",
            "stablecoin_stress",
            "native_load",
            "high_concurrency"
        ]

        comprehensive_results = {
            "start_time": time_module.time(),
            "environment": self.environment,
            "scenarios": {},
            "overall_metrics": {}
        }

        total_tests = 0
        total_successful = 0
        total_duration = 0

        for scenario_name in test_scenarios:
            try:
                print(f"\n{'='*20} {scenario_name.upper()} {'='*20}")
                result = await self.run_scenario_test(scenario_name)

                metrics = result["metrics"]
                comprehensive_results["scenarios"][scenario_name] = metrics

                # Acumular estatísticas
                total_tests += metrics["total_tests"]
                total_successful += metrics["successful_tests"]
                total_duration += metrics["duration"]

                print(f"OK Cenario concluido:")
                print(f"   Taxa de sucesso: {metrics['success_rate']:.1f}%")
                print(f"   Tempo medio: {metrics['avg_response_time']:.3f}s")
                print(f"   Nota: {metrics['grade']}")

            except Exception as e:
                print(f"ERRO no cenario {scenario_name}: {e}")
                comprehensive_results["scenarios"][scenario_name] = {
                    "error": str(e),
                    "success_rate": 0,
                    "grade": "F"
                }

        # Calcular métricas gerais
        overall_success_rate = (total_successful / total_tests * 100) if total_tests > 0 else 0
        overall_throughput = total_tests / total_duration if total_duration > 0 else 0

        comprehensive_results["overall_metrics"] = {
            "total_tests": total_tests,
            "total_successful": total_successful,
            "overall_success_rate": overall_success_rate,
            "total_duration": total_duration,
            "overall_throughput": overall_throughput,
            "overall_grade": calculate_benchmark_grade({
                "success_rate": overall_success_rate,
                "avg_response_time": total_duration / total_tests if total_tests > 0 else 999
            })
        }

        comprehensive_results["end_time"] = time_module.time()

        return comprehensive_results

    def print_final_report(self, results: Dict) -> None:
        """Imprime relatório final formatado"""
        print("\n" + "="*80)
        print("RELATORIO FINAL - TESTE ABRANGENTE DE PAGAMENTOS")
        print("="*80)

        overall = results["overall_metrics"]
        print(f"\nRESUMO GERAL:")
        print(f"   Ambiente: {self.environment}")
        print(f"   Total de testes: {overall['total_tests']}")
        print(f"   Sucessos: {overall['total_successful']}")
        print(f"   Taxa de sucesso: {overall['overall_success_rate']:.1f}%")
        print(f"   Duracao total: {overall['total_duration']:.1f}s")
        print(f"   Throughput: {overall['overall_throughput']:.1f} testes/s")
        print(f"   Nota geral: {overall['overall_grade']}")

        print(f"\nRESULTADOS POR CENARIO:")
        for scenario, metrics in results["scenarios"].items():
            if "error" in metrics:
                print(f"   ERRO {scenario}: {metrics['error']}")
            else:
                print(f"   * {scenario}:")
                print(f"      Taxa de sucesso: {metrics['success_rate']:.1f}%")
                print(f"      Tempo medio: {metrics.get('avg_response_time', 0):.3f}s")
                print(f"      Nota: {metrics['grade']}")

        # Recomendações
        print(f"\nRECOMENDACOES:")

        if overall['overall_success_rate'] < 95:
            print("   * Taxa de sucesso baixa - verificar conectividade")

        if overall['overall_throughput'] < 10:
            print("   * Throughput baixo - otimizar performance")

        if overall['overall_grade'] in ['D', 'F']:
            print("   * Sistema necessita otimizacoes urgentes")
        elif overall['overall_grade'] in ['A+', 'A']:
            print("   * Sistema com excelente performance!")

        print("\n" + "="*80)

def main():
    parser = argparse.ArgumentParser(
        description="🚀 Teste Abrangente de Pagamentos Multi-Chain",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--scenario", "-s",
        type=str,
        help="Cenário específico para executar"
    )

    parser.add_argument(
        "--comprehensive", "-c",
        action="store_true",
        help="Executa teste abrangente de todos os cenários"
    )

    parser.add_argument(
        "--environment", "-e",
        type=str,
        default="development",
        choices=["development", "staging", "production"],
        help="Ambiente para teste"
    )

    parser.add_argument(
        "--list-scenarios", "-l",
        action="store_true",
        help="Lista cenários disponíveis"
    )

    parser.add_argument(
        "--url", "-u",
        type=str,
        help="URL customizada da API (sobrescreve configuração do ambiente)"
    )

    args = parser.parse_args()

    if args.list_scenarios:
        print("CENARIOS DE TESTE DISPONIVEIS:\n")
        for name, config in PAYMENT_TEST_SCENARIOS.items():
            print(f"* {name}:")
            print(f"   {config['description']}")
            print()
        return

    # Criar runner
    runner = PaymentTestRunner(environment=args.environment)

    # Sobrescrever URL se fornecida
    if args.url:
        runner.config['base_url'] = args.url
        print(f"Usando URL customizada: {args.url}")

    if args.environment == "production":
        response = input("AVISO: Voce esta executando em PRODUCAO. Continuar? (yes/no): ")
        if response.lower() != "yes":
            print("Teste cancelado.")
            return

    if args.comprehensive:
        # Teste abrangente
        print(f"Iniciando teste abrangente no ambiente: {args.environment}")
        results = asyncio.run(runner.run_comprehensive_test())
        runner.print_final_report(results)

        # Salvar resultados
        timestamp = time_module.strftime("%Y%m%d_%H%M%S")
        filename = f"comprehensive_payment_test_{timestamp}.json"
        with open(filename, 'w') as f:
            json.dump(results, f, indent=2)
        print(f"\nResultados salvos em: {filename}")

    elif args.scenario:
        # Cenário específico
        if args.scenario not in PAYMENT_TEST_SCENARIOS:
            print(f"Cenario '{args.scenario}' nao encontrado!")
            print("Use --list-scenarios para ver opcoes disponiveis.")
            return

        print(f"Executando cenario especifico: {args.scenario}")
        result = asyncio.run(runner.run_scenario_test(args.scenario))

        metrics = result["metrics"]
        print(f"\nCenario concluido!")
        print(f"   Taxa de sucesso: {metrics['success_rate']:.1f}%")
        print(f"   Tempo medio: {metrics['avg_response_time']:.3f}s")
        print(f"   Throughput: {metrics['throughput']:.1f} req/s")
        print(f"   Nota: {metrics['grade']}")
        print(f"   Arquivo: {result['filename']}")

    else:
        parser.print_help()

if __name__ == "__main__":
    main()