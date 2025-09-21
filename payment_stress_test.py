#!/usr/bin/env python3
"""
🚀 Sistema de Teste de Stress para Pagamentos Multi-Chain
Testa todas as moedas e chains suportadas pelo bot de pagamentos
"""

import asyncio
import aiohttp
import json
import time
import random
import logging
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from collections import defaultdict
import psutil
import os

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('payment_stress.log'),
        logging.StreamHandler()
    ]
)
LOG = logging.getLogger("payment_stress")

@dataclass
class PaymentTestResult:
    """Resultado de um teste de pagamento"""
    chain_id: str
    token_symbol: str
    tx_hash: str
    success: bool
    response_time: float
    error_message: Optional[str] = None
    usd_amount: Optional[float] = None
    status_code: Optional[int] = None

@dataclass
class ChainTestStats:
    """Estatísticas de teste por chain"""
    chain_id: str
    chain_name: str
    total_tests: int = 0
    successful_tests: int = 0
    failed_tests: int = 0
    avg_response_time: float = 0.0
    errors: Dict[str, int] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = defaultdict(int)

class PaymentStressTest:
    """Sistema de teste de stress para pagamentos"""

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url
        self.results: List[PaymentTestResult] = []
        self.chain_stats: Dict[str, ChainTestStats] = {}
        self.start_time = 0
        self.end_time = 0

        # Chains e tokens suportados (do payments.py)
        self.chains = {
            "0x1": {"name": "Ethereum", "symbol": "ETH", "decimals": 18},
            "0x38": {"name": "BNB Smart Chain", "symbol": "BNB", "decimals": 18},
            "0x89": {"name": "Polygon", "symbol": "MATIC", "decimals": 18},
            "0xa4b1": {"name": "Arbitrum One", "symbol": "ETH", "decimals": 18},
            "0xa": {"name": "OP Mainnet", "symbol": "ETH", "decimals": 18},
            "0x2105": {"name": "Base", "symbol": "ETH", "decimals": 18},
            "0xa86a": {"name": "Avalanche", "symbol": "AVAX", "decimals": 18},
            "0x144": {"name": "zkSync Era", "symbol": "ETH", "decimals": 18},
            "0xe708": {"name": "Linea", "symbol": "ETH", "decimals": 18},
            "0x13e31": {"name": "Blast", "symbol": "ETH", "decimals": 18},
            "0xa4ec": {"name": "Celo", "symbol": "CELO", "decimals": 18},
            "0x1388": {"name": "Mantle", "symbol": "MNT", "decimals": 18},
            "0xcc": {"name": "opBNB", "symbol": "BNB", "decimals": 18},
            "0x82750": {"name": "Scroll", "symbol": "ETH", "decimals": 18},
            "0xfa": {"name": "Fantom", "symbol": "FTM", "decimals": 18},
            "0x64": {"name": "Gnosis", "symbol": "xDAI", "decimals": 18},
            "0x507": {"name": "Moonbeam", "symbol": "GLMR", "decimals": 18},
            "0x505": {"name": "Moonriver", "symbol": "MOVR", "decimals": 18},
            "0x19": {"name": "Cronos", "symbol": "CRO", "decimals": 18},
            "0x7a69": {"name": "Zora", "symbol": "ETH", "decimals": 18},
            "0x1b3": {"name": "Ape Chain", "symbol": "APE", "decimals": 18},
            "0x2710": {"name": "Morph", "symbol": "ETH", "decimals": 18}
        }

        # Tokens ERC-20 principais para teste
        self.test_tokens = {
            # USDC em diferentes chains
            "0x1": [
                {"address": "0xa0b86991c31cc170c8b9e71b51e1a53af4e9b8c9e", "symbol": "USDC", "decimals": 6},
                {"address": "0xdac17f958d2ee523a2206206994597c13d831ec7", "symbol": "USDT", "decimals": 6}
            ],
            "0x38": [
                {"address": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d", "symbol": "USDC", "decimals": 18},
                {"address": "0x55d398326f99059ff775485246999027b3197955", "symbol": "USDT", "decimals": 18},
                {"address": "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c", "symbol": "BTCB", "decimals": 18}
            ],
            "0x89": [
                {"address": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174", "symbol": "USDC", "decimals": 6},
                {"address": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f", "symbol": "USDT", "decimals": 6}
            ],
            "0xa4b1": [
                {"address": "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "symbol": "USDC", "decimals": 6}
            ],
            "0xa": [
                {"address": "0x0b2c639c533813f4aa9d7837caf62653d097ff85", "symbol": "USDC", "decimals": 6}
            ],
            "0x2105": [
                {"address": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913", "symbol": "USDC", "decimals": 6}
            ]
        }

        # Valores de teste em USD (diferentes planos)
        self.test_amounts_usd = [0.1, 0.5, 1.0, 1.5, 2.5, 3.0, 5.0, 10.0]

    def generate_fake_tx_hash(self) -> str:
        """Gera hash de transação fake para teste (formato válido)"""
        return "0x" + "".join(random.choices("0123456789abcdef", k=64))

    def generate_test_scenarios(self, num_tests: int = 100) -> List[Dict[str, Any]]:
        """Gera cenários de teste para diferentes chains e tokens"""
        scenarios = []

        for _ in range(num_tests):
            # Escolher chain aleatória
            chain_id = random.choice(list(self.chains.keys()))
            chain_info = self.chains[chain_id]

            # Decidir se é nativo ou token (70% nativo, 30% token)
            is_native = random.random() < 0.7

            if is_native:
                scenario = {
                    "type": "native",
                    "chain_id": chain_id,
                    "chain_name": chain_info["name"],
                    "token_symbol": chain_info["symbol"],
                    "tx_hash": self.generate_fake_tx_hash(),
                    "amount_usd": random.choice(self.test_amounts_usd)
                }
            else:
                # Usar token se disponível para a chain
                tokens = self.test_tokens.get(chain_id, [])
                if tokens:
                    token = random.choice(tokens)
                    scenario = {
                        "type": "token",
                        "chain_id": chain_id,
                        "chain_name": chain_info["name"],
                        "token_symbol": token["symbol"],
                        "token_address": token["address"],
                        "tx_hash": self.generate_fake_tx_hash(),
                        "amount_usd": random.choice(self.test_amounts_usd)
                    }
                else:
                    # Fallback para nativo se não tem tokens
                    scenario = {
                        "type": "native",
                        "chain_id": chain_id,
                        "chain_name": chain_info["name"],
                        "token_symbol": chain_info["symbol"],
                        "tx_hash": self.generate_fake_tx_hash(),
                        "amount_usd": random.choice(self.test_amounts_usd)
                    }

            scenarios.append(scenario)

        return scenarios

    async def test_payment_endpoint(self, session: aiohttp.ClientSession, scenario: Dict[str, Any]) -> PaymentTestResult:
        """Testa um pagamento específico"""
        start_time = time.time()

        # Dados do teste
        test_data = {
            "hash": scenario["tx_hash"],
            "uid": f"temp_{int(time.time() * 1000000)}"  # UID temporário
        }

        try:
            # Simular chamada para API de pagamento
            url = f"{self.base_url}/api/validate"

            async with session.post(url, json=test_data, timeout=30) as response:
                response_time = time.time() - start_time

                if response.status == 200:
                    data = await response.json()
                    success = data.get("success", False)

                    return PaymentTestResult(
                        chain_id=scenario["chain_id"],
                        token_symbol=scenario["token_symbol"],
                        tx_hash=scenario["tx_hash"],
                        success=success,
                        response_time=response_time,
                        usd_amount=scenario["amount_usd"],
                        status_code=response.status
                    )
                else:
                    error_text = await response.text()
                    return PaymentTestResult(
                        chain_id=scenario["chain_id"],
                        token_symbol=scenario["token_symbol"],
                        tx_hash=scenario["tx_hash"],
                        success=False,
                        response_time=response_time,
                        error_message=f"HTTP {response.status}: {error_text[:100]}",
                        usd_amount=scenario["amount_usd"],
                        status_code=response.status
                    )

        except asyncio.TimeoutError:
            return PaymentTestResult(
                chain_id=scenario["chain_id"],
                token_symbol=scenario["token_symbol"],
                tx_hash=scenario["tx_hash"],
                success=False,
                response_time=time.time() - start_time,
                error_message="Timeout (>30s)",
                usd_amount=scenario["amount_usd"]
            )
        except Exception as e:
            return PaymentTestResult(
                chain_id=scenario["chain_id"],
                token_symbol=scenario["token_symbol"],
                tx_hash=scenario["tx_hash"],
                success=False,
                response_time=time.time() - start_time,
                error_message=str(e)[:100],
                usd_amount=scenario["amount_usd"]
            )

    async def run_concurrent_tests(self, scenarios: List[Dict[str, Any]], concurrency: int = 10) -> None:
        """Executa testes de pagamento concorrentes"""
        LOG.info(f"🚀 Iniciando {len(scenarios)} testes de pagamento com concorrência {concurrency}")

        connector = aiohttp.TCPConnector(limit=concurrency * 2)
        timeout = aiohttp.ClientTimeout(total=60)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            # Dividir scenarios em batches
            batch_size = concurrency
            for i in range(0, len(scenarios), batch_size):
                batch = scenarios[i:i + batch_size]

                LOG.info(f"📦 Processando batch {i//batch_size + 1}/{(len(scenarios) + batch_size - 1)//batch_size} ({len(batch)} testes)")

                # Executar batch em paralelo
                tasks = [self.test_payment_endpoint(session, scenario) for scenario in batch]
                batch_results = await asyncio.gather(*tasks, return_exceptions=True)

                # Processar resultados
                for result in batch_results:
                    if isinstance(result, PaymentTestResult):
                        self.results.append(result)
                        self.update_chain_stats(result)
                    else:
                        LOG.error(f"Erro no teste: {result}")

                # Pequena pausa entre batches
                await asyncio.sleep(0.5)

    def update_chain_stats(self, result: PaymentTestResult) -> None:
        """Atualiza estatísticas por chain"""
        chain_id = result.chain_id

        if chain_id not in self.chain_stats:
            chain_name = self.chains.get(chain_id, {}).get("name", chain_id)
            self.chain_stats[chain_id] = ChainTestStats(
                chain_id=chain_id,
                chain_name=chain_name,
                errors=defaultdict(int)
            )

        stats = self.chain_stats[chain_id]
        stats.total_tests += 1

        if result.success:
            stats.successful_tests += 1
        else:
            stats.failed_tests += 1
            error_key = result.error_message or "Unknown error"
            stats.errors[error_key] += 1

        # Atualizar tempo médio de resposta
        if stats.total_tests == 1:
            stats.avg_response_time = result.response_time
        else:
            stats.avg_response_time = (
                (stats.avg_response_time * (stats.total_tests - 1) + result.response_time)
                / stats.total_tests
            )

    def generate_report(self) -> str:
        """Gera relatório detalhado dos testes"""
        if not self.results:
            return "❌ Nenhum resultado de teste disponível"

        total_tests = len(self.results)
        successful_tests = sum(1 for r in self.results if r.success)
        failed_tests = total_tests - successful_tests
        success_rate = (successful_tests / total_tests) * 100

        avg_response_time = sum(r.response_time for r in self.results) / total_tests
        test_duration = self.end_time - self.start_time

        # Análise por token
        token_stats = defaultdict(lambda: {"success": 0, "total": 0, "avg_time": 0})
        for result in self.results:
            token = result.token_symbol
            token_stats[token]["total"] += 1
            if result.success:
                token_stats[token]["success"] += 1

            # Atualizar tempo médio
            current_avg = token_stats[token]["avg_time"]
            total = token_stats[token]["total"]
            token_stats[token]["avg_time"] = (
                (current_avg * (total - 1) + result.response_time) / total
            )

        # Relatório principal
        report = f"""
🚀 RELATÓRIO DE TESTE DE STRESS - PAGAMENTOS MULTI-CHAIN
================================================================================

📊 RESUMO EXECUTIVO:
   Total de Testes: {total_tests}
   Sucessos: {successful_tests} ({success_rate:.1f}%)
   Falhas: {failed_tests} ({100-success_rate:.1f}%)
   Tempo Médio: {avg_response_time:.3f}s
   Duração Total: {test_duration:.1f}s
   Throughput: {total_tests/test_duration:.1f} testes/s

💰 RESULTADOS POR TOKEN:
"""

        for token, stats in sorted(token_stats.items()):
            success_rate_token = (stats["success"] / stats["total"]) * 100
            report += f"   {token:8} | {stats['success']:3}/{stats['total']:3} ({success_rate_token:5.1f}%) | Tempo: {stats['avg_time']:.3f}s\n"

        report += f"\n🌐 RESULTADOS POR CHAIN:\n"

        for chain_id, stats in sorted(self.chain_stats.items(), key=lambda x: x[1].total_tests, reverse=True):
            success_rate_chain = (stats.successful_tests / stats.total_tests) * 100 if stats.total_tests > 0 else 0
            report += f"   {stats.chain_name:15} | {stats.successful_tests:3}/{stats.total_tests:3} ({success_rate_chain:5.1f}%) | Tempo: {stats.avg_response_time:.3f}s\n"

            # Mostrar principais erros se houver
            if stats.errors:
                top_errors = sorted(stats.errors.items(), key=lambda x: x[1], reverse=True)[:2]
                for error, count in top_errors:
                    report += f"     └─ {error[:50]}... ({count}x)\n"

        # Análise de performance
        report += f"\n⚡ ANÁLISE DE PERFORMANCE:\n"

        if success_rate >= 95:
            grade = "A+"
            status = "Excelente"
        elif success_rate >= 90:
            grade = "A"
            status = "Muito Bom"
        elif success_rate >= 80:
            grade = "B"
            status = "Bom"
        elif success_rate >= 70:
            grade = "C"
            status = "Regular"
        else:
            grade = "D"
            status = "Crítico"

        report += f"   Nota Geral: {grade} ({status})\n"

        if avg_response_time < 1.0:
            report += f"   ✅ Tempo de resposta excelente (<1s)\n"
        elif avg_response_time < 3.0:
            report += f"   ⚠️ Tempo de resposta aceitável (<3s)\n"
        else:
            report += f"   ❌ Tempo de resposta lento (>{avg_response_time:.1f}s)\n"

        if total_tests/test_duration > 5:
            report += f"   ✅ Throughput bom (>{total_tests/test_duration:.1f} req/s)\n"
        else:
            report += f"   ⚠️ Throughput baixo ({total_tests/test_duration:.1f} req/s)\n"

        # Recomendações
        report += f"\n💡 RECOMENDAÇÕES:\n"

        if success_rate < 95:
            report += f"   🔴 Taxa de sucesso baixa - verificar conectividade com RPCs\n"

        if avg_response_time > 2.0:
            report += f"   🟡 Implementar cache para preços de tokens\n"
            report += f"   🟡 Otimizar timeouts de RPC\n"

        report += f"   💡 Implementar circuit breaker para RPCs lentos\n"
        report += f"   💡 Adicionar monitoramento em tempo real\n"
        report += f"   💡 Configurar alertas para chains com alta taxa de erro\n"

        # Informações do sistema
        cpu_percent = psutil.cpu_percent()
        memory_percent = psutil.virtual_memory().percent

        report += f"\n🖥️ SISTEMA DURANTE TESTE:\n"
        report += f"   CPU: {cpu_percent}%\n"
        report += f"   Memória: {memory_percent}%\n"

        return report

    def save_results(self, filename: str = None) -> None:
        """Salva resultados em JSON"""
        if filename is None:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = f"payment_stress_results_{timestamp}.json"

        data = {
            "test_summary": {
                "total_tests": len(self.results),
                "successful_tests": sum(1 for r in self.results if r.success),
                "failed_tests": sum(1 for r in self.results if not r.success),
                "avg_response_time": sum(r.response_time for r in self.results) / len(self.results) if self.results else 0,
                "test_duration": self.end_time - self.start_time,
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
            },
            "chain_stats": {
                chain_id: {
                    "chain_name": stats.chain_name,
                    "total_tests": stats.total_tests,
                    "successful_tests": stats.successful_tests,
                    "failed_tests": stats.failed_tests,
                    "success_rate": (stats.successful_tests / stats.total_tests * 100) if stats.total_tests > 0 else 0,
                    "avg_response_time": stats.avg_response_time,
                    "errors": dict(stats.errors)
                }
                for chain_id, stats in self.chain_stats.items()
            },
            "individual_results": [
                {
                    "chain_id": r.chain_id,
                    "token_symbol": r.token_symbol,
                    "tx_hash": r.tx_hash,
                    "success": r.success,
                    "response_time": r.response_time,
                    "error_message": r.error_message,
                    "usd_amount": r.usd_amount,
                    "status_code": r.status_code
                }
                for r in self.results
            ]
        }

        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)

        LOG.info(f"📄 Resultados salvos em: {filename}")

async def main():
    """Função principal para executar testes"""
    # Configurações do teste
    num_tests = int(os.getenv("PAYMENT_TESTS", "50"))
    concurrency = int(os.getenv("PAYMENT_CONCURRENCY", "5"))
    base_url = os.getenv("PAYMENT_TEST_URL", "http://localhost:8000")

    LOG.info(f"🧪 Iniciando teste de stress para pagamentos")
    LOG.info(f"   Testes: {num_tests}")
    LOG.info(f"   Concorrência: {concurrency}")
    LOG.info(f"   URL: {base_url}")

    # Criar instância do teste
    tester = PaymentStressTest(base_url=base_url)

    # Gerar cenários de teste
    scenarios = tester.generate_test_scenarios(num_tests)
    LOG.info(f"📋 Gerados {len(scenarios)} cenários de teste")

    # Log das chains que serão testadas
    chains_to_test = set(s["chain_id"] for s in scenarios)
    LOG.info(f"🌐 Chains que serão testadas: {len(chains_to_test)}")
    for chain_id in sorted(chains_to_test):
        chain_name = tester.chains.get(chain_id, {}).get("name", chain_id)
        count = sum(1 for s in scenarios if s["chain_id"] == chain_id)
        LOG.info(f"   {chain_name}: {count} testes")

    # Executar testes
    tester.start_time = time.time()
    await tester.run_concurrent_tests(scenarios, concurrency)
    tester.end_time = time.time()

    # Gerar e exibir relatório
    report = tester.generate_report()
    print(report)

    # Salvar resultados
    tester.save_results()

    LOG.info("✅ Teste de stress de pagamentos concluído!")

if __name__ == "__main__":
    asyncio.run(main())