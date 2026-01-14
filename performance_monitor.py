"""
Sistema de Monitoramento de Performance em Tempo Real
Monitora métricas críticas do bot durante testes de stress e operação normal
"""

import asyncio
import time
import logging
import json
import psutil
import threading
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict
from collections import deque, defaultdict
import weakref

@dataclass
class MetricPoint:
    """Ponto de métrica com timestamp"""
    timestamp: float
    value: float
    tags: Dict[str, str] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = {}

class MetricsCollector:
    """Coletor de métricas em tempo real"""

    def __init__(self, max_points: int = 1000):
        self.max_points = max_points
        self.metrics: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_points))
        self.counters: Dict[str, int] = defaultdict(int)
        self.gauges: Dict[str, float] = defaultdict(float)
        self._lock = threading.Lock()

    def record_metric(self, name: str, value: float, tags: Dict[str, str] = None):
        """Registra uma métrica"""
        with self._lock:
            point = MetricPoint(timestamp=time.time(), value=value, tags=tags or {})
            self.metrics[name].append(point)

    def increment_counter(self, name: str, tags: Dict[str, str] = None):
        """Incrementa um contador"""
        counter_key = f"{name}:{json.dumps(tags or {}, sort_keys=True)}"
        with self._lock:
            self.counters[counter_key] += 1

    def set_gauge(self, name: str, value: float, tags: Dict[str, str] = None):
        """Define valor de um gauge"""
        gauge_key = f"{name}:{json.dumps(tags or {}, sort_keys=True)}"
        with self._lock:
            self.gauges[gauge_key] = value

    def get_metric_stats(self, name: str, window_seconds: int = 60) -> Dict[str, float]:
        """Calcula estatísticas de uma métrica em uma janela de tempo"""
        with self._lock:
            if name not in self.metrics:
                return {}

            cutoff_time = time.time() - window_seconds
            values = [
                point.value for point in self.metrics[name]
                if point.timestamp >= cutoff_time
            ]

            if not values:
                return {}

            return {
                "count": len(values),
                "min": min(values),
                "max": max(values),
                "avg": sum(values) / len(values),
                "sum": sum(values)
            }

    def get_counter_value(self, name: str, tags: Dict[str, str] = None) -> int:
        """Obtém valor de um contador"""
        counter_key = f"{name}:{json.dumps(tags or {}, sort_keys=True)}"
        with self._lock:
            return self.counters.get(counter_key, 0)

    def get_gauge_value(self, name: str, tags: Dict[str, str] = None) -> float:
        """Obtém valor de um gauge"""
        gauge_key = f"{name}:{json.dumps(tags or {}, sort_keys=True)}"
        with self._lock:
            return self.gauges.get(gauge_key, 0.0)

class BotPerformanceMonitor:
    """Monitor de performance específico para o bot Telegram"""

    def __init__(self, metrics_collector: MetricsCollector):
        self.metrics = metrics_collector
        self.monitoring = False
        self.monitor_thread: Optional[threading.Thread] = None
        self.request_start_times: Dict[str, float] = {}

    def start_monitoring(self, interval: float = 1.0):
        """Inicia o monitoramento contínuo"""
        if self.monitoring:
            return

        self.monitoring = True
        self.monitor_thread = threading.Thread(
            target=self._monitoring_loop,
            args=(interval,),
            daemon=True
        )
        self.monitor_thread.start()
        logging.info("🔍 Monitoramento de performance iniciado")

    def stop_monitoring(self):
        """Para o monitoramento"""
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=5)
        logging.info("⏹️ Monitoramento de performance parado")

    def _monitoring_loop(self, interval: float):
        """Loop principal de monitoramento"""
        while self.monitoring:
            try:
                self._collect_system_metrics()
                time.sleep(interval)
            except Exception as e:
                logging.error(f"Erro no loop de monitoramento: {e}")

    def _collect_system_metrics(self):
        """Coleta métricas do sistema"""
        # CPU
        cpu_percent = psutil.cpu_percent(interval=None)
        self.metrics.set_gauge("system.cpu.percent", cpu_percent)

        # Memória
        memory = psutil.virtual_memory()
        self.metrics.set_gauge("system.memory.percent", memory.percent)
        self.metrics.set_gauge("system.memory.used_mb", memory.used / 1024 / 1024)
        self.metrics.set_gauge("system.memory.available_mb", memory.available / 1024 / 1024)

        # Disco
        disk = psutil.disk_usage('/')
        self.metrics.set_gauge("system.disk.percent", disk.percent)

        # Network
        net_io = psutil.net_io_counters()
        self.metrics.record_metric("system.network.bytes_sent", net_io.bytes_sent)
        self.metrics.record_metric("system.network.bytes_recv", net_io.bytes_recv)

        # Process específico (se possível detectar o processo do Python)
        current_process = psutil.Process()
        self.metrics.set_gauge("bot.process.cpu_percent", current_process.cpu_percent())
        self.metrics.set_gauge("bot.process.memory_mb", current_process.memory_info().rss / 1024 / 1024)
        self.metrics.set_gauge("bot.process.threads", current_process.num_threads())

    # Métodos para instrumentar o bot
    def record_request_start(self, request_id: str, request_type: str = "unknown"):
        """Registra início de uma requisição"""
        self.request_start_times[request_id] = time.time()
        self.metrics.increment_counter("bot.requests.started", {"type": request_type})

    def record_request_end(self, request_id: str, request_type: str = "unknown", success: bool = True):
        """Registra fim de uma requisição"""
        start_time = self.request_start_times.pop(request_id, None)
        if start_time:
            duration = time.time() - start_time
            self.metrics.record_metric("bot.request.duration", duration, {"type": request_type})

        status = "success" if success else "error"
        self.metrics.increment_counter("bot.requests.completed", {"type": request_type, "status": status})

    def record_vip_join_request(self, user_id: int, approved: bool):
        """Registra tentativa de entrada no VIP"""
        status = "approved" if approved else "denied"
        self.metrics.increment_counter("bot.vip.join_requests", {"status": status})
        self.metrics.record_metric("bot.vip.join_request_time", time.time())

    def record_database_query(self, table: str, operation: str, duration: float):
        """Registra query no banco de dados"""
        self.metrics.record_metric("bot.database.query_duration", duration, {
            "table": table,
            "operation": operation
        })
        self.metrics.increment_counter("bot.database.queries", {
            "table": table,
            "operation": operation
        })

    def record_telegram_api_call(self, method: str, duration: float, success: bool):
        """Registra chamada para API do Telegram"""
        status = "success" if success else "error"
        self.metrics.record_metric("bot.telegram.api_duration", duration, {"method": method})
        self.metrics.increment_counter("bot.telegram.api_calls", {
            "method": method,
            "status": status
        })

    def get_current_performance_summary(self) -> Dict[str, Any]:
        """Retorna resumo atual de performance"""
        return {
            "system": {
                "cpu_percent": self.metrics.get_gauge_value("system.cpu.percent"),
                "memory_percent": self.metrics.get_gauge_value("system.memory.percent"),
                "memory_used_mb": self.metrics.get_gauge_value("system.memory.used_mb"),
            },
            "bot_process": {
                "cpu_percent": self.metrics.get_gauge_value("bot.process.cpu_percent"),
                "memory_mb": self.metrics.get_gauge_value("bot.process.memory_mb"),
                "threads": self.metrics.get_gauge_value("bot.process.threads"),
            },
            "requests": {
                "total_started": sum(self.metrics.counters.get(k, 0) for k in self.metrics.counters if k.startswith("bot.requests.started")),
                "total_completed": sum(self.metrics.counters.get(k, 0) for k in self.metrics.counters if k.startswith("bot.requests.completed")),
                "avg_duration_last_60s": self.metrics.get_metric_stats("bot.request.duration", 60).get("avg", 0),
            },
            "vip": {
                "join_requests_approved": self.metrics.get_counter_value("bot.vip.join_requests", {"status": "approved"}),
                "join_requests_denied": self.metrics.get_counter_value("bot.vip.join_requests", {"status": "denied"}),
            }
        }

class PerformanceAnalyzer:
    """Analisador de performance para detectar problemas"""

    def __init__(self, monitor: BotPerformanceMonitor):
        self.monitor = monitor
        self.alerts: List[Dict[str, Any]] = []

    def analyze_performance(self) -> Dict[str, Any]:
        """Analisa performance atual e detecta problemas"""
        summary = self.monitor.get_current_performance_summary()
        issues = []
        recommendations = []

        # Análise de CPU
        cpu_percent = summary["system"]["cpu_percent"]
        if cpu_percent > 80:
            issues.append(f"CPU alta: {cpu_percent:.1f}%")
            recommendations.append("Considere otimizar handlers assíncronos")

        # Análise de memória
        memory_percent = summary["system"]["memory_percent"]
        if memory_percent > 85:
            issues.append(f"Memória alta: {memory_percent:.1f}%")
            recommendations.append("Verifique vazamentos de memória e implemente cleanup")

        # Análise de tempo de resposta
        avg_duration = summary["requests"]["avg_duration_last_60s"]
        if avg_duration > 2.0:
            issues.append(f"Tempo de resposta alto: {avg_duration:.2f}s")
            recommendations.append("Otimize queries de banco e chamadas externas")

        # Análise de threads
        thread_count = summary["bot_process"]["threads"]
        if thread_count > 100:
            issues.append(f"Muitas threads: {thread_count}")
            recommendations.append("Verifique vazamentos de thread e use pool")

        # Taxa de aprovação VIP
        approved = summary["vip"]["join_requests_approved"]
        denied = summary["vip"]["join_requests_denied"]
        total_vip = approved + denied
        if total_vip > 0:
            approval_rate = (approved / total_vip) * 100
            if approval_rate < 50:
                issues.append(f"Taxa de aprovação VIP baixa: {approval_rate:.1f}%")
                recommendations.append("Verifique lógica de validação de convites VIP")

        return {
            "timestamp": datetime.now().isoformat(),
            "summary": summary,
            "issues": issues,
            "recommendations": recommendations,
            "health_score": self._calculate_health_score(summary, issues)
        }

    def _calculate_health_score(self, summary: Dict[str, Any], issues: List[str]) -> int:
        """Calcula score de saúde (0-100)"""
        score = 100

        # Penalidades por uso de recursos
        cpu_percent = summary["system"]["cpu_percent"]
        memory_percent = summary["system"]["memory_percent"]

        score -= max(0, cpu_percent - 50)  # Penalidade acima de 50% CPU
        score -= max(0, memory_percent - 70)  # Penalidade acima de 70% memória

        # Penalidades por problemas
        score -= len(issues) * 10

        # Penalidade por tempo de resposta alto
        avg_duration = summary["requests"]["avg_duration_last_60s"]
        if avg_duration > 1.0:
            score -= (avg_duration - 1.0) * 20

        return max(0, min(100, int(score)))

# Instância global do sistema de monitoramento
_metrics_collector = MetricsCollector()
_performance_monitor = BotPerformanceMonitor(_metrics_collector)
_performance_analyzer = PerformanceAnalyzer(_performance_monitor)

# Funções de conveniência para uso no bot
def start_monitoring():
    """Inicia o monitoramento global"""
    _performance_monitor.start_monitoring()

def stop_monitoring():
    """Para o monitoramento global"""
    _performance_monitor.stop_monitoring()

def get_performance_summary():
    """Obtém resumo de performance atual"""
    return _performance_analyzer.analyze_performance()

def record_request(request_id: str, request_type: str = "telegram_update"):
    """Inicia o tracking de uma requisição"""
    _performance_monitor.record_request_start(request_id, request_type)

def end_request(request_id: str, request_type: str = "telegram_update", success: bool = True):
    """Finaliza o tracking de uma requisição"""
    _performance_monitor.record_request_end(request_id, request_type, success)

def record_vip_action(user_id: int, approved: bool):
    """Registra ação VIP"""
    _performance_monitor.record_vip_join_request(user_id, approved)

def record_db_query(table: str, operation: str, duration: float):
    """Registra query de banco"""
    _performance_monitor.record_database_query(table, operation, duration)

def record_telegram_call(method: str, duration: float, success: bool = True):
    """Registra chamada Telegram API"""
    _performance_monitor.record_telegram_api_call(method, duration, success)

# Context manager para timing automático
class TimedOperation:
    """Context manager para medir duração de operações"""

    def __init__(self, operation_name: str, tags: Dict[str, str] = None):
        self.operation_name = operation_name
        self.tags = tags or {}
        self.start_time = None

    def __enter__(self):
        self.start_time = time.time()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.start_time:
            duration = time.time() - self.start_time
            _metrics_collector.record_metric(self.operation_name, duration, self.tags)

# Decorator para monitorar funções automaticamente
def monitor_function(operation_name: str = None, tags: Dict[str, str] = None):
    """Decorator para monitorar duração de funções"""
    def decorator(func):
        nonlocal operation_name
        if operation_name is None:
            operation_name = f"function.{func.__name__}.duration"

        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                with TimedOperation(operation_name, tags):
                    return await func(*args, **kwargs)
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                with TimedOperation(operation_name, tags):
                    return func(*args, **kwargs)
            return sync_wrapper

    return decorator