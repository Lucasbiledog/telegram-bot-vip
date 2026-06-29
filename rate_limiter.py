# rate_limiter.py
import asyncio
import time
from typing import Dict, Optional
from collections import defaultdict, deque
import logging

LOG = logging.getLogger(__name__)

class RateLimiter:
    """Sistema de rate limiting inteligente para APIs e operações"""

    def __init__(self,
                 max_requests: int,
                 time_window: int,
                 max_concurrent: int = None):
        self.max_requests = max_requests
        self.time_window = time_window
        self.max_concurrent = max_concurrent or max_requests
        self.requests: Dict[str, deque] = defaultdict(deque)
        self.semaphores: Dict[str, asyncio.Semaphore] = {}
        # Criar semáforo global (lazy initialization)
        self._global_semaphore = None

    @property
    def global_semaphore(self):
        """Lazy initialization do semáforo global"""
        if self._global_semaphore is None:
            self._global_semaphore = asyncio.Semaphore(self.max_concurrent)
        return self._global_semaphore

    def _cleanup_old_requests(self, key: str, now: float):
        """Remove requisições antigas da janela de tempo"""
        while (self.requests[key] and
               now - self.requests[key][0] > self.time_window):
            self.requests[key].popleft()

    async def acquire(self, key: str = "global") -> bool:
        """Adquire permissão para fazer uma requisição"""
        now = time.time()

        # Criar semáforo específico se não existir
        if key not in self.semaphores:
            self.semaphores[key] = asyncio.Semaphore(self.max_concurrent)

        # Limpar requisições antigas
        self._cleanup_old_requests(key, now)

        # Verificar se excedeu o limite
        if len(self.requests[key]) >= self.max_requests:
            return False

        # Adquirir semáforo (bloqueia se muitas requisições simultâneas)
        await self.semaphores[key].acquire()
        await self.global_semaphore.acquire()

        # Registrar a requisição
        self.requests[key].append(now)

        return True

    def release(self, key: str = "global"):
        """Libera os semáforos após a requisição"""
        try:
            if key in self.semaphores:
                self.semaphores[key].release()
            self.global_semaphore.release()
        except ValueError:
            # Semáforo já foi liberado
            pass

    def get_wait_time(self, key: str = "global") -> float:
        """Calcula tempo de espera até próxima requisição disponível"""
        now = time.time()
        self._cleanup_old_requests(key, now)

        if len(self.requests[key]) < self.max_requests:
            return 0.0

        # Tempo até a requisição mais antiga expirar
        oldest_request = self.requests[key][0]
        return max(0.0, self.time_window - (now - oldest_request))

class TelegramRateLimiter:
    """Rate limiter específico para Telegram API"""

    def __init__(self):
        # Limites do Telegram
        self.global_limiter = RateLimiter(30, 1)  # 30 req/seg global
        self.chat_limiter = RateLimiter(20, 60)   # 20 req/min por chat
        self.group_limiter = RateLimiter(20, 60)  # 20 req/min para grupos

    async def acquire_for_chat(self, chat_id: int, is_group: bool = False) -> bool:
        """Adquire permissão para enviar mensagem para um chat"""
        # Sempre verificar limite global primeiro
        global_ok = await self.global_limiter.acquire("global")
        if not global_ok:
            return False

        # Verificar limite específico do chat
        limiter = self.group_limiter if is_group else self.chat_limiter
        chat_key = f"chat_{chat_id}"

        chat_ok = await limiter.acquire(chat_key)
        if not chat_ok:
            # Liberar global se não conseguiu o específico
            self.global_limiter.release("global")
            return False

        return True

    def release_for_chat(self, chat_id: int, is_group: bool = False):
        """Libera rate limit para um chat"""
        self.global_limiter.release("global")

        limiter = self.group_limiter if is_group else self.chat_limiter
        chat_key = f"chat_{chat_id}"
        limiter.release(chat_key)

class APIRateLimiter:
    """Rate limiter para APIs externas (CoinGecko, blockchain, etc.)"""

    def __init__(self):
        # Diferentes limitadores para diferentes APIs
        # CoinGecko free tier: 10-30 req/min, sendo conservador com 10 req/min
        self.coingecko_limiter = RateLimiter(10, 60, 2)  # 10 req/min, 2 simultâneas (REDUZIDO para evitar 429)
        self.blockchain_limiter = RateLimiter(100, 60, 10)  # 100 req/min, 10 simultâneas
        self.general_limiter = RateLimiter(60, 60, 8)    # Geral: 60 req/min, 8 simultâneas

    async def acquire_for_api(self, api_name: str) -> bool:
        """Adquire permissão para chamada de API"""
        limiter_map = {
            'coingecko': self.coingecko_limiter,
            'blockchain': self.blockchain_limiter,
            'general': self.general_limiter
        }

        limiter = limiter_map.get(api_name, self.general_limiter)
        return await limiter.acquire(api_name)

    def release_for_api(self, api_name: str):
        """Libera rate limit para API"""
        limiter_map = {
            'coingecko': self.coingecko_limiter,
            'blockchain': self.blockchain_limiter,
            'general': self.general_limiter
        }

        limiter = limiter_map.get(api_name, self.general_limiter)
        limiter.release(api_name)

    def get_wait_time_for_api(self, api_name: str) -> float:
        """Tempo de espera para próxima chamada de API"""
        limiter_map = {
            'coingecko': self.coingecko_limiter,
            'blockchain': self.blockchain_limiter,
            'general': self.general_limiter
        }

        limiter = limiter_map.get(api_name, self.general_limiter)
        return limiter.get_wait_time(api_name)

# Instâncias globais
telegram_limiter = TelegramRateLimiter()
api_limiter = APIRateLimiter()

# Decoradores para facilitar uso
def with_telegram_rate_limit(func):
    """Decorator para funções que enviam mensagens Telegram"""
    async def wrapper(*args, **kwargs):
        # Extrair chat_id dos argumentos
        chat_id = None
        is_group = False

        # Tentar extrair chat_id de diferentes formas
        if 'chat_id' in kwargs:
            chat_id = kwargs['chat_id']
        elif len(args) > 0:
            if hasattr(args[0], 'effective_chat'):
                chat_id = args[0].effective_chat.id
                is_group = args[0].effective_chat.type in ['group', 'supergroup']
            elif isinstance(args[0], int):
                chat_id = args[0]

        if chat_id is None:
            # Fallback: usar rate limiting global
            acquired = await telegram_limiter.global_limiter.acquire("fallback")
            if not acquired:
                LOG.warning("Rate limit global excedido")
                await asyncio.sleep(1.0)
                return await func(*args, **kwargs)
        else:
            # Rate limiting específico do chat
            acquired = await telegram_limiter.acquire_for_chat(chat_id, is_group)
            if not acquired:
                wait_time = telegram_limiter.global_limiter.get_wait_time("global")
                LOG.warning(f"Rate limit excedido para chat {chat_id}, aguardando {wait_time:.1f}s")
                await asyncio.sleep(max(wait_time, 1.0))
                return await func(*args, **kwargs)

        try:
            return await func(*args, **kwargs)
        finally:
            if chat_id:
                telegram_limiter.release_for_chat(chat_id, is_group)
            else:
                telegram_limiter.global_limiter.release("fallback")

    return wrapper

def with_api_rate_limit(api_name: str = "general"):
    """Decorator para funções que fazem chamadas de API externa"""
    def decorator(func):
        async def wrapper(*args, **kwargs):
            acquired = await api_limiter.acquire_for_api(api_name)
            if not acquired:
                wait_time = api_limiter.get_wait_time_for_api(api_name)
                LOG.warning(f"Rate limit excedido para API {api_name}, aguardando {wait_time:.1f}s")
                await asyncio.sleep(max(wait_time, 1.0))
                # Tentar novamente após espera
                acquired = await api_limiter.acquire_for_api(api_name)
                if not acquired:
                    raise Exception(f"Rate limit persistente para API {api_name}")

            try:
                return await func(*args, **kwargs)
            finally:
                api_limiter.release_for_api(api_name)

        return wrapper
    return decorator

# Funções utilitárias
async def smart_delay(operation_type: str = "general",
                     base_delay: float = 0.1,
                     max_delay: float = 5.0) -> float:
    """Calcula delay inteligente baseado no tipo de operação"""

    delay_map = {
        'telegram_message': 0.05,      # 50ms entre mensagens Telegram
        'database_write': 0.01,        # 10ms entre writes no DB
        'api_call': 0.2,              # 200ms entre API calls
        'file_upload': 0.5,           # 500ms entre uploads
        'general': base_delay
    }

    calculated_delay = delay_map.get(operation_type, base_delay)
    final_delay = min(calculated_delay, max_delay)

    await asyncio.sleep(final_delay)
    return final_delay

async def batch_with_rate_limit(items,
                               processor_func,
                               batch_size: int = 10,
                               delay_between_batches: float = 1.0):
    """Processa itens em lotes com rate limiting"""
    results = []

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]

        # Processar lote em paralelo
        batch_results = await asyncio.gather(
            *[processor_func(item) for item in batch],
            return_exceptions=True
        )

        results.extend(batch_results)

        # Delay entre lotes (exceto no último)
        if i + batch_size < len(items):
            await asyncio.sleep(delay_between_batches)

    return results