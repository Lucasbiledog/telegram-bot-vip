# circuit_breaker.py
import asyncio
import time
from typing import Callable, Any, Dict, Optional
from enum import Enum
import logging

LOG = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"      # Normal, permitindo requisições
    OPEN = "open"          # Falhas detectadas, bloqueando requisições
    HALF_OPEN = "half_open"  # Testando recuperação

class CircuitBreakerError(Exception):
    """Erro lançado quando circuit breaker está aberto"""
    pass

class CircuitBreaker:
    """
    Implementação do padrão Circuit Breaker para proteger o sistema
    contra cascata de falhas em serviços externos
    """

    def __init__(self,
                 failure_threshold: int = 5,
                 recovery_timeout: int = 60,
                 expected_exception: Exception = Exception,
                 name: str = "default"):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name

        # Estado do circuit breaker
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.success_count_in_half_open = 0
        self.required_successes_to_close = 3  # Sucessos necessários para fechar novamente

        # Estatísticas
        self.total_requests = 0
        self.total_failures = 0
        self.total_successes = 0
        self.total_circuit_open_time = 0
        self.circuit_opened_at = None

    def _should_allow_request(self) -> bool:
        """Determina se a requisição deve ser permitida"""
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            # Verificar se é hora de tentar novamente (half-open)
            if (self.last_failure_time and
                time.time() - self.last_failure_time >= self.recovery_timeout):
                self.state = CircuitState.HALF_OPEN
                self.success_count_in_half_open = 0
                LOG.info(f"Circuit breaker '{self.name}' mudou para HALF_OPEN")
                return True
            return False

        if self.state == CircuitState.HALF_OPEN:
            # No estado half-open, permitir apenas uma requisição por vez
            return True

        return False

    def _record_success(self):
        """Registra uma operação bem-sucedida"""
        self.total_requests += 1
        self.total_successes += 1
        self.failure_count = 0

        if self.state == CircuitState.HALF_OPEN:
            self.success_count_in_half_open += 1
            if self.success_count_in_half_open >= self.required_successes_to_close:
                self.state = CircuitState.CLOSED
                self.success_count_in_half_open = 0

                # Calcular tempo que esteve aberto
                if self.circuit_opened_at:
                    self.total_circuit_open_time += time.time() - self.circuit_opened_at
                    self.circuit_opened_at = None

                LOG.info(f"Circuit breaker '{self.name}' FECHADO após recuperação")

    def _record_failure(self, exception: Exception):
        """Registra uma falha"""
        self.total_requests += 1
        self.total_failures += 1
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Se falhou no half-open, voltar para open
            self.state = CircuitState.OPEN
            LOG.warning(f"Circuit breaker '{self.name}' voltou para OPEN após falha em HALF_OPEN: {exception}")

        elif (self.state == CircuitState.CLOSED and
              self.failure_count >= self.failure_threshold):
            # Abrir o circuit breaker
            self.state = CircuitState.OPEN
            self.circuit_opened_at = time.time()
            LOG.error(f"Circuit breaker '{self.name}' ABERTO após {self.failure_count} falhas: {exception}")

    async def call(self, func: Callable, *args, **kwargs) -> Any:
        """Executa função com proteção do circuit breaker"""
        if not self._should_allow_request():
            raise CircuitBreakerError(
                f"Circuit breaker '{self.name}' está OPEN. "
                f"Tentativa em {self.recovery_timeout - (time.time() - self.last_failure_time):.1f}s"
            )

        try:
            # Executar a função
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Sucesso
            self._record_success()
            return result

        except self.expected_exception as e:
            # Falha esperada
            self._record_failure(e)
            raise
        except Exception as e:
            # Falha inesperada - também contar como falha
            self._record_failure(e)
            raise

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas do circuit breaker"""
        current_time = time.time()
        total_open_time = self.total_circuit_open_time

        if self.circuit_opened_at:
            total_open_time += current_time - self.circuit_opened_at

        return {
            "name": self.name,
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
            "total_requests": self.total_requests,
            "total_successes": self.total_successes,
            "total_failures": self.total_failures,
            "success_rate": (self.total_successes / self.total_requests * 100) if self.total_requests > 0 else 0,
            "total_open_time_seconds": total_open_time,
            "last_failure_time": self.last_failure_time,
            "time_until_retry": max(0, self.recovery_timeout - (current_time - self.last_failure_time)) if self.last_failure_time else 0
        }

    def reset(self):
        """Reseta o circuit breaker para estado inicial"""
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self.success_count_in_half_open = 0
        if self.circuit_opened_at:
            self.total_circuit_open_time += time.time() - self.circuit_opened_at
            self.circuit_opened_at = None
        LOG.info(f"Circuit breaker '{self.name}' foi resetado")

# =========================
# CIRCUIT BREAKERS PRÉ-CONFIGURADOS
# =========================

class CircuitBreakerManager:
    """Gerenciador de circuit breakers para diferentes serviços"""

    def __init__(self):
        self.breakers: Dict[str, CircuitBreaker] = {}

    def get_breaker(self, name: str, **kwargs) -> CircuitBreaker:
        """Obtém ou cria um circuit breaker"""
        if name not in self.breakers:
            self.breakers[name] = CircuitBreaker(name=name, **kwargs)
        return self.breakers[name]

    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Retorna estatísticas de todos os circuit breakers"""
        return {name: breaker.get_stats() for name, breaker in self.breakers.items()}

    def reset_breaker(self, name: str) -> bool:
        """Reseta um circuit breaker específico"""
        if name in self.breakers:
            self.breakers[name].reset()
            return True
        return False

    def reset_all(self):
        """Reseta todos os circuit breakers"""
        for breaker in self.breakers.values():
            breaker.reset()
        LOG.info("Todos os circuit breakers foram resetados")

# Instância global do gerenciador
breaker_manager = CircuitBreakerManager()

# Circuit breakers pré-configurados para serviços específicos
def get_database_breaker() -> CircuitBreaker:
    """Circuit breaker para operações de banco de dados"""
    return breaker_manager.get_breaker(
        "database",
        failure_threshold=10,
        recovery_timeout=30,
        expected_exception=Exception
    )

def get_telegram_api_breaker() -> CircuitBreaker:
    """Circuit breaker para Telegram API"""
    return breaker_manager.get_breaker(
        "telegram_api",
        failure_threshold=5,
        recovery_timeout=60,
        expected_exception=Exception
    )

def get_coingecko_breaker() -> CircuitBreaker:
    """Circuit breaker para CoinGecko API"""
    return breaker_manager.get_breaker(
        "coingecko",
        failure_threshold=3,
        recovery_timeout=120,  # 2 minutos para APIs externas
        expected_exception=Exception
    )

def get_blockchain_rpc_breaker() -> CircuitBreaker:
    """Circuit breaker para chamadas RPC blockchain"""
    return breaker_manager.get_breaker(
        "blockchain_rpc",
        failure_threshold=5,
        recovery_timeout=90,
        expected_exception=Exception
    )

def get_payment_validation_breaker() -> CircuitBreaker:
    """Circuit breaker para validação de pagamentos"""
    return breaker_manager.get_breaker(
        "payment_validation",
        failure_threshold=3,
        recovery_timeout=60,
        expected_exception=Exception
    )

# =========================
# DECORATORS PARA FACILITAR USO
# =========================

def with_circuit_breaker(breaker_name: str, **breaker_kwargs):
    """Decorator para aplicar circuit breaker em funções"""
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            breaker = breaker_manager.get_breaker(breaker_name, **breaker_kwargs)
            return await breaker.call(func, *args, **kwargs)

        def sync_wrapper(*args, **kwargs):
            breaker = breaker_manager.get_breaker(breaker_name, **breaker_kwargs)
            return asyncio.run(breaker.call(func, *args, **kwargs))

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator

def with_database_protection(func):
    """Decorator específico para proteção de operações de banco"""
    @with_circuit_breaker("database", failure_threshold=10, recovery_timeout=30)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)
    return wrapper

def with_api_protection(api_name: str):
    """Decorator específico para proteção de APIs externas"""
    def decorator(func):
        @with_circuit_breaker(f"api_{api_name}", failure_threshold=3, recovery_timeout=120)
        async def wrapper(*args, **kwargs):
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# =========================
# FUNÇÕES UTILITÁRIAS
# =========================

async def health_check_with_breakers() -> Dict[str, Any]:
    """Health check que inclui status dos circuit breakers"""
    return {
        "circuit_breakers": breaker_manager.get_all_stats(),
        "total_breakers": len(breaker_manager.breakers),
        "open_breakers": [
            name for name, stats in breaker_manager.get_all_stats().items()
            if stats["state"] == "open"
        ]
    }

def get_service_health(service_name: str) -> Dict[str, Any]:
    """Verifica saúde de um serviço específico via seu circuit breaker"""
    if service_name in breaker_manager.breakers:
        stats = breaker_manager.breakers[service_name].get_stats()
        return {
            "service": service_name,
            "healthy": stats["state"] != "open",
            "stats": stats
        }
    return {
        "service": service_name,
        "healthy": True,  # Se não tem breaker, assume healthy
        "stats": {"message": "No circuit breaker configured"}
    }