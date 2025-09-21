"""
Configurações para Testes de Stress do Bot Telegram
Ajuste estes valores conforme necessário para seu ambiente
"""

import os
from dataclasses import dataclass
from typing import List, Dict, Any

@dataclass
class StressTestConfig:
    """Configuração completa para testes de stress"""

    # === Configurações de Teste ===
    # Número total de usuários falsos a serem testados
    total_users: int = 1000

    # Usuários por segundo (rate limiting)
    users_per_second: int = 100

    # Tamanho do lote para processamento paralelo
    batch_size: int = 10

    # Duração máxima do teste em segundos
    max_test_duration: int = 300  # 5 minutos

    # Delay entre lotes (para controle fino de rate)
    delay_between_batches: float = 0.1

    # Máximo de requisições concorrentes
    max_concurrent_requests: int = 50

    # === URLs e Endpoints ===
    # URL do webhook do bot (ajustar para seu ambiente)
    webhook_url: str = "http://localhost:8000/webhook"

    # URL da API local do bot (se houver)
    api_base_url: str = "http://localhost:8000"

    # === IDs do Telegram ===
    # ID do grupo VIP (ajustar para o seu grupo)
    vip_group_id: int = -1002791988432

    # ID do grupo FREE (se houver)
    free_group_id: int = -1001234567890

    # ID de um admin real para simulações
    test_admin_id: int = 123456789

    # === Cenários de Teste ===
    test_scenarios: List[Dict[str, Any]] = None

    def __post_init__(self):
        if self.test_scenarios is None:
            self.test_scenarios = [
                {
                    "name": "Carga Baixa",
                    "users": 100,
                    "users_per_second": 10,
                    "description": "Teste básico com carga baixa"
                },
                {
                    "name": "Carga Média",
                    "users": 500,
                    "users_per_second": 50,
                    "description": "Teste com carga média"
                },
                {
                    "name": "Carga Alta",
                    "users": 1000,
                    "users_per_second": 100,
                    "description": "Teste com carga alta"
                },
                {
                    "name": "Stress Extremo",
                    "users": 2000,
                    "users_per_second": 200,
                    "description": "Teste extremo de stress"
                },
                {
                    "name": "Spike Test",
                    "users": 1500,
                    "users_per_second": 300,
                    "description": "Teste de pico repentino"
                }
            ]

# Configurações específicas por ambiente
ENVIRONMENTS = {
    "local": {
        "webhook_url": "http://localhost:8000/webhook",
        "api_base_url": "http://localhost:8000",
        "max_concurrent_requests": 50,
    },
    "development": {
        "webhook_url": "https://your-dev-bot.herokuapp.com/webhook",
        "api_base_url": "https://your-dev-bot.herokuapp.com",
        "max_concurrent_requests": 30,
    },
    "production": {
        "webhook_url": "https://your-prod-bot.com/webhook",
        "api_base_url": "https://your-prod-bot.com",
        "max_concurrent_requests": 100,
        # CUIDADO: Testes em produção devem ser limitados!
        "total_users": 50,
        "users_per_second": 5,
    }
}

# Thresholds para análise de performance
PERFORMANCE_THRESHOLDS = {
    "response_time": {
        "excellent": 0.5,  # < 500ms
        "good": 1.0,       # < 1s
        "acceptable": 2.0,  # < 2s
        "poor": 5.0,       # < 5s
        # > 5s = crítico
    },
    "success_rate": {
        "excellent": 99.5,  # > 99.5%
        "good": 99.0,       # > 99%
        "acceptable": 95.0,  # > 95%
        "poor": 90.0,       # > 90%
        # < 90% = crítico
    },
    "throughput": {
        "excellent": 100,   # > 100 req/s
        "good": 50,         # > 50 req/s
        "acceptable": 20,   # > 20 req/s
        "poor": 10,         # > 10 req/s
        # < 10 req/s = crítico
    },
    "system": {
        "cpu_warning": 70,     # % CPU
        "cpu_critical": 90,    # % CPU
        "memory_warning": 80,   # % Memory
        "memory_critical": 95,  # % Memory
    }
}

# Mensagens de teste simuladas
FAKE_MESSAGES = [
    "Olá, quero entrar no VIP!",
    "Como faço para acessar o conteúdo premium?",
    "Preciso de ajuda com o pagamento",
    "O bot não está respondendo",
    "Quando sai o próximo pack?",
    "Como renovar minha assinatura?",
    "Qual o preço do VIP?",
    "O link não está funcionando",
    "Problemas com o pagamento",
    "Quero cancelar minha assinatura"
]

# Nomes falsos para usuários de teste
FAKE_NAMES = [
    "TestUser", "FakeUser", "BotTest", "UserTest", "TestAccount",
    "DemoUser", "SampleUser", "MockUser", "TestProfile", "DevUser"
]

# Templates de relatório
REPORT_TEMPLATE = {
    "test_info": {
        "start_time": None,
        "end_time": None,
        "duration_seconds": None,
        "environment": None,
        "bot_version": None
    },
    "configuration": {
        "total_users": None,
        "users_per_second": None,
        "batch_size": None,
        "concurrent_requests": None
    },
    "results": {
        "total_requests": None,
        "successful_requests": None,
        "failed_requests": None,
        "success_rate_percent": None,
        "average_response_time_ms": None,
        "p95_response_time_ms": None,
        "throughput_rps": None
    },
    "system_performance": {
        "max_cpu_percent": None,
        "max_memory_percent": None,
        "average_cpu_percent": None,
        "average_memory_percent": None
    },
    "error_analysis": {},
    "recommendations": []
}

def get_config_for_environment(env: str = "local") -> StressTestConfig:
    """Retorna configuração para um ambiente específico"""
    config = StressTestConfig()

    if env in ENVIRONMENTS:
        env_config = ENVIRONMENTS[env]
        for key, value in env_config.items():
            if hasattr(config, key):
                setattr(config, key, value)

    return config

def get_scenario_config(scenario_name: str) -> Dict[str, Any]:
    """Retorna configuração para um cenário específico"""
    config = StressTestConfig()

    for scenario in config.test_scenarios:
        if scenario["name"] == scenario_name:
            return scenario

    return config.test_scenarios[0]  # Retorna primeiro se não encontrar

def validate_config(config: StressTestConfig) -> List[str]:
    """Valida configuração e retorna lista de warnings/erros"""
    warnings = []

    # Validações básicas
    if config.total_users > 5000:
        warnings.append("⚠️ Número muito alto de usuários (>5000) pode sobrecarregar o sistema")

    if config.users_per_second > 500:
        warnings.append("⚠️ Rate muito alto (>500/s) pode ser limitado pelo Telegram")

    if config.max_concurrent_requests > 200:
        warnings.append("⚠️ Muitas requisições concorrentes podem esgotar recursos")

    if config.batch_size > config.max_concurrent_requests:
        warnings.append("⚠️ Batch size maior que max concurrent requests")

    # Validações de URLs
    if not config.webhook_url.startswith(('http://', 'https://')):
        warnings.append("❌ URL do webhook inválida")

    # Validações de performance esperada
    estimated_duration = config.total_users / config.users_per_second
    if estimated_duration > config.max_test_duration:
        warnings.append(f"⚠️ Duração estimada ({estimated_duration:.0f}s) excede limite ({config.max_test_duration}s)")

    return warnings

# Funções utilitárias
def print_config_summary(config: StressTestConfig):
    """Imprime resumo da configuração"""
    print("Configuracao do Teste de Stress")
    print("=" * 50)
    print(f"Total de usuarios: {config.total_users:,}")
    print(f"Usuarios por segundo: {config.users_per_second}")
    print(f"Tamanho do lote: {config.batch_size}")
    print(f"Webhook URL: {config.webhook_url}")
    print(f"Duracao maxima: {config.max_test_duration}s")
    print(f"Requisicoes concorrentes: {config.max_concurrent_requests}")

    # Estimativas
    estimated_duration = config.total_users / config.users_per_second
    print(f"\nEstimativas:")
    print(f"   Duracao esperada: {estimated_duration:.1f}s")
    print(f"   Batches totais: {config.total_users // config.batch_size}")

    # Warnings
    warnings = validate_config(config)
    if warnings:
        print(f"\nAvisos:")
        for warning in warnings:
            print(f"   {warning}")

def get_optimized_config_for_target_rps(target_rps: int) -> StressTestConfig:
    """Retorna configuração otimizada para um RPS alvo"""
    config = StressTestConfig()

    # Ajustar configurações baseado no RPS alvo
    config.users_per_second = target_rps
    config.batch_size = min(20, max(5, target_rps // 10))
    config.max_concurrent_requests = min(100, max(10, target_rps // 2))
    config.delay_between_batches = max(0.01, 1.0 / target_rps)

    # Ajustar total de usuários para teste de 60 segundos
    config.total_users = target_rps * 60

    return config