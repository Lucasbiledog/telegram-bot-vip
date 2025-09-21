#!/usr/bin/env python3
"""
Script Executável para Testes de Stress do Bot Telegram
Use este script para executar diferentes tipos de teste facilmente
"""

import asyncio
import sys
import argparse
import json
import time
from pathlib import Path

# Importar nossos módulos
try:
    from stress_test import StressTester
    from test_config import (
        StressTestConfig, get_config_for_environment,
        get_scenario_config, print_config_summary,
        validate_config, PERFORMANCE_THRESHOLDS
    )
    from performance_monitor import start_monitoring, stop_monitoring, get_performance_summary
except ImportError as e:
    print(f"Erro ao importar modulos: {e}")
    print("Certifique-se de que todos os arquivos estão no mesmo diretório")
    sys.exit(1)

def create_argument_parser():
    """Cria parser de argumentos de linha de comando"""
    parser = argparse.ArgumentParser(
        description="🤖 Teste de Stress para Bot Telegram VIP",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos de uso:

  # Teste rápido com 100 usuários
  python run_stress_test.py --quick

  # Teste com cenário predefinido
  python run_stress_test.py --scenario "Carga Alta"

  # Teste customizado
  python run_stress_test.py --users 500 --rps 50 --webhook http://localhost:8000/webhook

  # Teste em ambiente específico
  python run_stress_test.py --environment production --scenario "Carga Baixa"

  # Teste com monitoramento avançado
  python run_stress_test.py --users 1000 --rps 100 --monitor --export-metrics

Cenários disponíveis:
  • Carga Baixa: 100 usuários, 10/s
  • Carga Média: 500 usuários, 50/s
  • Carga Alta: 1000 usuários, 100/s
  • Stress Extremo: 2000 usuários, 200/s
  • Spike Test: 1500 usuários, 300/s
        """
    )

    # Argumentos principais
    parser.add_argument(
        "--users", "-u",
        type=int,
        help="Número total de usuários falsos (padrão: 1000)"
    )

    parser.add_argument(
        "--rps", "-r",
        type=int,
        help="Requisições por segundo (padrão: 100)"
    )

    parser.add_argument(
        "--webhook", "-w",
        type=str,
        help="URL do webhook do bot (padrão: http://localhost:8000/webhook)"
    )

    parser.add_argument(
        "--duration", "-d",
        type=int,
        help="Duração máxima em segundos (padrão: 300)"
    )

    # Cenários predefinidos
    parser.add_argument(
        "--scenario", "-s",
        type=str,
        choices=["Carga Baixa", "Carga Média", "Carga Alta", "Stress Extremo", "Spike Test"],
        help="Usar cenário predefinido"
    )

    parser.add_argument(
        "--environment", "-e",
        type=str,
        choices=["local", "development", "production"],
        default="local",
        help="Ambiente alvo (padrão: local)"
    )

    # Testes rápidos
    parser.add_argument(
        "--quick", "-q",
        action="store_true",
        help="Teste rápido (100 usuários, 20/s)"
    )

    parser.add_argument(
        "--spike",
        action="store_true",
        help="Teste de pico (300 req/s por 30s)"
    )

    # Opções avançadas
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        help="Tamanho do lote para processamento paralelo"
    )

    parser.add_argument(
        "--concurrent", "-c",
        type=int,
        help="Máximo de requisições concorrentes"
    )

    parser.add_argument(
        "--monitor", "-m",
        action="store_true",
        help="Ativar monitoramento avançado de performance"
    )

    parser.add_argument(
        "--export-metrics",
        action="store_true",
        help="Exportar métricas detalhadas"
    )

    # Output e logging
    parser.add_argument(
        "--output", "-o",
        type=str,
        help="Arquivo de saída para o relatório (JSON)"
    )

    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Output verboso"
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas mostrar configuração sem executar"
    )

    return parser

def build_config_from_args(args) -> StressTestConfig:
    """Constrói configuração baseada nos argumentos"""

    # Começar com configuração do ambiente
    config = get_config_for_environment(args.environment)

    # Aplicar cenário se especificado
    if args.scenario:
        scenario = get_scenario_config(args.scenario)
        config.total_users = scenario["users"]
        config.users_per_second = scenario["users_per_second"]

    # Aplicar testes rápidos
    if args.quick:
        config.total_users = 100
        config.users_per_second = 20
        config.max_test_duration = 60

    if args.spike:
        config.total_users = 300
        config.users_per_second = 300
        config.max_test_duration = 30

    # Aplicar argumentos específicos (sobrescrevem cenários)
    if args.users:
        config.total_users = args.users

    if args.rps:
        config.users_per_second = args.rps

    if args.webhook:
        config.webhook_url = args.webhook

    if args.duration:
        config.max_test_duration = args.duration

    if args.batch_size:
        config.batch_size = args.batch_size

    if args.concurrent:
        config.max_concurrent_requests = args.concurrent

    return config

def print_test_banner():
    """Imprime banner do teste"""
    print("\n" + "="*80)
    print("TESTE DE STRESS - BOT TELEGRAM VIP")
    print("="*80)
    print("ATENCAO: Este teste pode gerar alta carga no servidor!")
    print("Monitor performance em tempo real para detectar problemas")
    print("Use Ctrl+C para interromper o teste a qualquer momento")
    print("="*80 + "\n")

async def run_test_with_monitoring(config: StressTestConfig, enable_monitoring: bool = True) -> dict:
    """Executa teste com monitoramento opcional"""

    if enable_monitoring:
        print("🔍 Iniciando monitoramento de performance...")
        start_monitoring()

    try:
        async with StressTester(config) as tester:
            report = await tester.run_stress_test()

            if enable_monitoring:
                # Adicionar métricas de performance ao relatório
                perf_summary = get_performance_summary()
                report["advanced_monitoring"] = perf_summary

            return report

    finally:
        if enable_monitoring:
            stop_monitoring()

def analyze_results(report: dict) -> dict:
    """Analisa resultados e gera insights"""
    analysis = {
        "overall_grade": "Unknown",
        "performance_issues": [],
        "bottlenecks": [],
        "optimization_suggestions": []
    }

    if "test_summary" not in report:
        return analysis

    summary = report["test_summary"]
    response_times = report.get("response_times", {})

    # Calcular nota geral
    success_rate = summary.get("success_rate_percent", 0)
    avg_response_time = response_times.get("average_ms", 0) / 1000  # converter para segundos
    throughput = summary.get("requests_per_second", 0)

    # Sistema de pontuação
    score = 0

    # Pontuação por taxa de sucesso
    if success_rate >= PERFORMANCE_THRESHOLDS["success_rate"]["excellent"]:
        score += 40
    elif success_rate >= PERFORMANCE_THRESHOLDS["success_rate"]["good"]:
        score += 30
    elif success_rate >= PERFORMANCE_THRESHOLDS["success_rate"]["acceptable"]:
        score += 20
    else:
        score += 10

    # Pontuação por tempo de resposta
    if avg_response_time <= PERFORMANCE_THRESHOLDS["response_time"]["excellent"]:
        score += 30
    elif avg_response_time <= PERFORMANCE_THRESHOLDS["response_time"]["good"]:
        score += 25
    elif avg_response_time <= PERFORMANCE_THRESHOLDS["response_time"]["acceptable"]:
        score += 15
    else:
        score += 5

    # Pontuação por throughput
    if throughput >= PERFORMANCE_THRESHOLDS["throughput"]["excellent"]:
        score += 30
    elif throughput >= PERFORMANCE_THRESHOLDS["throughput"]["good"]:
        score += 25
    elif throughput >= PERFORMANCE_THRESHOLDS["throughput"]["acceptable"]:
        score += 15
    else:
        score += 5

    # Determinar nota
    if score >= 85:
        analysis["overall_grade"] = "A+ (Excelente)"
    elif score >= 75:
        analysis["overall_grade"] = "A (Muito Bom)"
    elif score >= 65:
        analysis["overall_grade"] = "B (Bom)"
    elif score >= 50:
        analysis["overall_grade"] = "C (Regular)"
    else:
        analysis["overall_grade"] = "D (Necessita Melhorias)"

    # Identificar problemas específicos
    if success_rate < 95:
        analysis["performance_issues"].append(f"Taxa de sucesso baixa ({success_rate:.1f}%)")

    if avg_response_time > 2.0:
        analysis["performance_issues"].append(f"Tempo de resposta alto ({avg_response_time:.2f}s)")
        analysis["bottlenecks"].append("Possível gargalo em queries de banco de dados")

    if throughput < 20:
        analysis["performance_issues"].append(f"Throughput baixo ({throughput:.1f} req/s)")
        analysis["bottlenecks"].append("Possível limitação de concorrência")

    # Sugestões de otimização baseadas nos resultados
    if len(analysis["performance_issues"]) > 0:
        analysis["optimization_suggestions"].extend([
            "Implementar cache Redis para dados frequentemente acessados",
            "Otimizar queries de banco com indices apropriados",
            "Usar connection pooling para banco de dados",
            "Implementar rate limiting inteligente",
            "Considerar arquitetura assincrona com filas"
        ])

    return analysis

def print_final_report(report: dict, analysis: dict, config: StressTestConfig):
    """Imprime relatório final formatado"""
    print("\n" + "="*80)
    print("RELATORIO FINAL DO TESTE DE STRESS")
    print("="*80)

    # Resumo executivo
    print(f"\nRESUMO EXECUTIVO:")
    print(f"   Nota Geral: {analysis['overall_grade']}")

    if "test_summary" in report:
        summary = report["test_summary"]
        print(f"   Taxa de Sucesso: {summary.get('success_rate_percent', 0):.1f}%")
        print(f"   Requests/Segundo: {summary.get('requests_per_second', 0):.1f}")
        print(f"   Duração Total: {summary.get('test_duration_seconds', 0):.1f}s")

    # Métricas detalhadas
    if "response_times" in report:
        rt = report["response_times"]
        print(f"\nTEMPOS DE RESPOSTA:")
        print(f"   Médio: {rt.get('average_ms', 0):.0f}ms")
        print(f"   P95: {rt.get('p95_ms', 0):.0f}ms")
        print(f"   Máximo: {rt.get('maximum_ms', 0):.0f}ms")

    # Problemas identificados
    if analysis["performance_issues"]:
        print(f"\nPROBLEMAS IDENTIFICADOS:")
        for issue in analysis["performance_issues"]:
            print(f"   • {issue}")

    # Gargalos
    if analysis["bottlenecks"]:
        print(f"\n🔍 POSSÍVEIS GARGALOS:")
        for bottleneck in analysis["bottlenecks"]:
            print(f"   • {bottleneck}")

    # Recomendações
    if "recommendations" in report:
        print(f"\nRECOMENDACOES:")
        for rec in report["recommendations"][:5]:  # Top 5
            print(f"   {rec}")

    print("\n" + "="*80)

async def main():
    """Função principal"""
    parser = create_argument_parser()
    args = parser.parse_args()

    # Banner inicial
    print_test_banner()

    # Construir configuração
    config = build_config_from_args(args)

    # Mostrar configuração
    print_config_summary(config)

    # Validar configuração
    warnings = validate_config(config)
    if warnings:
        print(f"\nAVISOS DE CONFIGURACAO:")
        for warning in warnings:
            print(f"   {warning}")

    # Dry run - apenas mostrar configuração
    if args.dry_run:
        print("\n🏃 DRY RUN - Configuração validada, não executando teste")
        return

    # Confirmação do usuário
    if args.environment == "production":
        print(f"\nATENCAO: Executando em PRODUCAO!")
        confirm = input("Digite 'CONFIRMO' para continuar: ")
        if confirm != "CONFIRMO":
            print("Teste cancelado")
            return

    elif not args.quick:
        print(f"\nATENCAO: Este teste enviara {config.total_users:,} requisicoes")
        confirm = input("Pressione ENTER para continuar ou Ctrl+C para cancelar...")

    print(f"\nIniciando teste de stress...")
    print(f"Monitoramento: {'Ativado' if args.monitor else 'Basico'}")

    start_time = time.time()

    try:
        # Executar teste
        report = await run_test_with_monitoring(config, args.monitor)

        # Analisar resultados
        analysis = analyze_results(report)

        # Mostrar relatório
        print_final_report(report, analysis, config)

        # Salvar relatório se solicitado
        if args.output:
            output_file = args.output
        else:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            output_file = f"stress_test_report_{timestamp}.json"

        # Adicionar análise ao relatório
        report["analysis"] = analysis
        report["test_config"] = {
            "total_users": config.total_users,
            "users_per_second": config.users_per_second,
            "environment": args.environment,
            "scenario": args.scenario
        }

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        print(f"\n💾 Relatório completo salvo em: {output_file}")

        # Exportar métricas se solicitado
        if args.export_metrics and args.monitor:
            metrics_file = output_file.replace('.json', '_metrics.json')
            perf_data = get_performance_summary()
            with open(metrics_file, 'w', encoding='utf-8') as f:
                json.dump(perf_data, f, indent=2, ensure_ascii=False)
            print(f"Metricas detalhadas em: {metrics_file}")

    except KeyboardInterrupt:
        print(f"\nTeste interrompido pelo usuario")
        print(f"Tempo decorrido: {time.time() - start_time:.1f}s")

    except Exception as e:
        print(f"\nErro durante execucao: {e}")
        if args.verbose:
            import traceback
            traceback.print_exc()
        return 1

    print(f"\nTeste concluido em {time.time() - start_time:.1f}s")
    return 0

if __name__ == "__main__":
    try:
        exit_code = asyncio.run(main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n👋 Saindo...")
        sys.exit(0)