"""
Handler VIP Otimizado para Alta Concorrência
Implementa melhorias de performance baseadas em análise de stress testing
"""

import asyncio
import time
import logging
import hashlib
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
from dataclasses import dataclass
from telegram import Update
from telegram.ext import ContextTypes
from sqlalchemy.orm import Session
from concurrent.futures import ThreadPoolExecutor
import redis
from functools import wraps

# Para usar com o sistema de performance monitor
try:
    from performance_monitor import (
        record_request, end_request, record_vip_action,
        record_db_query, TimedOperation, monitor_function
    )
    MONITORING_AVAILABLE = True
except ImportError:
    MONITORING_AVAILABLE = False
    # Fallback decorators se monitoring não disponível
    def monitor_function(*args, **kwargs):
        def decorator(func):
            return func
        return decorator

@dataclass
class VipRequestMetrics:
    """Métricas de uma requisição VIP"""
    user_id: int
    request_time: float
    processing_time: float
    cache_hit: bool
    db_queries: int
    success: bool
    error_message: Optional[str] = None

class OptimizedVipCache:
    """Sistema de cache otimizado para operações VIP"""

    def __init__(self, redis_client=None, default_ttl: int = 300):
        self.redis_client = redis_client
        self.default_ttl = default_ttl
        self.local_cache: Dict[str, Any] = {}
        self.cache_stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0
        }

    def _get_key(self, prefix: str, *args) -> str:
        """Gera chave de cache consistente"""
        key_data = f"{prefix}:" + ":".join(str(arg) for arg in args)
        return hashlib.md5(key_data.encode()).hexdigest()[:16]

    async def get(self, prefix: str, *args) -> Optional[Any]:
        """Busca valor no cache (Redis primeiro, depois local)"""
        key = self._get_key(prefix, *args)

        try:
            # Tentar Redis primeiro se disponível
            if self.redis_client:
                value = await self.redis_client.get(key)
                if value:
                    self.cache_stats["hits"] += 1
                    return json.loads(value)

            # Fallback para cache local
            if key in self.local_cache:
                entry = self.local_cache[key]
                if time.time() < entry["expires"]:
                    self.cache_stats["hits"] += 1
                    return entry["value"]
                else:
                    del self.local_cache[key]

        except Exception as e:
            logging.warning(f"Erro ao buscar cache {key}: {e}")

        self.cache_stats["misses"] += 1
        return None

    async def set(self, prefix: str, value: Any, ttl: Optional[int] = None, *args):
        """Define valor no cache"""
        key = self._get_key(prefix, *args)
        ttl = ttl or self.default_ttl

        try:
            # Tentar Redis primeiro
            if self.redis_client:
                await self.redis_client.setex(key, ttl, json.dumps(value))
            else:
                # Fallback para cache local
                self.local_cache[key] = {
                    "value": value,
                    "expires": time.time() + ttl
                }

            self.cache_stats["sets"] += 1

        except Exception as e:
            logging.warning(f"Erro ao definir cache {key}: {e}")

    async def invalidate(self, prefix: str, *args):
        """Invalida entrada de cache"""
        key = self._get_key(prefix, *args)

        try:
            if self.redis_client:
                await self.redis_client.delete(key)

            if key in self.local_cache:
                del self.local_cache[key]

        except Exception as e:
            logging.warning(f"Erro ao invalidar cache {key}: {e}")

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas do cache"""
        total_requests = self.cache_stats["hits"] + self.cache_stats["misses"]
        hit_rate = (self.cache_stats["hits"] / total_requests * 100) if total_requests > 0 else 0

        return {
            "hits": self.cache_stats["hits"],
            "misses": self.cache_stats["misses"],
            "sets": self.cache_stats["sets"],
            "hit_rate_percent": round(hit_rate, 2),
            "local_cache_size": len(self.local_cache)
        }

class OptimizedVipHandler:
    """Handler VIP otimizado para alta concorrência"""

    def __init__(self, session_factory, bot, group_vip_id: int, redis_client=None):
        self.session_factory = session_factory
        self.bot = bot
        self.group_vip_id = group_vip_id
        self.cache = OptimizedVipCache(redis_client)

        # Pool de threads para operações I/O blocking
        self.thread_pool = ThreadPoolExecutor(max_workers=10, thread_name_prefix="vip_handler")

        # Rate limiting inteligente
        self.rate_limiter = IntelligentRateLimiter()

        # Métricas
        self.metrics: List[VipRequestMetrics] = []
        self.start_time = time.time()

    @monitor_function("vip_handler.join_request")
    async def handle_join_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """Handler otimizado para join requests"""
        request_start = time.time()
        request_id = f"vip_join_{update.chat_join_request.from_user.id}_{int(request_start)}"

        if MONITORING_AVAILABLE:
            record_request(request_id, "vip_join_request")

        try:
            req = update.chat_join_request
            if not req or req.chat.id != self.group_vip_id:
                return False

            user_id = req.from_user.id
            invite_link = req.invite_link.invite_link if req.invite_link else None

            # Rate limiting inteligente
            if not await self.rate_limiter.allow_request(user_id, "vip_join"):
                await self._decline_request(req.chat.id, user_id)
                return False

            # Validação rápida com cache
            is_valid = await self._validate_vip_request_cached(user_id, invite_link)

            if is_valid:
                success = await self._approve_request(req.chat.id, user_id)
                if MONITORING_AVAILABLE:
                    record_vip_action(user_id, success)
            else:
                await self._decline_request(req.chat.id, user_id)
                if MONITORING_AVAILABLE:
                    record_vip_action(user_id, False)

            # Cleanup assíncrono do invite link
            if invite_link:
                asyncio.create_task(self._revoke_invite_link_async(invite_link))

            return is_valid

        except Exception as e:
            logging.error(f"Erro no handler VIP: {e}", exc_info=True)
            return False

        finally:
            processing_time = time.time() - request_start
            if MONITORING_AVAILABLE:
                end_request(request_id, "vip_join_request", True)

    async def _validate_vip_request_cached(self, user_id: int, invite_link: Optional[str]) -> bool:
        """Validação com cache para performance"""
        if not invite_link:
            return False

        # Verificar cache primeiro
        cache_key = f"vip_validation_{user_id}_{invite_link}"
        cached_result = await self.cache.get("vip_validation", user_id, invite_link)

        if cached_result is not None:
            return cached_result["valid"]

        # Executar validação em thread pool para não bloquear
        with TimedOperation("vip_handler.db_validation"):
            result = await asyncio.get_event_loop().run_in_executor(
                self.thread_pool,
                self._validate_vip_membership_sync,
                user_id,
                invite_link
            )

        # Cache resultado por 60 segundos
        await self.cache.set("vip_validation", {"valid": result}, 60, user_id, invite_link)

        return result

    def _validate_vip_membership_sync(self, user_id: int, invite_link: str) -> bool:
        """Validação síncrona de membership VIP (executa em thread pool)"""
        query_start = time.time()

        try:
            with self.session_factory() as session:
                # Query otimizada com índices
                from main import VipMembership, now_utc

                vm = session.query(VipMembership).filter(
                    VipMembership.invite_link == invite_link,
                    VipMembership.user_id == user_id,
                    VipMembership.active == True
                ).first()

                if not vm:
                    return False

                # Verificar expiração
                if vm.expires_at and vm.expires_at <= now_utc():
                    return False

                return True

        except Exception as e:
            logging.error(f"Erro na validação VIP: {e}")
            return False

        finally:
            query_duration = time.time() - query_start
            if MONITORING_AVAILABLE:
                record_db_query("vip_membership", "select", query_duration)

    async def _approve_request(self, chat_id: int, user_id: int) -> bool:
        """Aprova join request com retry"""
        return await self._telegram_api_with_retry(
            self.bot.approve_chat_join_request,
            chat_id=chat_id,
            user_id=user_id
        )

    async def _decline_request(self, chat_id: int, user_id: int) -> bool:
        """Recusa join request com retry"""
        return await self._telegram_api_with_retry(
            self.bot.decline_chat_join_request,
            chat_id=chat_id,
            user_id=user_id
        )

    async def _telegram_api_with_retry(self, api_method, max_retries: int = 3, **kwargs) -> bool:
        """Executa método da API Telegram com retry e rate limiting"""
        for attempt in range(max_retries):
            try:
                start_time = time.time()

                # Rate limiting para API do Telegram
                await self.rate_limiter.wait_for_telegram_api()

                result = await api_method(**kwargs)

                if MONITORING_AVAILABLE:
                    duration = time.time() - start_time
                    record_telegram_call(api_method.__name__, duration, True)

                return True

            except Exception as e:
                if attempt == max_retries - 1:
                    logging.error(f"Falha na API Telegram após {max_retries} tentativas: {e}")
                    if MONITORING_AVAILABLE:
                        duration = time.time() - start_time
                        record_telegram_call(api_method.__name__, duration, False)
                    return False

                # Backoff exponencial
                await asyncio.sleep(2 ** attempt)

        return False

    async def _revoke_invite_link_async(self, invite_link: str):
        """Revoga link de convite de forma assíncrona"""
        try:
            await asyncio.sleep(0.1)  # Small delay para não interferir com response
            await self.bot.revoke_chat_invite_link(
                chat_id=self.group_vip_id,
                invite_link=invite_link
            )
        except Exception as e:
            logging.warning(f"Erro ao revogar link {invite_link}: {e}")

    def get_performance_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas de performance"""
        uptime = time.time() - self.start_time

        return {
            "uptime_seconds": uptime,
            "cache_stats": self.cache.get_stats(),
            "rate_limiter_stats": self.rate_limiter.get_stats(),
            "thread_pool_active": self.thread_pool._threads,
        }

class IntelligentRateLimiter:
    """Rate limiter inteligente que se adapta à carga"""

    def __init__(self):
        self.request_counts: Dict[str, List[float]] = {}
        self.telegram_api_last_call = 0
        self.telegram_api_min_interval = 0.05  # 20 calls/second max

        # Configurações adaptáveis
        self.max_requests_per_minute = 60
        self.burst_allowance = 10
        self.stats = {
            "requests_allowed": 0,
            "requests_blocked": 0,
            "adaptations": 0
        }

    async def allow_request(self, user_id: int, request_type: str) -> bool:
        """Verifica se requisição deve ser permitida"""
        now = time.time()
        key = f"{user_id}:{request_type}"

        # Inicializar se necessário
        if key not in self.request_counts:
            self.request_counts[key] = []

        # Limpar requisições antigas (mais de 1 minuto)
        cutoff = now - 60
        self.request_counts[key] = [
            req_time for req_time in self.request_counts[key]
            if req_time > cutoff
        ]

        # Verificar limite
        current_count = len(self.request_counts[key])

        if current_count >= self.max_requests_per_minute:
            self.stats["requests_blocked"] += 1
            return False

        # Verificar burst
        last_minute_requests = [
            req_time for req_time in self.request_counts[key]
            if req_time > now - 10  # Últimos 10 segundos
        ]

        if len(last_minute_requests) >= self.burst_allowance:
            self.stats["requests_blocked"] += 1
            return False

        # Permitir requisição
        self.request_counts[key].append(now)
        self.stats["requests_allowed"] += 1

        # Adaptação inteligente baseada na carga
        self._adapt_limits()

        return True

    async def wait_for_telegram_api(self):
        """Aguarda rate limit da API do Telegram"""
        now = time.time()
        time_since_last = now - self.telegram_api_last_call

        if time_since_last < self.telegram_api_min_interval:
            sleep_time = self.telegram_api_min_interval - time_since_last
            await asyncio.sleep(sleep_time)

        self.telegram_api_last_call = time.time()

    def _adapt_limits(self):
        """Adapta limites baseado na carga atual"""
        total_requests = self.stats["requests_allowed"] + self.stats["requests_blocked"]

        if total_requests > 0:
            block_rate = self.stats["requests_blocked"] / total_requests

            # Se muitos requests estão sendo bloqueados, relaxar um pouco
            if block_rate > 0.2:  # Mais de 20% bloqueados
                self.max_requests_per_minute = min(120, self.max_requests_per_minute + 10)
                self.stats["adaptations"] += 1

            # Se poucos blocks, pode apertar um pouco
            elif block_rate < 0.05:  # Menos de 5% bloqueados
                self.max_requests_per_minute = max(30, self.max_requests_per_minute - 5)
                self.stats["adaptations"] += 1

    def get_stats(self) -> Dict[str, Any]:
        """Retorna estatísticas do rate limiter"""
        total = self.stats["requests_allowed"] + self.stats["requests_blocked"]
        block_rate = (self.stats["requests_blocked"] / total * 100) if total > 0 else 0

        return {
            "requests_allowed": self.stats["requests_allowed"],
            "requests_blocked": self.stats["requests_blocked"],
            "block_rate_percent": round(block_rate, 2),
            "current_limit_per_minute": self.max_requests_per_minute,
            "adaptations_made": self.stats["adaptations"],
            "active_users": len(self.request_counts)
        }

# Funções de integração com o bot principal

def integrate_optimized_vip_handler(application, session_factory, group_vip_id: int, redis_client=None):
    """Integra o handler otimizado ao bot principal"""

    optimized_handler = OptimizedVipHandler(
        session_factory=session_factory,
        bot=application.bot,
        group_vip_id=group_vip_id,
        redis_client=redis_client
    )

    # Wrapper para compatibilidade com telegram.ext
    async def vip_join_request_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        return await optimized_handler.handle_join_request(update, context)

    # Substituir handler existente
    from telegram.ext import ChatJoinRequestHandler
    application.add_handler(
        ChatJoinRequestHandler(vip_join_request_wrapper),
        group=0  # Prioridade alta
    )

    # Adicionar comando para estatísticas (opcional)
    async def vip_stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in ADMIN_IDS:  # Definir ADMIN_IDS
            return

        stats = optimized_handler.get_performance_stats()
        stats_text = f"""
📊 **Estatísticas VIP Handler Otimizado**

🔄 **Uptime:** {stats['uptime_seconds']:.0f}s

💾 **Cache:**
• Hit Rate: {stats['cache_stats']['hit_rate_percent']:.1f}%
• Hits: {stats['cache_stats']['hits']}
• Misses: {stats['cache_stats']['misses']}

🚦 **Rate Limiter:**
• Permitidos: {stats['rate_limiter_stats']['requests_allowed']}
• Bloqueados: {stats['rate_limiter_stats']['requests_blocked']}
• Taxa de Block: {stats['rate_limiter_stats']['block_rate_percent']:.1f}%
• Usuários Ativos: {stats['rate_limiter_stats']['active_users']}

🧵 **Thread Pool:**
• Threads Ativas: {stats['thread_pool_active']}
        """

        await update.message.reply_text(stats_text, parse_mode='Markdown')

    from telegram.ext import CommandHandler
    application.add_handler(CommandHandler("vip_stats", vip_stats_cmd))

    logging.info("🚀 Handler VIP otimizado integrado com sucesso")
    return optimized_handler

# Exemplo de uso:
"""
# No seu main.py, substitua o handler VIP existente por:

redis_client = redis.Redis(host='localhost', port=6379, decode_responses=True)  # Opcional
optimized_vip = integrate_optimized_vip_handler(
    application=application,
    session_factory=SessionLocal,
    group_vip_id=GROUP_VIP_ID,
    redis_client=redis_client  # Opcional - usa cache local se None
)
"""