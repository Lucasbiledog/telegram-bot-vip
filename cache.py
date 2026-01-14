# cache.py
import json
import logging
import os
from typing import Any, Optional, List
import asyncio
from datetime import datetime, timedelta

# Usar redis síncrono e fazer wrapper assíncrono para compatibilidade
try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False

LOG = logging.getLogger(__name__)

class CacheManager:
    """Sistema de cache Redis com fallback para memória local"""

    def __init__(self):
        self.redis_client = None
        self.local_cache = {}
        self.local_cache_expiry = {}
        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.use_redis = os.getenv("USE_REDIS", "true").lower() == "true"

    async def init_redis(self):
        """Inicializa conexão Redis com fallback para cache local"""
        if not self.use_redis or not REDIS_AVAILABLE:
            LOG.info("Redis desabilitado ou não disponível, usando cache local")
            return

        try:
            # Usar redis síncrono com pool de conexões
            self.redis_client = redis.Redis.from_url(
                self.redis_url,
                max_connections=20,
                socket_timeout=5,
                socket_connect_timeout=5,
                retry_on_timeout=True,
                decode_responses=True
            )
            # Teste de conectividade
            self.redis_client.ping()
            LOG.info("Redis conectado com sucesso")
        except Exception as e:
            LOG.warning(f"Falha ao conectar Redis: {e}, usando cache local")
            self.redis_client = None

    async def set(self, key: str, value: Any, ttl_seconds: int = 300):
        """Define valor no cache com TTL"""
        try:
            if self.redis_client:
                serialized = json.dumps(value) if not isinstance(value, str) else value
                # Executar em thread pool para não bloquear async
                await asyncio.get_event_loop().run_in_executor(
                    None, self.redis_client.setex, key, ttl_seconds, serialized
                )
            else:
                # Fallback para cache local
                self.local_cache[key] = value
                self.local_cache_expiry[key] = datetime.now() + timedelta(seconds=ttl_seconds)
        except Exception as e:
            LOG.warning(f"Erro ao definir cache {key}: {e}")

    async def get(self, key: str) -> Optional[Any]:
        """Recupera valor do cache"""
        try:
            if self.redis_client:
                # Executar em thread pool para não bloquear async
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self.redis_client.get, key
                )
                if result:
                    try:
                        return json.loads(result)
                    except json.JSONDecodeError:
                        return result
                return None
            else:
                # Fallback para cache local
                if key in self.local_cache:
                    if datetime.now() < self.local_cache_expiry.get(key, datetime.now()):
                        return self.local_cache[key]
                    else:
                        # Expired, remove
                        self.local_cache.pop(key, None)
                        self.local_cache_expiry.pop(key, None)
                return None
        except Exception as e:
            LOG.warning(f"Erro ao recuperar cache {key}: {e}")
            return None

    async def delete(self, key: str):
        """Remove chave do cache"""
        try:
            if self.redis_client:
                await asyncio.get_event_loop().run_in_executor(
                    None, self.redis_client.delete, key
                )
            else:
                self.local_cache.pop(key, None)
                self.local_cache_expiry.pop(key, None)
        except Exception as e:
            LOG.warning(f"Erro ao deletar cache {key}: {e}")

    async def exists(self, key: str) -> bool:
        """Verifica se chave existe no cache"""
        try:
            if self.redis_client:
                result = await asyncio.get_event_loop().run_in_executor(
                    None, self.redis_client.exists, key
                )
                return bool(result)
            else:
                return key in self.local_cache and datetime.now() < self.local_cache_expiry.get(key, datetime.now())
        except Exception as e:
            LOG.warning(f"Erro ao verificar cache {key}: {e}")
            return False

    async def clear_pattern(self, pattern: str):
        """Remove chaves que correspondem ao padrão"""
        try:
            if self.redis_client:
                keys = await asyncio.get_event_loop().run_in_executor(
                    None, self.redis_client.keys, pattern
                )
                if keys:
                    await asyncio.get_event_loop().run_in_executor(
                        None, self.redis_client.delete, *keys
                    )
            else:
                # Para cache local, remover chaves que começam com o padrão
                pattern = pattern.replace("*", "")
                keys_to_remove = [k for k in self.local_cache.keys() if k.startswith(pattern)]
                for key in keys_to_remove:
                    self.local_cache.pop(key, None)
                    self.local_cache_expiry.pop(key, None)
        except Exception as e:
            LOG.warning(f"Erro ao limpar cache pattern {pattern}: {e}")

# Instância global do cache
cache = CacheManager()

# Funções de conveniência para caching de dados específicos
async def cache_price(symbol: str, price: float, ttl: int = 1800):
    """Cache preço de criptomoeda por 30 minutos"""
    await cache.set(f"price:{symbol.lower()}", price, ttl)

async def get_cached_price(symbol: str) -> Optional[float]:
    """Recupera preço do cache"""
    result = await cache.get(f"price:{symbol.lower()}")
    return float(result) if result is not None else None

async def cache_admin_list(admin_ids: List[int], ttl: int = 300):
    """Cache lista de administradores por 5 minutos"""
    await cache.set("admin_list", admin_ids, ttl)

async def get_cached_admin_list() -> Optional[List[int]]:
    """Recupera lista de administradores do cache"""
    return await cache.get("admin_list")

async def cache_user_vip_status(user_id: int, is_vip: bool, expires_at: str = None, ttl: int = 600):
    """Cache status VIP do usuário por 10 minutos"""
    data = {"is_vip": is_vip, "expires_at": expires_at}
    await cache.set(f"vip_status:{user_id}", data, ttl)

async def get_cached_vip_status(user_id: int) -> Optional[dict]:
    """Recupera status VIP do cache"""
    return await cache.get(f"vip_status:{user_id}")

async def cache_payment_result(tx_hash: str, result: dict, ttl: int = 3600):
    """Cache resultado de validação de pagamento por 1 hora"""
    await cache.set(f"payment:{tx_hash}", result, ttl)

async def get_cached_payment_result(tx_hash: str) -> Optional[dict]:
    """Recupera resultado de pagamento do cache"""
    return await cache.get(f"payment:{tx_hash}")

async def invalidate_user_cache(user_id: int):
    """Invalida todos os caches relacionados a um usuário"""
    await cache.delete(f"vip_status:{user_id}")

async def invalidate_price_cache():
    """Invalida cache de preços"""
    await cache.clear_pattern("price:*")