#!/usr/bin/env python3
"""
Script de Teste de Stress para Bot Telegram - Simulação de Usuários VIP
Este script simula múltiplos usuários falsos tentando entrar no grupo VIP simultaneamente
para testar a capacidade e performance do bot.
"""

import asyncio
import aiohttp
import time
import json
import logging
import random
import string
from typing import List, Dict, Any
from datetime import datetime
import statistics
import concurrent.futures
import psutil
import os
import sys
from dataclasses import dataclass
from threading import Thread, Event

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('stress_test.log'),
        logging.StreamHandler()
    ]
)

@dataclass
class TestConfig:
    """Configuração do teste de stress"""
    # Webhook URL do bot (ajustar conforme necessário)
    webhook_url: str = "http://localhost:8000/webhook"

    # Configurações de teste
    users_per_second: int = 100
    total_users: int = 1000
    batch_size: int = 10
    max_test_duration: int = 300  # 5 minutos

    # IDs para simulação (ajustar conforme seu setup)
    vip_group_id: int = -1002791988432
    test_admin_id: int = 123456789  # ID de um admin real para teste

    # Rate limiting
    max_concurrent_requests: int = 50
    delay_between_batches: float = 0.1

@dataclass
class TestResult:
    """Resultado de um teste individual"""
    user_id: int
    request_time: float
    response_time: float
    success: bool
    error_message: str = ""
    http_status: int = 0

class PerformanceMonitor:
    """Monitor de performance do sistema"""

    def __init__(self):
        self.cpu_usage = []
        self.memory_usage = []
        self.monitoring = False
        self.monitor_thread = None
        self.stop_event = Event()

    def start_monitoring(self):
        """Inicia o monitoramento de performance"""
        self.monitoring = True
        self.stop_event.clear()
        self.monitor_thread = Thread(target=self._monitor_loop)
        self.monitor_thread.start()
        logging.info("Monitoramento de performance iniciado")

    def stop_monitoring(self):
        """Para o monitoramento de performance"""
        self.monitoring = False
        self.stop_event.set()
        if self.monitor_thread:
            self.monitor_thread.join()
        logging.info("Monitoramento de performance parado")

    def _monitor_loop(self):
        """Loop de monitoramento executado em thread separada"""
        while not self.stop_event.wait(1.0):  # Monitora a cada 1 segundo
            try:
                cpu = psutil.cpu_percent(interval=None)
                memory = psutil.virtual_memory().percent
                self.cpu_usage.append(cpu)
                self.memory_usage.append(memory)
            except Exception as e:
                logging.error(f"Erro no monitoramento: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas de performance"""
        if not self.cpu_usage or not self.memory_usage:
            return {}

        return {
            "cpu": {
                "min": min(self.cpu_usage),
                "max": max(self.cpu_usage),
                "avg": statistics.mean(self.cpu_usage),
                "median": statistics.median(self.cpu_usage)
            },
            "memory": {
                "min": min(self.memory_usage),
                "max": max(self.memory_usage),
                "avg": statistics.mean(self.memory_usage),
                "median": statistics.median(self.memory_usage)
            }
        }

class StressTester:
    """Classe principal para execução do teste de stress"""

    def __init__(self, config: TestConfig):
        self.config = config
        self.session = None
        self.results: List[TestResult] = []
        self.monitor = PerformanceMonitor()
        self.start_time = None

    async def __aenter__(self):
        """Context manager entry"""
        connector = aiohttp.TCPConnector(
            limit=self.config.max_concurrent_requests,
            limit_per_host=self.config.max_concurrent_requests
        )
        timeout = aiohttp.ClientTimeout(total=30)
        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if self.session:
            await self.session.close()

    def generate_fake_user_id(self) -> int:
        """Gera um ID de usuário falso único"""
        return random.randint(1000000000, 9999999999)

    def generate_fake_username(self) -> str:
        """Gera um username falso"""
        return ''.join(random.choices(string.ascii_lowercase + string.digits, k=8))

    def generate_fake_update(self, user_id: int, username: str) -> Dict[str, Any]:
        """Gera um update falso do Telegram simulando join request"""
        return {
            "update_id": random.randint(1000000, 9999999),
            "chat_join_request": {
                "chat": {
                    "id": self.config.vip_group_id,
                    "type": "supergroup",
                    "title": "Grupo VIP Test"
                },
                "from": {
                    "id": user_id,
                    "is_bot": False,
                    "first_name": f"TestUser{user_id}",
                    "username": username,
                    "language_code": "pt-br"
                },
                "date": int(time.time()),
                "invite_link": {
                    "invite_link": f"https://t.me/+fake_invite_link_{random.randint(1000, 9999)}",
                    "creator": {
                        "id": self.config.test_admin_id,
                        "is_bot": True,
                        "first_name": "TestBot"
                    },
                    "creates_join_request": True,
                    "is_primary": False,
                    "is_revoked": False
                }
            }
        }

    async def send_fake_join_request(self, user_id: int, username: str) -> TestResult:
        """Envia uma requisição falsa de join request"""
        request_time = time.time()

        try:
            fake_update = self.generate_fake_update(user_id, username)

            async with self.session.post(
                self.config.webhook_url,
                json=fake_update,
                headers={"Content-Type": "application/json"}
            ) as response:
                response_time = time.time()

                # Ler resposta
                response_text = await response.text()

                return TestResult(
                    user_id=user_id,
                    request_time=request_time,
                    response_time=response_time - request_time,
                    success=response.status == 200,
                    http_status=response.status,
                    error_message="" if response.status == 200 else response_text[:200]
                )

        except asyncio.TimeoutError:
            return TestResult(
                user_id=user_id,
                request_time=request_time,
                response_time=time.time() - request_time,
                success=False,
                error_message="Timeout"
            )
        except Exception as e:
            return TestResult(
                user_id=user_id,
                request_time=request_time,
                response_time=time.time() - request_time,
                success=False,
                error_message=str(e)[:200]
            )

    async def run_batch(self, batch_users: List[tuple]) -> List[TestResult]:
        """Executa um lote de requisições em paralelo"""
        tasks = []
        for user_id, username in batch_users:
            task = self.send_fake_join_request(user_id, username)
            tasks.append(task)

        return await asyncio.gather(*tasks, return_exceptions=False)

    async def run_stress_test(self) -> Dict[str, Any]:
        """Executa o teste de stress completo"""
        logging.info(f"Iniciando teste de stress:")
        logging.info(f"   • Total de usuários: {self.config.total_users}")
        logging.info(f"   • Usuários por segundo: {self.config.users_per_second}")
        logging.info(f"   • Tamanho do lote: {self.config.batch_size}")
        logging.info(f"   • Duração máxima: {self.config.max_test_duration}s")

        self.start_time = time.time()
        self.monitor.start_monitoring()

        try:
            # Gerar usuários falsos
            fake_users = [
                (self.generate_fake_user_id(), self.generate_fake_username())
                for _ in range(self.config.total_users)
            ]

            # Processar em lotes
            for i in range(0, len(fake_users), self.config.batch_size):
                batch_start_time = time.time()
                batch = fake_users[i:i + self.config.batch_size]

                # Verificar se não excedeu tempo limite
                elapsed_time = time.time() - self.start_time
                if elapsed_time > self.config.max_test_duration:
                    logging.info(f"⏰ Tempo limite atingido ({self.config.max_test_duration}s)")
                    break

                # Executar lote
                batch_results = await self.run_batch(batch)
                self.results.extend(batch_results)

                # Estatísticas em tempo real
                successful = sum(1 for r in batch_results if r.success)
                failed = len(batch_results) - successful
                avg_response_time = statistics.mean([r.response_time for r in batch_results])

                logging.info(
                    f"Lote {i//self.config.batch_size + 1}: "
                    f"{successful}/{len(batch_results)} sucessos, "
                    f"tempo médio: {avg_response_time:.3f}s"
                )

                # Delay entre lotes para controle de rate
                batch_duration = time.time() - batch_start_time
                if batch_duration < self.config.delay_between_batches:
                    await asyncio.sleep(self.config.delay_between_batches - batch_duration)

        finally:
            self.monitor.stop_monitoring()

        return self.generate_report()

    def generate_report(self) -> Dict[str, Any]:
        """Gera relatório detalhado dos resultados"""
        if not self.results:
            return {"error": "Nenhum resultado para analisar"}

        # Estatísticas básicas
        total_requests = len(self.results)
        successful_requests = sum(1 for r in self.results if r.success)
        failed_requests = total_requests - successful_requests
        success_rate = (successful_requests / total_requests) * 100

        # Tempos de resposta
        response_times = [r.response_time for r in self.results if r.success]
        if response_times:
            avg_response_time = statistics.mean(response_times)
            min_response_time = min(response_times)
            max_response_time = max(response_times)
            median_response_time = statistics.median(response_times)
            p95_response_time = sorted(response_times)[int(len(response_times) * 0.95)]
        else:
            avg_response_time = min_response_time = max_response_time = median_response_time = p95_response_time = 0

        # Análise de erros
        error_analysis = {}
        for result in self.results:
            if not result.success:
                error_key = result.error_message or f"HTTP {result.http_status}"
                error_analysis[error_key] = error_analysis.get(error_key, 0) + 1

        # Taxa de requisições por segundo
        total_duration = time.time() - self.start_time if self.start_time else 1
        requests_per_second = total_requests / total_duration

        # Performance do sistema
        performance_stats = self.monitor.get_stats()

        report = {
            "test_summary": {
                "total_requests": total_requests,
                "successful_requests": successful_requests,
                "failed_requests": failed_requests,
                "success_rate_percent": round(success_rate, 2),
                "max_test_duration": round(total_duration, 2),
                "requests_per_second": round(requests_per_second, 2)
            },
            "response_times": {
                "average_ms": round(avg_response_time * 1000, 2),
                "minimum_ms": round(min_response_time * 1000, 2),
                "maximum_ms": round(max_response_time * 1000, 2),
                "median_ms": round(median_response_time * 1000, 2),
                "p95_ms": round(p95_response_time * 1000, 2)
            },
            "error_analysis": error_analysis,
            "system_performance": performance_stats,
            "recommendations": self.generate_recommendations(success_rate, avg_response_time, requests_per_second)
        }

        return report

    def generate_recommendations(self, success_rate: float, avg_response_time: float, rps: float) -> List[str]:
        """Gera recomendações baseadas nos resultados"""
        recommendations = []

        if success_rate < 95:
            recommendations.append("🔴 Taxa de sucesso baixa (<95%). Considere implementar rate limiting mais agressivo.")

        if avg_response_time > 2.0:
            recommendations.append("🟡 Tempo de resposta alto (>2s). Considere otimizar queries de banco de dados.")

        if rps < 50:
            recommendations.append("🟡 Throughput baixo (<50 req/s). Considere implementar cache ou otimizar handlers.")

        if not recommendations:
            recommendations.append("✅ Performance excelente! O bot está lidando bem com a carga.")

        # Recomendações específicas para o bot
        recommendations.extend([
            "💡 Implementar cache Redis para status VIP dos usuários",
            "💡 Usar connection pooling para o banco de dados",
            "💡 Implementar circuit breaker para APIs externas",
            "💡 Adicionar métricas em tempo real com Prometheus",
            "💡 Considerar usar webhooks assíncronos"
        ])

        return recommendations

async def main():
    """Função principal do teste de stress"""
    print("🤖 Teste de Stress - Bot Telegram VIP")
    print("=" * 50)

    # Configuração do teste
    config = TestConfig()

    # Perguntar configurações ao usuário
    try:
        users_input = input(f"Número total de usuários falsos [{config.total_users}]: ").strip()
        if users_input:
            config.total_users = int(users_input)

        rps_input = input(f"Usuários por segundo [{config.users_per_second}]: ").strip()
        if rps_input:
            config.users_per_second = int(rps_input)

        webhook_input = input(f"URL do webhook [{config.webhook_url}]: ").strip()
        if webhook_input:
            config.webhook_url = webhook_input

    except ValueError:
        print("❌ Entrada inválida, usando valores padrão")

    print(f"\n🎯 Configuração do teste:")
    print(f"   • Total de usuários: {config.total_users}")
    print(f"   • Usuários por segundo: {config.users_per_second}")
    print(f"   • URL do webhook: {config.webhook_url}")

    input("\nATENCAO: Este teste enviara muitas requisicoes ao seu bot. Pressione ENTER para continuar...")

    # Executar teste
    async with StressTester(config) as tester:
        try:
            report = await tester.run_stress_test()

            # Mostrar relatório
            print("\n" + "="*80)
            print("RELATORIO DE TESTE DE STRESS")
            print("="*80)

            print(f"\n📈 RESUMO:")
            for key, value in report["test_summary"].items():
                print(f"   • {key.replace('_', ' ').title()}: {value}")

            print(f"\nTEMPOS DE RESPOSTA:")
            for key, value in report["response_times"].items():
                print(f"   • {key.replace('_', ' ').title()}: {value}")

            if report["error_analysis"]:
                print(f"\n❌ ANÁLISE DE ERROS:")
                for error, count in report["error_analysis"].items():
                    print(f"   • {error}: {count} ocorrências")

            if report["system_performance"]:
                print(f"\n💻 PERFORMANCE DO SISTEMA:")
                perf = report["system_performance"]
                if "cpu" in perf:
                    print(f"   • CPU: {perf['cpu']['avg']:.1f}% (máx: {perf['cpu']['max']:.1f}%)")
                if "memory" in perf:
                    print(f"   • Memória: {perf['memory']['avg']:.1f}% (máx: {perf['memory']['max']:.1f}%)")

            print(f"\n💡 RECOMENDAÇÕES:")
            for rec in report["recommendations"]:
                print(f"   {rec}")

            # Salvar relatório
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            report_file = f"stress_test_report_{timestamp}.json"
            with open(report_file, 'w', encoding='utf-8') as f:
                json.dump(report, f, indent=2, ensure_ascii=False)

            print(f"\n💾 Relatório salvo em: {report_file}")
            print(f"📋 Log detalhado em: stress_test.log")

        except KeyboardInterrupt:
            print("\nTeste interrompido pelo usuario")
        except Exception as e:
            print(f"\n❌ Erro durante o teste: {e}")
            logging.error(f"Erro no teste: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(main())