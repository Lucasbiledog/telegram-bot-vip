#!/usr/bin/env python3
"""
⚙️ Configurações para Teste de Stress de Pagamentos
Configurações específicas para testar diferentes moedas e cenários
"""

import os
from typing import Dict, List, Any

# =========================
# Configurações Básicas
# =========================

# URL da API de pagamentos (ajustar conforme necessário)
PAYMENT_API_URL = os.getenv("PAYMENT_API_URL", "http://localhost:8000")

# Wallet de teste (usar wallet de teste, não produção!)
TEST_WALLET_ADDRESS = os.getenv("TEST_WALLET_ADDRESS", "0x742d35Cc6634C0532925a3b8d0")

# Configurações de teste
DEFAULT_TEST_CONFIG = {
    "max_concurrent_requests": 20,
    "request_timeout": 30,
    "retry_attempts": 3,
    "batch_size": 10,
    "delay_between_batches": 0.5
}

# =========================
# Chains e Tokens para Teste
# =========================

# Chains principais para teste intensivo
PRIORITY_CHAINS = [
    "0x1",    # Ethereum
    "0x38",   # BSC
    "0x89",   # Polygon
    "0xa4b1", # Arbitrum
    "0xa",    # Optimism
    "0x2105", # Base
    "0xa86a", # Avalanche
]

# Chains secundárias para teste básico
SECONDARY_CHAINS = [
    "0x144",   # zkSync Era
    "0xe708",  # Linea
    "0x13e31", # Blast
    "0xa4ec",  # Celo
    "0x1388",  # Mantle
    "0xcc",    # opBNB
    "0x82750", # Scroll
    "0xfa",    # Fantom
    "0x64",    # Gnosis
    "0x507",   # Moonbeam
    "0x505",   # Moonriver
    "0x19",    # Cronos
    "0x7a69",  # Zora
    "0x1b3",   # Ape Chain
    "0x2710",  # Morph
]

# Tokens críticos para teste (stablecoins principalmente)
CRITICAL_TOKENS = {
    "0x1": [  # Ethereum
        {
            "address": "0xa0b86991c31cc170c8b9e71b51e1a53af4e9b8c9e",
            "symbol": "USDC",
            "decimals": 6,
            "priority": "high"
        },
        {
            "address": "0xdac17f958d2ee523a2206206994597c13d831ec7",
            "symbol": "USDT",
            "decimals": 6,
            "priority": "high"
        }
    ],
    "0x38": [  # BSC
        {
            "address": "0x8ac76a51cc950d9822d68b83fe1ad97b32cd580d",
            "symbol": "USDC",
            "decimals": 18,
            "priority": "high"
        },
        {
            "address": "0x55d398326f99059ff775485246999027b3197955",
            "symbol": "USDT",
            "decimals": 18,
            "priority": "high"
        },
        {
            "address": "0x7130d2a12b9bcbfae4f2634d864a1ee1ce3ead9c",
            "symbol": "BTCB",
            "decimals": 18,
            "priority": "medium"
        }
    ],
    "0x89": [  # Polygon
        {
            "address": "0x2791bca1f2de4661ed88a30c99a7a9449aa84174",
            "symbol": "USDC",
            "decimals": 6,
            "priority": "high"
        },
        {
            "address": "0xc2132d05d31c914a87c6611c10748aeb04b58e8f",
            "symbol": "USDT",
            "decimals": 6,
            "priority": "high"
        }
    ],
    "0xa4b1": [  # Arbitrum
        {
            "address": "0xaf88d065e77c8cc2239327c5edb3a432268e5831",
            "symbol": "USDC",
            "decimals": 6,
            "priority": "high"
        }
    ],
    "0xa": [  # Optimism
        {
            "address": "0x0b2c639c533813f4aa9d7837caf62653d097ff85",
            "symbol": "USDC",
            "decimals": 6,
            "priority": "high"
        }
    ],
    "0x2105": [  # Base
        {
            "address": "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913",
            "symbol": "USDC",
            "decimals": 6,
            "priority": "high"
        }
    ]
}

# =========================
# Cenários de Teste Específicos
# =========================

PAYMENT_TEST_SCENARIOS = {
    # Teste básico de conectividade
    "connectivity": {
        "name": "Connectivity Test",
        "description": "Testa conectividade básica com todas as chains",
        "chains": PRIORITY_CHAINS + SECONDARY_CHAINS,
        "tokens_per_chain": 1,
        "tests_per_token": 1,
        "concurrency": 5,
        "amount_range": [1.0, 2.0]  # USD
    },

    # Teste intensivo de stablecoins
    "stablecoin_stress": {
        "name": "Stablecoin Stress Test",
        "description": "Testa USDC/USDT em todas as chains que suportam",
        "chains": ["0x1", "0x38", "0x89", "0xa4b1", "0xa", "0x2105"],
        "token_filter": ["USDC", "USDT"],
        "tests_per_token": 10,
        "concurrency": 15,
        "amount_range": [0.5, 5.0]
    },

    # Teste de carga de pagamentos nativos
    "native_load": {
        "name": "Native Token Load Test",
        "description": "Testa tokens nativos (ETH, BNB, MATIC, etc.)",
        "chains": PRIORITY_CHAINS,
        "native_only": True,
        "tests_per_chain": 15,
        "concurrency": 12,
        "amount_range": [0.1, 10.0]
    },

    # Teste de alta concorrência
    "high_concurrency": {
        "name": "High Concurrency Test",
        "description": "Testa sistema sob alta concorrência",
        "chains": ["0x38", "0x89"],  # Chains mais rápidas
        "tests_per_chain": 50,
        "concurrency": 30,
        "amount_range": [1.0, 3.0]
    },

    # Teste de valores diversos
    "amount_diversity": {
        "name": "Amount Diversity Test",
        "description": "Testa diferentes valores de pagamento",
        "chains": PRIORITY_CHAINS[:3],
        "tests_per_chain": 20,
        "concurrency": 8,
        "amount_range": [0.01, 100.0],  # Faixa muito ampla
        "amount_distribution": "logarithmic"
    },

    # Teste de recuperação de erro
    "error_recovery": {
        "name": "Error Recovery Test",
        "description": "Testa recuperação de erros e timeouts",
        "chains": SECONDARY_CHAINS[:5],
        "tests_per_chain": 10,
        "concurrency": 20,  # Alta concorrência para forçar erros
        "amount_range": [1.0, 2.0],
        "timeout": 5  # Timeout baixo para forçar falhas
    },

    # Teste completo de produção
    "production_readiness": {
        "name": "Production Readiness Test",
        "description": "Teste completo simulando carga de produção",
        "chains": PRIORITY_CHAINS + SECONDARY_CHAINS,
        "tests_per_chain": 25,
        "concurrency": 20,
        "amount_range": [0.1, 50.0],
        "duration_minutes": 30
    }
}

# =========================
# Valores de Teste Realísticos
# =========================

# Distribuição de valores baseada em dados reais de uso
REALISTIC_AMOUNT_DISTRIBUTION = {
    # 50% dos pagamentos são pequenos (planos básicos)
    "small": {
        "weight": 0.5,
        "range": [0.05, 1.99],
        "description": "Planos básicos (30-60 dias)"
    },
    # 30% são médios (planos intermediários)
    "medium": {
        "weight": 0.3,
        "range": [2.00, 4.99],
        "description": "Planos intermediários (180 dias)"
    },
    # 15% são altos (planos anuais)
    "large": {
        "weight": 0.15,
        "range": [5.00, 19.99],
        "description": "Planos anuais"
    },
    # 5% são muito altos (múltiplas contas ou premium)
    "premium": {
        "weight": 0.05,
        "range": [20.00, 100.00],
        "description": "Múltiplas contas ou premium"
    }
}

# =========================
# Métricas de Performance Esperadas
# =========================

PERFORMANCE_BENCHMARKS = {
    "excellent": {
        "success_rate": 99.5,
        "avg_response_time": 1.0,
        "p95_response_time": 2.0,
        "throughput_per_second": 50,
        "grade": "A+"
    },
    "good": {
        "success_rate": 98.0,
        "avg_response_time": 2.0,
        "p95_response_time": 4.0,
        "throughput_per_second": 25,
        "grade": "A"
    },
    "acceptable": {
        "success_rate": 95.0,
        "avg_response_time": 3.0,
        "p95_response_time": 6.0,
        "throughput_per_second": 15,
        "grade": "B"
    },
    "poor": {
        "success_rate": 90.0,
        "avg_response_time": 5.0,
        "p95_response_time": 10.0,
        "throughput_per_second": 8,
        "grade": "C"
    },
    "critical": {
        "success_rate": 80.0,
        "avg_response_time": 10.0,
        "p95_response_time": 20.0,
        "throughput_per_second": 3,
        "grade": "D"
    }
}

# =========================
# Configurações por Ambiente
# =========================

ENVIRONMENT_CONFIGS = {
    "development": {
        "base_url": "http://localhost:8000",
        "max_concurrency": 10,
        "timeout": 30,
        "retry_attempts": 2
    },
    "staging": {
        "base_url": "https://staging-bot.example.com",
        "max_concurrency": 20,
        "timeout": 15,
        "retry_attempts": 3
    },
    "production": {
        "base_url": "https://bot.example.com",
        "max_concurrency": 30,
        "timeout": 10,
        "retry_attempts": 1,
        "warning": "⚠️ CUIDADO: Ambiente de produção!"
    }
}

# =========================
# Funções Utilitárias
# =========================

def get_environment_config(env: str = "development") -> Dict[str, Any]:
    """Obtém configuração para ambiente específico"""
    return ENVIRONMENT_CONFIGS.get(env, ENVIRONMENT_CONFIGS["development"])

def get_scenario_config(scenario: str) -> Dict[str, Any]:
    """Obtém configuração para cenário específico"""
    return PAYMENT_TEST_SCENARIOS.get(scenario, {})

def get_chains_for_priority(priority: str = "high") -> List[str]:
    """Obtém lista de chains baseada na prioridade"""
    if priority == "high":
        return PRIORITY_CHAINS
    elif priority == "medium":
        return PRIORITY_CHAINS + SECONDARY_CHAINS[:5]
    else:
        return PRIORITY_CHAINS + SECONDARY_CHAINS

def get_tokens_for_chain(chain_id: str, priority: str = "high") -> List[Dict]:
    """Obtém tokens para uma chain específica baseado na prioridade"""
    tokens = CRITICAL_TOKENS.get(chain_id, [])
    if priority == "high":
        return [t for t in tokens if t.get("priority") == "high"]
    return tokens

def calculate_benchmark_grade(metrics: Dict[str, float]) -> str:
    """Calcula nota baseada nas métricas de performance"""
    success_rate = metrics.get("success_rate", 0)
    avg_response_time = metrics.get("avg_response_time", 999)

    for grade, benchmark in PERFORMANCE_BENCHMARKS.items():
        if (success_rate >= benchmark["success_rate"] and
            avg_response_time <= benchmark["avg_response_time"]):
            return benchmark["grade"]

    return "F"

# =========================
# Validações
# =========================

def validate_test_config(config: Dict[str, Any]) -> List[str]:
    """Valida configuração de teste e retorna lista de erros"""
    errors = []

    if not config.get("chains"):
        errors.append("Lista de chains não pode estar vazia")

    concurrency = config.get("concurrency", 0)
    if concurrency <= 0 or concurrency > 50:
        errors.append("Concorrência deve estar entre 1 e 50")

    amount_range = config.get("amount_range", [])
    if len(amount_range) != 2 or amount_range[0] >= amount_range[1]:
        errors.append("Range de valores inválido")

    return errors

# =========================
# Configuração Dinâmica
# =========================

def create_custom_scenario(name: str, **kwargs) -> Dict[str, Any]:
    """Cria cenário customizado baseado nos parâmetros"""
    scenario = {
        "name": name,
        "description": kwargs.get("description", f"Cenário customizado: {name}"),
        "chains": kwargs.get("chains", PRIORITY_CHAINS[:3]),
        "tests_per_chain": kwargs.get("tests_per_chain", 10),
        "concurrency": kwargs.get("concurrency", 10),
        "amount_range": kwargs.get("amount_range", [1.0, 3.0])
    }

    # Validar configuração
    errors = validate_test_config(scenario)
    if errors:
        raise ValueError(f"Configuração inválida: {', '.join(errors)}")

    return scenario

# =========================
# Configuração de Logs
# =========================

LOGGING_CONFIG = {
    "level": "INFO",
    "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    "handlers": {
        "file": {
            "filename": "payment_stress.log",
            "max_bytes": 10 * 1024 * 1024,  # 10MB
            "backup_count": 5
        },
        "console": {
            "enabled": True
        }
    }
}

if __name__ == "__main__":
    # Teste das configurações
    print("⚙️ Teste das Configurações de Pagamento\n")

    print(f"📋 Cenários disponíveis: {len(PAYMENT_TEST_SCENARIOS)}")
    for name, config in PAYMENT_TEST_SCENARIOS.items():
        print(f"   - {name}: {config['description']}")

    print(f"\n🌐 Chains de prioridade alta: {len(PRIORITY_CHAINS)}")
    print(f"🌐 Chains secundárias: {len(SECONDARY_CHAINS)}")
    print(f"💰 Tokens críticos configurados: {sum(len(tokens) for tokens in CRITICAL_TOKENS.values())}")

    # Teste de validação
    test_config = {
        "chains": ["0x1", "0x38"],
        "concurrency": 10,
        "amount_range": [1.0, 5.0]
    }

    errors = validate_test_config(test_config)
    print(f"\n✅ Teste de validação: {'Passou' if not errors else f'Falhou - {errors}'}")